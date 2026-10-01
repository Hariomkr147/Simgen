"""Offline self-check. No network, no API keys. `python test_simgen.py`"""
import json
import os
import tempfile
from pathlib import Path

os.environ.update({
    "MODEL_t_BASE_URL": "x", "MODEL_t_API_KEY": "x", "MODEL_t_ID": "t",
    "MODEL_t_IN": "10", "MODEL_t_OUT": "50",
    "MODEL_s_BASE_URL": "x", "MODEL_s_API_KEY": "x", "MODEL_s_ID": "s",
    "MODEL_s_IN": "0.30", "MODEL_s_OUT": "2.50",
})

from simgen import llm, pipeline, retrieve  # noqa: E402
from simgen.__main__ import report_md, slug  # noqa: E402

pipeline.BLUEPRINTS = Path(tempfile.mkdtemp())  # never touch the real blueprints/ store

GOOD = """<!DOCTYPE html><html><head><style>body{}</style></head><body>
<canvas id=c width=400 height=300></canvas>
<input type="range" min=0 max=10><button>Go</button>
<p>What happens when length doubles? Why does mass not matter? What is T at L=1m?</p>
<script>const g=9.81;</script>
<!--PADDING""" + "p" * 3000 + """--></body></html>"""

PLAN = {"title": "Pendulum", "grade": 9, "physics": {"equations": ["T=2*pi*sqrt(L/g)"]}}
TOPIC = pipeline.EXAMPLE_TOPIC   # a complete, working TOPIC script (the shell's own example)


def test_cost():
    spec = llm.model_spec("t")
    assert llm.cost_usd(spec, 1_000_000, 0) == 10
    assert llm.cost_usd(spec, 0, 1_000_000) == 50
    assert abs(llm.cost_usd(spec, 100, 500) - (0.001 + 0.025)) < 1e-9
    assert abs(llm.cost_usd(llm.model_spec("s"), 100, 500) - (0.00003 + 0.00125)) < 1e-9


def test_shared_gateway_fallback():
    """OpenRouter setup: one LLM_BASE_URL / LLM_API_KEY covers every alias."""
    os.environ.update({"LLM_BASE_URL": "https://openrouter.ai/api/v1", "LLM_API_KEY": "sk-or-x",
                       "MODEL_g_ID": "anthropic/claude-opus-5", "MODEL_g_IN": "5", "MODEL_g_OUT": "25"})
    spec = llm.model_spec("g")
    assert spec["base_url"].endswith("openrouter.ai/api/v1") and spec["api_key"] == "sk-or-x"
    os.environ["MODEL_g_API_KEY"] = "per-alias-wins"
    assert llm.model_spec("g")["api_key"] == "per-alias-wins"
    del os.environ["MODEL_g_API_KEY"]


def test_reported_cost_beats_table():
    """OpenRouter bills a real number; it must override the .env price table."""
    llm.TRANSPORT = lambda a, s, u, max_tokens=0: ("hi", 1_000_000, 0, 0.0042)
    try:
        _, usage = llm.call("t", "s", "u")          # table would say $10.00
        assert usage.cost_usd == 0.0042 and usage.cost_source == "reported"
    finally:
        llm.TRANSPORT = None
    llm.TRANSPORT = lambda a, s, u, max_tokens=0: ("hi", 1_000_000, 0)
    try:
        _, usage = llm.call("t", "s", "u")
        assert usage.cost_usd == 10.0 and usage.cost_source == "table"
    finally:
        llm.TRANSPORT = None


def test_usage_fields_parse_openrouter_shape():
    class U:
        def model_dump(self):
            return {"prompt_tokens": 194, "completion_tokens": 2, "cost": 0.95,
                    "prompt_tokens_details": {"cached_tokens": 100}}
    assert llm._usage_fields(U()) == (0.95, 100)

    class Plain:
        def model_dump(self):
            return {"prompt_tokens": 1, "completion_tokens": 1}
    assert llm._usage_fields(Plain()) == (None, 0)


