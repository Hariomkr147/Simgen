"""Narration: spoken audio for a simulation's coach lines, questions and explanations.

A simulation's TOPIC script is read (QuickJS, nothing is run) for its texts; each becomes a short MP3 stored
beside the simulation (<name>.audio/<lang>/<id>.mp3). The Lab shell plays them when the matching coach line,
question or explanation appears. Two languages: "en" (the text as written) and "hi" (Hinglish: a model rewrites
each line the way a teacher would say it, Hindi words in Devanagari and English science terms in Latin script).
Three voices: Gemini 3.8 Flash TTS and Kokoro 82M through OpenRouter (same key as every other model), and Sarvam Bulbul v3.
"""
import base64
import concurrent.futures as cf
import hashlib
import io
import html as htmllib
import json
import os
import re
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from . import llm
from .pipeline import assemble, extract_json, topic_of

PROVIDERS = {"gemini": "Gemini 3.8 Flash TTS", "sarvam": "Sarvam Bulbul v3", "kokoro": "Kokoro 82M", "indic": "Hindi-English TTS (shared server)"}
DEFAULT_PROVIDER = "gemini"
LANGS = {"en": "English", "hi": "Hinglish"}
CLIP_FILE = re.compile(r"[a-z0-9_.-]+\.mp3")
USD_INR = float(os.getenv("USD_INR", "95.6"))


# ---------------------------------------------------------------- what to say

def script_data(topic_js):
    """STEPS / THINK / FINISH of a TOPIC script, read by QuickJS (the script's own functions never run)."""
    import quickjs
    ctx = quickjs.Context()
    ctx.eval(topic_js)
    return json.loads(ctx.eval("JSON.stringify({steps: STEPS, think: typeof THINK==='undefined'?[]:THINK, "
                               "finish: typeof FINISH==='undefined'?{}:FINISH})"))


_SPOKEN = (("→", " to "), ("->", " to "), ("°C", " degrees Celsius"), ("°", " degrees"), ("×", " times "),
           ("÷", " divided by "), ("²", " squared"), ("³", " cubed"), ("≈", " approximately "), ("≥", " at least "),
           ("≤", " at most "), ("Δ", "change in "), ("Ω", " ohm"), ("µ", "micro"), ("μ", "micro"))


def spoken(text):
    """HTML snippet from the simulation -> plain text a voice can read."""
    t = re.sub(r"</p>|<br\s*/?>", " ", str(text or ""))   # paragraph ends are pauses, not glued words
    t = re.sub(r"<[^>]+>", "", t)
    t = htmllib.unescape(t)
    for a, b in _SPOKEN:
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _with_options(q):
    opts = " ".join(f"{'ABCD'[j]}. {spoken(o)}." for j, o in enumerate((q.get("o") or [])[:4]))
    return f"{spoken(q.get('q'))} Options. {opts}" if opts else spoken(q.get("q"))


def clip_texts(topic_js):
    """{clip id: English text to speak}, in playing order. Ids: s<i>.do / s<i>.q / s<i>.e (step i's coach
    line, question with options, explanation), t<j>.q / t<j>.e (Think questions), finish."""
    d = script_data(topic_js)
    out = {}
    for i, s in enumerate(d["steps"]):
        out[f"s{i}.do"] = f"{spoken(s.get('do'))} Tap {spoken(s.get('name'))}."
        if s.get("ask"):
            out[f"s{i}.q"] = _with_options(s["ask"])
            out[f"s{i}.e"] = spoken(s["ask"].get("e"))
    for j, q in enumerate(d["think"] or []):
        out[f"t{j}.q"] = _with_options(q)
        out[f"t{j}.e"] = spoken(q.get("e"))
    f = d["finish"] or {}
    out["finish"] = f"{spoken(f.get('title'))}. {spoken(f.get('html'))}".strip(". ")
    return {k: v for k, v in out.items() if v.strip(" .")}


HINGLISH_SYS = ("You write spoken narration for Indian school students (NCERT). Output JSON only, no code fences.")


