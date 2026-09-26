"""Cost table: every teacher_student build grouped by topic and blueprint, from the app's own
logs (runs/library.jsonl + blueprints/). No model calls. Served live by app.py at /costs, and
the same page can be published as a snapshot (where the links are absolute + copyable)."""
import html
import json
import time
from collections import OrderedDict
from pathlib import Path

NAMES = {"opus55": "Claude Opus 5.5", "opus5": "Claude Opus 5", "fable51": "Claude Fable 5.1",
         "astra6": "GPT-6 Astra", "sol6": "GPT-6 Sol", "glm53": "GLM 5.3",
         "gemini38flash": "Gemini 3.8 Flash", "gemini31flashlite": "Gemini 3.1 Flash-Lite",
         "gemini35flashlite": "Gemini 3.5 Flash-Lite", "glm53flash": "GLM 5.3 Flash",
         "qwen38flash": "Qwen 3.8 Flash", "deepseekv4flash": "DeepSeek V4 Flash",
         "deepseekv41flash": "DeepSeek V4.1 Flash", "luna6pro": "GPT-6 Luna Pro",
         "grok47": "Grok 4.7", "qwen38max": "Qwen 3.8 Max", "mimo26pro": "MiMo V2.6 Pro", "musespark13c": "Muse Spark 1.3", "gemini37flash": "Gemini 3.7 Flash", "hy4preview": "Hy4 Preview (Tencent)"}

STYLE = """<style>
:root{--bg:#f6f7f6;--surface:#ffffff;--ink:#1b2320;--muted:#5f6b66;--rule:#dde3e0;
  --band:#e9f1ed;--accent:#1d6b58;--ok:#23734a;--bad:#b0392c;--low:#23734a;--high:#9a5a12;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121715;--surface:#18201d;
  --ink:#e5ece8;--muted:#96a49d;--rule:#2a3531;--band:#1d2a25;--accent:#62c8a9;--ok:#5fcf93;
  --bad:#f0806f;--low:#5fcf93;--high:#e3a55a;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121715;--surface:#18201d;--ink:#e5ece8;--muted:#96a49d;--rule:#2a3531;
  --band:#1d2a25;--accent:#62c8a9;--ok:#5fcf93;--bad:#f0806f;--low:#5fcf93;--high:#e3a55a;color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif;
  padding-inline:16px;padding-block:32px 48px}
.wrap{max-width:960px;margin:0 auto;display:grid;gap:28px}
h1{font-size:26px;font-weight:600;margin:0;text-wrap:balance}
.intro{color:var(--muted);margin:6px 0 0;max-width:72ch}
.intro strong{color:var(--ink)}
h2{font-size:17px;font-weight:600;margin:0;text-wrap:balance}
.sub{color:var(--muted);font-size:13px;margin:2px 0 10px}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--rule);border-radius:8px}
table{width:100%;border-collapse:collapse;min-width:640px}
th{text-align:left;font-size:11.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
  color:var(--muted);padding:10px 12px;border-bottom:1px solid var(--rule)}
td{padding:9px 12px;border-top:1px solid var(--rule)}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums;
  text-align:right;white-space:nowrap}
tr.bp td{background:var(--band);font-size:14px}
tr.bp .num{text-align:left;margin-inline:6px;font-weight:500}
.bp-label{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  color:var(--accent);margin-right:8px}
.muted{color:var(--muted);font-size:13px}
.ok{color:var(--ok)} .bad{color:var(--bad);font-weight:600}
.tag{font-size:11px;font-weight:600;border:1px solid currentColor;border-radius:99px;
  padding:0 7px;margin-left:6px;white-space:nowrap}
.tag.low{color:var(--low)} .tag.high{color:var(--high)}
a{color:var(--accent);font-weight:600}
a:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.open{display:flex;gap:8px;align-items:center;white-space:nowrap}
button.copy{font:600 12px "IBM Plex Sans",system-ui,sans-serif;color:var(--accent);background:transparent;
  border:1px solid var(--rule);border-radius:6px;padding:3px 9px;cursor:pointer}
button.copy:hover{border-color:var(--accent)}
code.url{font:12px "IBM Plex Mono",ui-monospace,monospace;background:var(--band);padding:2px 6px;
  border-radius:4px;user-select:all;word-break:break-all}
.note{color:var(--muted);font-size:13px;margin:0}
</style>"""

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:'
         'wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">')

