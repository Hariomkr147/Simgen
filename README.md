# simgen — teacher→student simulation pipeline (prototype)

Input: a class topic. Output: a self-contained interactive HTML simulation, plus a
cost/quality report comparing three routes to it.

| route | what happens | what it tells you |
|---|---|---|
| `teacher_only` | frontier model writes the whole sim | quality ceiling + cost ceiling |
| `student_only` | small model writes the whole sim | cost floor + quality floor |
| `teacher_student` | frontier model writes a JSON blueprint, small model implements it | the actual proposal |

## Simulation format: the Lab app

Every simulation comes out as the same "Lab app" (modelled on a hand-built Ester Lab):
a **Play** tab with a Guide/Challenge coach, an animated canvas under a 4-value HUD,
preset chips (e.g. reactant pairs, planets), optional sliders, and 3–6 ordered step
buttons with a question after each step in Guide mode; a **Think** tab with 3 questions;
a **?** key-words sheet; phone and dark layouts.

That shell is fixed code in `simgen/shell.html`. Models never write it — they write
only the **TOPIC script** (the science, steps, questions and canvas drawing), which
`pipeline.assemble()` splices into the shell. So every output looks the same, and the
builder outputs a few thousand tokens instead of a whole page. The API the topic script
fills is `TOPIC_CONTRACT` in `simgen/pipeline.py`; the builder prompt includes two
complete examples: the shell's own Pendulum Lab (an experiment) and
`simgen/example_ester.js` (a mechanism). Open `simgen/shell.html` in a browser to see
the format working. A topic script that fails to load or throws shows its error in the
coach bar rather than a blank page.

The teacher's blueprint (`PLAN_SCHEMA`) is shaped for this: app name/colour, science
(equations, constants, update rule), presets with their data, sliders, the 4 HUD values,
the stage layout, and each step's action, animation, banner and question. Blueprints
carry `"schema": 2`; older free-form ones stay listed but are never reused.

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env         # fill in real model IDs, keys, prices, NCERT_DSN
python test_simgen.py        # offline self-check, no keys needed
python dryrun.py "Class 9 Science: Simple Pendulum" --grade 9   # wiring check, no network

python -m simgen "Class 9 Science: Simple Pendulum" --grade 9 --judge
```

Writes `runs/<topic-slug>/{teacher_only,student_only,teacher_student}.html`,
`teacher_student.plan.json`, `report.json`, `report.md`. Open the HTML files in a
browser side by side — that is the real quality comparison.

Useful flags: `--modes teacher_student` · `--teacher astra6` · `--student gemini38flash`
· `--no-rag` · `--project 50000` (queries to project cost over).

## Live test UI

```bash
python app.py     # http://127.0.0.1:5050
```

Pick a topic, grade and mode(s), hit Generate. Each mode's simulation renders live in
an iframe so you can actually play with it, with its measured cost/time/checks above
it. A request blocks for as long as the real model call takes (30s–2min) — that's the
browser's own spinner, no JS needed. Runs are written to `runs/` same as the CLI.

## GeoGebra routes

Two extra buttons in the live UI; the student writes GeoGebra commands (JSON, ~1-2k tokens)
instead of a whole HTML/JS sim, and GeoGebra does the maths and drawing. Best for maths,
ray optics, kinematics — keep the canvas routes for biology/chemistry.

| button | mode | calls |
|---|---|---|
| GeoGebra + blueprint | `geogebra` | stored teacher blueprint (reused) + 1 student call |
| GeoGebra direct | `geogebra_direct` | 1 student call, NCERT context in the prompt, writes its own questions |

A spec that fails `geogebra.checks()` gets one repair call. Commands GeoGebra rejects at runtime
are shown in red on the page. GeoGebra itself is served offline from `static/geogebra/`
(the [Math Apps Bundle](https://download.geogebra.org/package/geogebra-math-apps-bundle));
`GGB_CODEBASE` overrides it. CLI: `--modes geogebra` / `geogebra_direct`, but open the result
through `app.py` (`/sim/...`) — a file:// page can't reach `/static/geogebra/`.
GeoGebra is free for non-commercial use only: https://www.geogebra.org/license

## Configuration

Everything runs through OpenRouter — one base URL, one key, all four models:

```
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-...