def hinglish(texts, grade=None, alias=None):
    """{id: English} -> ({id: Hinglish}, Usage). One model call; every id must come back."""
    alias = alias or os.getenv("NARRATION_MODEL") or "gemini38flash"
    prompt = (f"Rewrite each line below as a class {grade or 9} teacher would SAY it aloud in natural Hinglish: "
              "everyday Hindi words in Devanagari script, English science terms, units, chemical formulas and the "
              "option letters A B C D in Latin script (e.g. 'बर्फ को heat देने पर वो water में बदल जाती है'). "
              "Keep the meaning, every number and every option exactly; add no new facts; short spoken sentences; "
              "write numbers over 4 digits with commas. Keep the words 'Options.' as 'Options.' and 'Tap' as 'Tap'. "
              'Return one JSON object {"<id>": "<Hinglish text>"} with exactly these ids.\n\n'
              f"{json.dumps(texts, ensure_ascii=False, indent=1)}")
    raw, u = llm.call(alias, HINGLISH_SYS, prompt, role="narration", max_tokens=16000, temperature=0.3)
    out = extract_json(raw)
    missing = [k for k in texts if not str(out.get(k, "")).strip()]
    if missing:
        raise ValueError(f"Hinglish rewrite by {alias} skipped {len(missing)} lines (e.g. {missing[0]})")
    return {k: str(out[k]).strip() for k in texts}, u


# ---------------------------------------------------------------- voices

def _post(url, headers, body, timeout=120):
    """One HTTP POST (JSON in) -> (bytes, content type). The single network point, so tests can swap it.
    One retry on 429/5xx."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers}, method="POST")
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if attempt == 2 or e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"TTS request failed: HTTP {e.code} {e.read()[:300].decode('utf-8', 'replace')}") from None
            time.sleep(1.5)


def _chunks(text, limit):
    """Split at sentence ends so each piece fits a provider's per-request limit."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for s in re.split(r"(?<=[.!?।])\s+", text):
        if cur and len(cur) + len(s) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur} {s}".strip()
    return parts + [cur] if cur else parts


