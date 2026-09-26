"""Three routes from a class topic to a runnable HTML simulation, so they can be
compared on the same topic: teacher_only, student_only, teacher_student.

teacher_student reuses a stored blueprint (blueprints/<slug>.json) when one exists,
so the frontier model is paid once per topic and every later build is student-only cost.
"""
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .llm import Usage, call
from .retrieve import ncert_context

# ---------------------------------------------------------------- shared spec

SIM_CONTRACT = """A simulation is ONE self-contained .html file that:
1. Animates the phenomenon on a <canvas> or inline SVG, driven by the real equations.
2. Exposes 2-5 controls (range sliders / buttons) bound to physical parameters, each labelled with its unit.
3. Shows live numeric readouts of the computed quantities, and prints the governing equation.
4. Has a "What to observe" panel and 3 check-your-understanding questions with click-to-reveal answers.
5. Uses no external scripts, stylesheets, fonts or images — zero network requests, works offline.
6. Uses language and mathematics appropriate to the stated grade (no calculus below Class 11).
7. Is factually correct and consistent with the NCERT chapter given as context."""

PLAN_SCHEMA = """{
  "title": str, "grade": int, "subject": str,
  "ncert_refs": [str],
  "learning_objectives": [str],
  "physics": {
    "equations": [str],
    "parameters": [{"name": str, "symbol": str, "unit": str, "min": num, "max": num, "default": num}],
    "state_update": str
  },
  "visualization": str,
  "controls": [str], "readouts": [str],
  "observations": [str],
  "questions": [{"q": str, "options": [str], "answer": str, "explanation": str}],
  "misconceptions": [str],
  "grade_language_notes": str
}"""

TEACHER_PLAN_SYS = (
    "You are a senior NCERT curriculum designer and physicist. You write simulation "
    "blueprints that a smaller model will implement literally. Be exact and numeric: "
    "give real equations, real parameter ranges with units, and the exact update rule. "
    "This is a machine-readable spec, not an essay: every string field is a short "
    "phrase or one sentence, never a paragraph. Target under 1200 tokens total. "
    "Output JSON only — no prose, no code fences."
)

BUILD_SYS = "You are an expert front-end engineer building physics/chemistry/biology teaching simulations."


def _ctx(context):
    return f"\n\nNCERT SOURCE MATERIAL (ground every fact in this):\n{context}\n" if context else ""


def _plan_prompt(topic, grade, context):
    return (f"Topic: {topic}\nGrade: {grade or 'infer from topic'}\n{_ctx(context)}\n"
            f"Design the simulation blueprint. Target output contract:\n{SIM_CONTRACT}\n\n"
            f"Emit exactly this JSON shape:\n{PLAN_SCHEMA}")


def _build_prompt(topic, grade, context, plan=None):
    head = f"Topic: {topic}\nGrade: {grade or 'infer from topic'}\n{_ctx(context)}"
    if plan:
        head += ("\nBLUEPRINT — implement this exactly. Do not invent different equations, "
                 f"parameters or questions:\n{json.dumps(plan, indent=2)}\n")
    return (head + f"\nBuild the simulation.\n{SIM_CONTRACT}\n\n"
            "Return ONLY the complete HTML document in a single ```html code fence.")


# ---------------------------------------------------------------- extraction

FENCE = re.compile(r"```(?:html)?\s*(<!DOCTYPE.*?|<html.*?)```", re.S | re.I)


