"""One-off backfill: judge every already-generated simulation in the library that
hasn't been judged yet, and log the result so the library cards, /view and /costs
all pick it up. Makes real OpenRouter calls (costs money, ~1 call per unjudged file).

Judges several files concurrently (network-bound, GIL doesn't matter) and stops
submitting new work past a time budget so it fits inside one shell call; already-
judged files are always skipped, so just re-run this until it reports 0 left.

Run: python judge_library.py [--force] [--seconds N] [--workers N]
"""
import sys
import time
import concurrent.futures as cf
from pathlib import Path

import app as appmod
from simgen import pipeline

FORCE = "--force" in sys.argv


def arg(name, default, cast=float):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


BUDGET = arg("--seconds", 90.0)
WORKERS = arg("--workers", 5, int)


def judge_one(f, entry, judge_alias):
    run_slug, name = f.parent.name, f.stem
    html_text = f.read_text(encoding="utf-8")
    topic = entry.get("topic") or run_slug.replace("-", " ").title()
    grade = entry.get("grade")
    scores, u = pipeline.judge(html_text, topic, grade, judge_alias)  # may raise
    checks = pipeline.static_checks(html_text)
    # Replace any previous judge entry (a --force re-judge, e.g. switching JUDGE models)
    # instead of stacking a second one on top of it -- both in the model-call list and in
    # the totals it fed. Subtracts every prior judge-role cost/time, however many stacked
    # up, so this also self-heals data that an earlier buggy pass already double-counted.
    old_judges = [m for m in (entry.get("models") or []) if m.get("role") == "judge"]
    models = [m for m in (entry.get("models") or []) if m.get("role") != "judge"]
    models.append({"role": "judge", "model": u.model, "in_tokens": u.in_tokens,
                   "out_tokens": u.out_tokens, "cached_tokens": u.cached_tokens,
                   "cost_source": u.cost_source, "cost_usd": u.cost_usd, "seconds": u.seconds})
    base_cost = (entry.get("cost_usd") or 0) - sum(m.get("cost_usd") or 0 for m in old_judges)
    base_secs = (entry.get("seconds") or 0) - sum(m.get("seconds") or 0 for m in old_judges)
    merged = {
        "ts": entry.get("ts", f.stat().st_mtime), "slug": run_slug,
        "mode": entry.get("mode", name.split("__")[0]), "topic": topic, "grade": grade,
        "teacher": entry.get("teacher"), "student": entry.get("student"),
        "cost_usd": base_cost + u.cost_usd,
        "seconds": base_secs + u.seconds,
        "checks_passed": entry.get("checks_passed", sum(checks.values())),
        "checks_total": entry.get("checks_total", len(checks)),
        "models": models, "rag": entry.get("rag", False), "file": entry.get("file") or name,
        "confidence_pct": pipeline.confidence_pct(scores), "judge": scores,
    }
    for k in ("blueprint_cost_usd", "blueprint_reused", "blueprint_file", "student_cost_usd"):
        if k in entry:
            merged[k] = entry[k]
    return run_slug, name, merged, u.cost_usd


def main():
    judge_alias = appmod.os.environ.get("JUDGE", "opus5")
    files = sorted(appmod.RUNS.glob("*/*.html"))
    meta = appmod.load_meta()
    todo = []
    skipped = 0
    for f in files:
        entry = meta.get(f.parent.name, {}).get(f.stem, {})
        # --force means "redo anything not already scored by *this* JUDGE model", not
        # "redo everything every time" -- otherwise a time-budgeted --force run can never
        # progress past file 1, since every resumed call sees the same untouched backlog.
        judge_models = [m.get("model") for m in (entry.get("models") or []) if m.get("role") == "judge"]
        already_current = judge_models and judge_models[-1].startswith(f"{judge_alias}:")
        if not entry.get("judge") or (FORCE and not already_current):
            todo.append((f, entry))
        else:
            skipped += 1

    t0 = time.time()
    judged = failed = 0
    total_cost = 0.0
    it = iter(todo)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {}
        for f, entry in [next(it, None) for _ in range(WORKERS)]:
            if f is None:
                break
            futures[ex.submit(judge_one, f, entry, judge_alias)] = (f, entry)
        while futures:
            done, _ = cf.wait(futures, return_when=cf.FIRST_COMPLETED)
            for fut in done:
                f, entry = futures.pop(fut)
                try:
                    run_slug, name, merged, cost = fut.result()
                except Exception as e:
                    print(f"[FAIL] {f.parent.name}/{f.stem}: {e}", flush=True)
                    failed += 1
                else:
                    appmod.log_run(merged)
                    total_cost += cost
                    judged += 1
                    print(f"[{judged}] {run_slug}/{name}: {merged['confidence_pct']}% conf, "
                         f"${cost:.4f}", flush=True)
                if time.time() - t0 < BUDGET:
                    nxt = next(it, None)
                    if nxt:
                        futures[ex.submit(judge_one, *nxt, judge_alias)] = nxt

    remaining = sum(1 for _ in it) + len(futures)
    print(f"\njudged {judged} this run, skipped {skipped} (already judged), "
         f"failed {failed}, remaining {remaining}")
    print(f"spend this run: ${total_cost:.4f} (Rs {total_cost * appmod.USD_INR:.2f})")


if __name__ == "__main__":
    main()
