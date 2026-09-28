"""NCERT grounding via Postgres full-text search. No embedding model needed —
the DB already has the content; to_tsvector/plainto_tsquery ranks it. Schema
lives in .env so this doesn't assume your table shape."""
import os

DEFAULT_SQL = """
SELECT content
FROM ncert_chunks
WHERE (%(grade)s IS NULL OR grade = %(grade)s)
  AND to_tsvector('english', content) @@ plainto_tsquery('english', %(q)s)
ORDER BY ts_rank(to_tsvector('english', content), plainto_tsquery('english', %(q)s)) DESC
LIMIT %(k)s
"""


def ncert_context(topic, grade=None, k=None):
    """Top-k NCERT chunks joined as text. Returns '' when NCERT_DSN is unset (RAG off)."""
    dsn = os.getenv("NCERT_DSN")
    if not dsn:
        return ""
    import psycopg
    k = k or int(os.getenv("NCERT_TOP_K", "6"))
    sql = os.getenv("NCERT_SQL", DEFAULT_SQL)
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(sql, {"q": topic, "grade": grade, "k": k})
            rows = [r[0] for r in cur.fetchall()]
        return "\n\n---\n\n".join(rows)
    except psycopg.OperationalError as e:
        # ponytail: DB unreachable (wrong/stale NCERT_DSN, no network path from
        # this host) -- degrade to ungrounded instead of failing the whole
        # generation. Same fallback an unset NCERT_DSN already gets; a bad one
        # shouldn't be worse. Upgrade path: retry/circuit-break if this fires
        # often enough that silently-ungrounded runs become a problem.
        print(f"NCERT grounding skipped, DB unreachable: {e}")
        return ""
    except psycopg.errors.UndefinedTable:
        # We guessed a table name (ncert_chunks); your DB uses something else.
        # Rather than guess further, hand back what's actually there so NCERT_SQL
        # in .env can be pointed at the real table/column names.
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' ORDER BY 1")
            tables = [r[0] for r in cur.fetchall()]
        raise RuntimeError(
            "NCERT_SQL references a table that doesn't exist in this database. "
            f"Tables actually in the public schema: {', '.join(tables) or '(none)'}. "
            "Set NCERT_SQL in .env to match — e.g. SELECT <text_col> FROM <table> "
            "WHERE (%(grade)s IS NULL OR <grade_col> = %(grade)s) AND "
            "to_tsvector('english', <text_col>) @@ plainto_tsquery('english', %(q)s) "
            "ORDER BY ts_rank(...) DESC LIMIT %(k)s"
        ) from None
