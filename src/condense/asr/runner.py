"""The script Condense runs in the Whisper Python: `python -c WHISPER_RUNNER <args>`."""

# Script run in the Whisper Python (the one with mlx-whisper / torch), as `python -c WHISPER_RUNNER <args>`.
# Options via environment: PIPELINE_HF_TOKEN, PIPELINE_TASK, PIPELINE_PROMPT, PIPELINE_MODE, PIPELINE_SEGMENTS_JSON.
WHISPER_RUNNER = """
import json, math, os, sys, wave
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # must be set before torch is imported

import numpy as np

audio_path = sys.argv[1]
wav_16k_path = sys.argv[2]
model_name = sys.argv[3]
lang = sys.argv[4]
out_json = sys.argv[5]
diarize_mode = sys.argv[6]
num_speakers = int(sys.argv[7])
hf_token = os.environ.get("PIPELINE_HF_TOKEN", "")
backend = sys.argv[8]

# openai-whisper model names -> MLX-converted weights on Hugging Face (downloaded on first use)
MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}

kwargs = {"word_timestamps": True, "verbose": False}
if lang != "auto":
    kwargs["language"] = lang
task = os.environ.get("PIPELINE_TASK", "transcribe")
if task == "translate":
    kwargs["task"] = "translate"  # any language -> English
prompt = os.environ.get("PIPELINE_PROMPT", "").strip()
if prompt:
    kwargs["initial_prompt"] = prompt  # vocabulary: names, places, activity words
MODE = os.environ.get("PIPELINE_MODE", "full")  # "full" or "diarize_only" (speakers for existing segments)

result = None
backend_label = ""
if MODE == "diarize_only":
    with open(os.environ["PIPELINE_SEGMENTS_JSON"], encoding="utf-8") as fh:
        result = {"segments": json.load(fh)}
    backend_label = "existing transcript"
    print(f"Re-running speaker detection on {len(result['segments'])} existing segments.", flush=True)
elif backend.startswith("MLX"):
    try:
        import mlx_whisper
    except ImportError:
        mlx_whisper = None
        print("mlx-whisper is not installed in this Python; falling back to openai-whisper on CPU.", flush=True)
    if mlx_whisper is not None:
        repo = MLX_REPOS.get(model_name, model_name)
        print(f"Running MLX Whisper ({repo}) on the Apple Silicon GPU. First run downloads the model.", flush=True)
        try:
            result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=repo, hallucination_silence_threshold=2.0, **kwargs)
            kwargs["hallucination_silence_threshold"] = 2.0
        except TypeError:  # older mlx-whisper without this option
            result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=repo, **kwargs)
        backend_label = "MLX on Apple Silicon GPU"

def gpu_api_label(torch_mod):
    return "ROCm" if getattr(torch_mod.version, "hip", None) else "CUDA"

if result is None:
    # openai-whisper on a CUDA device: NVIDIA (CUDA) or AMD (ROCm builds of PyTorch use the same torch.cuda API).
    # Apple's MPS stays off: openai-whisper is unreliable there (sparse alignment-head tensors, fp16 NaNs).
    whisper_device, gpu_label = "cpu", ""
    if backend.startswith("PyTorch"):
        try:
            import torch
            if torch.cuda.is_available():
                whisper_device = "cuda"
                gpu_label = f"{gpu_api_label(torch)} GPU ({torch.cuda.get_device_name(0)})"
            else:
                print("PyTorch sees no GPU (check the ROCm/CUDA build of torch and your GPU driver); using the CPU.", flush=True)
        except ImportError:
            print("PyTorch is not installed in this Python; using the CPU.", flush=True)
    import whisper
    if whisper_device == "cuda":
        print(f"Running openai-whisper '{model_name}' on the {gpu_label}. First run downloads the model.", flush=True)
    model = whisper.load_model(model_name, device=whisper_device)
    use_fp16 = whisper_device == "cuda"
    try:
        result = model.transcribe(audio_path, fp16=use_fp16, hallucination_silence_threshold=2.0, **kwargs)
        kwargs["hallucination_silence_threshold"] = 2.0
    except TypeError:
        result = model.transcribe(audio_path, fp16=use_fp16, **kwargs)
    backend_label = f"openai-whisper on {gpu_label}" if gpu_label else "openai-whisper on CPU"

segments = result.get("segments", [])
result["diarization_method"] = "Disabled"
result["backend_label"] = backend_label

def pick_torch_device():
    import torch
    if torch.cuda.is_available():  # NVIDIA CUDA or AMD ROCm
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    return "mps" if (mps is not None and mps.is_available()) else "cpu"

def device_label(dev):
    if dev == "mps":
        return "Metal/MPS"
    if dev == "cuda":
        import torch
        return gpu_api_label(torch)
    return "CPU"

def run_spectral_diarization(wav_file, segs, k_Hint, dev):
    import torch
    import torch.nn.functional as F
    label = device_label(dev)
    if not segs or not os.path.exists(wav_file):
        return "Disabled"
    with wave.open(wav_file, "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)
    audio_np = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if wf.getnchannels() > 1:
        audio_np = audio_np.reshape(-1, wf.getnchannels()).mean(axis=1)

    n_fft = 1024
    hop = 256
    n_mels = 40
    win = torch.hann_window(n_fft, device=dev)
    f_min, f_max = 60.0, 7600.0
    m_min = 2595.0 * math.log10(1.0 + f_min / 700.0)
    m_max = 2595.0 * math.log10(1.0 + f_max / 700.0)
    # Filterbank / DCT are built element-by-element: do that on CPU, then move once to the GPU.
    m_pts = torch.linspace(m_min, m_max, n_mels + 2)
    hz_pts = 700.0 * (10.0 ** (m_pts / 2595.0) - 1.0)
    bins = torch.floor((n_fft + 1) * hz_pts / sr).long()
    fb = torch.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        l, c, r = int(bins[m - 1]), int(bins[m]), int(bins[m + 1])
        for k in range(l, c):
            if c > l:
                fb[m - 1, k] = (k - l) / (c - l)
        for k in range(c, r):
            if r > c:
                fb[m - 1, k] = (r - k) / (r - c)
    fb = fb.to(dev)

    n_mfcc = 20
    dct_m = torch.zeros((n_mfcc, n_mels))
    for k in range(n_mfcc):
        for n in range(n_mels):
            dct_m[k, n] = math.cos(math.pi * k * (2 * n + 1) / (2 * n_mels))
    dct_m = dct_m.to(dev)

    freqs = torch.linspace(0, sr / 2.0, n_fft // 2 + 1, device=dev)
    low_band = (freqs >= 85.0) & (freqs <= 260.0)
    mid_band = (freqs > 260.0) & (freqs <= 1200.0)
    high_band = (freqs > 1200.0) & (freqs <= 4500.0)

    embeds = []
    valid_indices = []
    for idx, seg in enumerate(segs):
        s_i = max(0, int(float(seg.get("start", 0.0)) * sr))
        e_i = min(len(audio_np), int(float(seg.get("end", 0.0)) * sr))
        if e_i - s_i < int(0.15 * sr):
            s_i = max(0, s_i - int(0.15 * sr))
            e_i = min(len(audio_np), e_i + int(0.15 * sr))
        chunk = audio_np[s_i:e_i]
        if len(chunk) < n_fft:
            chunk = np.pad(chunk, (0, n_fft - len(chunk)))
        wav_t = torch.from_numpy(chunk).to(dev)
        stft = torch.stft(wav_t, n_fft=n_fft, hop_length=hop, window=win, return_complex=True)
        mag = torch.abs(stft) + 1e-7
        power = mag ** 2
        frame_energy = power.mean(dim=0)
        thresh = torch.quantile(frame_energy, 0.30) if frame_energy.numel() > 4 else 0.0
        voiced = power[:, frame_energy >= thresh]
        if voiced.shape[1] == 0:
            voiced = power

        mel_spec = torch.matmul(fb, voiced)
        log_mel = torch.log(mel_spec + 1e-7)
        mfcc = torch.matmul(dct_m, log_mel)
        mfcc_mean = mfcc.mean(dim=1)
        mfcc_std = mfcc.std(dim=1) if mfcc.shape[1] > 1 else torch.zeros_like(mfcc_mean)

        e_low = voiced[low_band, :].mean().unsqueeze(0)
        e_mid = voiced[mid_band, :].mean().unsqueeze(0)
        e_high = voiced[high_band, :].mean().unsqueeze(0)
        ratios = torch.log(torch.cat([e_low / (e_mid + 1e-7), e_mid / (e_high + 1e-7), e_low / (e_high + 1e-7)]) + 1e-7)

        feat = torch.cat([mfcc_mean, mfcc_std, ratios], dim=0)
        embeds.append(feat)
        valid_indices.append(idx)

    if len(embeds) < 2:
        for s in segs:
            s["speaker"] = "SPEAKER_01"
        return f"{label} Spectral (1 Speaker)"

    # Clustering is many tiny ops + .item() syncs; with unified memory the CPU is faster for this part.
    X = torch.stack(embeds, dim=0).to("cpu")
    X_mean = X.mean(dim=0, keepdim=True)
    X_std = X.std(dim=0, keepdim=True) + 1e-6
    X_norm = F.normalize((X - X_mean) / X_std, p=2, dim=1)

    N = X_norm.shape[0]
    if k_Hint <= 0:
        best_k = 2
        best_score = -1.0
        max_k = min(6, N - 1)
        if max_k < 2:
            best_k = 1
        else:
            sim_mat = torch.matmul(X_norm, X_norm.T)
            dist_mat = 1.0 - sim_mat
            for cand_k in range(2, max_k + 1):
                centers = [0]
                for _ in range(cand_k - 1):
                    d_to_c = dist_mat[:, centers].min(dim=1).values
                    centers.append(int(torch.argmax(d_to_c).item()))
                C = X_norm[centers].clone()
                for _ in range(25):
                    d = 1.0 - torch.matmul(X_norm, C.T)
                    labels = torch.argmin(d, dim=1)
                    for c_i in range(cand_k):
                        mask = (labels == c_i)
                        if mask.any():
                            C[c_i] = F.normalize(X_norm[mask].mean(dim=0), p=2, dim=0)
                sil_vals = []
                for i in range(N):
                    my_c = int(labels[i].item())
                    same_mask = (labels == my_c)
                    same_mask[i] = False
                    a_i = dist_mat[i, same_mask].mean().item() if same_mask.any() else 0.0
                    b_i = 1e9
                    for other_c in range(cand_k):
                        if other_c == my_c:
                            continue
                        other_mask = (labels == other_c)
                        if other_mask.any():
                            b_i = min(b_i, dist_mat[i, other_mask].mean().item())
                    if b_i < 1e8 and max(a_i, b_i) > 0:
                        sil_vals.append((b_i - a_i) / max(a_i, b_i))
                avg_sil = sum(sil_vals) / len(sil_vals) if sil_vals else -1.0
                if avg_sil > best_score:
                    best_score = avg_sil
                    best_k = cand_k
            if best_score < 0.09:
                best_k = 1
        k_final = best_k
    else:
        k_final = min(k_Hint, N)

    if k_final <= 1:
        for s in segs:
            s["speaker"] = "SPEAKER_01"
        return f"{label} Spectral (1 Speaker)"

    dist_mat = 1.0 - torch.matmul(X_norm, X_norm.T)
    centers = [0]
    for _ in range(k_final - 1):
        d_to_c = dist_mat[:, centers].min(dim=1).values
        centers.append(int(torch.argmax(d_to_c).item()))
    C = X_norm[centers].clone()
    for _ in range(30):
        d = 1.0 - torch.matmul(X_norm, C.T)
        labels = torch.argmin(d, dim=1)
        for c_i in range(k_final):
            mask = (labels == c_i)
            if mask.any():
                C[c_i] = F.normalize(X_norm[mask].mean(dim=0), p=2, dim=0)

    raw_lbls = [int(labels[i].item()) for i in range(N)]
    for i in range(1, N - 1):
        dur = float(segs[valid_indices[i]].get("end", 0)) - float(segs[valid_indices[i]].get("start", 0))
        if dur < 1.0 and raw_lbls[i - 1] == raw_lbls[i + 1] and raw_lbls[i] != raw_lbls[i - 1]:
            raw_lbls[i] = raw_lbls[i - 1]

    remap = {}
    next_id = 1
    for i, seg_idx in enumerate(valid_indices):
        c_id = raw_lbls[i]
        if c_id not in remap:
            remap[c_id] = f"SPEAKER_{next_id:02d}"
            next_id += 1
        segs[seg_idx]["speaker"] = remap[c_id]

    return f"{label} Spectral Clustering ({len(remap)} Speakers)"

if not diarize_mode.startswith("Disabled") and len(segments) > 0:
    try:
        import torch
        device = pick_torch_device()
    except ImportError:
        torch = None
        device = None
        print("PyTorch is not installed (pip install torch); skipping speaker diarization.", flush=True)

    used_pyannote = False
    if torch is not None and "Pyannote" in diarize_mode:
        try:
            from pyannote.audio import Pipeline as PyannotePipeline
            repo_id = "pyannote/speaker-diarization-3.1"
            if hf_token:
                try:
                    pipeline = PyannotePipeline.from_pretrained(repo_id, token=hf_token)  # pyannote >= 4
                except TypeError:
                    pipeline = PyannotePipeline.from_pretrained(repo_id, use_auth_token=hf_token)  # pyannote 3.x
            else:
                pipeline = PyannotePipeline.from_pretrained(repo_id)
            if pipeline is None:
                raise RuntimeError("could not load pyannote pipeline (check HF token / model licence)")
            try:
                pipeline.to(torch.device(device))
            except Exception:
                device = "cpu"
                pipeline.to(torch.device("cpu"))
            p_kwargs = {"num_speakers": num_speakers} if num_speakers > 0 else {}
            diar = pipeline(wav_16k_path, **p_kwargs)
            turns = []
            for turn, _, spk in diar.itertracks(yield_label=True):
                turns.append((turn.start, turn.end, spk))
            remap = {}
            next_id = 1
            for seg in segments:
                s0, s1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
                best_spk = "SPEAKER_01"
                best_ov = 0.0
                for t0, t1, spk in turns:
                    ov = max(0.0, min(s1, t1) - max(s0, t0))
                    if ov > best_ov:
                        best_ov = ov
                        best_spk = spk
                if best_spk not in remap:
                    remap[best_spk] = f"SPEAKER_{next_id:02d}"
                    next_id += 1
                seg["speaker"] = remap[best_spk]
            dev_lbl = device_label(device)
            result["diarization_method"] = f"pyannote.audio 3.1 {dev_lbl} ({len(remap)} Speakers)"
            used_pyannote = True
        except Exception as exc:
            print(f"pyannote diarization unavailable ({exc}); using spectral clustering.", flush=True)

    if torch is not None and not used_pyannote:
        try:
            method_str = run_spectral_diarization(wav_16k_path, segments, num_speakers, device)
        except Exception as exc:
            if device == "cpu":
                raise
            print(f"GPU diarization failed ({exc}); retrying on CPU.", flush=True)
            method_str = run_spectral_diarization(wav_16k_path, segments, num_speakers, "cpu")
        result["diarization_method"] = method_str

def _pkg_version(dist):
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return None

result["runtime"] = {
    "python": sys.version.split()[0],
    "packages": {d: _pkg_version(d) for d in ("mlx-whisper", "mlx", "openai-whisper", "torch", "pyannote.audio", "numpy")},
    "mlx_repo": MLX_REPOS.get(model_name, model_name) if backend_label.startswith("MLX") else None,
    "transcribe_options": {k: v for k, v in kwargs.items() if k != "verbose"},
}

with open(out_json, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("WHISPER_DONE=1", flush=True)
"""


def runner_command(python: str, temp_dir) -> list:
    """[python, ...] that runs WHISPER_RUNNER. Normally `python -c <script>`; on Windows the script goes into a
    file in temp_dir, because the command line there is limited to 32 767 characters (the script is ~15 000)."""
    import os
    from pathlib import Path
    if os.name == "nt":
        path = Path(temp_dir) / "condense_whisper_runner.py"
        path.write_text(WHISPER_RUNNER, encoding="utf-8")
        return [python, str(path)]
    return [python, "-c", WHISPER_RUNNER]

