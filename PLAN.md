# Implementation plan — NCERT simulation pipeline

## 1. What it does

`topic (+ grade) → interactive HTML simulation + cost record`

One frontier model (Astra 6 / Fable 5.1 / Opus 5) acts as **teacher**: it does not
write the simulation, it writes a compact JSON **blueprint** — equations, parameter
ranges with units, the numeric update rule, what to visualise, the misconceptions to
attack, three assessment questions. A small model (Gemini 3.8 Flash or similar) acts
as **student**: it implements that blueprint as one self-contained HTML file.

The split is the whole point. Curriculum judgement and physics correctness are where
frontier models are worth $50/1M tokens; emitting 6,000 tokens of canvas boilerplate
is not. The blueprint is ~900 tokens, the simulation is ~6,000 — so the expensive
model produces 13% of the tokens and all of the judgement.

## 2. Architecture

```
topic, grade
     │
     ├─► Postgres full-text search ──► NCERT chunks (grounding context)
     │
     ├─► TEACHER  (frontier)  ──► blueprint JSON  ──► [cacheable, per topic]
     │
     └─► STUDENT  (small)     ──► simulation.html
                                      │
                                      ├─► static_checks()   no model call, hard gate
                                      └─► judge (optional)  rubric /25, one extra call
                                             │
                                          report.json / report.md  (tokens, USD, latency)
```

Files:

| file | role |
|---|---|
| `simgen/llm.py` | model registry from env, single OpenRouter call path, `Usage` + cost |
| `simgen/retrieve.py` | full-text NCERT query (no embeddings); SQL lives in `.env` so it is schema-agnostic |
| `simgen/pipeline.py` | prompts, the three routes, HTML/JSON extraction, static checks, judge |
| `simgen/__main__.py` | CLI, run loop, cost report |
| `test_simgen.py` | offline assertions (cost maths, extraction, routing, report) |
| `dryrun.py` | full end-to-end with faked model calls — no keys, no network |

Everything model-specific is config. Adding Fable 5.1 as teacher is five env vars.

## 3. Cost model

Everything runs through **OpenRouter**: one base URL, one key, all four models.
OpenRouter returns the actually-charged `cost` on every call, so the pipeline records
that and treats the `.env` price table as a fallback — `report.json` marks each call
`cost_source: "reported"` or `"table"`.

Slugs and prices below were read from `openrouter.ai/api/v1/models`:

| alias | slug | in / out $ per 1M |
|---|---|---|
| `astra6` | `openai/gpt-6-astra` | 10 / 50 |
| `fable51` | `anthropic/claude-fable-5.1` | 10 / 50 |
| `opus5` | `anthropic/claude-opus-5` | 5 / 25 |
| `gemini38flash` | `google/gemini-3.8-flash` | 0.75 / 3.75 |

Per query, assuming ~3,000 tokens of NCERT context, ~500 tokens of prompt, a
900-token blueprint and a 6,000-token simulation:

| route | teacher in/out | student in/out | USD / query |
|---|---|---|---|
| `teacher_only` (Astra 6) | 3.5k / 6k | — | **$0.335** |
| `teacher_student` (Astra 6 → Flash) | 3.5k / 0.9k | 1.4k / 6k | **$0.104** |
| `teacher_student` (Opus 5 → Flash) | 3.5k / 0.9k | 1.4k / 6k | **$0.064** |
| `student_only` (Flash) | — | 3.5k / 6k | **$0.025** |

Three levers turn that into something much smaller, and they are the reason to build
it this way:

**Blueprint caching.** NCERT is a *closed* curriculum — roughly 1,500 chapter-level
topics across Classes 6–12. A blueprint is deterministic given (topic, grade), so the
teacher runs **once per topic, ever**, not once per query. Store it next to the NCERT
chunks in the same Postgres. After the backfill, every query is a student call only:
**$0.025**, which is 13× cheaper than `teacher_only` while keeping the frontier
model's plan.

**Batch tier.** The backfill is offline and latency-insensitive, which is exactly what
OpenRouter's `:batch` variants are for — same models, ~50% off. Cost to blueprint the
entire NCERT curriculum, once:

| teacher for the backfill | 1,500 blueprints |
|---|---|
| `openai/gpt-6-astra` | $120 |
| `openai/gpt-6-astra:batch` | $60 |
| `anthropic/claude-opus-5` | $60 |
| `anthropic/claude-opus-5:batch` | **$30** |

