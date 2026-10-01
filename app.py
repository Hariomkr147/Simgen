"""Local test UI for the simgen pipeline. Run: python app.py
Then open http://127.0.0.1:5050

ponytail: Flask (one dep), sync request/response — a 30-180s LLM call is fine
blocking, no JS needed for the "loading" state (the browser's own spinner
covers it). Library is a filesystem scan (runs/*/*.html), not a database —
the files are already the source of truth; a sidecar JSONL only adds the
cost/model metadata scans can't recover.
"""
import html
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, make_response, redirect, request, send_file

from simgen import pipeline, tts as speech
from simgen.__main__ import load_env, slug

load_env()
app = Flask(__name__)
# Set ACCESS_CODE to gate the /run (generate) endpoint behind a shared code --
# protects your model-API budget on a public deploy. Empty (default) = no gate.
ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
RUNS = Path("runs")
LOG = RUNS / "library.jsonl"

MODE_LABEL = {"teacher_only": "teacher only", "student_only": "student only",
              "teacher_student": "teacher -> student", "blueprint": "blueprint only"}
MODE_BADGE = {"teacher_only": "badge-t", "student_only": "badge-s", "teacher_student": "badge-ts",
              "blueprint": "badge-t"}

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>simgen</title>
<style>
:root{
  --bg:#f1edf5; --surface:#ffffff; --surface-2:#f0ebf6; --ink:#1d1a26; --ink-dim:#5f5a6e;
  --border:#ddd6e6; --accent:#b0226b; --accent-ink:#ffffff;
  --teacher:#a5690f; --student:#5850c9; --hybrid:#7a3fd1;
  --good:#0e7a3c; --good-soft:#dcefe2; --bad:#b93838; --bad-soft:#f8e3e3; --radius:16px;
  --shadow:0 6px 24px rgba(16,40,24,.10);
  --serif:"Baloo 2","Trebuchet MS",system-ui,sans-serif;
  --sans:"Atkinson Hyperlegible","Segoe UI",system-ui,sans-serif;
  --mono:"Chakra Petch",ui-monospace,"Cascadia Code",Menlo,monospace;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#15121b; --surface:#1f1a28; --surface-2:#2a2334; --ink:#ece8f2; --ink-dim:#a8a1b8;
  --border:#342c40; --accent-ink:#1a0710;
  --teacher:#d9a441; --student:#9089ef; --hybrid:#9089ef;
  --good:#5cc88a; --good-soft:#15301f; --bad:#ff8080; --bad-soft:#3a1a1c;
  --shadow:0 6px 24px rgba(0,0,0,.35); color-scheme:dark;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
     padding:2.5rem 1rem 4rem;min-height:100vh}
.wrap{max-width:980px;margin:0 auto}
header{margin-bottom:2rem;display:flex;align-items:center;gap:.9rem}
.logo{width:42px;height:42px;border-radius:12px;flex:none;display:grid;place-items:center;
      background:linear-gradient(160deg,var(--accent),var(--hybrid));color:#fff;
      font-family:var(--serif);font-weight:800;font-size:18px}
header h1{font-family:var(--serif);font-size:2rem;font-weight:600;margin:0 0 .2rem;
          letter-spacing:-.01em}
header p{margin:0;color:var(--ink-dim);font-size:.95rem}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
       box-shadow:var(--shadow);padding:1.5rem;margin-bottom:1.75rem}
.panel h2{font-family:var(--serif);font-size:1.05rem;font-weight:600;margin:0 0 1rem}
form{display:grid;gap:1rem}
.row{display:grid;grid-template-columns:2fr 1fr;gap:1rem}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem}
@media (max-width:640px){.row,.row3{grid-template-columns:1fr}}
.field{display:flex;flex-direction:column;gap:.35rem}
label{font-size:.78rem;color:var(--ink-dim);text-transform:uppercase;letter-spacing:.04em}
input,select{padding:.6rem .7rem;border-radius:12px;border:1.5px solid var(--border);
             background:var(--bg);color:var(--ink);font:inherit;font-size:.92rem}
input:focus,select:focus{outline:3px solid var(--accent);outline-offset:1px}
fieldset{border:0;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:.5rem 1.25rem}
fieldset label{text-transform:none;font-size:.88rem;color:var(--ink);display:flex;
                align-items:center;gap:.4rem;letter-spacing:0}
.actions{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
.hint{font-size:.8rem;color:var(--ink-dim)}
button{padding:.7rem 1.4rem;border-radius:12px;border:0;background:var(--accent);
       color:var(--accent-ink);font-weight:700;font-size:.92rem;cursor:pointer}
button:hover{opacity:.9}
.buttons{display:flex;gap:.6rem;flex-wrap:wrap}
.badge{display:inline-block;padding:.15rem .6rem;border-radius:99px;font-size:.72rem;
       font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#fff}
.badge-t{background:var(--teacher)}.badge-s{background:var(--student)}.badge-ts{background:var(--hybrid)}
.stat{font-family:var(--mono);font-variant-numeric:tabular-nums}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
      box-shadow:var(--shadow);margin-bottom:1.25rem;overflow:hidden}
.card-head{display:flex;justify-content:space-between;align-items:center;gap:1rem;
           padding:.85rem 1.1rem;background:var(--surface-2);flex-wrap:wrap}
.card-head .stats{display:flex;gap:1rem;font-size:.85rem;color:var(--ink-dim)}
.checks-ok,.checks-bad{font-weight:700;border-radius:99px;padding:.1rem .55rem;font-size:.78rem}
.checks-ok{color:var(--good);background:var(--good-soft)}
.checks-bad{color:var(--bad);background:var(--bad-soft)}
.err{padding:1rem 1.1rem;color:var(--bad);font-size:.9rem}
iframe{width:100%;height:620px;border:0;display:block;background:#fff}
.lib-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:.9rem}
.lib-card{display:block;background:var(--surface);border:1px solid var(--border);
          border-radius:var(--radius);padding:.9rem 1rem}
.lib-card:hover{border-color:var(--accent)}
.lib-card-link{display:block;text-decoration:none;color:inherit}
.download-link{display:inline-block;margin-top:.55rem;font-size:.75rem;color:var(--accent);
           text-decoration:none;border:1px solid var(--border);border-radius:6px;padding:.2rem .5rem}
.download-link:hover{border-color:var(--accent)}
.lib-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:.55rem}
.lib-when{font-size:.72rem;color:var(--ink-dim)}
.lib-card h4{margin:0 0 .4rem;font-family:var(--serif);font-size:.98rem;font-weight:600;
             line-height:1.3}