def test_missing_env_is_loud():
    try:
        llm.model_spec("nope")
    except SystemExit as e:
        assert "MODEL_nope_ID" in str(e)
    else:
        raise AssertionError("silent on missing config")


def test_extract_html():
    assert pipeline.extract_html("blah\n```html\n" + GOOD + "\n```\nthanks").startswith("<!DOCTYPE")
    assert pipeline.extract_html("prose " + GOOD + " trailing").startswith("<!DOCTYPE")
    assert pipeline.extract_html(GOOD) == GOOD


def test_extract_json():
    assert pipeline.extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert pipeline.extract_json('here you go {"a": 1} ok') == {"a": 1}
    # Measured against the real judge: it sometimes thinks out loud first, and that
    # prose can quote HTML/CSS containing stray braces before the real JSON at the end.
    reasoning = 'Let me look... the CSS has .card{color:red} and a style block {bad json'
    assert pipeline.extract_json(reasoning + ' Final answer: {"a": 1, "b": 2}') == {"a": 1, "b": 2}


def test_static_checks():
    c = pipeline.static_checks(pipeline.assemble(TOPIC))
    assert all(c.values()), [k for k, v in c.items() if not v]
    assert not pipeline.static_checks(GOOD)["lab_shell"]          # free-form page, not a Lab app
    assert not pipeline.static_checks(pipeline.SHELL)["lab_shell"]  # example never replaced
    bad = GOOD.replace("<script>", '<script src="https://cdn.example.com/x.js"></script><script>')
    assert not pipeline.static_checks(bad)["offline"]
    assert not pipeline.static_checks("just some text")["is_html"]


def test_topic_spliced_into_shell():
    page = pipeline.build_html("Here you go:\n```js\n" + TOPIC + "\n```\nDone.")
    assert page.startswith("<!doctype html>") and pipeline.topic_of(page) == TOPIC
    assert pipeline.EXAMPLE_SENTINEL not in page and "</html>" in page
    assert pipeline.build_html(TOPIC) == page                      # bare, unfenced topic
    assert pipeline.build_html("```html\n" + GOOD + "\n```") == GOOD  # whole document kept as-is
    assert pipeline.build_html("sorry, I can't") == ""
    evil = pipeline.assemble('const APP={name:"</script><b>x"};')
    assert "</script><b>" not in evil and "<\\/script><b>" in evil  # can't close the shell's <script>
    # the builder prompt carries the API and a worked example; the plan prompt keeps its marker
    p = pipeline._build_prompt("Esterification", 12, "", PLAN)
    assert "TOPIC script" in p and "const STEPS" in p and pipeline.EXAMPLE_SENTINEL not in p
    assert "JSON shape" in pipeline._plan_prompt("Esterification", 12, "")


def test_pipeline_modes_and_token_split():
    """teacher_student must bill two calls; student_only one. Fake transport, no network."""
    seen = []

    def fake(alias, system, user, max_tokens=0):
        seen.append(alias)
        body = json.dumps(PLAN) if "JSON shape" in user else "```js\n" + TOPIC + "\n```"
        return body, len(user) // 4, len(body) // 4

    llm.TRANSPORT = fake
    try:
        html, plan, us = pipeline.run("teacher_student", "Pendulum", 9, "t", "s", use_rag=False)
        assert seen == ["t", "s"] and len(us) == 2 and plan["grade"] == 9
        assert [u.role for u in us] == ["teacher", "student"]
        assert pipeline.static_checks(html)["lab_shell"]
        cheap = sum(u.cost_usd for u in us)

        seen.clear()
        _, _, us_t = pipeline.run("teacher_only", "Pendulum", 9, "t", "s", use_rag=False)
        assert seen == ["t"] and len(us_t) == 1
        assert sum(u.cost_usd for u in us_t) > cheap, "teacher_only should cost more than teacher->student"

        seen.clear()
        _, _, us_s = pipeline.run("student_only", "Pendulum", 9, "t", "s", use_rag=False)
        assert seen == ["s"]
    finally:
        llm.TRANSPORT = None


