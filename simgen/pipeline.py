"""Class topic -> runnable HTML simulation: a teacher model writes a blueprint, a student model builds it.

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
from .retrieve import ncert_ground, NCERTError, keywords, GENERIC as _GENERIC

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
SCHEMA_VERSION = 3   # blueprints from an older prompt (no build_tier, grounding check, sliders rule) are never reused

LAB_RULES = """What makes a good Lab app (it is judged on this):
- The steps are the real sequence of the process or experiment (mechanism steps, stages of a cycle,
  the procedure of an experiment). Each is an action the learner triggers and each has a visible
  cause -> effect on the stage that stays after the step ends.
- Every step's animation is concrete: what moves, appears or changes, from where to where, in what
  colour, with which arrow. Every object on the stage is labelled.
- Presets are genuinely different variants of the same phenomenon (reactant pairs, planets,
  materials, organisms, circuits), each carrying the numbers the science needs; the same steps
  work for every preset and the stage/HUD change with the preset.
- Give the learner something to vary, not only steps to click: 1-2 sliders for the continuous quantities that
  change the outcome (temperature, concentration or mole ratio, current, angle, speed, length...), with the
  stage and the HUD responding live as the slider moves. Only a topic with no continuous quantity at all gets
  none, and then the toggle (reveal labels, vectors, ions...) is required.
- The 4 HUD values change as the steps happen (step count, a computed or measured value with its
  unit, a state label...).
- Step questions ask WHY that step happens; Think questions apply the idea to a new situation.
  Exactly one correct option, distractors drawn from real misconceptions, correct-option position varied.
