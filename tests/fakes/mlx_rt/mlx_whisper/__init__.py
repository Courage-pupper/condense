import json, os, sys, wave
def transcribe(audio, path_or_hf_repo=None, **kw):
    """Fake MLX Whisper for re-transcription tests: reports the options it got; text depends on task."""
    print("FAKE_OPTIONS " + json.dumps({k: v for k, v in kw.items() if k != "verbose"} | {"repo": path_or_hf_repo}), flush=True)
    with wave.open(audio) as wf:
        dur = wf.getnframes() / wf.getframerate()
    print(f"FAKE_CLIP_DURATION {dur:.2f}", flush=True)
    words = ["We", "went", "to", "the", "playground"] if kw.get("task") != "translate" else ["We", "went", "outside"]
    step = (dur - 0.6) / len(words)
    ws = [{"word": " " + w, "start": round(0.3 + i * step, 3), "end": round(0.3 + (i + 1) * step, 3), "probability": 0.88} for i, w in enumerate(words)]
    # also a word inside the padding that must be dropped
    ws.insert(0, {"word": " uh", "start": 0.0, "end": 0.1, "probability": 0.3})
    return {"text": "x", "language": kw.get("language", "en"), "segments": [{"start": 0.0, "end": dur, "text": " junk text", "words": ws}]}
