"""Export writers: SRT, VTT, TXT, paragraph TXT/RTF, confidence RTF."""
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


class RTFConfidenceWriter:
    @staticmethod
    def _escape_rtf(text: str) -> str:
        out = []
        for ch in text:
            code = ord(ch)
            if ch == "\\":
                out.append("\\\\")
            elif ch == "{":
                out.append("\\{")
            elif ch == "}":
                out.append("\\}")
            elif ch == "\n":
                out.append("\\line ")
            elif 32 <= code <= 126:
                out.append(ch)
            else:
                # RTF \uN takes signed 16-bit values; characters outside the BMP (e.g. emoji) need a surrogate pair.
                units = [code] if code <= 0xFFFF else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
                for u in units:
                    signed = u if u < 32768 else u - 65536
                    out.append(f"\\u{signed}?")
        return "".join(out)

    @staticmethod
    def format_timestamp(seconds: float, srt_style: bool = False) -> str:
        # Work in whole milliseconds so rounding can never produce "00:00:60,000".
        total_ms = int(round(max(0.0, float(seconds)) * 1000))
        hrs, rem = divmod(total_ms, 3_600_000)
        mins, rem = divmod(rem, 60_000)
        secs, millis = divmod(rem, 1000)
        sep = "," if srt_style else "."
        return f"{hrs:02d}:{mins:02d}:{secs:02d}{sep}{millis:03d}"

    @staticmethod
    def compute_confidence_interval(probs: List[float]) -> Tuple[float, float, float, float]:
        clean = [max(0.0, min(1.0, float(p))) for p in probs if p is not None]
        if not clean:
            return (0.0, 0.0, 0.0, 0.0)
        mean_val = statistics.mean(clean)
        if len(clean) < 2:
            return (mean_val, mean_val, mean_val, 0.0)
        stdev_val = statistics.stdev(clean)
        se = stdev_val / math.sqrt(len(clean))
        margin = 1.96 * se
        ci_low = max(0.0, mean_val - margin)
        ci_high = min(1.0, mean_val + margin)
        return (mean_val, ci_low, ci_high, margin)

    @classmethod
    def write_srt(cls, segments: List[Dict], output_path: Path) -> None:
        lines = []
        for idx, seg in enumerate((s for s in segments if not is_blank_text(s.get("text"))), start=1):
            start_ts = cls.format_timestamp(seg.get("start", 0.0), srt_style=True)
            end_ts = cls.format_timestamp(seg.get("end", 0.0), srt_style=True)
            text = (seg.get("text") or "").strip()
            speaker = seg.get("speaker")
            prefix = f"[{speaker}] " if speaker else ""
            lines.append(f"{idx}\n{start_ts} --> {end_ts}\n{prefix}{text}\n")
        output_path.write_text("\n".join(lines), encoding="utf-8")

    @classmethod
    def write_vtt(cls, segments: List[Dict], output_path: Path) -> None:
        lines = ["WEBVTT\n"]
        for seg in segments:
            if is_blank_text(seg.get("text")):
                continue
            start_ts = cls.format_timestamp(seg.get("start", 0.0), srt_style=False)
            end_ts = cls.format_timestamp(seg.get("end", 0.0), srt_style=False)
            text = (seg.get("text") or "").strip()
            speaker = seg.get("speaker")
            prefix = f"<v {speaker}>" if speaker else ""
            lines.append(f"{start_ts} --> {end_ts}\n{prefix}{text}\n")
        output_path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _hms(seconds: float) -> str:
        total = int(max(0.0, float(seconds)))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    @classmethod
    def write_paragraphs_txt(cls, paragraphs: List[Dict], output_path: Path) -> None:
        """One paragraph per speaker turn: 'Speaker: text [hh:mm:ss]'. The speaker-colon start is what
        MAXQDA's focus-group import looks for; the [hh:mm:ss] at the end of each paragraph (end of the turn)
        is a timestamp format MAXQDA links to the media file."""
        blocks = []
        for p in paragraphs:
            spk = f"{p['speaker']}: " if p.get("speaker") else ""
            blocks.append(f"{spk}{p['text']} [{cls._hms(p['end'])}]")
        output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    @classmethod
    def write_paragraphs_rtf(cls, paragraphs: List[Dict], output_path: Path, title: str) -> None:
        """Clean reading/analysis copy for Word, Pages or MAXQDA: no colours, one paragraph per turn."""
        parts = [
            r"{\rtf1\ansi\deff0{\fonttbl{\f0\fswiss Helvetica Neue;}}{\colortbl;\red110\green110\blue110;}",
            r"\viewkind4\uc1\pard\sa240\sl300\slmult1\f0\fs22",
            f"\\b\\fs28 {cls._escape_rtf(title)}\\b0\\fs22\\par",
        ]
        for p in paragraphs:
            spk = f"\\b {cls._escape_rtf(p['speaker'])}:\\b0  " if p.get("speaker") else ""
            parts.append(f"{spk}{cls._escape_rtf(p['text'])} \\cf1 [{cls._hms(p['end'])}]\\cf0\\par")
        parts.append("}")
        output_path.write_text("\n".join(parts), encoding="utf-8")

    @classmethod
    def write_txt(cls, segments: List[Dict], output_path: Path) -> None:
        out_lines = []
        last_speaker = None
        for seg in segments:
            text = (seg.get("text") or "").strip()
            if is_blank_text(text):
                continue
            speaker = seg.get("speaker")
            if speaker:
                if speaker != last_speaker:
                    out_lines.append(f"\n[{speaker}]")
                    last_speaker = speaker
                out_lines.append(text)
            else:
                out_lines.append(text)
        output_path.write_text("\n".join(out_lines).lstrip() + "\n", encoding="utf-8")

    @classmethod
    def analyze_confidence(
        cls,
        result_data: Dict,
        high_thresh: float = 0.85,
        med_thresh: float = 0.60,
        poor_thresh: float = 0.40,
    ) -> Dict:
        """Per-word / per-segment confidence summary used by the RTF export and the transcript editor."""
        segments = result_data.get("segments", [])
        all_probs: List[float] = []
        high_count = med_count = low_count = poor_count = 0

        enriched_segments = []
        for seg in segments:
            words = seg.get("words") or []
            seg_probs = []
            if words:
                for w in words:
                    p = float(w.get("probability", w.get("prob", 0.85)))
                    seg_probs.append(p)
                    all_probs.append(p)
                    if p >= high_thresh:
                        high_count += 1
                    elif p >= med_thresh:
                        med_count += 1
                    elif p >= poor_thresh:
                        low_count += 1
                    else:
                        poor_count += 1
            else:
                avg_lp = float(seg.get("avg_logprob", -0.25))
                est_p = max(0.0, min(1.0, math.exp(avg_lp)))
                seg_probs.append(est_p)
                all_probs.append(est_p)

            enriched_segments.append(
                {
                    "start": float(seg.get("start", 0.0)),
                    "end": float(seg.get("end", 0.0)),
                    "text": (seg.get("text") or "").strip(),
                    "speaker": seg.get("speaker"),
                    "words": words,
                    "ci": cls.compute_confidence_interval(seg_probs),
                }
            )

        doc_mean, doc_low, doc_high, doc_margin = cls.compute_confidence_interval(all_probs)
        return {
            "overall_ci": (doc_mean, doc_low, doc_high, doc_margin),
            "counts": {"high": high_count, "medium": med_count, "low": low_count, "poor": poor_count},
            "segments": enriched_segments,
            "high_thresh": high_thresh,
            "med_thresh": med_thresh,
            "poor_thresh": poor_thresh,
            "diarization_method": result_data.get("diarization_method", "Disabled"),
        }

    @classmethod
    def write_rtf_with_confidence(
        cls,
        result_data: Dict,
        output_path: Path,
        source_name: str,
        model_name: str,
        high_thresh: float = 0.85,
        med_thresh: float = 0.60,
        poor_thresh: float = 0.40,
    ) -> Dict:
        report = cls.analyze_confidence(result_data, high_thresh, med_thresh, poor_thresh)
        doc_mean, doc_low, doc_high, doc_margin = report["overall_ci"]
        c = report["counts"]
        high_count, med_count, low_count, poor_count = c["high"], c["medium"], c["low"], c["poor"]
        total_words = max(1, high_count + med_count + low_count + poor_count)
        diarization_method = report["diarization_method"]
        backend_label = result_data.get("backend_label") or "Apple Silicon"

        # Color table: cf1=Slate, cf2=Green, cf3=Amber, cf4=Orange, cf5=Red, cf6=Grey, cf7=Blue, cf8=Purple
        rtf_parts = [
            r"{\rtf1\ansi\deff0",
            r"{\fonttbl{\f0\fswiss Helvetica Neue;}{\f1\fmodern Menlo;}}",
            r"{\colortbl;"
            r"\red30\green41\blue59;"
            r"\red21\green128\blue61;"
            r"\red180\green83\blue9;"
            r"\red217\green119\blue6;"
            r"\red220\green38\blue38;"
            r"\red100\green116\blue139;"
            r"\red37\green99\blue235;"
            r"\red124\green58\blue237;}",
            r"\viewkind4\uc1\pard\sa160\sl276\slmult1",
            r"\f0\fs32\b\cf1 Whisper Transcription, Diarization & Confidence Report\b0\fs22\par",
            f"\\cf6\\b Source File:\\b0  {cls._escape_rtf(source_name)}\\line "
            f"\\b Whisper Model:\\b0  {cls._escape_rtf(model_name)} ({cls._escape_rtf(backend_label)})\\line "
            f"\\b Speaker Diarization:\\b0  {cls._escape_rtf(diarization_method)}\\line "
            f"\\b Generated:\\b0  {cls._escape_rtf(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}\\line "
            f"\\b Overall 95% Confidence Interval:\\b0  "
            f"\\cf7\\b {doc_mean * 100:.1f}% \\u177? {doc_margin * 100:.1f}% "
            f"[{doc_low * 100:.1f}% - {doc_high * 100:.1f}%]\\cf6\\b0\\par",
            r"\pard\sa180\sl260\slmult1\f0\fs20"
            f"\\cf1\\b Confidence Color Legend:\\b0\\line "
            f"\\cf2\\b [HIGH >= {int(high_thresh * 100)}%]\\b0  ({high_count} words / {high_count * 100 / total_words:.1f}%)   "
            f"\\cf3\\b [MEDIUM {int(med_thresh * 100)}-{int(high_thresh * 100)}%]\\b0  ({med_count} words / {med_count * 100 / total_words:.1f}%)   "
            f"\\cf4\\b [LOW {int(poor_thresh * 100)}-{int(med_thresh * 100)}%]\\b0  ({low_count} words / {low_count * 100 / total_words:.1f}%)   "
            f"\\cf5\\ul\\b [POOR < {int(poor_thresh * 100)}%]\\b0\\ulnone  ({poor_count} words / {poor_count * 100 / total_words:.1f}%)\\par",
            r"\pard\sa200\sl276\slmult1",
        ]

        for seg in report["segments"]:
            t_start = cls.format_timestamp(seg["start"], srt_style=False)
            t_end = cls.format_timestamp(seg["end"], srt_style=False)
            s_mean, s_low, s_high, s_margin = seg["ci"]
            speaker = seg.get("speaker")
            spk_rtf = f"\\cf8\\b [{cls._escape_rtf(speaker)}]\\b0  " if speaker else ""

            if s_mean >= high_thresh:
                ci_color = r"\cf2"
            elif s_mean >= med_thresh:
                ci_color = r"\cf3"
            elif s_mean >= poor_thresh:
                ci_color = r"\cf4"
            else:
                ci_color = r"\cf5"

            rtf_parts.append(
                f"\\f1\\fs18 {spk_rtf}\\cf6 [{t_start} -> {t_end}] "
                f"{ci_color}\\b (95% CI: {s_mean * 100:.1f}% \\u177? {s_margin * 100:.1f}% "
                f"[{s_low * 100:.0f}%-{s_high * 100:.0f}%])\\b0\\cf1\\f0\\fs22\\line "
            )

            words = seg["words"]
            if words:
                word_tokens_rtf = []
                for w in words:
                    escaped = cls._escape_rtf(str(w.get("word", "")))
                    prob = float(w.get("probability", w.get("prob", 0.85)))
                    if prob >= high_thresh:
                        word_tokens_rtf.append(f"\\cf2 {escaped}\\cf1")
                    elif prob >= med_thresh:
                        word_tokens_rtf.append(f"\\cf3 {escaped}\\cf1")
                    elif prob >= poor_thresh:
                        word_tokens_rtf.append(f"\\cf4 {escaped}\\cf1")
                    else:
                        word_tokens_rtf.append(
                            f"\\cf5\\ul\\b {escaped}\\b0\\ulnone\\cf1"
                        )
                rtf_parts.append("".join(word_tokens_rtf).lstrip() + r"\par")
            else:
                rtf_parts.append(f"{ci_color}{cls._escape_rtf(seg['text'])}\\cf1\\par")

        rtf_parts.append("}")
        output_path.write_text("\n".join(rtf_parts), encoding="utf-8")
        return report


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------