COPY_JS = """<script>
document.addEventListener('click', function (ev) {
  var b = ev.target.closest('button.copy'); if (!b) return;
  var url = b.getAttribute('data-url'), label = b.textContent;
  function done(t) { b.textContent = t; setTimeout(function () { b.textContent = label; }, 1400); }
  try {
    navigator.clipboard.writeText(url).then(function () { done('Copied'); }, function () { fallback(); });
  } catch (e) { fallback(); }
  function fallback() {
    var i = document.createElement('input'); i.value = url; document.body.appendChild(i); i.select();
    try { document.execCommand('copy'); done('Copied'); } catch (e) { done('Select & copy'); }
    i.remove();
  }
});
</script>"""


def _inr(usd, rate):
    return f"&#8377;{usd * rate:,.2f}"


def _alias(m):
    return str(m).split(":")[0].lower()


def _pretty(topic, slug):
    t = (topic or slug)
    if " " not in t:
        t = t.replace("-", " ")
    t = t.rstrip(".")
    return t[:1].upper() + t[1:]


def page(runs_dir, bp_dir, rate, link_prefix="", exists=None, snapshot=False):
    """Returns (head_html, body_html). link_prefix "" gives same-origin links (app route);
    "http://127.0.0.1:5050" plus snapshot=True gives absolute links with copy buttons."""
    runs_dir, bp_dir = Path(runs_dir), Path(bp_dir)
    exists = exists or (lambda slug, stem: (runs_dir / slug / f"{stem}.html").exists())
    log = runs_dir / "library.jsonl"
    all_rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.exists() else []
    # library.jsonl is append-only: judging (or any other backfill) re-logs a fuller record
    # for a file that was already logged, rather than patching the JSON in place. Keep only
    # the LATEST row per (slug, file) -- same rule app.py's load_meta() uses -- or a re-judged
    # build shows up twice. Legacy fixed-name runs have no "file"; their key falls back to
    # "mode", which is also the actual filename for those, so dedup handles them the same way.
    latest = {}
    for e in all_rows:
        latest[(e.get("slug"), e.get("file") or e.get("mode"))] = e
    rows = [e for e in latest.values() if e.get("mode") == "teacher_student"]

    topics, skipped = OrderedDict(), 0
    for e in rows:
        if e.get("blueprint_cost_usd") is None or e.get("student_cost_usd") is None:
            skipped += 1       # early runs: blueprint vs student cost wasn't logged separately yet
            continue
        ms = e.get("models") or []
        t = next((m for m in ms if m.get("role") == "teacher"), {})
        s = next((m for m in reversed(ms) if m.get("role") == "student"), {})
        teacher = _alias(t.get("model", e.get("teacher")))
        student = _alias(s.get("model", e.get("student")))
        stem = e.get("file") or e.get("mode")   # legacy rows: file is unset, mode is the real stem
        link = f"{link_prefix}/view/{e['slug']}/{stem}" if exists(e["slug"], stem) else None
        created = None
        if e.get("blueprint_file"):
            p = bp_dir / f"{e['blueprint_file']}.json"
            if p.exists():
                created = json.loads(p.read_text(encoding="utf-8")).get("created")
        tp = topics.setdefault(e["slug"], {"name": _pretty(e.get("topic"), e["slug"]),
                                           "grade": e.get("grade"), "bps": OrderedDict(), "last": 0})
        tp["grade"] = tp["grade"] or e.get("grade")
        tp["last"] = max(tp["last"], e.get("ts", 0))
        bp = tp["bps"].setdefault((teacher, round(e["blueprint_cost_usd"], 6)),
                                  {"teacher": teacher, "cost": e["blueprint_cost_usd"], "created": created, "runs": []})
        bp["created"] = bp["created"] or created
        bp["runs"].append({"student": student, "cost": e["student_cost_usd"], "secs": e.get("seconds") or 0,
                           "checks": (e.get("checks_passed"), e.get("checks_total")),
                           "confidence": e.get("confidence_pct"), "link": link})

    order = sorted(topics.values(), key=lambda t: (-sum(len(b["runs"]) for b in t["bps"].values()), -t["last"]))
    sections = []
    for t in order:
        body = []
        for bp in t["bps"].values():
            runs = sorted(bp["runs"], key=lambda r: r["cost"])
            lo, hi = runs[0]["cost"], runs[-1]["cost"]
            made = f" &middot; made {html.escape(bp['created'])}" if bp["created"] else ""
            body.append(f"""<tr class="bp"><td colspan="6"><span class="bp-label">Blueprint</span>
              <strong>{html.escape(NAMES.get(bp['teacher'], bp['teacher']))}</strong>
              <span class="num">{_inr(bp['cost'], rate)}</span>
              <span class="muted">paid once{made}</span></td></tr>""")
            for r in runs:
                p, tot = r["checks"]
                tag = (' <span class="tag low">cheapest</span>' if len(runs) > 1 and r["cost"] == lo else
                       ' <span class="tag high">priciest</span>' if len(runs) > 1 and r["cost"] == hi else "")
                if r["link"]:
                    u = html.escape(r["link"])
                    copy = f'<button class="copy" type="button" data-url="{u}">Copy link</button>' if snapshot else ""
                    open_ = f'<span class="open"><a href="{u}" target="_blank" rel="noopener">Open</a>{copy}</span>'
                else:
                    open_ = '<span class="muted" title="Saved over by a later run before every generation was kept">replaced</span>'
                conf = r["confidence"]
                conf_cell = (f'<span class="{"ok" if conf >= 70 else "bad"}">{conf}%</span>'
                            if conf is not None else '<span class="muted">not judged</span>')
                body.append(f"""<tr><td>{html.escape(NAMES.get(r['student'], r['student']))}{tag}</td>
                  <td class="num">{_inr(r['cost'], rate)}</td>
                  <td class="num">{_inr(bp['cost'] + r['cost'], rate)}</td>
                  <td class="num">{r['secs']:.0f}s</td>
                  <td class="num {'ok' if p == tot else 'bad'}">{p}/{tot}</td>
                  <td class="num">{conf_cell}</td>
                  <td>{open_}</td></tr>""")
        n = sum(len(b["runs"]) for b in t["bps"].values())
        grade = f"Class {t['grade']} &middot; " if t["grade"] else ""
        sections.append(f"""<section><h2>{html.escape(t['name'])}</h2>
          <p class="sub">{grade}{n} student build{'s' if n != 1 else ''}</p>
          <div class="scroll"><table>
            <thead><tr><th>Student model</th><th class="num">Student cost</th>
              <th class="num">Blueprint + student</th><th class="num">Time</th>
              <th class="num">Checks</th><th class="num">Confidence</th><th>Simulation</th></tr></thead>
            <tbody>{''.join(body)}</tbody></table></div></section>""")

    if snapshot:
        how = (f"<strong>To open a simulation in Chrome:</strong> start <code>python app.py</code>, then go to "
               f'<code class="url">{html.escape(link_prefix)}/costs</code> for this same table with working links, '
               "or press <strong>Copy link</strong> on a row and paste it into Chrome. "
               "Chrome blocks links from a web page to your own computer, so Open may do nothing here.")
    else:
        how = '<a href="/">&larr; back to simgen</a>'
    head = f"<title>Simgen Cost Table</title>{FONTS}{STYLE}"
    body = f"""<div class="wrap"><header><h1>Simgen Cost Table</h1>
      <p class="intro">What each teacher_student simulation cost, and how confident an LLM judge is in its
      correctness. Each topic's blueprint is paid once, then every student build reuses it. Prices in rupees
      at 1 USD = &#8377;{rate:.2f}. {how}</p></header>
      {''.join(sections) or '<p class="note">No teacher_student builds logged yet.</p>'}
      <p class="note">{'Snapshot taken ' + time.strftime('%d %b %Y') + '. ' if snapshot else ''}Not shown:
      {skipped} earlier runs from before blueprint and student costs were logged separately, plus
      single-model runs. &ldquo;Replaced&rdquo; means a later run was saved over that file before every
      generation was kept.</p></div>{COPY_JS if snapshot else ''}"""
    return head, body


