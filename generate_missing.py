"""Generate heart diagram simulations for Claude Fable 5.1 and Claude Opus 5,
then evaluate all three models (including GPT-6 Astra) with static checks and judge.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from simgen.__main__ import load_env
from simgen import pipeline
from simgen.llm import call

load_env()

TOPIC = "Class 10 Science: Structure and Function of Human Heart"
GRADE = 10

OUT_DIR = Path("runs") / "heart-diagram-models"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Load existing results if any
results_file = OUT_DIR / "results.json"
results = json.loads(results_file.read_text(encoding="utf-8")) if results_file.exists() else {}

TARGETS = [
    {"key": "claude_fable_5_1", "alias": "fable51", "name": "Claude Fable 5.1"},
    {"key": "claude_opus_5", "alias": "opus5", "name": "Claude Opus 5"},
]

for m in TARGETS:
    key = m["key"]
    alias = m["alias"]
    name = m["name"]
    print(f"\n==========================================", flush=True)
    print(f"Generating for {name} ({alias})...", flush=True)
    print(f"==========================================", flush=True)

    t0 = time.time()
    try:
        html_raw, u = call(alias, pipeline.BUILD_SYS,
                           pipeline._build_prompt(TOPIC, GRADE, context=""),
                           role="teacher", max_tokens=16000, reasoning_tokens=1200)
        html = pipeline.extract_html(html_raw)
        
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
        results[key] = res
    except Exception as e:
        print(f"[{name}] ERROR: {e}", flush=True)
        results[key] = {
            "name": name,
            "alias": alias,
            "key": key,
            "status": "failed",
            "error": str(e)
        }
    
    results_file.write_text(json.dumps(results, indent=2), encoding="utf-8")

print("\nAll generations finished. Running automated evaluations...", flush=True)
for key, res in results.items():
    html_path = OUT_DIR / f"{key}.html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        res["size_bytes"] = len(html)
        res["lines"] = len(html.splitlines())
        res["checks"] = pipeline.static_checks(html)

results_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
print("Saved updated results.json", flush=True)
