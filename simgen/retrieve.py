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
SELECT ground_truth_content, class_level
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

# Words that name the book, not the concept, plus glue words. (Postgres' English config drops stop
# words itself, but the relevance scoring below runs in Python and has to agree with it.)
_NOISE = {"class", "grade", "std", "chapter", "ch", "unit", "science", "maths", "math", "mathematics",
          "physics", "chemistry", "biology", "ncert", "simulation", "lab", "and", "with", "using",
          "through", "from", "into", "between", "about", "over", "under", "during", "their", "there",
          "what", "when", "where", "which", "how", "why", "does", "the", "for", "are", "its"}
# Words that describe the *kind* of lesson, not the subject ("Working of DC Motor"): a chunk about the
# motor needn't contain "working", so they are never searched for and never required.
GENERIC = {"working", "mechanism", "process", "experiment", "principle", "structure", "introduction",
           "basics", "types", "properties", "applications", "application", "concept", "study"}
_SHORT_OK = {"ph"}          # 2-letter terms worth keeping; upper-case ones (DC, AC) are kept too


class NCERTError(RuntimeError):
    """Grounding was asked for but couldn't be delivered. Generation must stop rather than
    silently produce an ungrounded simulation."""


def keywords(topic):
    """'Class 10 Science: Reflection of Light' -> ['reflection', 'light'] (order kept, deduped).
    'DC' and 'AC' survive (a 3-letter minimum silently turned 'Working of DC Motor' into 'motor',
    which found motor neurons)."""
    out = []
    for t in re.findall(r"[A-Za-z][A-Za-z0-9]+", topic or ""):
        w = t.lower()
        if w in _NOISE or w in out or (len(w) == 2 and not (t.isupper() or w in _SHORT_OK)):
            continue
        out.append(w)
    return out


def core_keywords(topic):
    """The subject words of a topic: keywords() minus the lesson-kind words; all of them if that's all there is."""
    kw = keywords(topic)
    return [w for w in kw if w not in GENERIC] or kw


# ---- is this chunk really ABOUT the topic? (the full-text match only says the words occur)

CORE_SCORE = 1.5     # relevance a chunk needs to be shown to the teacher; see relevance()
_WORD = re.compile(r"[a-z0-9]+")


def _stem(w):
    return w[:4]


def relevance(text, kw):
    """How much a chunk is about the topic words `kw`, 0..4. Postgres only says the words occur somewhere
    in the chunk, so a page on motor neurons matches 'motor' and a chapter summary matches everything.
    Score = 2 x share of topic words in the heading + 1 if all of them sit in one 30-word stretch
    (a passing mention has them far apart) + up to 1 for how often they occur. Passing mentions score
    ~1, a section on the topic 2+; CORE_SCORE sits between. Summary/exercise chunks count for 30%."""
    stems = {_stem(w) for w in kw}
    first = text.strip().split("\n", 1)[0]
    head = first if first.startswith("#") or len(first) <= 100 else ""   # a long first line is body text
    head_hit = len(stems & {_stem(t) for t in _WORD.findall(head.lower())}) / len(stems)
    toks = [_stem(t) for t in _WORD.findall(text.lower())]
    pos = [(i, t) for i, t in enumerate(toks) if t in stems]
    present = {t for _, t in pos}
    if len(present) < (len(stems) if len(stems) < 3 else len(stems) - 1):
        return 0.0
    best, cnt, have, left = 10 ** 9, {}, 0, 0          # smallest window holding every present stem
    for i, t in pos:
        cnt[t] = cnt.get(t, 0) + 1
        have += cnt[t] == 1
        while have == len(present):
            best = min(best, i - pos[left][0] + 1)
            lt = pos[left][1]
            cnt[lt] -= 1
            have -= cnt[lt] == 0
            left += 1
    score = 2 * head_hit + (best <= 30) + min(len(pos) / max(len(toks), 1) * 100 / 3, 1)
    return score * (0.3 if re.search(r"summary|exercise|points to ponder", head, re.I) else 1)


def _queries(kw):
    """tsquery strings, most precise first. Every chunk must contain ALL the topic's words (or all but
    one, for 3+ word topics): an any-word match lets a generic word ("motion") pull in a different topic,
    and a strictly grounded teacher then builds THAT."""
    out = [" & ".join(kw)]
    if len(kw) >= 3:
        out.append(" | ".join("(" + " & ".join(w for w in kw if w != x) + ")" for x in kw))
    return out