def extract_html(text):
    m = FENCE.search(text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(<!DOCTYPE html.*</html>|<html.*</html>)", text, re.S | re.I)
    return m.group(1).strip() if m else text.strip()


def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # A reasoning model sometimes thinks out loud before the JSON, and that prose can
    # quote source code containing stray '{'/'}' -- a greedy regex can start the match
    # at one of those instead of the real object. Scan back from the LAST '}' and count
    # brace depth to find its true matching '{', however much prose comes before it.
    end = text.rfind("}")
    if end == -1:
        raise json.JSONDecodeError("no JSON object found", text, 0)
    depth, start = 0, None
    for i in range(end, -1, -1):
        if text[i] == "}":
            depth += 1
        elif text[i] == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start is None:
        raise json.JSONDecodeError("unbalanced braces", text, 0)
    return json.loads(text[start:end + 1])


EXTERNAL = re.compile(r'<(?:script|link|img|iframe)[^>]*\b(?:src|href)\s*=\s*["\']?(?:https?:)?//', re.I)


def static_checks(html):
    """Cheap gate, runs with no model call. Fails loudly on the things that break a demo."""
    low = html.lower()
    return {
        "is_html": low.lstrip().startswith(("<!doctype", "<html")) and "</html>" in low,
        "has_visual": "<canvas" in low or "<svg" in low or "ggbapplet(" in low,
        "has_controls": 'type="range"' in low or "<input" in low or "<button" in low,
        "has_script": "<script" in low,
        "offline": not EXTERNAL.search(html),
        # measured false negative: real outputs often phrase items as "Explain why…"
        # rather than ending in "?" — count either signal.
        "has_questions": low.count("?") >= 3 or low.count("question") >= 3,
        "size_ok": 2000 < len(html) < 400_000,
    }


# ---------------------------------------------------------------- routes

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


BLUEPRINTS = Path("blueprints")
SAFE_NAME = re.compile(r"[a-z0-9_-]+")   # every stored file stem we generate matches this


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def write_new(directory, stem, ext, text):
    """Write text under a name that doesn't exist yet and return its path. Nothing that
    was generated is ever overwritten: a clash (same second) gets a -1, -2... suffix."""
    directory.mkdir(parents=True, exist_ok=True)
    n = 0
    while True:
        p = directory / f"{stem}{f'-{n}' if n else ''}{ext}"
        try:
            with open(p, "x", encoding="utf-8") as f:
                f.write(text)
            return p
        except FileExistsError:
            n += 1


def run_name(mode, teacher, student):
    """File stem for one generated simulation: <mode>__<models>__<timestamp>."""
    models = {"teacher_only": teacher, "student_only": student}.get(mode, f"{teacher}-{student}")
    return f"{mode}__{slug(models)}__{stamp()}"


def blueprint_files(topic=None, teacher=None):
    """Stored blueprint files, newest first. Layout: <topic-slug>__<teacher>__<timestamp>.json,
    one file per generation, so every blueprint ever made is kept."""
    pat = f"{slug(topic) if topic else '*'}__{slug(teacher) if teacher else '*'}__*.json"
    return sorted(BLUEPRINTS.glob(pat), key=lambda p: p.stem.split("__")[-1], reverse=True)


def read_blueprint(path):
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["file"] = path.stem
    return rec


def load_blueprint(topic, teacher=None):
    """Newest stored blueprint for this topic (by this teacher, if given), or None."""
    files = blueprint_files(topic, teacher)
    return read_blueprint(files[0]) if files else None


def blueprint(topic, grade, teacher, use_rag=True, force=False):
    """The teacher's blueprint for a topic: served from the store if present, else
    generated once and stored. Returns (record, [Usage]) — usages are only the calls
    made now, so a stored blueprint costs this run nothing (its original cost stays
    in the record). Reuse is per teacher: picking a different teacher makes that teacher's
    own blueprint (stored alongside), it never silently borrows another model's."""
    if not force:
        rec = load_blueprint(topic, teacher)
        if rec:
            return rec, []
    context = ncert_context(topic, grade) if use_rag else ""
    usages = []
    # measured: Opus 5 ignores the brevity ask and runs ~3.5-6k tokens regardless
    # (6000 is a real ceiling, not a target), and occasionally emits invalid JSON.
    # One retry on a bad parse is cheaper and more robust than a hand-rolled repair.
    for attempt in (1, 2):
        raw, u = call(teacher, TEACHER_PLAN_SYS, _plan_prompt(topic, grade, context),
                      role="teacher", max_tokens=6000, temperature=0.2)
        usages.append(u)
        try:
            plan = extract_json(raw)
            break
        except (ValueError, KeyError):
            if attempt == 2:
                raise
    rec = {"topic": topic, "grade": grade, "teacher": teacher, "model": usages[-1].model,
           "rag": bool(context), "created": time.strftime("%Y-%m-%d %H:%M"),
           "cost_usd": round(sum(u.cost_usd for u in usages), 6),
           "in_tokens": sum(u.in_tokens for u in usages),
           "out_tokens": sum(u.out_tokens for u in usages),
           "seconds": round(sum(u.seconds for u in usages), 2), "plan": plan}
    p = write_new(BLUEPRINTS, f"{slug(topic)}__{slug(teacher)}__{stamp()}", ".json",
                  json.dumps(rec, indent=2, ensure_ascii=False))
    rec["file"] = p.stem
    return rec, usages


def run(mode, topic, grade, teacher, student, use_rag=True, reuse_blueprint=True):
    """mode in {teacher_only, student_only, teacher_student}.
    Returns (html, plan_or_None, [Usage]) — usages are only calls made in this run."""
    if mode == "teacher_student":
        rec, usages = blueprint(topic, grade, teacher, use_rag, force=not reuse_blueprint)
        # The blueprint already carries the grounded facts, so the student doesn't
        # re-pay for the NCERT context. That is where most of the saving comes from.
        text, u = call(student, BUILD_SYS, _build_prompt(topic, grade, "", rec["plan"]), role="student")
        html_out = extract_html(text)
        if not html_out:
            raise ValueError(f"{student} returned an empty response.")
        return html_out, rec["plan"], usages + [u]

    context = ncert_context(topic, grade) if use_rag else ""
    if mode in ("teacher_only", "student_only"):
        alias, role = (teacher, "teacher") if mode == "teacher_only" else (student, "student")
        text, u = call(alias, BUILD_SYS, _build_prompt(topic, grade, context), role=role)
        html_out = extract_html(text)
        if not html_out:
            raise ValueError(f"{alias} returned an empty response.")
        return html_out, None, [u]

    raise ValueError(f"unknown mode {mode}")


MODES = ("teacher_only", "student_only", "teacher_student")


# ---------------------------------------------------------------- judge

JUDGE_SYS = ("You are an NCERT science examiner grading a teaching simulation's source code. "
             "Be strict. Output JSON only.")

JUDGE_RUBRIC = """Score 0-5 on each, then give a one-line verdict:
{"scientific_accuracy": int, "ncert_alignment": int, "interactivity": int,
 "grade_appropriateness": int, "pedagogy": int, "verdict": str}
scientific_accuracy: are the equations and the numeric update rule right?
ncert_alignment: does it cover the chapter's actual learning outcomes?
interactivity: do the controls change the simulation in a physically meaningful way?
grade_appropriateness: vocabulary and maths level.
pedagogy: observations + questions actually teach the concept."""


def judge(html, topic, grade, judge_alias):
    if judge_alias in ("jev113", "typesafe/jev-1.13"):
        return judge_jev(html, topic, grade)
    # max_tokens must clear the reasoning budget (also 1500 by default in call()) with
    # room to spare, or a reasoning model burns the whole cap on hidden thinking and
    # returns empty content -> "Expecting value"/"no JSON object found" errors. Measured:
    # a cheap model reading a full ~60k-char simulation can burn 3.5k+ tokens just thinking
    # before it writes the JSON verdict, so 4000 was too tight and failed intermittently
    # under load. 8000 costs nothing extra unless the model actually uses it.
    text, u = call(judge_alias, JUDGE_SYS,
                   f"Topic: {topic}\nGrade: {grade}\n\n{JUDGE_RUBRIC}\n\nSIMULATION SOURCE:\n{html[:60000]}",
                   role="judge", max_tokens=8000)
    scores = extract_json(text)
    keys = ("scientific_accuracy", "ncert_alignment", "interactivity",
            "grade_appropriateness", "pedagogy")
    scores["total"] = sum(int(scores.get(k, 0)) for k in keys)
    return scores, u


def confidence_pct(scores):
    """Judge rubric total (0-25) -> a 0-100 confidence-of-correctness percentage."""
    return round(scores["total"] / 25 * 100) if scores else None


# ---------------------------------------------------------------- judge: Jev (TypeSafe)
#
# typesafe/jev-1.13 is a "decision model", not a chat model: it returns typed, probability-
# weighted scores instead of generated prose, through OpenRouter's separate Decisions API
# (not the chat/completions endpoint every other model in this file uses). Same 5-axis
# rubric as judge() above, same (scores, Usage) return shape, so JUDGE=jev113 is a drop-in
# swap in .env -- nothing downstream (confidence_pct, /view, the library, /costs) changes.
# Measured: ~$0.0002/simulation vs ~$0.05-0.15 for an Opus-5 judge call -- roughly 500x cheaper.

JEV_AXES = {
    "scientific_accuracy": "are the equations and the numeric update rule right?",
    "ncert_alignment": "does it cover the chapter's actual learning outcomes?",
    "interactivity": "do the controls change the simulation in a physically meaningful way?",
    "grade_appropriateness": "is the vocabulary and maths level right for the grade?",
    "pedagogy": "do the observations and questions actually teach the concept?",
}
JEV_SCALE = ["0 - completely wrong or absent", "1 - mostly wrong", "2 - partially right, real gaps",
             "3 - mostly right, minor issues", "4 - right, small polish possible", "5 - fully correct"]


def judge_jev(html, topic, grade):
    model = os.environ.get("MODEL_jev113_ID", "typesafe/jev-1.13")
    t0 = time.time()
    body = {
        "model": model,
        "state": {"topic": topic, "grade": grade, "simulation_source": html[:60000]},
        "questions": {k: {"type": "score",
                          "instructions": f'For topic "{topic}" (grade {grade}): {q}',
                          "criteria": JEV_SCALE}
                     for k, q in JEV_AXES.items()},
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/alpha/decisions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    scores = {k: data["answers"][k]["score"] for k in JEV_AXES}
    scores["total"] = round(sum(scores.values()), 2)
    weakest = min(JEV_AXES, key=scores.get)
    scores["verdict"] = f"Jev score {scores['total']:.1f}/25 (weakest: {weakest.replace('_', ' ')})"
    u = data.get("usage") or {}
    return scores, Usage("judge", f"jev113:{model}", u.get("input_tokens", 0), u.get("output_tokens", 0),
                         round(u.get("cost", 0), 6), round(time.time() - t0, 2), 0, "reported")
