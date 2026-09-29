"""NCERT grounding via Postgres full-text search. No embedding model needed —
the DB already has the content; to_tsvector/to_tsquery ranks it.

Topic strings look like "Class 10 Science: Reflection of Light". Feeding that whole string to
plainto_tsquery ANDs every word, so "class" and "science" must appear in the same chunk and
almost nothing matches (26/26 stored blueprints came out ungrounded). Instead we reduce the topic
to its content words and require all of them (see _plan for the fallbacks)."""
import os
import re

# Rank by heading first: chunks look like "## 9.3 Refraction of Light\n<body>", so a section whose
# title matches the topic (weight A) beats one that merely mentions the words in passing (weight D).
DEFAULT_SQL = """
SELECT ground_truth_content
FROM knowledge_graph.graph_nodes,
     LATERAL (SELECT setweight(to_tsvector('english', split_part(ground_truth_content, chr(10), 1)), 'A')
                  || setweight(to_tsvector('english', ground_truth_content), 'D') AS v,
                     to_tsquery('english', %(tsq)s) AS tq) s
WHERE ground_truth_content IS NOT NULL
  AND (%(grade)s::int IS NULL OR class_level = %(grade)s::int)
  AND s.v @@ s.tq
  AND ground_truth_content NOT LIKE '![](%%'          -- bare figure stubs teach nothing
  AND length(ground_truth_content) > 60
ORDER BY ts_rank_cd(s.v, s.tq)
         * CASE WHEN split_part(ground_truth_content, chr(10), 1) ~* '(summary|exercise|points to ponder)' THEN 0.3 ELSE 1 END
         DESC
LIMIT %(k)s
"""

# Words that name the book, not the concept. (English config already drops "of", "the"...)
_NOISE = {"class", "grade", "std", "chapter", "ch", "unit", "science", "maths", "math", "mathematics",
          "physics", "chemistry", "biology", "ncert", "simulation", "lab", "and", "with", "using"}


class NCERTError(RuntimeError):
    """Grounding was asked for but couldn't be delivered. Generation must stop rather than
    silently produce an ungrounded simulation."""


def keywords(topic):
    """'Class 10 Science: Reflection of Light' -> ['reflection', 'light'] (order kept, deduped)."""
    out = []
    for w in re.findall(r"[a-z][a-z0-9]{2,}", (topic or "").lower()):
        if w not in _NOISE and w not in out:
            out.append(w)
    return out


def _plan(kw, grade):
    """Searches to try, most precise first. Every chunk must contain ALL the topic's words
    (or all but one, for 3+ word topics): an any-word match lets a generic word ("motion",
    "working") pull in a different topic, and a strictly grounded teacher then builds THAT.
    The grade filter is dropped only if the asked class has nothing, since NCERT teaches some
    topics in another class than the one asked for (projectile motion is Class 11)."""
    grades = [grade, None] if grade is not None else [None]
    plans = [(" & ".join(kw), g) for g in grades]
    if len(kw) >= 3:
        drop_one = " | ".join("(" + " & ".join(w for w in kw if w != x) + ")" for x in kw)
        plans += [(drop_one, g) for g in grades]
    return plans


def _query(sql, cur, tsq, grade, k, q):
    cur.execute(sql, {"q": q, "tsq": tsq, "grade": grade, "k": k})
    return [r[0] for r in cur.fetchall()]


def ncert_chunks(topic, grade=None, k=None, dsn=None, sql=None):
    """Top-k NCERT chunks for a topic (list of str). Raises NCERTError if the DB can't be
    reached or has nothing on the topic; returns [] only when NCERT_DSN is unset."""
    dsn = dsn or os.getenv("NCERT_DSN")
    if not dsn:
        return []
    import psycopg
    k = k or int(os.getenv("NCERT_TOP_K", "6"))
    sql = sql or os.getenv("NCERT_SQL") or DEFAULT_SQL
    kw = keywords(topic)
    if not kw:
        raise NCERTError(f"No searchable words in topic {topic!r}.")
    q = " ".join(kw)
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            rows = []
            for tsq, g in _plan(kw, grade):
                if len(rows) >= k:
                    break
                if g is None and grade is not None and rows:
                    continue        # the asked class has it: don't mix in other classes' text
                rows += [r for r in _query(sql, cur, tsq, g, k, q) if r not in rows]
            rows = rows[:k]
    except psycopg.OperationalError as e:
        raise NCERTError(f"NCERT database unreachable ({e}). Untick NCERT grounding to generate "
                         "ungrounded, or fix NCERT_DSN.") from None
    except psycopg.errors.UndefinedTable:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT table_schema||'.'||table_name FROM information_schema.tables "
                        "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1")
            tables = [r[0] for r in cur.fetchall()]
        raise NCERTError("The NCERT query references a table that doesn't exist. Tables here: "
                         f"{', '.join(tables) or '(none)'}. Set NCERT_SQL to match.") from None
    return rows


def ncert_context(topic, grade=None, k=None):
    """Chunks joined as text; '' only when NCERT_DSN is unset (RAG not configured)."""
    return "\n\n---\n\n".join(ncert_chunks(topic, grade, k))