**Prompt caching.** The NCERT context for a topic is byte-identical across runs, and
OpenRouter reports `cached_tokens` back — the pipeline records it, so phase 4 can
verify caching is actually landing instead of assuming it.

The report prints the measured version of this table for a real topic, plus a
projection at `--project N` queries. Measure before believing the estimates above.

## 4. NCERT grounding

Retrieval is Postgres full-text search — `to_tsvector`/`plainto_tsquery`/`ts_rank`
against the topic string. No embedding model, no vector column, no extra API call:
your NCERT table already has the text, so search it directly.

```
NCERT_DSN=postgresql://...
NCERT_TOP_K=6
NCERT_SQL=<your query; params %(q)s %(grade)s %(k)s>
```

Default assumes `ncert_chunks(content, grade)`, ranked by `ts_rank`, with a
`GIN (to_tsvector('english', content))` index for speed on a large table. Filter by
grade in SQL, not in the prompt — it is free there and it stops Class 12 material
leaking into a Class 9 simulation.

Grounding goes **into the teacher call only**. The blueprint carries the grounded
facts forward, so the student never re-pays for 3,000 tokens of textbook. That is the
bulk of the saving in the table above, and it is the one design decision worth
defending.

## 5. Evaluation

Two layers, deliberately:

**Static checks** (`static_checks()`, no model call, runs on every output) — is it a
real HTML document, does it have a canvas/SVG, does it have controls, does it have a
script, does it make **zero network requests**, does it have questions, is the size
plausible. This is a hard gate: a sim that pulls a CDN script fails in a classroom
with no internet, so `offline` failing means the output is rejected, not scored.

**Rubric judge** (`--judge`) — the frontier model grades the student's source on
scientific accuracy, NCERT alignment, interactivity, grade-appropriateness and
pedagogy, 0–5 each. Costs one extra call. Use it to rank routes, not to certify
correctness.

**Neither substitutes for opening the three HTML files side by side.** For the
prototype, hand-review 10 topics spanning Classes 6–12 and Physics/Chemistry/Biology.
Freeze those 10 as a regression set before touching the prompts.

## 6. Phases

| phase | work | done when |
|---|---|---|
| 0 — wiring | set `LLM_API_KEY` to your OpenRouter key; `python test_simgen.py`; `python dryrun.py` | dry run writes a report with no network |
| 1 — one topic | run all three modes on Simple Pendulum with RAG off; open all three HTMLs | you can see the quality gap yourself |
| 2 — grounding | point `NCERT_DSN` at your DB, tune `NCERT_SQL` and `NCERT_TOP_K`; rerun | retrieved chunks are actually the right chapter |
| 3 — the 10-topic set | run the regression set, `--judge` on, record `report.json` for each | a cost/quality table you trust |
| 4 — blueprint cache | add a `sim_blueprints(topic, grade, plan jsonb, created_at)` table; check it before calling the teacher; backfill via a `:batch` alias | second run of a topic costs student-only |
| 5 — serve | wrap `pipeline.run` in a FastAPI endpoint, serve cached HTML from disk/S3, log every `Usage` row | p95 latency and cost per query on a dashboard |

Phases 0–3 are the prototype and are what the current code supports. 4 and 5 are two
small additions each, and neither changes the pipeline's shape.

## 7. Risks

- **Prices drift.** The `.env` table is a fallback; OpenRouter's reported cost is the
  truth. If `report.json` shows `cost_source: "table"` on a route, the gateway did not
  return a cost and that row is an estimate, not a bill.
- **OpenRouter routes to whichever provider is up**, so latency and occasionally the
  exact model build vary between runs. Pin with `:nitro`/provider preferences if
  phase 3 numbers look unstable.
- **The student may quietly ignore the blueprint.** Watch for it: if the student's
  equations drift from `plan.physics.equations`, the whole economic argument
  collapses. Cheapest detector is a string check of each equation against the HTML;
  add it if phase 3 shows drift.
- **Retrieval quality dominates factual quality.** A bad chunk ranking hurts more
  than a weaker student model. Check the retrieved chunks by eye in phase 2.
- **Token estimates are guesses until measured.** Sim size varies 3× between a
  pendulum and a titration.

## 8. Deliberately not built

Fine-tuning and distillation (the report's phase 3) — blueprint caching gets most of
the cost win with none of the dataset curation, so only reach for fine-tuning if the
cached-blueprint student measurably underperforms in phase 3. Also skipped: a model
router, a queue, a training-data exporter, retries beyond the SDK's own, and any
abstraction over the single provider interface. Add each when a measurement asks for it.
