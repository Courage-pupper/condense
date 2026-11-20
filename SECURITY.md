# Security and privacy

Condense processes research recordings, which are often personal data. It is designed to run entirely on your own
computer: recordings and transcripts are never uploaded. The only network access is downloading software and models
(pip, Homebrew, Hugging Face).

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private reporting instead:
*Security* tab → *Report a vulnerability*. You will get an answer as soon as possible.

## Never share participant data

When reporting a bug or asking for help, **do not attach recordings, transcripts, `.transcript.json` files,
screenshots that show participant names or quotes, or folder paths that identify a study.** Describe the problem with
invented example text, or reproduce it with the synthetic test media (`python tests/make_media.py <folder>`).

## Secrets

Hugging Face tokens are stored in the macOS Keychain or, on Linux, the desktop keyring (Secret Service via
`secret-tool`) — never in settings files or transcripts, and never on a command line. Never paste a token into
an issue; if you did, revoke it at <https://huggingface.co/settings/tokens>.
