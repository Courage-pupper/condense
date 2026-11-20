# Contributing

Thanks for helping! Bug reports, ideas and pull requests are welcome.

## Ground rules

- **No participant data, ever** — not in code, tests, issues, screenshots or commit messages. Use invented names and
  sentences. See [SECURITY.md](SECURITY.md).
- Be kind and constructive. Harassment or demeaning behaviour is not tolerated; the maintainer may remove comments
  or block participants who do not respect this.

## Development setup

```bash
git clone https://github.com/Courage-pupper/condense.git && cd condense
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q            # needs ffmpeg; no GPU, display or real recordings
```

On an Apple Silicon Mac, `pip install -e ".[mac,dev]"` adds MLX Whisper and PyTorch so you can run the app with
`.venv/bin/condense`.

## Tests

`tests/scenarios/` holds end-to-end scenarios that drive the real worker and GUI logic with a fake Tk
(`tests/stub_tk2.py`), synthetic media (`tests/make_media.py`) and stand-ins for Whisper, HandBrake and the Keychain
(`tests/fakes/`). New features should come with a scenario; `tests/test_structure.py` checks imports and undefined names.

## Pull requests

- One topic per pull request; describe what changed and how you tested it.
- Keep the transcript format (`*.transcript.json`) backwards compatible, or bump `SCHEMA_VERSION` with a migration.
- Add a line to [CHANGELOG.md](CHANGELOG.md).
- By contributing you agree that your contribution is licensed under the MIT licence of this project.
