"""Apply LosslessCut (.llc) cut lists losslessly."""
import json
import math
import os
import platform
import plistlib
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from condense.core.util import *  # noqa: F401,F403


class LosslessCutHelper:
    @staticmethod
    def find_llc_for_video(video_path: Path) -> Optional[Path]:
        candidates = [
            video_path.with_suffix(".llc"),
            Path(str(video_path) + ".llc"),
            video_path.parent / f"{video_path.stem}-proj.llc",
        ]
        for cand in candidates:
            if cand.exists():
                return cand
        return None

    @staticmethod
    def parse_llc_segments(llc_path: Path) -> List[Tuple[float, float]]:
        content = llc_path.read_text(encoding="utf-8", errors="ignore")
        segments = []
        pattern = re.compile(
            r"start\s*:\s*([0-9.]+)\s*,\s*end\s*:\s*([0-9.]+)|"
            r'"start"\s*:\s*([0-9.]+)\s*,\s*"end"\s*:\s*([0-9.]+)'
        )
        for match in pattern.finditer(content):
            s = match.group(1) or match.group(3)
            e = match.group(2) or match.group(4)
            if s is not None and e is not None:
                segments.append((float(s), float(e)))
        return segments

    @classmethod
    def apply_llc_cuts_if_present(
        cls,
        video_path: Path,
        work_dir: Path,
        log_fn: Callable[[str, str], None],
        run_fn: Callable[[List[str], str], None],
    ) -> Tuple[Path, Optional[Dict]]:
        """Returns (video to use, record of the cuts applied or None). Cuts change the timeline, so the
        record goes into the transcript's provenance."""
        llc_file = cls.find_llc_for_video(video_path)
        if not llc_file:
            return video_path, None

        segments = cls.parse_llc_segments(llc_file)
        if not segments:
            return video_path, None

        log_fn(
            f"Found LosslessCut project ({llc_file.name}) with {len(segments)} segment(s). Trimming losslessly...",
            "INFO",
        )
        record = {
            "source": str(video_path),
            "llc_file": str(llc_file),
            "segments_s": [[round(s, 3), round(e, 3)] for s, e in segments],
            "note": "Stream-copy cuts snap to keyframes; transcript timestamps refer to the trimmed timeline.",
        }
        part_files = []
        for idx, (start_s, end_s) in enumerate(segments):
            part_out = work_dir / f"{video_path.stem}_llc_part{idx:03d}{video_path.suffix}"
            run_fn(
                [
                    "ffmpeg", "-hide_banner", "-y",
                    "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
                    "-i", str(video_path),
                    "-c", "copy", "-avoid_negative_ts", "make_zero",
                    str(part_out),
                ],
                f"LosslessCut trim of {video_path.name} (segment {idx + 1})",
            )
            part_files.append(part_out)

        if len(part_files) == 1:
            return part_files[0], record

        concat_list = work_dir / f"{video_path.stem}_llc_concat.txt"
        concat_list.write_text("\n".join(ffconcat_line(p) for p in part_files) + "\n", encoding="utf-8")
        merged_llc = work_dir / f"{video_path.stem}_llc_trimmed{video_path.suffix}"
        run_fn(
            ["ffmpeg", "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(merged_llc)],
            f"Joining LosslessCut segments of {video_path.name}",
        )
        return merged_llc, record
