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
#
# Every simulation is the same "Lab app" (modelled on a hand-built Ester Lab): a Play tab
# with a Guide/Challenge coach, a canvas stage under a 4-value HUD, preset chips, optional
# sliders, ordered step buttons and a question sheet after each step; a Think tab of MCQs;
# a key-words sheet; phone + dark layouts. That shell is fixed code (shell.html). Models
# only write the TOPIC script that fills it, so every output has the same look and the
# builder pays for a fraction of the output tokens a whole page would cost.

SHELL = (Path(__file__).parent / "shell.html").read_text(encoding="utf-8")
TOPIC_START, TOPIC_END = "/*@@TOPIC-START@@*/", "/*@@TOPIC-END@@*/"
TOPIC_RE = re.compile(re.escape(TOPIC_START) + r".*?" + re.escape(TOPIC_END), re.S)
EXAMPLE_SENTINEL = "__SHELL_EXAMPLE__"
EXAMPLE_TOPIC = "\n".join(l for l in TOPIC_RE.search(SHELL).group(0)[len(TOPIC_START):-len(TOPIC_END)]
                          .strip().splitlines() if not l.startswith("//"))
# The hand-built Ester Lab this format was modelled on, ported to the TOPIC API: the
# reference for mechanism/process topics, as the pendulum is for experiments.
EXAMPLE_ESTER = (Path(__file__).parent / "example_ester.js").read_text(encoding="utf-8")
SCHEMA_VERSION = 2   # blueprints made for the old free-form page are never reused

LAB_RULES = """What makes a good Lab app (it is judged on this):
- The steps are the real sequence of the process or experiment (mechanism steps, stages of a cycle,
  the procedure of an experiment). Each is an action the learner triggers and each has a visible
  cause -> effect on the stage that stays after the step ends.
- Every step's animation is concrete: what moves, appears or changes, from where to where, in what
  colour, with which arrow. Every object on the stage is labelled.
- Presets are genuinely different variants of the same phenomenon (reactant pairs, planets,
  materials, organisms, circuits), each carrying the numbers the science needs; the same steps
  work for every preset and the stage/HUD change with the preset.
- Sliders only for real continuous quantities that change the outcome; otherwise none.
- The 4 HUD values change as the steps happen (step count, a computed or measured value with its
  unit, a state label...).
- Step questions ask WHY that step happens; Think questions apply the idea to a new situation.
  Exactly one correct option, distractors drawn from real misconceptions, correct-option position varied.
- NCERT-accurate. Grade-appropriate language: short sentences, NCERT terms, no calculus below Class 11."""

PLAN_SCHEMA = """{
  "title": str,
  "grade": int, "subject": str, "ncert_refs": [str], "learning_objectives": [str],
  "app": {"name": "2-3 words ending in Lab, e.g. Ester Lab", "subtitle": "Class N · chapter · focus",
          "mark": "1-2 chars for the logo", "accent": "#hex, saturated, readable on white",
          "accent_dark": "#hex, lighter tint of accent for dark mode"},
  "science": {"equations": [str], "constants": {"name": "value unit"},
              "model": "the exact rule the sim follows: state after each step, or update equations with units"},
  "presets": {"label": "what varies, e.g. Reactants",
              "options": [{"name": str, "sub": "short result or value", "ico": "<=3 chars", "data": {"key": "number or string"}}]},
  "controls": [{"id": "js identifier", "label": str, "unit": str, "min": num, "max": num, "step": num, "value": num}],
  "toggle": {"label": "<=2 words, e.g. 18O tag", "shows": "what extra it reveals on the stage"} or null,
  "hud": [{"label": "<=2 words", "shows": "the value in each state, with unit"}],
  "stage": {"scene": "resting layout: every object, where it sits (left/centre/right, top/bottom), colour, label",
            "caption": "bottom caption text in each state"},
  "steps": [{"name": "<=3 words, verb first", "sub": "<=4 words", "do": "one coach sentence, may use <b>, no 'Tap'",
             "visual": "exactly what animates in this step: from -> to, arrows, colours; the end state",
             "result": "<=5-word banner", "dur": "seconds",
             "ask": {"q": str, "o": [4 x str], "a": "0-based index of the correct option", "e": "1-2 sentence explanation"}}],
  "think": [{"q": str, "o": [4 x str], "a": int, "e": str}],
  "words": [{"t": str, "d": "one sentence"}],
  "finish": {"title": "<=4 words", "summary": "2 sentences tying the steps together"},
  "misconceptions": [str],
  "grade_language_notes": str
}
Counts: presets.options 2-4, controls 0-2, hud exactly 4, steps 3-6, think exactly 3, words exactly 4."""

