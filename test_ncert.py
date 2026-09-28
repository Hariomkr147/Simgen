"""NCERT grounding test.

    python test_ncert.py                 # runs against NCERT_DSN if set (your real DB: a live smoke
                                         # test that prints hits per topic), else a throwaway local
                                         # Postgres if `pip install pgserver` is present, else skips DB tests
"""
import json
import os
import sys
import tempfile

from simgen import pipeline, retrieve
from simgen.retrieve import NCERTError, keywords, ncert_chunks

FIXTURE = [  # (class_level, text) -- stand-ins shaped like knowledge_graph.graph_nodes rows
    (10, "Reflection of light: the angle of incidence is equal to the angle of reflection. The incident ray, "
         "the reflected ray and the normal at the point of incidence all lie in the same plane."),
    (10, "Light travels in a straight line. A plane mirror forms a virtual image of the same size."),
    (10, "Ohm's law: the potential difference across a conductor is directly proportional to the current."),
    (9,  "A simple pendulum: the time period depends on the length of the string and on g, not on the mass."),
    (9,  "Sound needs a medium to travel. Reflection of sound is called an echo."),
]


def test_keywords():
    assert keywords("Class 10 Science: Reflection of Light") == ["reflection", "light"]
    assert keywords("Class 9 Science: Simple Pendulum") == ["simple", "pendulum"]
    assert keywords("Class 8 Maths: Chapter 3") == []                     # nothing searchable
    assert keywords("Ohm's Law; DROP TABLE x--") == ["ohm", "law", "drop", "table"]   # no tsquery syntax survives


def _db_tests(dsn):
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS knowledge_graph")
        c.execute("DROP TABLE IF EXISTS knowledge_graph.graph_nodes")
        c.execute("CREATE TABLE knowledge_graph.graph_nodes (id serial, class_level int, ground_truth_content text)")
        for g, t in FIXTURE:
            c.execute("INSERT INTO knowledge_graph.graph_nodes (class_level, ground_truth_content) VALUES (%s,%s)", (g, t))
        old = c.execute("SELECT count(*) FROM knowledge_graph.graph_nodes WHERE to_tsvector('english', ground_truth_content) "
                        "@@ plainto_tsquery('english', 'Class 10 Science: Reflection of Light')").fetchone()[0]
    assert old == 0, "the old query (whole topic string ANDed) should find nothing -- that was the bug"

    rows = ncert_chunks("Class 10 Science: Reflection of Light", 10, dsn=dsn)
    assert rows and "angle of incidence" in rows[0], rows                  # best match first
    assert all("pendulum" not in r for r in rows)
    assert not any("echo" in r for r in ncert_chunks("Class 10 Science: Reflection of Light", 10, dsn=dsn)), "grade filter leaked class 9"
    assert any("echo" in r for r in ncert_chunks("Reflection of sound", 9, dsn=dsn))
    assert len(ncert_chunks("Class 10 Science: Reflection of Light", 10, k=1, dsn=dsn)) == 1
    assert any("pendulum" in r for r in ncert_chunks("Class 9 Science: Simple Pendulum Motion", 9, dsn=dsn)), "OR top-up"
    assert ncert_chunks("Class 10 Science: Quantum Chromodynamics", 10, dsn=dsn) == []

    # strict grounding through the pipeline: asked for, not delivered -> error, never silently ungrounded
    os.environ["NCERT_DSN"] = dsn
    ctx = pipeline.grounding("Class 10 Science: Reflection of Light", 10, True)
    assert "angle of reflection" in ctx
    try:
        pipeline.grounding("Class 10 Science: Quantum Chromodynamics", 10, True)
        raise AssertionError("expected NCERTError for a topic with no NCERT text")
    except NCERTError:
        pass
    assert pipeline.grounding("Class 10 Science: Quantum Chromodynamics", 10, False) == ""
    try:
        ncert_chunks("Reflection of light", 10, dsn="postgresql://nobody@127.0.0.1:1/none")
        raise AssertionError("expected NCERTError for an unreachable DB")
    except NCERTError:
        pass
    return ctx


def test_evidence_check():
    ctx = FIXTURE[0][1]
    good = {"evidence": ["The angle of incidence is equal to the angle of reflection."]}
    made_up = {"evidence": ["Light bends towards the normal in a mirror."]}
    assert pipeline.verify_evidence(good, ctx) == (1, 1)
    assert pipeline.verify_evidence(made_up, ctx) == (1, 0)
    assert pipeline.verify_evidence({}, ctx) == (0, 0)


def test_blueprint_must_quote_source(tmp=None):
    """A teacher that ignores the source (no verifiable quotes) is retried once, then rejected."""
    from simgen import llm
    os.environ.update({"LLM_BASE_URL": "x", "LLM_API_KEY": "x", "MODEL_t_ID": "m", "MODEL_t_IN": "1", "MODEL_t_OUT": "1"})
    plans = {"ungrounded": {"title": "T", "steps": [], "evidence": ["totally invented sentence"]},
             "grounded": {"title": "T", "steps": [], "evidence": [FIXTURE[0][1].split(".")[0]]}}
    pipeline.grounding = lambda *a: FIXTURE[0][1]
    pipeline.BLUEPRINTS = __import__("pathlib").Path(tempfile.mkdtemp())
    for name, ok in (("ungrounded", False), ("grounded", True)):
        llm.TRANSPORT = lambda alias, system, user, max_tokens=0, n=name: (json.dumps(plans[n]), 10, 10)
        try:
            rec, _ = pipeline.blueprint("Reflection of light", 10, "t", force=True)
            assert ok and rec["evidence"] == {"quoted": 1, "verified": 1}, rec
        except ValueError:
            assert not ok


def main():
    test_keywords(); test_evidence_check()
    real = pipeline.grounding
    test_blueprint_must_quote_source(); pipeline.grounding = real
    dsn = os.environ.get("NCERT_DSN")
    if dsn:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=5) as c:
            n = c.execute("SELECT count(*), count(ground_truth_content) FROM knowledge_graph.graph_nodes").fetchone()
        print(f"LIVE DB: {n[0]} graph nodes, {n[1]} with content")
        topics = [l.strip() for l in open("topics.txt", encoding="utf-8") if l.strip() and not l.startswith("#")][:15]
        miss = 0
        for t in topics:
            t, _, g = t.partition("|")
            t, g = t.strip(), int(g) if g.strip().isdigit() else None
            hits = ncert_chunks(t, g)
            miss += not hits
            print(f"  {len(hits)} chunk(s)  {t}" + (f"  | top: {hits[0][:70]!r}" if hits else "  | NOT GROUNDED"))
        print(f"{len(topics) - miss}/{len(topics)} topics grounded")
    else:
        try:
            import pgserver
        except ImportError:
            print("skip DB tests (no NCERT_DSN, no pgserver)"); return
        srv = pgserver.get_server(tempfile.mkdtemp())
        _db_tests(srv.get_uri())
        print("ok fixture DB tests (retrieval, grade filter, OR top-up, strict errors)")
    print("all passed")


if __name__ == "__main__":
    main()
