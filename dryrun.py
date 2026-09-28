"""End-to-end wiring check with no API keys and no network.

    python dryrun.py "Class 9 Science: Simple Pendulum" --grade 9

Fakes the model calls with canned output but exercises the real routing,
extraction, static checks, cost maths and report writing.
"""
import os
import sys
import json

os.environ.update({
    "LLM_BASE_URL": "https://openrouter.ai/api/v1", "LLM_API_KEY": "sk-or-fake",
    "MODEL_fakeT_ID": "anthropic/claude-opus-5", "MODEL_fakeT_IN": "5", "MODEL_fakeT_OUT": "25",
    "MODEL_fakeS_ID": "google/gemini-3.8-flash", "MODEL_fakeS_IN": "0.75", "MODEL_fakeS_OUT": "3.75",
})

from simgen import llm, pipeline            # noqa: E402
from simgen.__main__ import main            # noqa: E402

PLAN = {"title": "Simple Pendulum", "grade": 9, "subject": "Physics",
        "ncert_refs": ["Class 9 Science, Ch. 8 Motion"],
        "physics": {"equations": ["T = 2*pi*sqrt(L/g)"],
                    "parameters": [{"name": "length", "symbol": "L", "unit": "m",
                                    "min": 0.2, "max": 2.0, "default": 1.0}],
                    "state_update": "theta'' = -(g/L) sin(theta), semi-implicit Euler, dt=0.016"}}


def fake(alias, system, user, max_tokens=0):
    body = json.dumps(PLAN) if "JSON shape" in user else "```js\n" + pipeline.EXAMPLE_TOPIC + "\n```"
    if "examiner" in system:
        body = json.dumps({"scientific_accuracy": 5, "ncert_alignment": 4, "interactivity": 4,
                           "grade_appropriateness": 5, "pedagogy": 4, "verdict": "solid"})
    return body, len(user) // 4, len(body) // 4


llm.TRANSPORT = fake
main(sys.argv[1:] + ["--teacher", "fakeT", "--student", "fakeS", "--judge",
                     "--judge-model", "fakeT", "--no-rag", "--out", "runs-dry"])