if __name__ == "__main__":      # self-check on a tiny fake log
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "runs" / "t").mkdir(parents=True)
    (d / "blueprints").mkdir()
    (d / "runs" / "t" / "teacher_student__a-b__1.html").write_text("x")
    row = {"mode": "teacher_student", "slug": "t", "topic": "cone", "file": "teacher_student__a-b__1",
          "ts": 1, "blueprint_cost_usd": 0.05, "student_cost_usd": 0.01, "checks_passed": 7, "checks_total": 7,
          "confidence_pct": 84,
          "models": [{"role": "teacher", "model": "opus55:x"}, {"role": "student", "model": "glm53flash:y"}]}
    # write it twice, as a re-judge would (library.jsonl is append-only) -- must not double-count
    (d / "runs" / "library.jsonl").write_text(json.dumps(row) + "\n" + json.dumps({**row, "confidence_pct": 91}) + "\n")
    h, b = page(d / "runs", d / "blueprints", 100)
    assert 'href="/view/t/teacher_student__a-b__1"' in b and "&#8377;5.00" in b and "&#8377;6.00" in b, b
    assert '<span class="ok">91%</span>' in b and "84%" not in b, b   # latest row wins, not both
    assert b.count("teacher_student__a-b__1") == 1, "re-judged build must not appear twice"
    h, b = page(d / "runs", d / "blueprints", 100, "http://127.0.0.1:5050", snapshot=True)
    assert 'data-url="http://127.0.0.1:5050/view/t/teacher_student__a-b__1"' in b
    print("costs ok")
