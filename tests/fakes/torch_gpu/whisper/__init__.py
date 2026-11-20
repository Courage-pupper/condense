"""Fake openai-whisper: records the device and fp16 flag it was given (printed for the scenario to check)."""
import sys


class _Model:
    def __init__(self, name, device):
        self.name, self.device = name, device

    def transcribe(self, audio, fp16=True, **kw):
        print(f"FAKE whisper model={self.name} device={self.device} fp16={fp16} lang={kw.get('language')}", flush=True)
        for p in (40, 100):
            print(f"{p}%|#####| {p}/100 [00:01<00:00]", file=sys.stderr, flush=True)
        return {"text": " Hello there. Pick a colour.", "language": "en", "segments": [
            {"start": 0.0, "end": 1.2, "text": " Hello there.", "avg_logprob": -0.1,
             "words": [{"word": " Hello", "start": 0.0, "end": 0.5, "probability": 0.96}, {"word": " there.", "start": 0.5, "end": 1.2, "probability": 0.9}]},
            {"start": 1.4, "end": 2.8, "text": " Pick a colour.", "avg_logprob": -0.2,
             "words": [{"word": " Pick", "start": 1.4, "end": 1.8, "probability": 0.93}, {"word": " a", "start": 1.8, "end": 2.0, "probability": 0.9},
                       {"word": " colour.", "start": 2.0, "end": 2.8, "probability": 0.41}]}]}


def load_model(name, device=None):
    return _Model(name, device)