TOPIC_CONTRACT = """You write ONLY the TOPIC script of a fixed "Lab app" shell. The shell already has the HTML, CSS,
Play/Think tabs, Guide/Challenge coach, question sheets, key-words sheet, HUD, preset chips, sliders,
step buttons, animation loop, resize/DPR and phone/dark layouts. Do not output any of that.

Declare these top-level names (plain script: no modules, imports, fetch, timers, DOM access, storage,
external assets or network):
const APP={name,subtitle,mark,accent,accentDark}
const PRESETS={label,options:[{name,sub,ico,...data}]}   // 2-4 variants; put each variant's numbers on it; or null
const CONTROLS=[{id,label,unit,min,max,step,value}]      // 0-2 sliders; [] if none
const TOGGLE={label}                                     // or null; read it as S.toggle
const HUD=["label1","label2","label3","label4"]           // exactly 4
const STEPS=[{name,sub,do,result,dur,ask:{q,o:[4 options],a,e}}]   // 3-6, in the real order
      // dur: seconds, or a function returning seconds (e.g. ()=>3*period())
const THINK=[{q,o:[4 options],a,e}]                      // exactly 3
const WORDS=[{t,d}]                                      // exactly 4
const FINISH={title,html}                                // html: 1-2 short <p>
Text fields may use <b>, <sub>, <sup>. Question field a = 0-based index of the correct option.

Hook functions (function declarations):
function resetSim()      fresh state for the current preset and sliders (load, preset change, reset, mode change)
function startStep(i)    the learner just triggered step i (0-based): set up its transition
function update(dt)      every frame (dt <= 0.033 s), also when idle, so continuous motion keeps going
function draw(ctx,W,H)   draw the whole scene in CSS pixels on the light board the shell already painted
function readouts()      return 4 short strings for the HUD, e.g. "2.84<small>s</small>"
optional: function onControl(id) after a slider moves; function onPreset(i) after a preset is picked

Shell state you READ (never reassign): step = steps completed (0..STEPS.length); anim = null, or
{i,t,dur,k} while step i animates (k: 0 -> 1); S.preset (index), S.c[id] (slider values), S.toggle;
T = seconds since load; VIEW = {x,y,w,h} the safe box below the HUD and above the caption.
Helpers: ease(t) smoothstep, lerp(a,b,k), clamp(v,a,b), rr(x,y,w,h,r) rounded-rect path,
txt(text,x,y,{size,weight,color,align}), pill(text) bottom caption, arrow(x0,y0,x1,y1,{color,width,label}),
curly(x0,y0,x1,y1,bend,prog,color) curved flow / electron-pushing arrow drawn up to fraction prog,
banner(text,bad) flash message, setControl(id,v) move a slider from code. INK = dark text colour, FONT = font stack.
Reserved (do not redeclare): $, cv, ctx, W, H, T, step, anim, S, VIEW, INK, FONT and every helper above.

Drawing rules:
- Scale everything to VIEW every frame so it fits a 320 px phone and a 1200 px desktop; never hard-code the canvas size.
- The board is always light: dark ink, saturated colours, no white text on it.
- During step i animate with ease(anim.k): show the cause (arrow, flow, force) and interpolate the change,
  then hold the new state once the step is done (derive what to draw from step and anim).
- Label every object (names, charges, forces, values with units). pill() a caption for the current state.
- Numbers on the stage and HUD come from the equations and the preset/slider values, not decoration."""