class Context(str):
    """The NCERT text handed to the teacher (a plain str to everything else) plus where it came from."""
    grades, note = (), ""


class Ground:
    """What retrieval found: `chunks` (only ones that are about the topic, best first), the classes they
    come from, a `note` when that isn't the class asked for, and `closest` headings that matched the
    words but were rejected as passing mentions (for the error message)."""

    def __init__(self, chunks=(), grades=(), note="", closest=()):
        self.chunks, self.grades, self.note, self.closest = list(chunks), tuple(grades), note, list(closest)

    def context(self):
        c = Context("\n\n---\n\n".join(self.chunks))
        c.grades, c.note = self.grades, self.note
        return c


def _collect(cur, sql, kw, grade, pool):
    """Candidate (text, class_level) rows for the topic words, de-duplicated. A custom NCERT_SQL that
    returns only the text gives class_level None."""
    seen, rows = set(), []
    q = " ".join(kw)
    for tsq in _queries(kw):
        cur.execute(sql, {"q": q, "tsq": tsq, "grade": grade, "k": pool})
        for r in cur.fetchall():
            if r[0] not in seen:
                seen.add(r[0])
                rows.append((r[0], r[1] if len(r) > 1 else None))
    return rows


def _rank(rows, kw):
    """(core, rejected): rows scored by relevance(); core = those >= CORE_SCORE, best first (ties keep the
    database's order)."""
    scored = sorted(((relevance(t, kw), t, g) for t, g in rows), key=lambda x: -x[0])
    return [x for x in scored if x[0] >= CORE_SCORE], [x for x in scored if x[0] < CORE_SCORE]


def _heading(text):
    return text.strip().split("\n", 1)[0].lstrip("# ").strip()[:80]


def ncert_ground(topic, grade=None, k=None, dsn=None, sql=None):
    """Chunks that are really about `topic`. Looks in the asked class first; only if nothing there is
    about the topic does it look across all classes and keep the one class whose text is best, saying so
    in `.note` (NCERT teaches some topics in another class than the one asked for). Raises NCERTError if
    the DB can't be reached; an empty Ground means nothing in NCERT is about the topic (or NCERT_DSN
    is unset)."""
    dsn = dsn or os.getenv("NCERT_DSN")
    if not dsn:
        return Ground()
    import psycopg
    k = k or int(os.getenv("NCERT_TOP_K", "6"))
    sql = sql or os.getenv("NCERT_SQL") or DEFAULT_SQL
    kw = core_keywords(topic)
    if not kw:
        raise NCERTError(f"No searchable words in topic {topic!r}.")
    pool = max(k * 4, 24)       # fetch wide, then keep what is really about the topic
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            core, rejected = _rank(_collect(cur, sql, kw, grade, pool), kw)
            note = ""
            if not core and grade is not None:
                core, more = _rank(_collect(cur, sql, kw, None, pool), kw)
                rejected += more
                by = {}                                  # one class only: don't blend Class 11 and 12 text
                for x in core:
                    by.setdefault(x[2], []).append(x)
                if by:
                    g = max(by, key=lambda g: (round(by[g][0][0], 1), -abs((g or grade) - grade)))
                    core = by[g]
                    note = f"NCERT teaches this in Class {g}, not Class {grade}." if g not in (None, grade) else ""
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
    core = core[:k]
    closest = list(dict.fromkeys(f"{_heading(t)} (Class {g})" if g else _heading(t) for _, t, g in rejected))[:3]
    return Ground([t for _, t, _ in core], sorted({g for _, _, g in core if g}), note, closest)


def ncert_chunks(topic, grade=None, k=None, dsn=None, sql=None):
    """Top-k NCERT chunks about the topic (list of str); [] if there are none or NCERT_DSN is unset."""
    return ncert_ground(topic, grade, k, dsn, sql).chunks


def ncert_context(topic, grade=None, k=None):
    """Chunks joined as text; '' when there are none or NCERT_DSN is unset (RAG not configured)."""
    return ncert_ground(topic, grade, k).context()
