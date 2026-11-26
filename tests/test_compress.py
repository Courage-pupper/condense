"""Compression goals (media/compress.py, pipeline/encode.py): AV1 / H.264 commands, the choice of encoder, the sample
check and its size estimate, never larger than the original, and the H.264 copy for MAXQDA — with real ffmpeg."""
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from condense.media import compress as c  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
needs_svt = pytest.mark.skipif(not (HAS_FFMPEG and c.encoder_works("libsvtav1")), reason="ffmpeg without SVT-AV1")
needs_x264 = pytest.mark.skipif(not (HAS_FFMPEG and c.encoder_works("libx264")), reason="ffmpeg without x264")


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def camera(tmp_path_factory):
    """A 'camera file': 720p (quick to encode in tests), slightly grainy, at a camera-like 6 Mbit/s H.264, with sound."""
    d = tmp_path_factory.mktemp("cam")
    src = d / "camera.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=14", "-f", "lavfi", "-i", "sine=f=440:d=14",
       "-vf", "noise=alls=3:allf=t,gblur=sigma=0.6", "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "6M", "-maxrate", "7M",
       "-bufsize", "10M", "-c:a", "aac", "-b:a", "192k", "-shortest", str(src))
    return src


def test_commands():
    src, dst = Path("in.mov"), Path("out.mp4")
    cmd = c.encode_command("libsvtav1", src, dst, 32)
    assert cmd[cmd.index("-c:v") + 1] == "libsvtav1" and cmd[cmd.index("-crf") + 1] == "32" and "-preset" in cmd
    assert "min(ih,1080)" in cmd[cmd.index("-vf") + 1] and "min(iw,1920)" in cmd[cmd.index("-vf") + 1]
    assert cmd[cmd.index("-c:a") + 1] == "aac" and cmd[cmd.index("-b:a") + 1] == "128k" and "+faststart" in cmd
    sample = c.encode_command("libsvtav1", src, Path("s.mkv"), 30, start=12.5, duration=6, progress=False)
    assert "-an" in sample and "-c:a" not in sample and sample.index("-ss") < sample.index("-i") and "+faststart" not in sample
    va = c.encode_command("av1_vaapi", src, dst, 120, vaapi_device="/dev/dri/renderD128")
    assert va.index("-vaapi_device") < va.index("-i") and "hwupload,scale_vaapi" in va[va.index("-vf") + 1]
    assert va[va.index("-qp") + 1] == "120" and va[va.index("-rc_mode") + 1] == "CQP"
    nv = c.encode_command("av1_nvenc", src, dst, 34)
    assert nv[nv.index("-cq") + 1] == "34" and nv[nv.index("-b:v") + 1] == "0"
    mx = c.maxqda_command(src, dst, max_h=720)
    assert mx[mx.index("-c:v") + 1] == "libx264" and "min(ih,720)" in mx[mx.index("-vf") + 1] and "+faststart" in mx
    assert c.goal_kind(c.GOALS[0]) == "av1" and c.goal_kind(c.GOALS[1]) == "h264" and c.goal_kind(c.GOALS[2]) == "custom"
    assert c.goal_kind("") == "custom"  # tasks saved before goals existed keep their encoder settings


def test_encoder_choice():
    yes = lambda *names: (lambda codec: codec in names)
    if not HAS_FFMPEG:
        pytest.skip("no ffmpeg")
    assert c.pick_encoder("av1", "/dev/dri/renderD128", check=yes("libsvtav1"), check_vaapi=lambda d: True) == "av1_vaapi"
    assert c.pick_encoder("av1", None, check=yes("av1_nvenc", "libsvtav1"), check_vaapi=lambda d: False) == "av1_nvenc"
    assert c.pick_encoder("av1", None, check=yes("libsvtav1"), check_vaapi=lambda d: False) == "libsvtav1"
    assert c.pick_encoder("av1", None, check=yes(), check_vaapi=lambda d: False) is None
    assert c.pick_encoder("h264", None, check=yes("libx264")) == "libx264"


def test_samples_and_padding():
    st = c.sample_starts(5 * 3600)
    assert len(st) == 4 and st == sorted(st) and st[0] > 600 and st[-1] < 5 * 3600 - 600
    assert c.sample_starts(30) == [round(30 * 0.05 + (30 * 0.9 - 6) * 0.5, 3)] and c.sample_starts(8) == [0.0]
    assert c.padded((1920, 1082), (3840, 2160)) and not c.padded((1920, 1080), (3840, 2160))
    assert not c.padded((1080, 1920), (1080, 1920)) and not c.padded((1280, 720), (1280, 720))


