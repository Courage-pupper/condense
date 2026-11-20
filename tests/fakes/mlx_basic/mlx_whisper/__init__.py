import sys
def transcribe(audio, path_or_hf_repo=None, word_timestamps=False, verbose=None, language=None):
    print(f"FAKE mlx_whisper repo={path_or_hf_repo} words={word_timestamps} lang={language}", flush=True)
    for p in (25, 60, 100):
        print(f"{p}%|#####| {p}/100 [00:01<00:00]", file=sys.stderr, flush=True)
    return {"text": " Hallo Welt. Wie geht's?", "language": "de", "segments": [
        {"start": 0.0, "end": 1.4, "text": " Hallo Welt.", "avg_logprob": -0.1,
         "words": [{"word": " Hallo", "start": 0.0, "end": 0.6, "probability": 0.97}, {"word": " Welt.", "start": 0.6, "end": 1.4, "probability": 0.55}]},
        {"start": 1.5, "end": 2.9, "text": " Wie geht's?", "avg_logprob": -0.4,
         "words": [{"word": " Wie", "start": 1.5, "end": 1.9, "probability": 0.91}, {"word": " geht's?", "start": 1.9, "end": 2.9, "probability": 0.33}]}]}