TEACHER_PLAN_SYS = (
    "You are a senior NCERT curriculum designer and interaction designer. You write the blueprint for one "
    "interactive 'Lab app' that a smaller model will implement literally. The app shell is fixed: a Play tab "
    "where the learner performs 3-6 ordered actions on an animated canvas (Guide mode asks one question after "
    "each action; Challenge mode makes them do the order unaided), a 4-value live HUD, 2-4 preset variants, "
    "optional sliders, a Think tab with 3 questions and a key-words sheet. Turn the topic into that experience. "
    "Be exact and concrete: real equations and values, precise drawing and animation instructions for every "
    "step, correct questions. Every string is a short phrase or one sentence. "
    "Output JSON only — no prose, no code fences."
)

BUILD_SYS = ("You are an expert front-end engineer and science educator. You write the TOPIC script that turns "
             "a fixed Lab-app shell into an accurate, animated NCERT teaching simulation.")


def _ctx(context):
    return f"\n\nNCERT SOURCE MATERIAL (ground every fact in this):\n{context}\n" if context else ""


def _plan_prompt(topic, grade, context):
    return (f"Topic: {topic}\nGrade: {grade or 'infer from topic'}\n{_ctx(context)}\n"
            f"Design the Lab app blueprint.\n\n{LAB_RULES}\n\n"
            f"Emit exactly this JSON shape:\n{PLAN_SCHEMA}")


def _build_prompt(topic, grade, context, plan=None):
    head = f"Topic: {topic}\nGrade: {grade or 'infer from topic'}\n{_ctx(context)}"
    if plan:
        head += ("\nBLUEPRINT — implement it exactly. Copy its text (app, presets, steps, questions, words, finish) "
                 "verbatim, use its equations, values and step visuals; do not invent different science. "
                 "Mapping: app.accent_dark -> APP.accentDark, presets.options[].data spread onto each option, "
                 "hud[].label -> HUD, finish.summary -> FINISH.html.\n"
                 f"{json.dumps(plan, indent=2, ensure_ascii=False)}\n")
    else:
        head += f"\nDesign the Lab app yourself.\n{LAB_RULES}\n"
    return (head + f"\n{TOPIC_CONTRACT}\n\n"
            "Two finished TOPIC scripts follow, on other topics. Match their quality, structure and drawing "
            "style; do not reuse their science or wording.\n"
            f"EXAMPLE 1 — an experiment with presets and a slider:\n```js\n{EXAMPLE_TOPIC}\n```\n"
            f"EXAMPLE 2 — a step-by-step mechanism with curly arrows:\n```js\n{EXAMPLE_ESTER}\n```\n\n"
            "Return ONLY your TOPIC script in a single ```js code fence.")


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


JS_FENCE = re.compile(r"```(?:js|javascript)[ \t]*\n(.*?)```", re.S | re.I)
ANY_FENCE = re.compile(r"```[a-z]*[ \t]*\n(.*?)```", re.S | re.I)


def extract_topic(text):
    """The TOPIC script from a builder reply: a ```js fence, else any fence or bare text
    that declares APP. '' if there's no topic script at all."""
    m = JS_FENCE.search(text)
    if m:
        return m.group(1).strip()
    m = ANY_FENCE.search(text)
    body = m.group(1) if m else text
    return body.strip() if "const APP" in body else ""


def assemble(topic_js):
    """Splice a TOPIC script into the fixed shell. '</script' inside the topic (e.g. in a
    string) would end the shell's <script> early, so it's escaped."""
    js = topic_js.replace("</script", "<\\/script")
    return TOPIC_RE.sub(lambda _: f"{TOPIC_START}\n{js}\n{TOPIC_END}", SHELL, count=1)