def _gemini(text, lang, voice=None):
    """Gemini 3.8 Flash TTS via OpenRouter's OpenAI-compatible /audio/speech: raw MP3 bytes back.
    Gemini picks the language from the text itself."""
    model = os.getenv("TTS_GEMINI_MODEL", "google/gemini-3.8-flash-tts")
    base = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    data, ctype = _post(f"{base}/audio/speech", {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"},
                        {"model": model, "input": text, "voice": voice_id("gemini", lang, voice),
                         "response_format": "pcm"})        # Gemini TTS refuses mp3: raw 24 kHz 16-bit mono PCM only
    if "json" in ctype.lower() or data[:1] == b"{":
        raise RuntimeError(f"Gemini TTS returned an error: {data[:300].decode('utf-8', 'replace')}")
    return _pcm_to_mp3(data)


def _pcm_to_mp3(pcm, rate=24000):
    """Raw s16le mono PCM -> MP3 (pure-pip lameenc, no ffmpeg needed on the server). MP3 pieces concatenate."""
    import lameenc
    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(rate)
    enc.set_channels(1)
    enc.set_quality(2)
    return bytes(enc.encode(pcm[: len(pcm) // 2 * 2])) + bytes(enc.flush())


def _sarvam(text, lang, voice=None):
    """Sarvam Bulbul v3: JSON with base64 audio. Hinglish goes as hi-IN with the English words left in Latin
    script (the model handles the switch itself)."""
    data, _ = _post("https://api.sarvam.ai/text-to-speech", {"api-subscription-key": os.environ["SARVAM_API_KEY"]},
                    {"text": text, "language_code": "hi-IN" if lang == "hi" else "en-IN", "model": "bulbul:v3",
                     "speaker": voice_id("sarvam", lang, voice), "output_audio_codec": "mp3",
                     "speech_sample_rate": 24000, "pace": float(os.getenv("SARVAM_PACE", "1.0"))})
    try:
        return base64.b64decode(json.loads(data)["audios"][0])
    except (ValueError, KeyError, IndexError):
        raise RuntimeError(f"Sarvam returned no audio: {data[:300].decode('utf-8', 'replace')}") from None


def _kokoro(text, lang, voice=None):
    """Kokoro 82M (open weights, served by DeepInfra) via OpenRouter /audio/speech. The voice's first letter
    picks the language (a = American English, h = Hindi), so Hinglish uses a Hindi voice. Kokoro's Hindi
    front end reads Devanagari; Latin-script English terms inside it may be skipped or mispronounced."""
    base = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    data, ctype = _post(f"{base}/audio/speech", {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"},
                        {"model": os.getenv("TTS_KOKORO_MODEL", "hexgrad/kokoro-82m"), "input": text,
                         "voice": voice_id("kokoro", lang, voice), "response_format": "mp3"})
    if "json" in ctype.lower() or data[:1] == b"{":
        raise RuntimeError(f"Kokoro TTS returned an error: {data[:300].decode('utf-8', 'replace')}")
    return data


def _indic(text, lang, voice=None):
    """The shared Hindi/English/mixed TTS server (kavya, agastya, maitri, vinaya): POST {base}/tts with an
    X-API-Key header returns a 24 kHz mono WAV, which becomes MP3 here. Hinglish text goes as it is."""
    base = os.environ["TTS_INDIC_URL"].rstrip("/")
    data, ctype = _post(f"{base}/tts", {"X-API-Key": os.environ["TTS_INDIC_KEY"], "User-Agent": "simgen/1.0"},
                        {"text": text, "speaker": voice_id("indic", lang, voice)}, timeout=110)
    if data[:4] != b"RIFF":
        raise RuntimeError(f"Indic TTS returned no audio: {data[:300].decode('utf-8', 'replace')}")
    with wave.open(io.BytesIO(data)) as w:
        return _pcm_to_mp3(w.readframes(w.getnframes()), w.getframerate())


# speakers the UI offers per provider (a provider not listed here has just its configured default)
VOICE_CHOICES = {"indic": ["kavya", "agastya", "maitri", "vinaya"], "gemini": ["Kore", "Puck", "Zephyr", "Charon", "Leda"]}


# request size limits per provider (characters; Devanagari is 3 bytes a character, so Gemini's is lower)
VOICES = {"gemini": (_gemini, 1200), "sarvam": (_sarvam, 2400), "kokoro": (_kokoro, 1000), "indic": (_indic, 600)}


def speak(provider, text, lang, voice=None):
    """MP3 bytes for text. Long text is split by sentence; MP3 pieces simply concatenate."""
    fn, limit = VOICES[provider]
    return b"".join(fn(piece, lang, voice) for piece in _chunks(text, limit))


def voice_id(provider, lang="en", voice=None):
    return voice or {"gemini": os.getenv("TTS_GEMINI_VOICE", "Kore"), "sarvam": os.getenv("SARVAM_SPEAKER", "shubh"),
            "indic": os.getenv("TTS_INDIC_SPEAKER", "kavya"),
            "kokoro": os.getenv("TTS_KOKORO_VOICE_HI", "hf_alpha") if lang == "hi" else os.getenv("TTS_KOKORO_VOICE_EN", "af_heart")}[provider]


def tts_cost_usd(provider, chars):
    """Estimated, not billed: Sarvam is Rs 30 per 10,000 characters; Gemini bills audio tokens (25 a second),
    ~14 spoken characters a second -> about $16 per million characters at $9 per million audio tokens
    (TTS_GEMINI_USD_PER_MCHAR to adjust, e.g. when Google's price changes on 1 Jan 2027)."""
    if provider == "sarvam":
        return chars * 0.003 / USD_INR
    if provider == "indic":
        return 0.0                         # a shared research server, no per-character bill
    if provider == "kokoro":
        return chars * 0.62 / 1e6          # DeepInfra via OpenRouter, $0.62 per million characters
    return chars * float(os.getenv("TTS_GEMINI_USD_PER_MCHAR", "16")) / 1e6


# ---------------------------------------------------------------- narrate a simulation

def _sha(provider, text, voice=None):
    return hashlib.sha1(f"{provider}|{voice_id(provider, voice=voice)}|{text}".encode("utf-8")).hexdigest()[:12]


def read_manifest(audio_dir):
    try:
        return json.loads((Path(audio_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"langs": {}}


def narrate(html, audio_dir, provider=DEFAULT_PROVIDER, langs=("en",), grade=None, workers=None):
    """Make (or refresh) the audio for a simulation. Returns (manifest, [Usage]). A clip whose text and voice
    are unchanged is not paid for again. Raises if the page has no TOPIC script or a clip can't be made."""
    provider, _, voice = provider.partition(":")      # "indic:maitri" = provider + speaker; plain name = its default speaker
    voice = voice or None
    if provider not in VOICES:
        raise ValueError(f"unknown TTS provider {provider!r}; choose from {', '.join(VOICES)}")
    topic_js = topic_of(html)
    if not topic_js:
        raise ValueError("this page is not a Lab app (no TOPIC script), so there is nothing to narrate")
    english = clip_texts(topic_js)
    audio_dir = Path(audio_dir)
    man, usages = read_manifest(audio_dir), []
    for lang in langs:
        if lang not in LANGS:
            raise ValueError(f"unknown narration language {lang!r}")
        old = (man["langs"].get(lang) or {}).get("clips", {})
        texts = english
        if lang == "hi":      # keep the wording already made for an unchanged line: a re-run must not re-pay for audio
            kept = {k: old[k]["text"] for k in english if old.get(k, {}).get("src") == english[k]}
            fresh = {k: v for k, v in english.items() if k not in kept}
            new, u = hinglish(fresh, grade) if fresh else ({}, None)
            usages += [u] if u else []
            texts = {k: kept.get(k) or new[k] for k in english}
        todo = {k: t for k, t in texts.items()
                if not ((audio_dir / lang / f"{k}.mp3").exists() and old.get(k, {}).get("sha") == _sha(provider, t, voice))}
        (audio_dir / lang).mkdir(parents=True, exist_ok=True)
        t0 = time.time()

        def one(item):
            k, t = item
            (audio_dir / lang / f"{k}.mp3").write_bytes(speak(provider, t, lang, voice))

        with cf.ThreadPoolExecutor(workers or int(os.getenv("TTS_WORKERS", "4"))) as ex:
            list(ex.map(one, todo.items()))      # re-raises the first failure
        chars = sum(len(t) for t in todo.values())
        man["langs"][lang] = {"label": LANGS[lang], "provider": provider, "voice": voice_id(provider, lang, voice),
                              "clips": {k: {"text": t, "sha": _sha(provider, t, voice), "src": english[k]} for k, t in texts.items()}}
        if todo:
            usages.append(llm.Usage("tts", f"{provider}:{PROVIDERS[provider]}", chars, 0,
                                    round(tts_cost_usd(provider, chars), 6), round(time.time() - t0, 2),
                                    cost_source="estimated"))
    man["provider"] = provider
    man["spent_usd"] = round(man.get("spent_usd", 0) + sum(u.cost_usd for u in usages), 6)
    (audio_dir / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    return man, usages


# ---------------------------------------------------------------- put it in the page

def narration_object(man, url_for):
    """Manifest -> what the page reads (window.NARRATION): {lang: {label, clips: {id: url}}}."""
    return {lang: {"label": v["label"], "clips": {k: url_for(lang, k) for k in v["clips"]}}
            for lang, v in (man.get("langs") or {}).items() if v.get("clips")}


SHELL_MARK = "<!--shell:2-->"     # bump in shell.html when its behaviour changes: older saved pages are rebuilt


def with_narration(html, narration):
    """The page with window.NARRATION injected. A page built on an older shell is first
    rebuilt on the current shell: the shell is fixed code, only the TOPIC script is the simulation."""
    if not narration:
        return html
    if SHELL_MARK not in html and topic_of(html) is not None:
        html = assemble(topic_of(html))
    blob = json.dumps(narration, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return html.replace("</head>", f"<script>window.NARRATION={blob};</script>\n</head>", 1)


def inline_audio(narration, audio_dir):
    """For a downloaded copy: swap each clip URL for a data: URI so the single file plays offline."""
    out = {}
    for lang, v in narration.items():
        out[lang] = {"label": v["label"], "clips": {
            k: "data:audio/mpeg;base64," + base64.b64encode((Path(audio_dir) / lang / f"{k}.mp3").read_bytes()).decode()
            for k in v["clips"] if (Path(audio_dir) / lang / f"{k}.mp3").exists()}}
    return out


def main(argv=None):
    import argparse
    from .__main__ import load_env
    ap = argparse.ArgumentParser(prog="simgen.tts", description="Add narration to an existing simulation HTML file.")
    ap.add_argument("html")
    ap.add_argument("--lang", default="en", help="en, hi (Hinglish) or en,hi")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER, choices=list(PROVIDERS))
    ap.add_argument("--grade", type=int)
    a = ap.parse_args(argv)
    load_env()
    p = Path(a.html)
    man, us = narrate(p.read_text(encoding="utf-8"), p.with_suffix(".audio"), a.provider, tuple(a.lang.split(",")), a.grade)
    print(f"{sum(len(v['clips']) for v in man['langs'].values())} clips in {p.with_suffix('.audio')}  "
          f"(~${sum(u.cost_usd for u in us):.4f}, estimated)")


if __name__ == "__main__":
    main()
