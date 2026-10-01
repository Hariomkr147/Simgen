"""CLI: one topic -> up to three simulations + a cost/quality comparison report.

    python -m simgen "Class 9 Science: Simple Pendulum" --grade 9 --judge
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import llm, pipeline


def load_env(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


slug = pipeline.slug


def report_md(topic, grade, rows, per_queries):
    out = [f"# {topic}", f"\nGrade: {grade or '-'} · generated {time.strftime('%Y-%m-%d %H:%M')}\n",
           "| mode | in tok | out tok | cost USD | sec | checks | judge /25 |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        checks = f"{sum(r['checks'].values())}/{len(r['checks'])}" if r.get("checks") else "-"
        j = r["judge"]["total"] if r.get("judge") else "-"
        out.append(f"| {r['mode']} | {r['in_tokens']} | {r['out_tokens']} | "
                   f"{r['cost_usd']:.4f} | {r['seconds']:.1f} | {checks} | {j} |")
    out.append(f"\n## Projected cost at {per_queries:,} queries\n")
    out.append("| mode | USD |\n|---|---:|")
    for r in rows:
        out.append(f"| {r['mode']} | {r['cost_usd'] * per_queries:,.2f} |")
    cheap = min(rows, key=lambda r: r["cost_usd"])
    dear = max(rows, key=lambda r: r["cost_usd"])
    if cheap["cost_usd"] > 0:
        out.append(f"\n`{dear['mode']}` costs **{dear['cost_usd'] / cheap['cost_usd']:.1f}x** "
                   f"`{cheap['mode']}` per query.")
    for r in rows:
        if r.get("checks"):
            failed = [k for k, v in r["checks"].items() if not v]
            if failed:
                out.append(f"\n- `{r['mode']}` failed static checks: {', '.join(failed)}")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="simgen")
    ap.add_argument("topic", help='e.g. "Class 9 Science: Simple Pendulum"')
    ap.add_argument("--grade", type=int)
    ap.add_argument("--teacher", default=os.getenv("TEACHER", "opus5"))
    ap.add_argument("--student", default=os.getenv("STUDENT", "auto"),
                    help="model alias, or 'auto' to pick by difficulty (STUDENT_EASY/MEDIUM/HARD)")
    ap.add_argument("--judge", action="store_true", help="grade each output with JUDGE model (costs extra)")
    ap.add_argument("--judge-model", default=os.getenv("JUDGE", "opus5"))
    ap.add_argument("--no-rag", action="store_true", help="skip NCERT retrieval")
    ap.add_argument("--narrate", default="", help="spoken audio: en, hi (Hinglish) or en,hi")
    ap.add_argument("--tts", default="gemini", choices=["gemini", "sarvam"], help="voice for --narrate")
    ap.add_argument("--fresh-blueprint", action="store_true",
                    help="regenerate the teacher blueprint even if one is stored in blueprints/")
    ap.add_argument("--out", default="runs")
    ap.add_argument("--project", type=int, default=10000, help="queries to project cost over")
    a = ap.parse_args(argv)

    load_env()
    modes = pipeline.MODES
    d = Path(a.out) / slug(a.topic)
    d.mkdir(parents=True, exist_ok=True)
    rows = []

    for mode in modes:
        print(f"[{mode}] running...", file=sys.stderr)
        try:
            html, plan, usages = pipeline.run(mode, a.topic, a.grade, a.teacher, a.student,
                                              use_rag=not a.no_rag,
                                              reuse_blueprint=not a.fresh_blueprint)
        except Exception as e:
            print(f"[{mode}] FAILED: {e}", file=sys.stderr)
            continue
        # unique name per run, never overwritten; the plan lives in blueprints/ already
        f = pipeline.write_new(d, pipeline.run_name(mode, a.teacher, a.student), ".html", html)
        row = {
            "mode": mode, "file": f.name,
            "in_tokens": sum(u.in_tokens for u in usages),
            "out_tokens": sum(u.out_tokens for u in usages),
            "cost_usd": sum(u.cost_usd for u in usages),
            "seconds": sum(u.seconds for u in usages),
            "calls": [u.dict() for u in usages],
            "checks": pipeline.static_checks(html),
            "bytes": len(html),
        }
        if a.narrate:
            try:
                from . import tts
                _, nus = tts.narrate(html, f.with_suffix(".audio"), a.tts, tuple(a.narrate.split(",")), a.grade)
                row["narration_cost_usd"] = sum(u.cost_usd for u in nus)
                row["cost_usd"] += row["narration_cost_usd"]
            except Exception as e:
                print(f"[{mode}] narration failed: {e}", file=sys.stderr)
        if a.judge:
            try:
                row["judge"], ju = pipeline.judge(html, a.topic, a.grade, a.judge_model)
                row["judge_cost_usd"] = ju.cost_usd
            except Exception as e:
                print(f"[{mode}] judge failed: {e}", file=sys.stderr)
        rows.append(row)
        print(f"[{mode}] ${row['cost_usd']:.4f}  {row['seconds']:.1f}s  {row['bytes']}B", file=sys.stderr)

    if not rows:
        sys.exit("all modes failed")
    ts = pipeline.stamp()
    pipeline.write_new(d, f"report__{ts}", ".json",
                       json.dumps({"topic": a.topic, "grade": a.grade, "rows": rows}, indent=2))
    md = report_md(a.topic, a.grade, rows, a.project)
    pipeline.write_new(d, f"report__{ts}", ".md", md)
    print(md)
    print(f"-> {d}/", file=sys.stderr)


if __name__ == "__main__":
    main()
