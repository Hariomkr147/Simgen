"""Model registry, one call path, cost accounting.

Everything goes through one OpenAI-compatible endpoint. With OpenRouter that means
one base URL and one key for every model, and OpenRouter reports the real charged
cost per call — so the price table in .env is only a fallback for other providers.
"""
import os
import time
from dataclasses import dataclass, asdict, field

FIELDS = ("ID", "IN", "OUT")          # per-alias, required
SHARED = ("BASE_URL", "API_KEY")      # per-alias, else LLM_* fallback


@dataclass
class Usage:
    role: str          # teacher | student | judge
    model: str
    in_tokens: int
    out_tokens: int
    cost_usd: float
    seconds: float
    cached_tokens: int = 0
    cost_source: str = "table"   # "reported" when the provider billed us a number
    finish: str = ""             # provider finish_reason; "length" = reply was cut off
    tier: str = ""               # easy | medium | hard: difficulty of building this sim (set by pipeline.run)

    dict = asdict


def model_spec(alias):
    """MODEL_<alias>_{ID,IN,OUT} required. BASE_URL/API_KEY fall back to LLM_BASE_URL /
    LLM_API_KEY so a single-gateway setup (OpenRouter) needs them only once.
    IN/OUT are USD per 1M tokens."""
    p = f"MODEL_{alias}_"
    missing = [p + f for f in FIELDS if not os.getenv(p + f)]
    missing += [f"{p}{f} or LLM_{f}" for f in SHARED
                if not os.getenv(p + f) and not os.getenv(f"LLM_{f}")]
    if missing:
        raise SystemExit(f"missing env: {', '.join(missing)} — see .env.example")
    return {
        "base_url": os.getenv(p + "BASE_URL") or os.environ["LLM_BASE_URL"],
        "api_key": os.getenv(p + "API_KEY") or os.environ["LLM_API_KEY"],
        "id": os.environ[p + "ID"],
        "in": float(os.environ[p + "IN"]),
        "out": float(os.environ[p + "OUT"]),
    }


def cost_usd(spec, in_tokens, out_tokens):
    return in_tokens / 1e6 * spec["in"] + out_tokens / 1e6 * spec["out"]


# ponytail: swappable for tests / dry runs. fn(alias, system, user, max_tokens=int)
# -> (text, in_tokens, out_tokens) or (text, in_tokens, out_tokens, reported_cost).
TRANSPORT = None


def _usage_fields(u):
    """OpenRouter adds `cost` and token details beyond the OpenAI schema; the SDK
    keeps them as model extras. Returns (reported_cost_or_None, cached_tokens)."""
    d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    cost = d.get("cost")
    cached = (d.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return (float(cost) if cost is not None else None), int(cached)


def call(alias, system, user, role="", max_tokens=32000, temperature=0.3, reasoning_tokens=1500, timeout=None):
    """Returns (text, Usage). `timeout` (seconds) fails the call fast and without a retry; the default
    10 min + 1 retry is for builds, which really are slow."""
    spec = model_spec(alias)
    t0 = time.time()
    reported, cached, finish = None, 0, ""

    if TRANSPORT:
        out = TRANSPORT(alias, system, user, max_tokens=max_tokens)
        text, nin, nout = out[:3]
        reported = out[3] if len(out) > 3 else None
    else:
        from openai import OpenAI
        client = OpenAI(base_url=spec["base_url"], api_key=spec["api_key"],
                        timeout=timeout or 600, max_retries=0 if timeout else 1)
        extra = {"reasoning": {"max_tokens": reasoning_tokens}} if reasoning_tokens is not None else None
        r = client.chat.completions.create(
            model=spec["id"],
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        text = r.choices[0].message.content or ""
        finish = r.choices[0].finish_reason or ""
        nin, nout = r.usage.prompt_tokens, r.usage.completion_tokens
        reported, cached = _usage_fields(r.usage)

    # Prefer what we were actually charged; the .env price table is the fallback.
    cost = reported if reported is not None else cost_usd(spec, nin, nout)
    return text, Usage(role or alias, f"{alias}:{spec['id']}", nin, nout,
                       round(cost, 6), round(time.time() - t0, 2), cached,
                       "reported" if reported is not None else "table", finish)