.lib-meta{font-size:.78rem;color:var(--ink-dim);display:flex;flex-wrap:wrap;justify-content:space-between;gap:.3rem}
.lib-models{display:flex;flex-wrap:wrap;gap:.35rem;margin:0 0 .5rem}
.model-tag{font-family:var(--mono);font-size:.7rem;background:var(--surface-2);
           border:1px solid var(--border);border-radius:6px;padding:.1rem .4rem;cursor:help}
.empty{color:var(--ink-dim);font-size:.9rem}
.split{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;padding:.6rem 1.1rem;font-size:.82rem;
       color:var(--ink-dim);border-top:1px solid var(--border)}
.split b{color:var(--ink);font-family:var(--mono);font-weight:600}
.bp-body{padding:.9rem 1.1rem;font-size:.86rem;line-height:1.5}
.bp-body code{font-family:var(--mono);font-size:.8rem;background:var(--surface-2);
              padding:.05rem .35rem;border-radius:4px}
.tbl-wrap{overflow-x:auto}
table.bp{width:100%;border-collapse:collapse;font-size:.84rem}
table.bp th{text-align:left;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;
            color:var(--ink-dim);font-weight:600;padding:.4rem .5rem;border-bottom:1px solid var(--border)}
table.bp td{padding:.55rem .5rem;border-bottom:1px solid var(--border);vertical-align:top}
table.bp td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
table.bp a{color:var(--accent)}
.tier{font-size:.7rem;font-weight:600;padding:.05rem .5rem;border-radius:99px;white-space:nowrap;
      border:1px solid currentColor}
