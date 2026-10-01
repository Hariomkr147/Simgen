"""Narration tests: offline (HTTP and the LLM are faked)."""
import base64
import json
import os
import tempfile
from pathlib import Path

from simgen import llm, pipeline, tts

os.environ.update({"LLM_BASE_URL": "https://or.test/api/v1", "LLM_API_KEY": "k", "SARVAM_API_KEY": "sk",
                   "MODEL_nar_ID": "m", "MODEL_nar_IN": "1", "MODEL_nar_OUT": "1", "NARRATION_MODEL": "nar"})
HTML = pipeline.assemble(pipeline.EXAMPLE_TOPIC)


def fake_http(calls):
    def post(url, headers, body, timeout=120):
        calls.append((url, headers, body))
        if "sarvam" in url:
            return json.dumps({"audios": [base64.b64encode(b"SARVAM").decode()]}).encode(), "application/json"
        return b"GEMINI", "audio/mpeg"
    return post


def fake_llm(replies):
    def t(alias, system, user, max_tokens=0):
        ids = json.loads(user[user.rindex("\n\n") + 2:])
        replies.append(list(ids))
        return json.dumps({k: "हिंदी " + v for k, v in ids.items()}), 10, 10
    return t


def test_clip_texts():
    c = tts.clip_texts(pipeline.topic_of(HTML))
    assert "s0.do" in c and c["s0.do"].endswith(".") and "Tap" in c["s0.do"]
    assert any(k.endswith(".q") and "Options. A." in v for k, v in c.items())
    assert "t0.q" in c and "finish" in c
    assert not any("<" in v for v in c.values())
    assert tts.spoken("<p>H<sub>2</sub>O &amp; x²</p><p>Next</p>") == "H2O & x squared Next"


def test_chunking():
    assert tts._chunks("Hi there.", 100) == ["Hi there."]
    parts = tts._chunks("One sentence here. " * 30, 100)
    assert len(parts) > 1 and all(len(p) <= 100 for p in parts) and " ".join(parts).count("One sentence here.") == 30


def test_providers_request_shape():
    calls, real = [], tts._post
    tts._post = fake_http(calls)
    try:
        assert tts.speak("gemini", "Hello", "en") == b"GEMINI"
        url, headers, body = calls[-1]
        assert url == "https://or.test/api/v1/audio/speech" and headers["Authorization"] == "Bearer k"
        assert body == {"model": "google/gemini-3.8-flash-tts", "input": "Hello", "voice": "Kore", "response_format": "mp3"}
        assert tts.speak("sarvam", "नमस्ते science", "hi") == b"SARVAM"
        url, headers, body = calls[-1]
        assert url == "https://api.sarvam.ai/text-to-speech" and headers["api-subscription-key"] == "sk"
        assert body["model"] == "bulbul:v3" and body["language_code"] == "hi-IN" and body["output_audio_codec"] == "mp3"
        tts.speak("sarvam", "Hello", "en")
        assert calls[-1][2]["language_code"] == "en-IN"
        n = len(calls)
        assert tts.speak("sarvam", "A sentence here. " * 400, "en").count(b"SARVAM") == len(calls) - n > 1   # split + joined
    finally:
        tts._post = real


def test_narrate_english_hinglish_and_cache():
    calls, replies, real = [], [], tts._post
    tts._post = fake_http(calls)
    llm.TRANSPORT = fake_llm(replies)
    d = Path(tempfile.mkdtemp()) / "x.audio"
    try:
        man, us = tts.narrate(HTML, d, "gemini", ("en", "hi"))
        ids = list(tts.clip_texts(pipeline.topic_of(HTML)))
        assert set(man["langs"]) == {"en", "hi"} and set(man["langs"]["en"]["clips"]) == set(ids)
        assert all((d / lang / f"{k}.mp3").read_bytes() == b"GEMINI" for lang in ("en", "hi") for k in ids)
        assert man["langs"]["hi"]["clips"]["s0.do"]["text"].startswith("हिंदी")
        assert [u.role for u in us] == ["tts", "narration", "tts"] or {u.role for u in us} == {"tts", "narration"}
        assert all(u.cost_usd > 0 for u in us if u.role == "tts")
        n = len(calls)
        man2, us2 = tts.narrate(HTML, d, "gemini", ("en", "hi"))          # nothing changed: nothing paid
        assert len(calls) == n and us2 == [] and man2["langs"]["hi"]["clips"] == man["langs"]["hi"]["clips"]
        assert replies == [ids]                                           # the Hinglish rewrite ran once only
        man3, us3 = tts.narrate(HTML, d, "sarvam", ("en",))               # a different voice re-makes the audio
        assert len(calls) == n + len(ids) and (d / "en" / "s0.do.mp3").read_bytes() == b"SARVAM"
        assert man3["provider"] == "sarvam" and man3["spent_usd"] > man["spent_usd"]
    finally:
        tts._post = real
        llm.TRANSPORT = None


