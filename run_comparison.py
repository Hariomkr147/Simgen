"""Run heart diagram simulation across GPT-6 Astra, Claude Fable 5.1, and Claude Opus 5.
Saves outputs to runs/heart-diagram-models/ and generates comparison metrics.
"""
import json
import os
import sys
import time
from pathlib import Path

# Add current dir to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from simgen.__main__ import load_env
from simgen import pipeline
from simgen.llm import call

load_env()

TOPIC = "Class 10 Science: Structure and Function of Human Heart"
GRADE = 10

MODELS = [
    {"key": "gpt_6_astra", "alias": "astra6", "name": "GPT-6 Astra"},
    {"key": "claude_fable_5_1", "alias": "fable51", "name": "Claude Fable 5.1"},
    {"key": "claude_opus_5", "alias": "opus5", "name": "Claude Opus 5"},
]

OUT_DIR = Path("runs") / "heart-diagram-models"
OUT_DIR.mkdir(parents=True, exist_ok=True)

results = {}

for m in MODELS:
    key = m["key"]
    alias = m["alias"]
    name = m["name"]
    print(f"\n==========================================", flush=True)
    print(f"Generating for {name} ({alias})...", flush=True)
    print(f"==========================================", flush=True)

    t0 = time.time()
    try:
        # Generate simulation using standalone build prompt (single frontier model)
        html_raw, u = call(alias, pipeline.BUILD_SYS,
                           pipeline._build_prompt(TOPIC, GRADE, context=""),
                           role="teacher", max_tokens=16000)
        html = pipeline.extract_html(html_raw)
        
        # Save HTML
        html_file = OUT_DIR / f"{key}.html"
        html_file.write_text(html, encoding="utf-8")
        
        checks = pipeline.static_checks(html)
        elapsed = time.time() - t0
        
        res = {
            "name": name,
            "alias": alias,
            "key": key,
            "html_path": str(html_file),
            "size_bytes": len(html),
            "lines": len(html.splitlines()),
            "usage": u.dict(),
            "checks": checks,
            "elapsed_seconds": round(elapsed, 2),
            "status": "success"
        }
        print(f"[{name}] Done! Size: {len(html):,} bytes | Cost: ${u.cost_usd:.4f} | Time: {u.seconds:.1f}s", flush=True)
        print(f"[{name}] Static checks passed: {sum(checks.values())}/{len(checks)}", flush=True)
    except Exception as e:
        print(f"[{name}] ERROR: {e}", flush=True)
        res = {
            "name": name,
            "alias": alias,
            "key": key,
            "status": "failed",
            "error": str(e)
        }
    
    results[key] = res

# Save preliminary results
(OUT_DIR / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print("\nGeneration complete. Now running automated judge...", flush=True)

# Run judge if possible
judge_alias = os.getenv("JUDGE", "opus5")
for key, res in results.items():
    if res.get("status") == "success":
        try:
            print(f"Judging {res['name']}...", flush=True)
            html = (OUT_DIR / f"{key}.html").read_text(encoding="utf-8")
            scores, ju = pipeline.judge(html, TOPIC, GRADE, judge_alias)
            res["judge"] = scores
            res["judge_usage"] = ju.dict()
            print(f"[{res['name']}] Judge score: {scores.get('total')}/25 - {scores.get('verdict')}", flush=True)
        except Exception as e:
            print(f"[{res['name']}] Judge error: {e}", flush=True)

(OUT_DIR / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
print("\nAll done! Results saved to", OUT_DIR / "results.json", flush=True)