.tier-easy{color:#1a7f4b}.tier-medium{color:#a5690f}.tier-hard{color:#c2362b}
.pill{font-size:.7rem;padding:.05rem .45rem;border-radius:99px;border:1px solid var(--border);
      color:var(--ink-dim);white-space:nowrap}
</style></head><body><div class="wrap">
<header>
  <span class="logo" aria-hidden="true">S</span>
  <div><h1>simgen</h1>
  <p>NCERT topic → interactive simulation. Teacher plans, student builds, every run priced in &#8377; (1 USD = &#8377;@@RATE@@). <a href="/costs" style="color:var(--accent);font-weight:600">Cost table &rarr;</a></p></div>
</header>

<div class="panel">
  <h2>Generate</h2>
  <form method="post" action="/run">
    <div class="row">
      <div class="field"><label for="topic">Class topic</label>
        <input id="topic" name="topic" list="bp-topics" value="@@TOPIC@@" placeholder="Class 9 Science: Simple Pendulum" required>
        <datalist id="bp-topics">@@BP_OPTIONS@@</datalist></div>
      <div class="field"><label for="grade">Grade</label>
        <input id="grade" name="grade" type="number" min="1" max="12" value="@@GRADE@@"></div>
    </div>
    <div class="row3">
      <div class="field"><label for="teacher">Teacher model</label>
        <select id="teacher" name="teacher">@@TEACHER_OPTS@@</select></div>
      <div class="field"><label for="student">Student model</label>
        <select id="student" name="student">@@STUDENT_OPTS@@</select></div>
      <div class="field"><label>&nbsp;</label>
        <label style="text-transform:none;display:flex;align-items:center;gap:.4rem;padding-top:.4rem">
          <input type="checkbox" name="rag" @@RAG@@ style="width:auto"> ground with NCERT (RAG)</label>
        <label style="text-transform:none;display:flex;align-items:center;gap:.4rem">
          <input type="checkbox" name="reuse" @@REUSE@@ style="width:auto"> reuse stored blueprint</label></div>
    </div>
    <div class="row3">
      <div class="field"><label for="tts">Voice (TTS) model</label>
        <select id="tts" name="tts">@@TTS_OPTS@@</select></div>
      <div class="field"><label for="narrate">Narration</label>
        <select id="narrate" name="narrate">@@NARRATE_OPTS@@</select></div>
      <div class="field"><label>&nbsp;</label><span class="hint">Spoken coach lines, questions and answers (about &#8377;6&ndash;15 per language). Hinglish is written by an extra model call.</span></div>
    </div>
    @@ACCESS_FIELD@@
    <div class="actions">
      <fieldset>
        <label><input type="checkbox" name="modes" value="teacher_student" @@M_TS@@> teacher_student</label>
        <label><input type="checkbox" name="modes" value="blueprint" @@M_BP@@> blueprint only</label>
      </fieldset>
      <div class="buttons">
        <button type="submit">Generate</button>
      </div>
    </div>
    <p class="hint">A run blocks for as long as the real model call takes — usually 30s to 2 minutes per mode.</p>
  </form>
</div>

@@RESULTS@@

<div class="panel">
  <h2>Blueprints <span class="hint">— @@BP_COUNT@@ stored · teacher_student reuses these, so only the student build is paid</span></h2>
  @@BLUEPRINTS@@
</div>

<div class="panel">
  <h2>Library <span class="hint">— @@LIB_COUNT@@ generated</span></h2>
  @@LIBRARY@@
</div>
</div></body></html>"""

ACCESS_FIELD = '''<div class="field"><label for="access_code">Access code</label>
      <input id="access_code" name="access_code" type="password" value="@@ACCESS_VAL@@"
             placeholder="ask the owner for the code" autocomplete="off"></div>'''


def model_options(kind, selected):
    """Dropdown aliases: TEACHER_MODELS / STUDENT_MODELS in .env (comma list), else every
    MODEL_<alias>_ID that isn't a :batch variant. Compared case-insensitively because
    Windows upper-cases os.environ keys."""
    listed = [a.strip() for a in os.getenv(f"{kind.upper()}_MODELS", "").split(",") if a.strip()]
    aliases = listed or sorted(k[len("MODEL_"):-len("_ID")] for k in os.environ
                               if k.startswith("MODEL_") and k.endswith("_ID") and "batch" not in k.lower())
    opts = []
    if kind == "student":
        aliases = [pipeline.AUTO] + aliases
    for a in aliases:
        sel = " selected" if a.lower() == (selected or "").lower() else ""
        label = "auto (by difficulty)" if a == pipeline.AUTO else a
        opts.append(f'<option value="{html.escape(a)}"{sel}>{html.escape(label)}</option>')
    return "".join(opts)


NARRATE_CHOICES = {"none": ((), "none"), "en": (("en",), "English"), "hi": (("hi",), "Hinglish"),
                   "both": (("en", "hi"), "English + Hinglish")}


def tts_options(selected):
    return "".join(f'<option value="{k}"{" selected" if k == selected else ""}>{html.escape(v)}</option>'
                   for k, v in speech.PROVIDERS.items())


def narrate_options(selected):
    return "".join(f'<option value="{k}"{" selected" if k == selected else ""}>{html.escape(v[1])}</option>'
                   for k, v in NARRATE_CHOICES.items())


def model_tags(models):
    """[{role, model, in_tokens, out_tokens, cached_tokens, cost_source}, ...] -> compact
    tags naming which model actually generated the output, with tokens/cache on hover."""
    role_short = {"teacher": "T", "student": "S", "judge": "J", "refine": "R", "tts": "V", "narration": "N"}
    tags = []
    for m in models or []:
        alias, _, mid = str(m.get("model", "")).partition(":")
        role = role_short.get(m.get("role"), (m.get("role") or "?")[:1].upper())
        tip = f"{mid or alias} - {m.get('in_tokens', 0)}->{m.get('out_tokens', 0)} tok"
        if m.get("cached_tokens"):
            tip += f" ({m['cached_tokens']} cached)"
        if m.get("cost_source") == "table":
            tip += " - estimated price"
        tags.append(f'<span class="model-tag" title="{html.escape(tip)}">{role}-{html.escape(alias)}</span>')
    return "".join(tags)


def tier_of(models):
    """easy | medium | hard from the per-call records (set by pipeline.run), else None."""
    return next((m.get("tier") for m in models or [] if m.get("tier")), None)


def tier_badge(tier):
    return (f'<span class="tier tier-{tier}" title="How hard this simulation is to build: estimated from the '
            f'topic and class, raised by the blueprint. Picks the student in auto mode.">{tier} difficulty</span>'
            if tier in pipeline.TIERS else "")


def confidence_badge(pct, verdict=None):
    """Judge's 0-100 confidence-of-correctness score as a small colored badge."""
    if pct is None:
        return '<span class="hint">not judged</span>'
    cls = "checks-ok" if pct >= 70 else "checks-bad"
    tip = f' title="{html.escape(verdict)}"' if verdict else ""
    return f'<span class="{cls}"{tip}>{pct}% confidence</span>'


USD_INR = float(os.getenv("USD_INR", "95.6"))  # OpenRouter bills USD; UI shows INR


def money(usd):
    """USD cost -> INR string. &#8377; (the rupee sign) keeps the source pure ASCII."""
    return f"&#8377;{usd * USD_INR:,.2f}" if usd is not None else "-"


def blueprint_items():
    """Every stored blueprint (all topics, all teachers, all versions), newest first."""
    recs = []
    for p in pipeline.blueprint_files():
        try:
            recs.append(pipeline.read_blueprint(p))
        except (OSError, json.JSONDecodeError):
            continue
    return recs


def render_blueprints(recs):
    if not recs:
        return '<p class="empty">No stored blueprints yet — tick "blueprint only" above, or run make_blueprints.py.</p>'
    rows = []
    for r in recs:
        q = urlencode({"topic": r["topic"], "grade": r.get("grade") or "", "teacher": r.get("teacher") or ""})
        alias, _, mid = str(r.get("model", r.get("teacher", ""))).partition(":")
        rows.append(f"""<tr>
          <td><a href="/?{html.escape(q)}" title="load into the form above">{html.escape(r['topic'])}</a></td>
          <td class="num">{r.get('grade') or '—'}</td>
          <td><span class="model-tag" title="{html.escape(mid or alias)}">T-{html.escape(alias)}</span></td>
          <td class="num">{money(r.get('cost_usd'))}</td>
          <td class="num">{r.get('in_tokens', 0)}&rarr;{r.get('out_tokens', 0)}</td>
          <td><span class="pill">{'NCERT-grounded' if r.get('rag') else 'ungrounded'}</span></td>
          <td class="num">{html.escape(r.get('created', ''))}</td>
          <td><a href="/blueprint/{html.escape(r['file'])}" target="_blank">JSON</a></td>
        </tr>""")
    total = sum(r.get("cost_usd") or 0 for r in recs)
    return f"""<div class="tbl-wrap"><table class="bp">
      <tr><th>Topic</th><th>Grade</th><th>Teacher</th><th>Cost</th><th>Tokens in&rarr;out</th>
          <th>Grounding</th><th>Created</th><th></th></tr>
      {''.join(rows)}</table></div>
      <p class="hint">Total spent on stored blueprints: <span class="stat">{money(total)}</span>.
      Every blueprint is kept (one file per generation). Click a topic to load it with its teacher;
      teacher_student then reuses that teacher's newest blueprint and only pays for the student.</p>"""


def cost_split(rec, fresh, student_cost, judge_cost=0, narr_cost=0):
    """Blueprint vs student-build (vs judge) cost line for a teacher_student result."""
    bp = rec.get("cost_usd") or 0
    how = "generated now" if fresh else f"stored, reused (made {html.escape(rec.get('created', ''))})"
    this_run = student_cost + judge_cost + narr_cost + (bp if fresh else 0)
    judge_part = (f'<span>Judge <b>{money(judge_cost)}</b></span>' if judge_cost else "") + \
                 (f'<span>Narration <b>{money(narr_cost)}</b></span>' if narr_cost else "")
    return (f'<div class="split"><span>Blueprint <b>{money(bp)}</b> · {how}</span>'
            f'<span>Student build <b>{money(student_cost)}</b></span>{judge_part}'
            f'<span>This run <b>{money(this_run)}</b></span></div>')


def log_run(entry):
    RUNS.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_report_meta():
    """slug -> {file stem: entry}, read from runs/<slug>/report__*.json -- written by the CLI
    (`python -m simgen`), which never touches library.jsonl. Without this, a CLI-generated sim
    picked up by the library scan shows no cost or model (library.jsonl is the only place those
    normally come from)."""
    meta = {}
    for p in RUNS.glob("*/report__*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        run_slug = p.parent.name
        for row in data.get("rows", []):
            stem = Path(row.get("file", "")).stem
            if not stem:
                continue
            calls = row.get("calls") or []
            checks = row.get("checks") or {}
            teacher = next((c.get("model") for c in calls if c.get("role") == "teacher"), None)
            student = next((c.get("model") for c in calls if c.get("role") == "student"), None)
            meta.setdefault(run_slug, {})[stem] = {
                "slug": run_slug, "mode": row.get("mode"), "file": stem,
                "topic": data.get("topic"), "grade": data.get("grade"),
                "teacher": teacher.partition(":")[0] if teacher else None,
                "student": student.partition(":")[0] if student else None,
                "cost_usd": row.get("cost_usd"), "seconds": row.get("seconds"),
                "checks_passed": sum(checks.values()), "checks_total": len(checks),
                "models": calls, "confidence_pct": pipeline.confidence_pct(row.get("judge")),
                "judge": row.get("judge"),
            }
    return meta


def load_meta():
    """slug -> {file stem: logged entry}: CLI report files first, then library.jsonl (this
    app's own /run calls) layered on top since it's the more complete, authoritative source
    when both exist. Older library.jsonl entries have no "file" and their html was saved as
    <mode>.html, so they key by mode."""
    meta = load_report_meta()
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta.setdefault(e["slug"], {})[e.get("file", e["mode"])] = e
    return meta


def download_link(run_slug, name, text="download"):
    return (f'<a class="download-link" href="/download/{run_slug}/{name}" '
            f'title="Download this simulation as an HTML file">&#8681; {text}</a>')


def library_items(limit=60):
    """Every generated simulation on disk, newest first — filesystem is the source
    of truth (also picks up CLI runs), library.jsonl only adds cost/model metadata."""
    if not RUNS.exists():
        return []
    files = sorted(RUNS.glob("*/*.html"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    meta = load_meta()
    items = []
    for f in files:
        run_slug, name = f.parent.name, f.stem
        e = meta.get(run_slug, {}).get(name, {})
        items.append({
            "slug": run_slug, "name": name, "mode": name.split("__")[0],
            "topic": e.get("topic") or run_slug.replace("-", " ").title(),
            "teacher": e.get("teacher"), "student": e.get("student"),
            "grade": e.get("grade"), "models": e.get("models"),
            "cost": e.get("cost_usd"), "passed": e.get("checks_passed"),
            "bp_cost": e.get("blueprint_cost_usd"), "student_cost": e.get("student_cost_usd"),
            "total": e.get("checks_total"), "mtime": f.stat().st_mtime,
            "confidence": e.get("confidence_pct"), "verdict": (e.get("judge") or {}).get("verdict"),
            "tier": e.get("tier") or tier_of(e.get("models")),
        })
    return items


def render_library():
    items = library_items()
    if not items:
        return '<p class="empty">No simulations generated yet — run one above.</p>', 0
    cards = []
    for it in items:
        cost = money(it["cost"])
        checks = f"{it['passed']}/{it['total']}" if it["passed"] is not None else "—"
        when = time.strftime("%b %d, %H:%M", time.localtime(it["mtime"]))
        badge = MODE_BADGE.get(it["mode"], "")
        # models[] (per-call actuals) if logged; else fall back to the picked teacher/student.
        tags = model_tags(it["models"]) or model_tags(
            ([{"role": "teacher", "model": it["teacher"]}] if it["teacher"] and it["mode"] != "student_only" else []) +
            ([{"role": "student", "model": it["student"]}] if it["student"] and it["mode"] != "teacher_only" else []))
        grade = f'<span class="lib-when">grade {it["grade"]}</span>' if it["grade"] else ""
        split = (f"blueprint {money(it['bp_cost'])} + student build {money(it['student_cost'])}"
                 if it["bp_cost"] is not None else "cost of this run")
        cards.append(f'''<div class="lib-card">
          <a class="lib-card-link" href="/view/{it['slug']}/{it['name']}" target="_blank">
            <div class="lib-top"><span class="badge {badge}">{MODE_LABEL.get(it['mode'], it['mode'])}</span>
              <span class="lib-when">{when}</span></div>
            <h4>{html.escape(it['topic'])}</h4>
            <div class="lib-models">{tags}{grade}</div>
            <div class="lib-meta"><span class="stat" title="{split}">{cost}</span><span>{checks} checks</span>
              {tier_badge(it["tier"])}{confidence_badge(it["confidence"], it["verdict"])}</div>
          </a>
          {download_link(it['slug'], it['name'])}
        </div>''')
    return f'<div class="lib-grid">{"".join(cards)}</div>', len(items)


def render(topic="", grade="", teacher=None, student=None, modes=("teacher_student",),
          rag=False, results="", reuse=True):
    lib_html, lib_count = render_library()
    bps = blueprint_items()
    out = PAGE
    for key, val in {
        "@@TOPIC@@": html.escape(topic), "@@GRADE@@": html.escape(str(grade)),
        "@@TEACHER_OPTS@@": model_options("teacher", teacher or os.environ["TEACHER"]),
        "@@STUDENT_OPTS@@": model_options("student", student or pipeline.AUTO),
        "@@TTS_OPTS@@": tts_options(request.form.get("tts") or speech.DEFAULT_PROVIDER),
        "@@NARRATE_OPTS@@": narrate_options(request.form.get("narrate") or "en"),
        "@@RAG@@": "checked" if rag else "",
        "@@M_TS@@": "checked" if "teacher_student" in modes else "",
        "@@M_BP@@": "checked" if "blueprint" in modes else "",
        "@@REUSE@@": "checked" if reuse else "",
        "@@BP_OPTIONS@@": "".join(f'<option value="{html.escape(r["topic"])}">' for r in bps),
        "@@BLUEPRINTS@@": render_blueprints(bps), "@@BP_COUNT@@": str(len(bps)),
        "@@RATE@@": f"{USD_INR:.2f}", "@@RESULTS@@": results, "@@LIBRARY@@": lib_html, "@@LIB_COUNT@@": str(lib_count),
        "@@ACCESS_FIELD@@": ACCESS_FIELD.replace(
            "@@ACCESS_VAL@@", html.escape(request.form.get("access_code") or request.cookies.get("access_code", ""))),
    }.items():
        out = out.replace(key, val)
    return out


@app.get("/")
def home():
    return render(request.args.get("topic", ""), request.args.get("grade", ""),
                  request.args.get("teacher") or None)


@app.post("/run")
def run():
    topic = request.form["topic"].strip()
    grade = request.form.get("grade") or ""
    teacher = request.form.get("teacher") or os.environ["TEACHER"]
    student = request.form.get("student") or pipeline.AUTO
    modes = request.form.getlist("modes") or ["teacher_student"]
    provider = request.form.get("tts") or speech.DEFAULT_PROVIDER
    langs = NARRATE_CHOICES.get(request.form.get("narrate") or "en", NARRATE_CHOICES["en"])[0]
    use_rag = bool(request.form.get("rag"))
    reuse = bool(request.form.get("reuse"))
    run_slug = slug(topic)
    g = int(grade) if grade else None

    # Access gate: every model call here costs real money, so a public deploy can require
    # a shared code (ACCESS_CODE) before it'll actually run the pipeline. No code entered
    # or a mismatch just re-renders the form with the submitted values and an error --
    # no generation happens. The code itself is never trusted from a cookie, only from the
    # submitted form field; the cookie set below only pre-fills the input for convenience.
    if ACCESS_CODE and request.form.get("access_code", "").strip() != ACCESS_CODE:
        err = '<div class="panel"><p class="err">Incorrect access code — ask the owner for the current one.</p></div>'
        return render(topic, grade, teacher, student, modes, use_rag, err, reuse)

    cards = []
    for mode in modes:
        if mode == "blueprint":
            try:
                rec, usages = pipeline.blueprint(topic, g, teacher, use_rag, force=not reuse)
            except Exception as e:
                cards.append(f'<div class="card"><div class="card-head"><span class="badge badge-t">blueprint only</span>'
                             f'</div><div class="err">{html.escape(str(e))}</div></div>')
                continue
            cards.append(render_blueprint_card(rec, bool(usages)))
            continue
        try:
            out_html, plan, usages = pipeline.run(
                mode, topic, g, teacher, student, use_rag=use_rag, reuse_blueprint=reuse)
        except Exception as e:
            cards.append(f'<div class="card"><div class="card-head"><span class="badge {MODE_BADGE.get(mode,"")}">'
                         f'{MODE_LABEL.get(mode, mode)}</span></div><div class="err">{html.escape(str(e))}</div></div>')
            continue

        def call_rec(u):
            return {"role": u.role, "model": u.model, "in_tokens": u.in_tokens, "out_tokens": u.out_tokens,
                    "cached_tokens": u.cached_tokens, "cost_source": u.cost_source,
                    "cost_usd": u.cost_usd, "seconds": u.seconds, "tier": u.tier}

        models = [call_rec(u) for u in usages]
        cost = sum(u.cost_usd for u in usages)
        secs = sum(u.seconds for u in usages)
        tier = tier_of(models)

        # Grade every generation with the JUDGE model (an LLM examiner, not a human) and
        # surface its rubric total as a 0-100 confidence-of-correctness label. Best-effort:
        # a judging failure (bad JSON, timeout) never breaks the generation itself.
        # JUDGE_ENABLED=false (the default) skips this entirely -- it's an extra paid call
        # on every generation, and Opus 5 judging got expensive fast. Flip it back on in .env.
        # A confidence below REFINE_BELOW (default 90) with concrete issues from the judge gets ONE
        # revision pass by the builder, re-judged; the better of the two is kept (JUDGE_REFINE=false: off).
        scores, judge_cost, refine_cost, refined = None, 0, 0, None
        if os.environ.get("JUDGE_ENABLED", "false").lower() in ("1", "true", "yes"):
            try:
                judge_alias = os.environ.get("JUDGE", "opus5")
                scores, ju = pipeline.judge(out_html, topic, g, judge_alias)
                models.append(call_rec(ju)); cost += ju.cost_usd; secs += ju.seconds; judge_cost += ju.cost_usd
                before = pipeline.confidence_pct(scores)
                if (os.environ.get("JUDGE_REFINE", "true").lower() in ("1", "true", "yes") and scores.get("issues")
                        and before < int(os.environ.get("REFINE_BELOW", "90")) and pipeline.topic_of(out_html)):
                    try:
                        built_by = next((u.model for u in reversed(usages) if u.role in ("student", "teacher")), "")
                        new_html, ru = pipeline.refine(out_html, topic, g,
                                                       os.environ.get("REFINE_MODEL") or built_by.partition(":")[0],
                                                       scores["issues"])
                        models += [call_rec(u) for u in ru]
                        refine_cost = sum(u.cost_usd for u in ru)
                        cost += refine_cost; secs += sum(u.seconds for u in ru)
                        s2, ju2 = pipeline.judge(new_html, topic, g, judge_alias)
                        models.append(call_rec(ju2)); cost += ju2.cost_usd; secs += ju2.seconds; judge_cost += ju2.cost_usd
                        after = pipeline.confidence_pct(s2)
                        kept = s2["total"] > scores["total"] and all(pipeline.static_checks(new_html).values())
                        refined = {"before": before, "after": after, "kept": kept}
                        if kept:
                            out_html, scores = new_html, s2
                    except Exception:
                        pass
            except Exception:
                pass
        confidence = pipeline.confidence_pct(scores)

        # one file per generation: a second model on the same topic never replaces the first
        name = pipeline.write_new(RUNS / run_slug, pipeline.run_name(mode, teacher, student),
                                  ".html", out_html).stem
        checks = pipeline.static_checks(out_html)
        passed, total = sum(checks.values()), len(checks)

        # Narration is an add-on: a failure (no key, provider down) never loses the simulation.
        narr_note, narr_cost = "", 0
        if langs:
            try:
                _, nus = speech.narrate(out_html, RUNS / run_slug / f"{name}.audio", provider, langs, g)
                models += [call_rec(u) for u in nus]
                narr_cost = sum(u.cost_usd for u in nus)
                cost += narr_cost; secs += sum(u.seconds for u in nus)
                narr_note = (f'<span class="hint">narrated ({" + ".join(speech.LANGS[l] for l in langs)}, '
                             f'{html.escape(speech.PROVIDERS[provider])})</span>')
            except Exception as e:
                narr_note = f'<span class="checks-bad" title="{html.escape(str(e))}">narration failed</span> <span class="hint">{html.escape(str(e)[:160])}</span>'

        split, extra = "", {}
        if mode == "teacher_student" and plan is not None:   # plan None: hard topic built whole by one model
            rec = pipeline.load_blueprint(topic, teacher) or {}  # the one run() just used
            fresh = any(u.role == "teacher" for u in usages)
            student_cost = sum(u.cost_usd for u in usages if u.role == "student") + refine_cost
            if not fresh:  # still name the teacher that wrote the stored blueprint
                models.insert(0, {"role": "teacher", "model": rec.get("model", rec.get("teacher", "?")),
                                  "in_tokens": rec.get("in_tokens", 0), "out_tokens": rec.get("out_tokens", 0),
                                  "cost_usd": rec.get("cost_usd"), "seconds": rec.get("seconds"),
                                  "cost_source": "stored blueprint"})
            split = cost_split(rec, fresh, student_cost, judge_cost, narr_cost)
            extra = {"blueprint_cost_usd": rec.get("cost_usd"), "blueprint_reused": not fresh,
                     "blueprint_file": rec.get("file"),
                     "student_cost_usd": student_cost}
        log_run({"ts": time.time(), "slug": run_slug, "mode": mode, "topic": topic, "tts": provider if langs else None,
                 "narration_cost_usd": narr_cost,
                 "grade": g, "teacher": teacher, "student": student,
                 "cost_usd": cost, "seconds": secs, "checks_passed": passed, "checks_total": total,
                 "models": models, "rag": use_rag, "file": name,
                 "confidence_pct": confidence, "judge": scores, "tier": tier, "refined": refined, **extra})
        badge = MODE_BADGE.get(mode, "")
        checks_cls = "checks-ok" if passed == total else "checks-bad"
        cards.append(f'''<div class="card">
          <div class="card-head">
            <span class="badge {badge}">{MODE_LABEL.get(mode, mode)}</span>
            <div class="lib-models">{model_tags(models)}</div>
            <div class="stats">
              <span class="stat">{money(cost)}</span><span class="stat">{secs:.1f}s</span>
              <span class="{checks_cls}">{passed}/{total} checks</span>
              {tier_badge(tier)}
              {confidence_badge(confidence, (scores or {}).get("verdict"))}
              {narr_note}
              {f'<span class="hint">revised once: {refined["before"]}% &rarr; {refined["after"]}%' + ('' if refined["kept"] else ' (kept the original)') + '</span>' if refined else ''}
            </div>
          </div>
          {split}
          <iframe src="/sim/{run_slug}/{name}"></iframe>
          <div class="split"><a href="/view/{run_slug}/{name}" target="_blank">open with full details</a></div>
        </div>''')

    resp = make_response(render(topic, grade, teacher, student, modes, use_rag, "\n".join(cards), reuse))
    if ACCESS_CODE:  # convenience only -- /run always re-checks the submitted form field, never this cookie
        resp.set_cookie("access_code", ACCESS_CODE, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax")
    return resp


def plan_facts(plan):
    """(equations, step names, question count, one-line shape) for a Lab-app blueprint,
    falling back to the older free-form schema so stored blueprints still display."""
    eqs = (plan.get("science") or plan.get("physics") or {}).get("equations") or []
    steps = [str(s.get("name", "")) for s in plan.get("steps") or [] if isinstance(s, dict)]
    n_q = (sum(1 for s in plan.get("steps") or [] if isinstance(s, dict) and s.get("ask"))
           + len(plan.get("think") or [])) or len(plan.get("questions") or [])
    presets = len((plan.get("presets") or {}).get("options") or [])
    if steps:
        shape = f"Lab app · {len(steps)} steps · {presets} presets · {len(plan.get('controls') or [])} sliders · {n_q} questions"
    else:
        shape = f"older free-form blueprint · {len((plan.get('physics') or {}).get('parameters') or [])} parameters · {n_q} questions"
    return eqs, steps, n_q, shape


def render_blueprint_card(rec, fresh):
    plan = rec.get("plan", {})
    eqs, steps, _, shape = plan_facts(plan)
    app_name = (plan.get("app") or {}).get("name")
    alias = str(rec.get("model", rec.get("teacher", ""))).partition(":")[0]
    return f'''<div class="card">
      <div class="card-head">
        <span class="badge badge-t">blueprint only</span>
        <div class="lib-models"><span class="model-tag">T-{html.escape(alias)}</span>
          <span class="pill">{"generated now" if fresh else "already stored — not regenerated"}</span></div>
        <div class="stats"><span class="stat">{money(rec.get("cost_usd"))}</span>
          <span class="stat">{rec.get("in_tokens", 0)}&rarr;{rec.get("out_tokens", 0)} tok</span>
          <span class="stat">{rec.get("seconds", 0)}s</span></div>
      </div>
      <div class="bp-body"><b>{html.escape(str(app_name or plan.get("title", rec.get("topic", ""))))}</b><br>
        {f'<span class="hint">{html.escape(" → ".join(steps))}</span><br>' if steps else ""}
        {" ".join(f"<code>{html.escape(str(e))}</code>" for e in eqs[:4])}<br>
        <span class="hint">{html.escape(shape)} ·
        {"NCERT-grounded" if rec.get("rag") else "ungrounded"} ·
        <a href="/blueprint/{html.escape(rec.get("file", ""))}" target="_blank">full JSON</a></span></div>
    </div>'''


@app.get("/blueprint/<name>")
def blueprint_json(name):
    f = pipeline.BLUEPRINTS / f"{name}.json"
    if not pipeline.SAFE_NAME.fullmatch(name) or not f.exists():   # no ..\ path tricks
        return "not found", 404
    return Response(f.read_text(encoding="utf-8"), mimetype="application/json")


@app.get("/download/<run_slug>/<name>")
def download(run_slug, name):
    """Send a library simulation to the browser as a file download -- a server-side copy
    (the old "save best" button) is useless on Render, where the disk is ephemeral and the
    visitor can't reach it anyway; the file itself is what's worth keeping."""
    src = RUNS / run_slug / f"{name}.html"
    if not (pipeline.SAFE_NAME.fullmatch(run_slug) and pipeline.SAFE_NAME.fullmatch(name)) or not src.exists():
        return "not found", 404
    resp = Response(speech.with_narration(src.read_text(encoding="utf-8"), narration_for(run_slug, name, inline=True)),
                    mimetype="text/html")
    resp.headers["Content-Disposition"] = f'attachment; filename="{run_slug}__{name}.html"'
    return resp


STYLE = PAGE[PAGE.index("<style>"):PAGE.index("</style>") + len("</style>")]
ROLE = {"teacher": "Teacher", "student": "Student", "judge": "Judge"}


def run_entry(run_slug, name):
    """The library.jsonl entry for one generated file (older entries key by mode)."""
    return load_meta().get(run_slug, {}).get(name, {})   # legacy <mode>.html: stem == mode == key


def narration_panel(run_slug, name):
    """What narration this simulation has, and a form to add or refresh it (also for older simulations)."""
    man = speech.read_manifest(RUNS / run_slug / f"{name}.audio")
    langs = man.get("langs") or {}
    have = "".join(detail_row(html.escape(v["label"]), f'{len(v["clips"])} clips &middot; {html.escape(speech.PROVIDERS.get(v["provider"], v["provider"]))}'
                              f' <span class="hint">voice {html.escape(v["voice"])}</span>') for v in langs.values())
    spent = f'{detail_row("Narration cost", money(man["spent_usd"]) + " <span class=hint>(estimated)</span>")}' if man.get("spent_usd") else ""
    code = ('<input name="access_code" type="password" placeholder="access code" style="width:9rem">' if ACCESS_CODE else "")
    return f"""<div class="panel"><h2>Narration <span class="hint">&mdash; spoken coach lines and questions</span></h2>
      <div class="tbl-wrap"><table class="bp">{have or detail_row("Audio", "none yet")}{spent}</table></div>
      <form method="post" action="/narrate/{run_slug}/{name}" style="display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.7rem;align-items:center">
        <select name="tts">{tts_options(speech.DEFAULT_PROVIDER)}</select>
        <select name="narrate">{narrate_options("en")}</select>{code}
        <button type="submit">{"Refresh" if langs else "Add"} narration</button></form></div>"""


def detail_row(k, v):
    return f"<tr><th>{k}</th><td>{v}</td></tr>"


@app.get("/view/<run_slug>/<name>")
def view(run_slug, name):
    """One generated simulation with everything known about how it was made."""
    f = RUNS / run_slug / f"{name}.html"
    if not (pipeline.SAFE_NAME.fullmatch(run_slug) and pipeline.SAFE_NAME.fullmatch(name)) or not f.exists():
        return "not found", 404
    e = run_entry(run_slug, name) or {}
    mode = name.split("__")[0]
    checks = pipeline.static_checks(f.read_text(encoding="utf-8"))   # recomputed, no model call
    passed = sum(checks.values())
    made = time.strftime("%d %b %Y, %H:%M:%S", time.localtime(e.get("ts") or f.stat().st_mtime))
    models = e.get("models") or []

    calls = "".join(
        f"""<tr><td>{ROLE.get(m.get('role'), html.escape(str(m.get('role'))))}</td>
          <td><span class="model-tag">{html.escape(str(m.get('model', '')).partition(':')[0])}</span>
              <span class="hint">{html.escape(str(m.get('model', '')).partition(':')[2])}</span></td>
          <td class="num">{m.get('in_tokens', 0)}&rarr;{m.get('out_tokens', 0)}</td>
          <td class="num">{f"{m['seconds']:.1f}s" if m.get('seconds') is not None else '-'}</td>
          <td class="num">{money(m.get('cost_usd'))}</td>
          <td>{'paid earlier (stored blueprint)' if m.get('cost_source') == 'stored blueprint'
               else 'billed by OpenRouter' if m.get('cost_source') == 'reported'
               else 'estimated from price table' if m.get('cost_source') == 'table' else '-'}</td></tr>"""
        for m in models) or '<tr><td colspan="6" class="hint">Per-call details were not logged for this older run.</td></tr>'

    bp_html = ""
    if mode == "teacher_student":
        bp_file = e.get("blueprint_file")
        bpf = pipeline.BLUEPRINTS / f"{bp_file}.json" if bp_file else None
        if bpf and bpf.exists():
            rec = pipeline.read_blueprint(bpf)
            plan = rec.get("plan", {})
            eqs, steps, n_q, shape = plan_facts(plan)
            bp_html = f"""<div class="panel"><h2>Blueprint used</h2><div class="tbl-wrap"><table class="bp">
              {detail_row("Title", html.escape(str((plan.get("app") or {}).get("name") or plan.get("title", ""))))}
              {detail_row("Shape", html.escape(shape))}
              {detail_row("Steps", html.escape(" → ".join(steps)) or "-")}
              {detail_row("Teacher", f'<span class="model-tag">T-{html.escape(str(rec.get("teacher")))}</span> <span class="hint">{html.escape(str(rec.get("model", "")).partition(":")[2])}</span>')}
              {detail_row("Created", html.escape(rec.get("created", "")))}
              {detail_row("Blueprint cost", f'{money(rec.get("cost_usd"))} <span class="hint">({rec.get("in_tokens", 0)}&rarr;{rec.get("out_tokens", 0)} tokens, {rec.get("seconds", 0)}s)</span>')}
              {detail_row("This run", "reused a stored blueprint (not paid again)" if e.get("blueprint_reused") else "generated in this run")}
              {detail_row("Grounding", "NCERT-grounded" if rec.get("rag") else "ungrounded")}
              {detail_row("Equations", " ".join(f"<code>{html.escape(str(x))}</code>" for x in eqs) or "-")}
              {detail_row("Questions", n_q)}
              {detail_row("File", f'<a href="/blueprint/{html.escape(rec["file"])}" target="_blank">{html.escape(rec["file"])}.json</a>')}
            </table></div></div>"""
        else:
            bp_html = ('<div class="panel"><h2>Blueprint used</h2><p class="empty">Not recorded for this run '
                       '(generated before blueprint tracking was added).</p></div>')

    judge_scores = e.get("judge")
    judge_cost = next((m.get("cost_usd") for m in models if m.get("role") == "judge"), None)

    split = ""
    if e.get("blueprint_cost_usd") is not None:
        judge_part = f'<span>Judge <b>{money(judge_cost)}</b></span>' if judge_cost else ""
        split = (f'<div class="split"><span>Blueprint <b>{money(e["blueprint_cost_usd"])}</b> · '
                 f'{"stored, reused" if e.get("blueprint_reused") else "generated in this run"}</span>'
                 f'<span>Student build <b>{money(e.get("student_cost_usd"))}</b></span>{judge_part}'
                 f'<span>This run <b>{money(e.get("cost_usd"))}</b></span></div>')

    judge_html = ""
    if judge_scores:
        axes = ("scientific_accuracy", "ncert_alignment", "interactivity",
                "grade_appropriateness", "pedagogy")
        judge_html = f"""<div class="panel"><h2>Judge assessment
          <span class="hint">— an LLM examiner's read, not a human's</span></h2><div class="tbl-wrap"><table class="bp">
          {detail_row("Confidence of correctness", confidence_badge(pipeline.confidence_pct(judge_scores)))}
          {"".join(detail_row(a.replace("_", " ").capitalize(), f"{judge_scores.get(a, '-')}/5") for a in axes)}
          {detail_row("Verdict", html.escape(str(judge_scores.get("verdict", "-"))))}
          {detail_row("Fixes the judge asked for", "<br>".join(f"<b>{html.escape(str(i.get('axis', '')).replace('_', ' '))}</b>: {html.escape(str(i.get('fix', '')))}" for i in judge_scores.get("issues") or []) or "none")}
          {detail_row("Revision pass", (f"{e['refined']['before']}% &rarr; {e['refined']['after']}% " + ("(kept)" if e['refined'].get('kept') else "(original kept)")) if e.get("refined") else "not run")}
        </table></div></div>"""
    elif mode != "blueprint":
        judge_html = '<div class="panel"><h2>Judge assessment</h2><p class="empty">Not judged for this run.</p></div>'

    failed = [k for k, v in checks.items() if not v]
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(e.get("topic") or run_slug)}</title>
{STYLE}</head><body><div class="wrap">
<header><p><a href="/" style="color:var(--accent)">&larr; back to simgen</a></p>
  <h1>{html.escape(e.get("topic") or run_slug.replace("-", " ").title())}</h1>
  <p>{f"Grade {e['grade']} · " if e.get("grade") else ""}{made}</p></header>
<div class="card">
  <div class="card-head">
    <span class="badge {MODE_BADGE.get(mode, "")}">{MODE_LABEL.get(mode, mode)}</span>
    <div class="lib-models">{model_tags(models)}</div>
    <div class="stats"><span class="stat">{money(e.get("cost_usd"))}</span>
      <span class="stat">{f"{e['seconds']:.1f}s" if e.get("seconds") is not None else "-"}</span>
      <span class="{"checks-ok" if not failed else "checks-bad"}">{passed}/{len(checks)} checks</span>
      {tier_badge(e.get("tier") or tier_of(models))}
      {confidence_badge(pipeline.confidence_pct(judge_scores), (judge_scores or {}).get("verdict"))}</div>
  </div>
  {split}
  <iframe src="/sim/{run_slug}/{name}" style="height:78vh"></iframe>
  <div class="split"><a href="/sim/{run_slug}/{name}" target="_blank">open simulation alone</a>
    {download_link(run_slug, name, "download HTML")}</div>
</div>
<div class="panel"><h2>Model calls</h2><div class="tbl-wrap"><table class="bp">
  <tr><th>Role</th><th>Model</th><th>Tokens in&rarr;out</th><th>Time</th><th>Cost</th><th>Billing</th></tr>
  {calls}</table></div></div>
{narration_panel(run_slug, name)}
{judge_html}
{bp_html}
<div class="panel"><h2>Run details</h2><div class="tbl-wrap"><table class="bp">
  {detail_row("Generated", made)}
  {detail_row("Mode", MODE_LABEL.get(mode, mode))}
  {detail_row("Teacher picked", html.escape(str(e.get("teacher") or "-")))}
  {detail_row("Student picked", html.escape(str(e.get("student") or "-")))}
  {detail_row("NCERT grounding (RAG)", "on" if e.get("rag") else "off" if e else "-")}
  {detail_row("Total cost", f'{money(e.get("cost_usd"))} <span class="hint">(1 USD = &#8377;{USD_INR:.2f})</span>')}
  {detail_row("Static checks", f'{passed}/{len(checks)}' + (f' <span class="hint">failed: {", ".join(failed)}</span>' if failed else ""))}
  {detail_row("File", f"runs/{run_slug}/{html.escape(name)}.html")}
</table></div></div>
</div></body></html>"""
    return page


@app.get("/costs")
def costs_page():
    """Every teacher_student build by topic and blueprint, with working links to each simulation."""
    import costs
    head, body = costs.page(RUNS, pipeline.BLUEPRINTS, USD_INR)
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{head}</head><body>{body}</body></html>')


def narration_for(run_slug, name, inline=False):
    """window.NARRATION for a simulation (None if it has no audio): clip URLs, or data: URIs for a download."""
    d = RUNS / run_slug / f"{name}.audio"
    obj = speech.narration_object(speech.read_manifest(d),
                                  lambda lang, k: f"audio/{name}/{lang}/{k}.mp3")   # relative to /sim/<slug>/<name>
    return speech.inline_audio(obj, d) if inline and obj else obj


@app.get("/sim/<run_slug>/<name>")
def sim(run_slug, name):
    f = RUNS / run_slug / f"{name}.html"
    if not (pipeline.SAFE_NAME.fullmatch(run_slug) and pipeline.SAFE_NAME.fullmatch(name)) or not f.exists():
        return "not found", 404
    return Response(speech.with_narration(f.read_text(encoding="utf-8"), narration_for(run_slug, name)),
                    mimetype="text/html")


@app.get("/sim/<run_slug>/audio/<name>/<lang>/<clip>")
def sim_audio(run_slug, name, lang, clip):
    f = RUNS / run_slug / f"{name}.audio" / lang / clip
    if not (pipeline.SAFE_NAME.fullmatch(run_slug) and pipeline.SAFE_NAME.fullmatch(name) and lang in speech.LANGS
            and speech.CLIP_FILE.fullmatch(clip)) or not f.exists():
        return "not found", 404
    return send_file(f.resolve(), mimetype="audio/mpeg", max_age=3600)


@app.post("/narrate/<run_slug>/<name>")
def narrate_existing(run_slug, name):
    """Add (or refresh) narration for a simulation that's already in the library."""
    f = RUNS / run_slug / f"{name}.html"
    if not (pipeline.SAFE_NAME.fullmatch(run_slug) and pipeline.SAFE_NAME.fullmatch(name)) or not f.exists():
        return "not found", 404
    if ACCESS_CODE and request.form.get("access_code", "").strip() != ACCESS_CODE:
        return "access code required", 403
    provider = request.form.get("tts") or speech.DEFAULT_PROVIDER
    langs = NARRATE_CHOICES.get(request.form.get("narrate") or "en", NARRATE_CHOICES["en"])[0]
    try:
        _, nus = speech.narrate(f.read_text(encoding="utf-8"), RUNS / run_slug / f"{name}.audio", provider, langs,
                                (run_entry(run_slug, name) or {}).get("grade"))
    except Exception as e:
        return Response(f"Narration failed: {html.escape(str(e))}", status=502, mimetype="text/plain")
    return redirect(f"/view/{run_slug}/{name}")


if __name__ == "__main__":
    # Render (and other PaaS) set PORT and expect a 0.0.0.0 bind; local dev keeps
    # the old 127.0.0.1:5050 default. In production, gunicorn imports `app`
    # directly (see Procfile) and this block never runs.
    port = int(os.environ.get("PORT", 5050))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    print(f"simgen live UI: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=("PORT" not in os.environ))