def test_hinglish_must_cover_every_line():
    llm.TRANSPORT = lambda a, s, u, max_tokens=0: (json.dumps({"s0.do": "x"}), 1, 1)
    try:
        tts.hinglish({"s0.do": "a", "s1.do": "b"})
        raise AssertionError("expected an error for a skipped line")
    except ValueError as e:
        assert "skipped 1" in str(e)
    finally:
        llm.TRANSPORT = None


def test_page_gets_narration_and_old_shell_is_rebuilt():
    man = {"langs": {"en": {"label": "English", "clips": {"s0.do": {}}}}}
    obj = tts.narration_object(man, lambda l, k: f"audio/n/{l}/{k}.mp3")
    assert obj == {"en": {"label": "English", "clips": {"s0.do": "audio/n/en/s0.do.mp3"}}}
    page = tts.with_narration(HTML, obj)
    assert 'window.NARRATION={"en"' in page and page.count("</head>") == 1 and 'id="bVoice"' in page
    old = HTML.replace('id="bVoice"', 'id="gone"')                       # a page from before the voice button
    assert 'id="bVoice"' in tts.with_narration(old, obj)
    assert tts.with_narration(HTML, None) == HTML
    evil = tts.with_narration(HTML, {"en": {"label": "</script><b>", "clips": {}}})
    assert "</script><b>" not in evil


def test_web_routes():
    import app as webapp
    webapp.RUNS = Path(tempfile.mkdtemp())
    (webapp.RUNS / "pend").mkdir()
    name = "teacher_student__a-b__20260101-000000"
    (webapp.RUNS / "pend" / f"{name}.html").write_text(HTML, encoding="utf-8")
    calls, real = [], tts._post
    tts._post = fake_http(calls)
    c = webapp.app.test_client()
    try:
        assert 'window.NARRATION={' not in c.get(f"/sim/pend/{name}").get_data(as_text=True)
        r = c.post(f"/narrate/pend/{name}", data={"tts": "sarvam", "narrate": "en"})
        assert r.status_code == 302 and calls
        page = c.get(f"/sim/pend/{name}").get_data(as_text=True)
        assert f'"s0.do":"audio/{name}/en/s0.do.mp3"' in page
        a = c.get(f"/sim/pend/audio/{name}/en/s0.do.mp3")
        assert a.status_code == 200 and a.data == b"SARVAM" and a.mimetype == "audio/mpeg"
        for bad in (f"/sim/pend/audio/{name}/xx/s0.do.mp3", f"/sim/pend/audio/{name}/en/..%2Fmanifest.json",
                    f"/sim/pend/audio/{name}/en/s0.do.wav"):
            assert c.get(bad).status_code == 404, bad
        dl = c.get(f"/download/pend/{name}").get_data(as_text=True)
        assert "data:audio/mpeg;base64," in dl and "audio/" + name not in dl            # single file, plays offline
        assert "Narration" in c.get(f"/view/pend/{name}").get_data(as_text=True)
        assert c.post(f"/narrate/pend/nope", data={}).status_code == 404
    finally:
        tts._post = real


if __name__ == "__main__":
    for n, fn in sorted(globals().items()):
        if n.startswith("test_"):
            fn()
            print("ok", n)
    print("all passed")