- NCERT-accurate. Grade-appropriate language: short sentences, NCERT terms, no calculus below Class 11."""

PLAN_SCHEMA = """{
  "title": str,
  "grade": int, "subject": str, "ncert_refs": [str], "learning_objectives": [str],
  "evidence": ["3-6 sentences copied VERBATIM from the NCERT SOURCE MATERIAL that the science relies on (empty list only if no source was given)"],
  "source_covers_topic": "full | partial | none: does the NCERT SOURCE MATERIAL actually teach the topic above? 'none' if it is about something else; 'partial' if key parts are missing (omit if no source was given)",
  "build_tier": "easy | medium | hard: how hard this blueprint is for a small model to code. easy = a few static objects and simple motion; medium = one continuous model or 2D geometry (rays, vectors, a circuit); hard = several interacting moving parts, coupled equations, 3D or field/wave geometry, or 5+ distinct step animations",
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
Counts: presets.options 2-4, controls 1-2 (0 only if the topic has no continuous quantity; then "toggle" is required), hud exactly 4, steps 3-6, think exactly 3, words exactly 4."""

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
txt(text,x,y,{size,weight,color,align,bg}) label (bg: optional box colour behind it), pill(text) bottom caption, arrow(x0,y0,x1,y1,{color,width,label}),
curly(x0,y0,x1,y1,bend,prog,color) curved flow / electron-pushing arrow drawn up to fraction prog,
banner(text,bad) flash message, setControl(id,v) move a slider from code. INK = dark text colour, FONT = font stack.
Reserved (do not redeclare): $, cv, ctx, W, H, T, step, anim, S, VIEW, INK, FONT and every helper above.

Drawing rules:
- Scale everything to VIEW every frame so it fits a 320 px phone and a 1200 px desktop; never hard-code the canvas size.
- The board is always light: dark ink, saturated colours, no white text on it.
- During step i animate with ease(anim.k): show the cause (arrow, flow, force) and interpolate the change,
  then hold the new state once the step is done (derive what to draw from step and anim).
- Label every object (names, charges, forces, values with units). pill() a caption for the current state.
- Draw ALL text with txt() (never ctx.fillText): the shell keeps txt() labels on the canvas and slides a
  label to the nearest free line if it would cover another. For a label on a box, pass {bg} instead of
  drawing the box yourself, so the box moves with the label. Keep labels short (<= 4 words).
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


def _ctx(context, grade=None):
    note = getattr(context, "note", "")
    note = (f"NOTE: {note} Use only what a Class {grade or 'N'} learner can follow; leave out the rest. ") if note else ""
    return ("\n\nNCERT SOURCE MATERIAL. " + note + "Use ONLY facts, definitions, values and terms found in this text. "
            "If something you would like to show is not in it, leave it out — do not fill gaps from memory. "
            "The topic above is fixed: passages here about other topics are search noise, ignore them; "
            "never switch to the topic of the source:\n"
            f"{context}\n") if context else ""


def _plan_prompt(topic, grade, context):
    return (f"Topic: {topic}\nGrade: {grade or 'infer from topic'}\n{_ctx(context, grade)}\n"
            f"Design the Lab app blueprint.\n\n{LAB_RULES}\n\n"
            f"Emit exactly this JSON shape:\n{PLAN_SCHEMA}")


def _build_prompt(topic, grade, context, plan=None):
    head = f"Topic: {topic}\nGrade: {grade or 'infer from topic'}\n{_ctx(context, grade)}"
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
    # an unclosed opening fence means the reply was cut off: keep the code, drop the fence,
    # so the syntax check reports the truncation instead of a stray ``` token
    body = m.group(1) if m else re.sub(r"^\s*```[a-z]*[ \t]*\n", "", text, flags=re.I)
    return body.strip() if "const APP" in body else ""


def js_error(topic_js):
    """Syntax error in a TOPIC script (str), or None. QuickJS parses it without running it;
    if QuickJS isn't installed the check is skipped (None)."""
    try:
        import quickjs
    except ImportError:
        return None
    try:
        quickjs.Context().eval("new Function(" + json.dumps(topic_js or "") + ")")
        return None
    except Exception as e:
        msg = str(e).strip().splitlines()
        line = next((l for l in msg if "<input>:" in l), "")
        return f"{msg[0]} {line.strip()}".strip()


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
        "js_parses": js_error(topic_of(html)) is None if topic_of(html) is not None else True,
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
    return f"{mode}__{slug(f'{teacher}-{student}')}__{stamp()}"


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


def _norm(t):
    return re.sub(r"\s+", " ", str(t)).strip().lower()


def verify_evidence(plan, context):
    """(quoted, verified): how many of the blueprint's evidence quotes really occur in the
    NCERT text it was given. A quote the model made up counts as quoted but not verified."""
    src = _norm(context)
    quotes = [q for q in (plan.get("evidence") or []) if isinstance(q, str) and q.strip()]
    return len(quotes), sum(1 for q in quotes if _norm(q).rstrip(".") in src)


def on_topic(topic, plan):
    """False when a blueprint is plainly about something else (a 'Projectile Motion' request that
    came back as an Ohm's-law lab). Compared on 4-letter stems of the topic's own words: the
    title/app name/subtitle must name at least one, and with the learning objectives at least half."""
    kw = [w for w in keywords(topic) if w not in _GENERIC]
    if not kw:
        return True
    app = plan.get("app") or {}
    head = " ".join(str(x) for x in (plan.get("title", ""), app.get("name", ""), app.get("subtitle", ""))).lower()
    full = head + " " + " ".join(str(x) for x in plan.get("learning_objectives") or []).lower()
    return any(w[:4] in head for w in kw) and sum(w[:4] in full for w in kw) * 2 >= len(kw)


def grounding(topic, grade, use_rag):
    """NCERT text for a topic, only chunks that are about it. RAG asked for but not delivered (no DSN,
    DB down, nothing in NCERT on the topic) raises NCERTError: 'grounded' must never silently mean
    'ungrounded', and a wrong-topic passage must never be passed off as the topic."""
    if not use_rag:
        return ""
    g = ncert_ground(topic, grade)
    if not g.chunks:
        near = f" Closest sections, rejected as passing mentions: {'; '.join(g.closest)}." if g.closest else ""
        raise NCERTError(f"No NCERT section is about {topic!r} (grade {grade or 'any'}).{near} Reword it the way "
                         "NCERT does (e.g. 'Electric motor' for 'DC motor'), or check NCERT_DSN, or generate "
                         "ungrounded (--no-rag / untick NCERT grounding).")
    return g.context()


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
    context = grounding(topic, grade, use_rag)
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
            if context and str(plan.get("source_covers_topic", "")).lower().startswith("none"):
                heads = "; ".join(c.strip().split("\n")[0][:60] for c in context.split("\n\n---\n\n"))
                raise NCERTError(f"The NCERT text found isn't about {topic!r}: the teacher read it and says it "
                                 f"covers none of the topic. Sections retrieved: {heads}.")
            quoted, verified = verify_evidence(plan, context) if context else (0, 0)
            if context and not verified:
                raise ValueError("blueprint quotes no NCERT text: evidence missing or not found in the source")
            if not on_topic(topic, plan):
                raise ValueError(f"blueprint is not about {topic!r} (got {plan.get('title')!r})")
            break
        except (ValueError, KeyError):
            if attempt == 2:
                raise
    rec = {"schema": SCHEMA_VERSION, "topic": topic, "grade": grade, "teacher": teacher, "model": usages[-1].model,
           "rag": bool(context), "evidence": {"quoted": quoted, "verified": verified} if context else None,
           "grounding": {"covers": plan.get("source_covers_topic"), "grades": list(getattr(context, "grades", ())),
                         "note": getattr(context, "note", "")} if context else None,
           "created": time.strftime("%Y-%m-%d %H:%M"),
           "cost_usd": round(sum(u.cost_usd for u in usages), 6),
           "in_tokens": sum(u.in_tokens for u in usages),
           "out_tokens": sum(u.out_tokens for u in usages),
           "seconds": round(sum(u.seconds for u in usages), 2), "plan": plan}
    p = write_new(BLUEPRINTS, f"{slug(topic)}__{slug(teacher)}__{stamp()}", ".json",
                  json.dumps(rec, indent=2, ensure_ascii=False))
    rec["file"] = p.stem
    return rec, usages


class BuildError(ValueError):
    """A builder gave a broken script even after the repair call. Carries the calls already paid for."""
    usages = ()


def _build(alias, role, prompt):
    """One builder call, plus one repair call if the script doesn't parse (a typo, or a reply
    cut off at the model's output limit). Returns (html, [Usage]); raises if still broken."""
    text, u = call(alias, BUILD_SYS, prompt, role=role)
    usages = [u]
    for attempt in (1, 2):
        js = extract_topic(text)
        page = build_html(text)
        err = "no TOPIC script found" if not page else (js_error(js) if js else None)
        if not err:
            return page, usages
        if attempt == 2:
            e = BuildError(f"{alias} built a broken TOPIC script: {err}")
            e.usages = usages
            raise e
        cut = u.finish == "length" or not (js or "").rstrip().endswith(("}", ";", ")", "]"))
        fix = (f"\n\nYOUR PREVIOUS ATTEMPT FAILED: {err}."
               + (" It was cut off before the end. Write the COMPLETE script again, more compactly: "
                  "shorter helper code, no comments, same content." if cut else
                  " Output the whole corrected TOPIC script."))
        text, u = call(alias, BUILD_SYS, prompt + fix, role=role)
        usages.append(u)


TIERS = ("easy", "medium", "hard")
# which student builds each tier; override in .env: STUDENT_EASY / STUDENT_MEDIUM / STUDENT_HARD
TIER_STUDENT = {"easy": "musespark13c", "medium": "gemini38flash", "hard": "opus55"}
AUTO = "auto"       # as the student alias: pick the student by difficulty


def tier_student(tier):
    return os.getenv(f"STUDENT_{tier.upper()}") or TIER_STUDENT[tier]


_DYNAMICS = re.compile(r"ray|lens|mirror|prism|refract|reflect|vector|force|motion|pendulum|circuit|current|wave|"
                       r"field|electro|magnet|motor|generator|gravit|pressure|heat|reaction", re.I)
_HARD = re.compile(r"induction|interference|diffraction|projectile|rotat|torque|oscillat|doppler|thermodynamic|"
                   r"kinetic theory|orbit|semiconductor|quantum|alternating|resonan|polari|collision|"
                   r"relativ|nuclear|moment of inertia|angular", re.I)


def estimate_tier(topic, grade=None, context=""):
    """Difficulty of BUILDING the simulation, guessed before any model is paid: (tier, reasons).
    Points for the class (11-12 = 2, 9-10 = 1), for continuous-physics/geometry words in the topic (1) or
    hard-physics words (2), and, if the NCERT text is passed, for how equation-heavy it is (per 1000
    chars: 6+ '=' or '$' = 2, 2+ = 1). 0-1 easy, 2-3 medium, 4+ hard. A cheap prior, not a verdict: the
    blueprint can only raise it (final_tier) and a broken build escalates (_build_tiered)."""
    why, pts = [], 0
    g = grade or int((re.search(r"class\s*(\d+)", topic or "", re.I) or [0, 0])[1]) or 0
    if g >= 11:
        pts += 2; why.append(f"class {g}")
    elif g >= 9:
        pts += 1; why.append(f"class {g}")
    if _HARD.search(topic or ""):
        pts += 2; why.append("hard-physics topic")
    elif _DYNAMICS.search(topic or ""):
        pts += 1; why.append("continuous physics / geometry")
    if context:
        load = (context.count("=") + context.count("$")) / max(len(context), 1) * 1000
        n = 2 if load >= 6 else 1 if load >= 2 else 0
        if n:
            pts += n; why.append(f"equation-heavy source ({load:.0f}/1000 chars)")
    return ("easy" if pts <= 1 else "medium" if pts <= 3 else "hard"), why


def _build_tiered(tier, prompt):
    """Build with the tier's student; a broken script moves up to the next tier (never down). Returns
    (html, [Usage]) with every attempt's cost, including the failed ones."""
    used = []
    for t in TIERS[TIERS.index(tier):]:
        try:
            page, bu = _build(tier_student(t), "student", prompt)
            return page, used + bu
        except BuildError as e:
            used += e.usages
            err = e
    err.usages = used
    raise err


def final_tier(pre, plan):
    """The blueprint can raise the pre-blueprint estimate, never lower it: by the teacher's own
    build_tier, and by how many equations the model has to implement (2+ medium, 5+ hard)."""
    t = str((plan or {}).get("build_tier", "")).strip().lower()
    n = len(((plan or {}).get("science") or {}).get("equations") or [])
    by_eq = "hard" if n >= 5 else "medium" if n >= 2 else "easy"
    return max(pre, t if t in TIERS else pre, by_eq, key=TIERS.index)


def _tag(usages, tier):
    for u in usages:
        u.tier = tier
    return usages


def run(mode, topic, grade, teacher, student, use_rag=True, reuse_blueprint=True):
    """mode: only "teacher_student". student == "auto" picks the student by
    difficulty (estimate_tier + the blueprint's build_tier -> STUDENT_EASY/MEDIUM/HARD); in
    teacher_student with HARD_SINGLE=1 a topic estimated hard skips the blueprint and one frontier
    call builds it whole. Returns (html, plan_or_None, [Usage]) — only calls made in this run."""
    auto = student == AUTO
    if mode == "teacher_student":
        pre = estimate_tier(topic, grade)[0]     # topic + class only: costs nothing, needs no database
        if auto and pre == "hard" and os.getenv("HARD_SINGLE", "").lower() in ("1", "true", "yes"):
            html_out, bu = _build(tier_student("hard"), "student",
                                  _build_prompt(topic, grade, grounding(topic, grade, use_rag)))
            return html_out, None, _tag(bu, "hard")
        rec, usages = blueprint(topic, grade, teacher, use_rag, force=not reuse_blueprint)
        # The blueprint already carries the grounded facts, so the student doesn't
        # re-pay for the NCERT context. That is where most of the saving comes from.
        prompt = _build_prompt(topic, grade, "", rec["plan"])
        tier = final_tier(pre, rec["plan"])
        html_out, bu = _build_tiered(tier, prompt) if auto else _build(student, "student", prompt)
        return html_out, rec["plan"], _tag(usages + bu, tier)

    raise ValueError(f"unknown mode {mode}")


MODES = ("teacher_student",)


# ---------------------------------------------------------------- judge

JUDGE_SYS = ("You are an NCERT science examiner grading a teaching simulation's source code. "
             "Be strict. Output JSON only.")

JUDGE_RUBRIC = """Score 0-5 on each, then give a one-line verdict and the concrete fixes:
{"scientific_accuracy": int, "ncert_alignment": int, "interactivity": int,
 "grade_appropriateness": int, "pedagogy": int, "verdict": str,
 "issues": [{"axis": "one of the five names above", "fix": "one concrete change to the TOPIC script"}]}
scientific_accuracy: are the equations and the numeric update rule right?
ncert_alignment: does it cover the chapter's actual learning outcomes?
interactivity: do the presets, sliders/toggle and steps change the stage and the HUD in a scientifically
  meaningful way? (Sliders are expected wherever the science has a continuous quantity.)
grade_appropriateness: vocabulary and maths level.
pedagogy: do the ordered steps, the question after each step and the Think questions teach the concept?
You are shown only the TOPIC script. It runs inside a fixed shell that already provides preset chips, ordered
step buttons, a Guide (steps with explanations) and a Challenge (a question after each step), a Think tab of 3 questions, a key-words
sheet, a 4-value HUD, sliders, a toggle, and phone and dark layouts. Do not deduct for what the shell does,
and judge only what the script adds on top of it. 5 means nothing a teacher would change. Every score below 5
needs an entry in "issues": a concrete, implementable change to the script (at most 5, most valuable first);
a score of 5 needs none."""


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
                   role="judge", max_tokens=8000,
                   timeout=int(os.getenv("JUDGE_TIMEOUT", "90")))   # the judge is optional: never wait minutes on it
    scores = extract_json(text)
    scores["issues"] = [i for i in scores.get("issues") or [] if isinstance(i, dict) and i.get("fix")][:5]
    keys = ("scientific_accuracy", "ncert_alignment", "interactivity",
            "grade_appropriateness", "pedagogy")
    scores["total"] = sum(int(scores.get(k, 0)) for k in keys)
    return scores, u


