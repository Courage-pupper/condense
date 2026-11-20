# Third-party software and models

Condense itself is MIT-licensed (see [LICENSE](LICENSE)). It does **not** bundle any of the software or models below:
Python packages are installed from PyPI by `pip`, ffmpeg and HandBrake are installed by the user (e.g. with Homebrew)
and run as separate programs, and Whisper / pyannote models are downloaded from Hugging Face on first use.
Each component keeps its own licence; this page lists them so users can check their obligations.

## Python packages (installed by pip)

| Package | Used for | Licence |
|---|---|---|
| [mlx-whisper](https://pypi.org/project/mlx-whisper/) / [MLX](https://github.com/ml-explore/mlx) | Whisper on Apple Silicon GPUs | MIT |
| [openai-whisper](https://github.com/openai/whisper) | Whisper on the CPU (fallback) | MIT |
| [PyTorch](https://pytorch.org) | Speaker detection (Metal/CUDA/ROCm) | BSD-3-Clause |
| [NumPy](https://numpy.org) | Audio features | BSD-3-Clause |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio) (optional) | Neural speaker detection | MIT |

Their own dependencies (e.g. `huggingface_hub`, `tiktoken`) carry their own, permissive licences.

## Models (downloaded on first use)

| Model | Licence | Notes |
|---|---|---|
| OpenAI Whisper weights (tiny … large-v3, large-v3-turbo) | MIT | Released by OpenAI together with the code. |
| `mlx-community/whisper-*` conversions on Hugging Face | MIT | MLX-format conversions of the Whisper weights. |
| `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0` | MIT | **Gated** on Hugging Face: each user must accept the model conditions (sharing contact details) and use their own access token. |

## External programs (installed separately, run as separate processes)

| Program | Licence | Notes |
|---|---|---|
| [ffmpeg](https://ffmpeg.org) | LGPL-2.1+ / GPL-2.0+ (depending on the build) | Called on the command line for cutting, converting and decoding. |
| [HandBrakeCLI](https://handbrake.fr) | GPL-2.0 | Optional; called on the command line for video compression. |
| [LosslessCut](https://github.com/mifi/lossless-cut) | GPL-2.0 | Not used as software: Condense only reads the `.llc` project files you create with it. |

Because these programs are not distributed with Condense and only exchange files and command-line arguments with it,
their copyleft licences do not extend to Condense. **If a future Condense release bundles ffmpeg or HandBrake
binaries** (for example a Windows installer), that release must include their licence texts and a way to obtain the
corresponding source code.

## Runtime

Python (PSF License) and Tcl/Tk (BSD-style licence), as installed by the user.

## Trademarks

Whisper, MLX, Apple Silicon, MAXQDA, ATLAS.ti, NVIDIA, AMD, HandBrake and other names are trademarks of their
respective owners. Condense is an independent project and is not affiliated with or endorsed by any of them.