def test_plan_retries_once_on_bad_json():
    """Measured against the real API: the teacher occasionally emits invalid JSON.
    One retry, not a hand-rolled repair parser."""
    calls = {"n": 0}

    def flaky(alias, system, user, max_tokens=0):
        if "JSON shape" in user:
            calls["n"] += 1
            if calls["n"] == 1:
                return '{"title": "bad", "grade": 9', 10, 10   # truncated/invalid
            return json.dumps(PLAN), 10, 10
        return "```html\n" + GOOD + "\n```", 10, 10

    llm.TRANSPORT = flaky
    try:
        html, plan, us = pipeline.run("teacher_student", "Pendulum", 9, "t", "s", use_rag=False,
                                      reuse_blueprint=False)
        assert calls["n"] == 2 and plan["grade"] == 9 and len(us) == 3
    finally:
        llm.TRANSPORT = None


def test_plan_gives_up_after_two_bad_json():
    llm.TRANSPORT = lambda a, s, u, max_tokens=0: ("not json {{{", 10, 10)
    try:
        try:
            pipeline.run("teacher_student", "Pendulum", 9, "t", "s", use_rag=False,
                         reuse_blueprint=False)
            raise AssertionError("should have raised")
        except ValueError:
            pass
    finally:
        llm.TRANSPORT = None


def test_blueprint_stored_then_reused():
    """Teacher pays once per topic; later teacher_student runs only bill the student."""
    seen = []

    def fake(alias, system, user, max_tokens=0):
        seen.append(alias)
        body = json.dumps(PLAN) if "JSON shape" in user else "```html\n" + GOOD + "\n```"
        return body, 100, 200, 0.05 if alias == "t" else 0.002

    llm.TRANSPORT = fake
    try:
        rec, us = pipeline.blueprint("Simple Pendulum", 11, "t", use_rag=False)
        assert seen == ["t"] and len(us) == 1 and rec["cost_usd"] == 0.05 and rec["grade"] == 11
        assert pipeline.load_blueprint("Simple Pendulum")["plan"] == PLAN

        seen.clear()
        _, again = pipeline.blueprint("Simple Pendulum", 11, "t", use_rag=False)
        assert seen == [] and again == []                       # served from store, no call

        seen.clear()
        html, plan, us = pipeline.run("teacher_student", "Simple Pendulum", 11, "t", "s", use_rag=False)
        assert seen == ["s"] and [u.role for u in us] == ["student"] and plan == PLAN

        seen.clear()
        _, _, us = pipeline.run("teacher_student", "Simple Pendulum", 11, "t", "s", use_rag=False,
                                reuse_blueprint=False)
        assert seen == ["t", "s"]                               # forced regeneration
        assert len(pipeline.blueprint_files("Simple Pendulum")) == 2  # kept both, not overwritten

        seen.clear()
        os.environ.update({"MODEL_t2_BASE_URL": "x", "MODEL_t2_API_KEY": "x", "MODEL_t2_ID": "t2",
                           "MODEL_t2_IN": "1", "MODEL_t2_OUT": "1"})
        rec2, _ = pipeline.blueprint("Simple Pendulum", 11, "t2", use_rag=False)
        assert seen == ["t2"] and rec2["teacher"] == "t2"       # other teacher: its own, never borrowed
        assert len(pipeline.blueprint_files("Simple Pendulum")) == 3
        assert pipeline.load_blueprint("Simple Pendulum", "t")["teacher"] == "t"
    finally:
        llm.TRANSPORT = None


def test_write_new_never_overwrites():
    d = Path(tempfile.mkdtemp())
    a = pipeline.write_new(d, "x", ".html", "one")
    b = pipeline.write_new(d, "x", ".html", "two")
    assert a != b and a.read_text() == "one" and b.read_text() == "two"
    n = pipeline.run_name("teacher_student", "opus55", "deepseekv4flash")
    assert n.startswith("teacher_student__opus55-deepseekv4flash__") and pipeline.SAFE_NAME.fullmatch(n)
    assert not pipeline.SAFE_NAME.fullmatch("..\\app")