def refine(html, topic, grade, alias, issues):
    """One revision pass: the builder gets its own script back with the examiner's concrete fixes.
    Returns (html, [Usage]) like _build (role 'refine'); raises BuildError if the result doesn't parse."""
    listed = "\n".join(f"- ({i.get('axis', '?')}) {i['fix']}" for i in issues)
    prompt = (f"Topic: {topic}\nGrade: {grade or 'infer from topic'}\n\n"
              "Below is a finished TOPIC script for the Lab-app shell. An examiner found these problems:\n"
              f"{listed}\n\nReturn the COMPLETE improved script with exactly these fixed. Keep everything that already "
              "works, keep the same API and contract, do not shorten it, do not add anything unrelated.\n\n"
              f"{TOPIC_CONTRACT}\n\nCURRENT SCRIPT:\n```js\n{topic_of(html)}\n```\n\n"
              "Return ONLY the script in a single ```js code fence.")
    return _build(alias, "refine", prompt)


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
# Jev scores each axis independently and, unlike the chat judge, sees no rubric -- so the rubric's two
# calibrations go into every question: what the fixed shell already provides, and that 5 is rare.
JEV_STRICT = ("You see only the TOPIC script; the fixed shell already provides preset chips, step buttons, a "
              "Guide/Challenge coach, a question per step, Think questions, a HUD, sliders and a toggle, so judge only "
              "what the script adds. Be strict: 5 only if a teacher would change nothing; a good script is 3 or 4.")