def test_vmaf_search_takes_the_smallest_setting_that_passes(monkeypatch, tmp_path, camera):
    """With VMAF: the largest value (smallest file) whose every sample reaches the target, never past the safe cap."""
    if not (HAS_FFMPEG and c.encoder_works("libsvtav1")):
        pytest.skip("ffmpeg without SVT-AV1")
    monkeypatch.setattr(c, "compare", lambda src, sample, start, length, use_vmaf: 130.0 - float(sample.stem.split("_")[1]))
    res = c.search_quality(camera, "libsvtav1", tmp_path / "w", use_vmaf=True)
    assert res["metric"] == "VMAF" and res["q"] == 36 and res["score"] == 94.0   # 130 − 36 = 94: the last that passes
    monkeypatch.setattr(c, "compare", lambda *a: 99.0)
    assert c.search_quality(camera, "libsvtav1", tmp_path / "w", use_vmaf=True)["q"] == c.RANGES["libsvtav1"][1]  # the cap


@needs_x264
def test_1080p_means_the_short_side(tmp_path):
    """Landscape 4K → 1920×1080, portrait (phone) 2160×3840 → 1080×1920, small stays small."""
    for (w, h), want in (((3840, 2160), (1920, 1080)), ((2160, 3840), (1080, 1920)), ((640, 360), (640, 360))):
        src = tmp_path / f"in_{w}x{h}.mp4"
        ff("-f", "lavfi", "-i", f"color=c=gray:s={w}x{h}:d=0.2", "-c:v", "libx264", "-preset", "ultrafast", str(src))
        out = tmp_path / f"out_{w}x{h}.mkv"
        subprocess.run(c.encode_command("libx264", src, out, 30, progress=False), check=True, capture_output=True)
        info = c.probe(out)
        assert (info["width"], info["height"]) == want, (w, h, info)
        assert not c.padded(want, (w, h))


@needs_svt
def test_av1_from_a_camera_file(tmp_path, camera):
    res = c.search_quality(camera, "libsvtav1", tmp_path / "w", use_vmaf=False)
    assert res["metric"] == "fixed" and res["q"] == 32 and res["size"] == (1280, 720)  # never enlarged
    assert 0 < res["estimate"] < 0.6 * res["source_size"] and not res["keep_original"], res
    assert not any((tmp_path / "w").iterdir())  # samples cleaned up


@needs_svt
def test_already_small_files_are_kept(tmp_path, camera):
    tiny = tmp_path / "tiny.mkv"   # an already efficient AV1 file: a copy would not be clearly smaller
    ff("-i", str(camera), "-c:v", "libsvtav1", "-crf", "50", "-preset", "12", "-c:a", "copy", str(tiny))
    res = c.search_quality(tiny, "libsvtav1", tmp_path / "w", use_vmaf=False)
    assert res["keep_original"] and "not worth it" in res["reason"]


@needs_svt
def test_pipeline_goal_encode(tmp_path, camera):
    from condense.pipeline.worker import PipelineWorker
    cfg = {"hb_goal": c.GOALS[0], "hb_target": c.TARGETS[0], "hb_encoder": "x265 (CPU)", "hb_quality": 22, "hb_resolution": "Original",
           "hb_fps": "Same as source", "hb_audio_bitrate": "192", "hb_container": "MKV"}
    w = PipelineWorker(cfg, queue.Queue(), threading.Event())
    out = w.run_handbrake_encode_for_jobs(camera, tmp_path / "camera_compressed.mkv", "Compress", 0.0, 50.0, [])
    info = c.probe(out)
    assert out == tmp_path / "camera_compressed.mkv" and info["codec"] == "av1" and info["height"] == 720
    assert info["size"] < 0.6 * camera.stat().st_size and abs(info["duration"] - 14) < 0.5
    rec = w.compression_record()
    assert rec["encoder"] in ("libsvtav1", "av1_vaapi", "av1_nvenc", "av1_amf", "av1_qsv") and rec["goal"] == c.GOALS[0]
    # already small: the original is used, nothing written
    tiny = tmp_path / "tiny.mkv"
    ff("-i", str(camera), "-c:v", "libsvtav1", "-crf", "50", "-preset", "12", "-c:a", "copy", str(tiny))
    out2 = w.run_handbrake_encode_for_jobs(tiny, tmp_path / "tiny_compressed.mkv", "Compress", 0.0, 50.0, [])
    assert out2 == tiny and not (tmp_path / "tiny_compressed.mkv").exists() and w.compression_record()["kept_original"]


@needs_x264
def test_compatible_goal_and_maxqda_copy(tmp_path, camera):
    from condense.pipeline.worker import PipelineWorker
    cfg = {"hb_goal": c.GOALS[1], "hb_target": c.TARGETS[0], "hb_fps": "Same as source", "hb_container": "MKV"}
    w = PipelineWorker(cfg, queue.Queue(), threading.Event())
    out = w.run_handbrake_encode_for_jobs(camera, tmp_path / "camera_compressed.mkv", "Compress", 0.0, 50.0, [])
    assert out.suffix == ".mp4" and c.probe(out)["codec"] == "h264"   # MP4 whatever the container setting
    copy = tmp_path / "camera_MAXQDA.mp4"
    subprocess.run(c.maxqda_command(camera, copy, max_h=720), check=True, capture_output=True)
    info = c.probe(copy)
    assert info["codec"] == "h264" and (info["width"], info["height"]) == (1280, 720) and abs(info["duration"] - 14) < 0.5