def build_html(text):
    """Builder reply -> full HTML page. A whole document (a model that ignored the format)
    is kept as-is; otherwise the TOPIC script goes into the shell. '' if neither."""
    page = extract_html(text)
    if page.lower().lstrip().startswith(("<!doctype", "<html")):
        return page
    js = extract_topic(text)
    return assemble(js) if js else ""


def topic_of(html):
    """The TOPIC script inside an assembled page, or None for a free-form page."""
    m = TOPIC_RE.search(html)
    return m.group(0)[len(TOPIC_START):-len(TOPIC_END)].strip() if m else None


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
        # built as a Lab app: shell present, the core TOPIC names declared, example replaced
        "lab_shell": (EXAMPLE_SENTINEL not in html and all(
            re.search(p, topic_of(html) or "") for p in (r"\bconst APP\b", r"\bconst STEPS\b", r"\bfunction draw\b"))),
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
    """Newest stored blueprint for this topic (by this teacher, if given) in the current
    Lab-app schema, or None. Older-schema files stay listed in the UI but are never reused."""
    for p in blueprint_files(topic, teacher):
        rec = read_blueprint(p)
        if rec.get("schema") == SCHEMA_VERSION:
            return rec
    return None


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
    # The Lab-app blueprint (steps + a question per step + think + words + visuals) runs
    # ~3-5k tokens; 12000 is a ceiling with room for reasoning, not a target. Models
    # occasionally emit invalid JSON: one retry is cheaper than a hand-rolled repair.
    for attempt in (1, 2):
        raw, u = call(teacher, TEACHER_PLAN_SYS, _plan_prompt(topic, grade, context),
                      role="teacher", max_tokens=12000, temperature=0.2)
        usages.append(u)
        try:
            plan = extract_json(raw)
            break
        except (ValueError, KeyError):
            if attempt == 2:
                raise
    rec = {"schema": SCHEMA_VERSION, "topic": topic, "grade": grade, "teacher": teacher, "model": usages[-1].model,
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
        html_out = build_html(text)
        if not html_out:
            raise ValueError(f"{student} returned no TOPIC script.")
        return html_out, rec["plan"], usages + [u]

    context = ncert_context(topic, grade) if use_rag else ""
    if mode in ("teacher_only", "student_only"):
        alias, role = (teacher, "teacher") if mode == "teacher_only" else (student, "student")
        text, u = call(alias, BUILD_SYS, _build_prompt(topic, grade, context), role=role)
        html_out = build_html(text)
        if not html_out:
            raise ValueError(f"{alias} returned no TOPIC script.")
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
interactivity: do the steps, presets and sliders change the simulation in a scientifically meaningful way?
grade_appropriateness: vocabulary and maths level.
pedagogy: do the ordered steps, the question after each step and the Think questions teach the concept?
(For a Lab app you are shown only its TOPIC script; the shared UI shell around it is fixed and not graded.)"""


def judge(html, topic, grade, judge_alias):
    if judge_alias in ("jev113", "typesafe/jev-1.13"):
        return judge_jev(html, topic, grade)
    # max_tokens must clear the reasoning budget (also 1500 by default in call()) with
    # room to spare, or a reasoning model burns the whole cap on hidden thinking and
    # returns empty content -> "Expecting value"/"no JSON object found" errors. Measured:
    # a cheap model reading a full ~60k-char simulation can burn 3.5k+ tokens just thinking
    # before it writes the JSON verdict, so 4000 was too tight and failed intermittently
    # under load. 8000 costs nothing extra unless the model actually uses it.
    src = topic_of(html) or html   # the fixed shell is the same for every sim: don't pay to grade it
    text, u = call(judge_alias, JUDGE_SYS,
                   f"Topic: {topic}\nGrade: {grade}\n\n{JUDGE_RUBRIC}\n\nSIMULATION SOURCE:\n{src[:60000]}",
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
        "state": {"topic": topic, "grade": grade, "simulation_source": (topic_of(html) or html)[:60000]},
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
