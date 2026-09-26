# simgen — teacher→student simulation pipeline (prototype)

Input: a class topic. Output: a self-contained interactive HTML simulation, plus a
cost/quality report comparing three routes to it.

| route | what happens | what it tells you |
|---|---|---|
| `teacher_only` | frontier model writes the whole sim | quality ceiling + cost ceiling |
| `student_only` | small model writes the whole sim | cost floor + quality floor |
| `teacher_student` | frontier model writes a JSON blueprint, small model implements it | the actual proposal |

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
has controls, has script, **makes zero network requests**, has questions, plausible size.
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
Start command: gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120
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
- The `--timeout 120` on the start command matters: a real model call can take
  30s–2min, and gunicorn's default 30s worker timeout would kill the request
  mid-generation.