JEV_SCALE = ["0 - completely wrong or absent", "1 - mostly wrong", "2 - partially right, real gaps",
             "3 - mostly right, minor issues", "4 - right, small polish possible", "5 - fully correct"]


def judge_jev(html, topic, grade):
    model = os.environ.get("MODEL_jev113_ID", "typesafe/jev-1.13")
    t0 = time.time()
    body = {
        "model": model,
        "state": {"topic": topic, "grade": grade, "simulation_source": (topic_of(html) or html)[:60000]},
        "questions": {k: {"type": "score",
                          "instructions": f'For topic "{topic}" (grade {grade}): {q} {JEV_STRICT}',
                          "criteria": JEV_SCALE}
                     for k, q in JEV_AXES.items()},
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/alpha/decisions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=int(os.getenv("JUDGE_TIMEOUT", "90"))) as resp:
        data = json.loads(resp.read())
    scores = {k: data["answers"][k]["score"] for k in JEV_AXES}
    scores["total"] = round(sum(scores.values()), 2)
    weakest = min(JEV_AXES, key=scores.get)
    scores["issues"] = []        # Jev returns scores, not fixes: nothing for refine() to act on, so it is skipped
    scores["verdict"] = f"Jev score {scores['total']:.1f}/25 (weakest: {weakest.replace('_', ' ')})"
    u = data.get("usage") or {}
    return scores, Usage("judge", f"jev113:{model}", u.get("input_tokens", 0), u.get("output_tokens", 0),
                         round(u.get("cost", 0), 6), round(time.time() - t0, 2), 0, "reported")