MODEL_<alias>_ID    OpenRouter slug, e.g. anthropic/claude-opus-5
MODEL_<alias>_IN    USD per 1M input tokens   (fallback only)
MODEL_<alias>_OUT   USD per 1M output tokens  (fallback only)
```

Adding a model is three lines. A model hosted elsewhere can still override
`MODEL_<alias>_BASE_URL` / `_API_KEY` for itself.

OpenRouter returns the **actually charged cost** on every call, so the pipeline uses
that and falls back to the price table only if it's absent — `report.json` records
`cost_source: "reported"` or `"table"` per call, along with `cached_tokens`. Refresh
slugs and prices with:

```bash
curl -s https://openrouter.ai/api/v1/models | grep -o '"id":"[^"]*"'
```

## NCERT grounding

Set `NCERT_DSN` to your Postgres DSN. Retrieval is plain full-text search
(`to_tsvector`/`plainto_tsquery`/`ts_rank`) — no embedding model, no vector column.
The query itself lives in `NCERT_SQL` so nothing here assumes your schema; the default
expects `ncert_chunks(content text, grade int)`. Named params: `%(q)s` (raw topic
text), `%(grade)s`, `%(k)s`. Leave `NCERT_DSN` empty to run ungrounded.

## Checks

`static_checks()` gates every output with no model call: is it HTML, has a canvas/SVG,
has controls, has script, **makes zero network requests**, has questions, plausible size,
`js_parses` (the topic script parses, checked with QuickJS) and `lab_shell`: it's a Lab app whose
topic script declares `APP`, `STEPS` and `draw` and actually replaced the shell's example.
A script that doesn't parse (a typo, or a reply cut off at the model's output limit) gets one
repair call before the run fails; a blueprint that isn't about the requested topic is retried once.
`--judge` adds a rubric-scored LLM grade (/25) at the cost of one extra call per output.
Static checks are the gate; the judge is a signal, not a verdict.

## Deploying to Render

The live UI (`app.py`) is a plain Flask app, served in production by `gunicorn`
(already in `requirements.txt`). Three ways in, pick one:

**Blueprint (easiest):** push this repo to GitHub, then on Render click
*New → Blueprint* and point it at the repo — it reads `render.yaml` and creates
the web service for you. Fill in the vars marked `sync: false`
(`LLM_API_KEY`, and `NCERT_DSN` if you're using NCERT grounding) in the
dashboard's Environment tab afterwards.

**Manual web service:** *New → Web Service* → connect the repo →

```
Build command: pip install -r requirements.txt
Start command: gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300
```

then add every var from `.env.example` under Environment (real values, not the
placeholders).

**Procfile:** if you don't use `render.yaml`, Render also picks up the
`Procfile` in this repo for the start command.

Notes:
- `NCERT_DSN` is optional — leave it unset to run ungrounded (no Postgres
  needed). If you do set it, use Render's own Postgres or any reachable DSN.
- Render's free-tier disk is ephemeral: files written to `runs/` and
  `Best Sim/` while the app is live are lost on redeploy/restart. Fine for a
  prototype; add a persistent disk (Render paid plans) if runs need to survive.
- The `--timeout 300` on the start command matters: `teacher_student` mode
  can chain a blueprint call + a student build + an optional judge call in
  *one* request, and picking multiple modes at once chains even more — each
  call alone can take 30s–2min. Too low a timeout kills the worker mid-call
  (`WORKER TIMEOUT` in the logs, seen as an Internal Server Error) even
  though the generation itself would've finished fine. If you still hit
  this on real traffic, raise it further or turn `JUDGE_ENABLED` off.
  **If you already created the service manually, updating this repo alone
  won't change it** — the Start Command you typed into Render's dashboard
  is what actually runs; update it there too (Settings → Start Command).
- This repo ships the app only, not a pre-built library: `runs/`, `blueprints/`
  and `Best Sim/` are all excluded (see `.gitignore`). A fresh deploy starts
  with empty "Blueprints" and "Library" sections -- visitors generate and view
  their own simulations rather than browsing pre-loaded ones.
- Set `ACCESS_CODE` (dashboard Environment tab, not in the repo) to require a
  shared code before `/run` will trigger a real (paid) generation. Leave it
  unset to run open, e.g. for local/private use. Viewing the page and any
  already-generated sims is never gated -- only the "Generate" action is.