def test_on_topic_guard():
    ohm = {"title": "Ohm's law", "app": {"name": "Ohm's Lab", "subtitle": "Class 10 · Electricity · V–I relationship"},
           "learning_objectives": ["relate V and I", "motion of electrons"]}
    assert not pipeline.on_topic("Projectile Motion", ohm)          # the real drift seen on Render
    assert pipeline.on_topic("Simple Pendulum", {"title": "Pendulum"})
    assert pipeline.on_topic("Mechanism of Esterification of Carboxylic Acid",
                             {"title": "Esterification", "app": {"name": "Ester Lab",
                              "subtitle": "Class 12 · Carboxylic Acids · Esterification mechanism"}})


def test_broken_script_gets_one_repair():
    good = pipeline.EXAMPLE_TOPIC
    for bad in (good[: len(good) // 2], good.replace("const APP={", "const APP={{", 1)):   # cut off / typo
        assert pipeline.js_error(bad)
        replies, prompts = [bad, "```js\n" + good + "\n```"], []
        llm.TRANSPORT = lambda a, s, u, max_tokens=0: (prompts.append(u) or replies.pop(0), 10, 10)
        os.environ.update({"MODEL_rep_ID": "m", "MODEL_rep_IN": "1", "MODEL_rep_OUT": "1", "LLM_BASE_URL": os.environ.get("LLM_BASE_URL", "x"), "LLM_API_KEY": os.environ.get("LLM_API_KEY", "x")})
        html, us = pipeline._build("rep", "student", "P")
        assert len(us) == 2 and "PREVIOUS ATTEMPT FAILED" in prompts[1] and pipeline.static_checks(html)["js_parses"]
    llm.TRANSPORT = lambda a, s, u, max_tokens=0: (good[:100], 10, 10)
    try:
        pipeline._build("rep", "student", "P")
        raise AssertionError("still broken after the repair call must raise")
    except ValueError:
        pass
    llm.TRANSPORT = None


def test_tier_estimate_and_final():
    t = pipeline.estimate_tier
    assert t("Class 6 Science: Parts of a plant", 6)[0] == "easy"
    assert t("Class 9 Science: Simple Pendulum", 9)[0] == "medium"
    assert t("Class 12 Physics: Electromagnetic induction", 12)[0] == "hard"
    assert t("Class 10 Science: Working of DC motor")[0] == "medium"          # class parsed from the topic text
    assert t("Class 8 Science: Cell", 8, "a = b = c = d = e = f = g = h = " * 3)[0] == "medium"   # equation-heavy source
    f = pipeline.final_tier
    assert f("easy", {"build_tier": "hard"}) == "hard"                         # teacher can raise
    assert f("hard", {"build_tier": "easy"}) == "hard"                         # never lowers
    assert f("easy", {"build_tier": "bogus"}) == "easy"
    assert f("easy", {"science": {"equations": ["a", "b"]}}) == "medium"
    assert f("easy", {"science": {"equations": list("abcde")}}) == "hard"


def test_auto_student_routes_by_tier_and_escalates():
    """student='auto': the blueprint's tier picks the student; a broken build moves up a tier."""
    good, built = pipeline.EXAMPLE_TOPIC, []
    for a in ("lo", "mid", "hi", "tch"):
        os.environ.update({f"MODEL_{a}_ID": a, f"MODEL_{a}_IN": "1", f"MODEL_{a}_OUT": "1"})
    os.environ.update({"LLM_BASE_URL": "x", "LLM_API_KEY": "x",
                       "STUDENT_EASY": "lo", "STUDENT_MEDIUM": "mid", "STUDENT_HARD": "hi"})
    broken = {"lo"}                                   # the easy-tier model can't build this one

    def fake(alias, system, user, max_tokens=0):
        if "JSON shape" in user:
            return json.dumps(dict(PLAN, build_tier="easy")), 10, 10
        built.append(alias)
        return ("```js\n" + good[:60] + "\n```" if alias in broken else "```js\n" + good + "\n```"), 10, 10

    llm.TRANSPORT = fake
    pipeline.BLUEPRINTS = Path(tempfile.mkdtemp())
    try:
        html, plan, us = pipeline.run("teacher_student", "Pendulum", 6, "tch", "auto",
                                      use_rag=False, reuse_blueprint=False)
        assert built == ["lo", "lo", "mid"], built       # easy tried (+ its repair), then medium built it
        assert [u.role for u in us] == ["teacher", "student", "student", "student"] and pipeline.static_checks(html)["js_parses"]
        built.clear(); broken.clear()
        pipeline.run("teacher_student", "Pendulum", 6, "tch", "auto", use_rag=False)  # stored blueprint
        assert built == ["lo"]
        built.clear()
        os.environ["HARD_SINGLE"] = "1"                  # hard topic + HARD_SINGLE: no blueprint, one frontier call
        html, plan, us = pipeline.run("teacher_student", "Class 12 Physics: Electromagnetic induction", 12, "tch", "auto",
                                      use_rag=False)
        assert plan is None and built == ["hi"] and len(us) == 1, (plan, built)
    finally:
        llm.TRANSPORT = None
        for k in ("HARD_SINGLE", "STUDENT_EASY", "STUDENT_MEDIUM", "STUDENT_HARD"):
            os.environ.pop(k, None)


def test_rag_off_without_dsn():
    os.environ.pop("NCERT_DSN", None)
    assert retrieve.ncert_context("Simple Pendulum", 9) == ""


def test_report_and_slug():
    rows = [{"mode": "teacher_only", "in_tokens": 100, "out_tokens": 5000, "cost_usd": 0.2505,
             "seconds": 40.0, "checks": pipeline.static_checks(GOOD), "judge": {"total": 22}},
            {"mode": "teacher_student", "in_tokens": 900, "out_tokens": 5400, "cost_usd": 0.0184,
             "seconds": 30.0, "checks": pipeline.static_checks(GOOD)}]
    md = report_md("Class 9: Pendulum", 9, rows, 10000)
    assert "13.6x" in md, md          # 0.2505 / 0.0184
    assert "2,505.00" in md and "184.00" in md
    assert slug("Class 9 Science: Simple Pendulum!") == "class-9-science-simple-pendulum"


def test_judge_jev_dispatch_and_scoring():
    """JUDGE=jev113 must route through the Decisions API path, not the chat path -- and
    parse its typed score response into the same (scores, Usage) shape judge() returns."""
    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"answers": {k: {"score": 4.0} for k in pipeline.JEV_AXES},
                               "usage": {"input_tokens": 500, "output_tokens": 40, "cost": 0.0002}}).encode()

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, json.loads(req.data)))
        return FakeResp()

    real_urlopen = pipeline.urllib.request.urlopen
    pipeline.urllib.request.urlopen = fake_urlopen
    os.environ.setdefault("LLM_API_KEY", "x")
    try:
        scores, u = pipeline.judge(GOOD, "Pendulum", 9, "jev113")
        assert calls[0][0] == "https://openrouter.ai/api/alpha/decisions"
        assert set(calls[0][1]["questions"]) == set(pipeline.JEV_AXES)
        assert scores["total"] == 20.0 and pipeline.confidence_pct(scores) == 80
        assert u.role == "judge" and u.cost_usd == 0.0002 and u.cost_source == "reported"
    finally:
        pipeline.urllib.request.urlopen = real_urlopen


def test_judge_scores_and_confidence():
    """judge() sums the 5-axis rubric into /25 and confidence_pct() turns that into 0-100."""
    scores_out = {"scientific_accuracy": 5, "ncert_alignment": 4, "interactivity": 4,
                 "grade_appropriateness": 5, "pedagogy": 2, "verdict": "solid but light on pedagogy"}

    def fake(alias, system, user, max_tokens=0):
        return json.dumps(scores_out), 100, 40

    llm.TRANSPORT = fake
    try:
        scores, u = pipeline.judge(GOOD, "Pendulum", 9, "t")
        assert scores["total"] == 20 and u.role == "judge"
        assert pipeline.confidence_pct(scores) == 80  # 20/25 -> 80%
    finally:
        llm.TRANSPORT = None
    assert pipeline.confidence_pct(None) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
