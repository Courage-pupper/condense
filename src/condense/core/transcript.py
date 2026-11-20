"""The master record of one transcript (<name>.transcript.json)."""
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

from condense.core.exports import RTFConfidenceWriter
from condense.core.util import *  # noqa: F401,F403


class TranscriptDocument:
    """One transcript = one '<base>.transcript.json'. It holds the full Whisper result (word timings,
    probabilities, speakers), the export settings, how the transcript was produced (provenance) and an
    edit history. Every export (SRT/VTT/TXT/RTF) is regenerated from this file, so they never drift apart."""

    SCHEMA_VERSION = 1
    SUFFIX = ".transcript.json"
    DEFAULT_FORMATS = {"srt": True, "vtt": False, "txt": True, "rtf": True, "para_rtf": True, "para_txt": False}
    DEFAULT_THRESHOLDS = {"high": 0.85, "medium": 0.60, "poor": 0.40}

    def __init__(self, path: Path, data: Dict):
        self.path = Path(path)
        self.data = data

    # ----- construction / IO -----
    @classmethod
    def create(
        cls,
        out_dir: Path,
        base_name: str,
        whisper_result: Dict,
        model_name: str,
        export_formats: Dict[str, bool],
        thresholds: Dict[str, float],
        media: Dict[str, Optional[str]],
        provenance: Dict,
    ) -> "TranscriptDocument":
        path = Path(out_dir) / f"{base_name}{cls.SUFFIX}"
        doc = cls(
            path,
            {
                "schema": cls.SCHEMA_VERSION,
                "base_name": base_name,
                "created": now_iso(),
                "updated": now_iso(),
                "model": model_name,
                "export_formats": dict(cls.DEFAULT_FORMATS, **export_formats),
                "confidence_thresholds": dict(cls.DEFAULT_THRESHOLDS, **thresholds),
                "media": {},
                "provenance": provenance,
                "edit_history": [],
                "whisper_result": whisper_result,
            },
        )
        for key, value in media.items():
            doc.set_media(key, value)
        return doc

    @classmethod
    def load(cls, path: Path) -> "TranscriptDocument":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if "whisper_result" not in data:
            raise ValueError(f"{path.name} is not a pipeline transcript file.")
        if int(data.get("schema", 1)) > cls.SCHEMA_VERSION:
            raise ValueError(f"{path.name} was written by a newer version of the pipeline.")
        return cls(path, data)

    BACKUP_EVERY_S = 300  # at most one backup per 5 minutes of editing
    BACKUPS_KEPT = 30

    @property
    def backup_dir(self) -> Path:
        return self.out_dir / "_backups"

    def list_backups(self) -> List[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob(f"{self.base_name}.*{self.SUFFIX}"), key=lambda p: p.stat().st_mtime, reverse=True)

    def _maybe_backup(self) -> Optional[Path]:
        """Copy the file as it is on disk (before this save) into _backups/, throttled and pruned."""
        if not self.path.exists():
            return None
        existing = self.list_backups()
        if existing and time.time() - existing[0].stat().st_mtime < self.BACKUP_EVERY_S:
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        dst = self.backup_dir / f"{self.base_name}.{datetime.now().strftime('%Y%m%d-%H%M%S')}{self.SUFFIX}"
        shutil.copy2(self.path, dst)
        for old in self.list_backups()[self.BACKUPS_KEPT:]:
            try:
                old.unlink()
            except OSError:
                pass
        return dst

    def save(self, backup: bool = False) -> None:
        if backup:
            try:
                self._maybe_backup()
            except Exception:
                pass  # a failed backup must never block saving the work itself
        self.data["updated"] = now_iso()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written master file

    # ----- accessors -----
    @property
    def base_name(self) -> str:
        return self.data.get("base_name") or self.path.name[: -len(self.SUFFIX)]

    @property
    def out_dir(self) -> Path:
        return self.path.parent

    @property
    def result(self) -> Dict:
        return self.data["whisper_result"]

    @property
    def segments(self) -> List[Dict]:
        return self.result.setdefault("segments", [])

    @property
    def thresholds(self) -> Dict[str, float]:
        return dict(self.DEFAULT_THRESHOLDS, **(self.data.get("confidence_thresholds") or {}))

    def set_media(self, key: str, value: Optional[str]) -> None:
        """Store media paths relative to the transcript folder when possible, so the folder can be moved."""
        if not value:
            self.data["media"][key] = None
            return
        p = Path(value)
        self.data["media"][key] = os.path.relpath(p, self.out_dir) if is_within(p, self.out_dir) else str(p.resolve())

    def media_path(self, key: str) -> Optional[Path]:
        value = (self.data.get("media") or {}).get(key)
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else (self.out_dir / p)

    def roster(self) -> List[str]:
        """All speakers of this transcript, in a stable order: the saved list (including speakers you added
        who have no segments yet), then any others in order of first appearance."""
        names = [n for n in (self.data.get("speakers") or []) if n]
        for seg in self.segments:
            spk = seg.get("speaker")
            if spk and spk not in names:
                names.append(spk)
        return names

    def add_speaker(self, name: str) -> bool:
        name = name.strip()
        roster = self.roster()
        if not name or name in roster:
            return False
        self.data["speakers"] = roster + [name]
        self.log_edit("add_speaker", name=name)
        return True

    def remove_speaker(self, name: str) -> bool:
        """Only speakers without segments can be removed (reassign their segments first)."""
        if name in self.speakers():
            return False
        roster = self.roster()
        if name not in roster:
            return False
        self.data["speakers"] = [n for n in roster if n != name]
        self.log_edit("remove_speaker", name=name)
        return True

    def speakers(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for seg in self.segments:
            spk = seg.get("speaker")
            if spk:
                counts[spk] = counts.get(spk, 0) + 1
        return counts

    def export_paths(self) -> Dict[str, Path]:
        b, d = self.base_name, self.out_dir
        return {
            "srt": d / f"{b}.srt",
            "vtt": d / f"{b}.vtt",
            "txt": d / f"{b}.txt",
            "rtf": d / f"{b}_confidence.rtf",
            "para_rtf": d / f"{b}_paragraphs.rtf",
            "para_txt": d / f"{b}_paragraphs.txt",
        }

    # ----- edits -----
    def log_edit(self, action: str, **details) -> None:
        self.data.setdefault("edit_history", []).append({"at": now_iso(), "action": action, **details})

    def rename_speaker(self, old: str, new: str) -> int:
        count = 0
        for seg in self.segments:
            if seg.get("speaker") == old:
                seg["speaker"] = new
                count += 1
        if count:
            self.log_edit("rename_speaker", old=old, new=new, segments=count)
        return count

    def rename_speakers(self, mapping: Dict[str, str]) -> int:
        """Apply several renames at once (so swaps like A->B, B->A work). Returns segments changed."""
        mapping = {o: n for o, n in mapping.items() if n and n != o}
        if not mapping:
            return 0
        count = 0
        for seg in self.segments:
            spk = seg.get("speaker")
            if spk in mapping:
                seg["speaker"] = mapping[spk]
                count += 1
        roster = self.roster()
        new_roster: List[str] = []
        for n in roster:
            n = mapping.get(n, n)
            if n not in new_roster:
                new_roster.append(n)
        renamed_unused = any(o in roster and o not in self.speakers() for o in mapping)
        self.data["speakers"] = new_roster
        if count or renamed_unused:
            self.log_edit("rename_speakers", mapping=mapping, segments=count)
        return count or (1 if renamed_unused else 0)

    @staticmethod
    def _rebuild_text(seg: Dict) -> None:
        words = seg.get("words") or []
        if words:
            seg["text"] = "".join(str(w.get("word", "")) for w in words)
            seg["start"] = float(words[0].get("start", seg.get("start", 0.0)))
            seg["end"] = float(words[-1].get("end", seg.get("end", 0.0)))

    def edit_segment_text(self, index: int, new_text: str) -> bool:
        """Replace a segment's text. Words that are unchanged keep their Whisper timing and probability;
        changed/inserted words get timings interpolated inside the span they replace, probability 1.0
        (checked by a person) and "edited": true. Returns False when nothing changed."""
        import difflib

        seg = self.segments[index]
        old_text = (seg.get("text") or "").strip()
        tokens = re.findall(r"\S+", new_text)
        if " ".join(tokens) == " ".join(old_text.split()):
            return False
        old_words = seg.get("words") or []
        if not old_words:
            seg["text"] = " " + " ".join(tokens)
            seg["edited"] = True
            self.log_edit("edit_text", segment=index, old=old_text, new=" ".join(tokens))
            return True

        old_tokens = [str(w.get("word", "")).strip() for w in old_words]
        seg_start, seg_end = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        new_words: List[Dict] = []
        matcher = difflib.SequenceMatcher(a=old_tokens, b=tokens, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                new_words.extend(dict(old_words[i1 + k]) for k in range(i2 - i1))
                continue
            if j2 == j1:  # pure deletion
                continue
            if i2 > i1:
                t0, t1 = float(old_words[i1]["start"]), float(old_words[i2 - 1]["end"])
            else:  # pure insertion: fit between neighbours
                t0 = float(new_words[-1]["end"]) if new_words else seg_start
                t1 = float(old_words[i1]["start"]) if i1 < len(old_words) else seg_end
                if t1 < t0:
                    t1 = t0
            n = j2 - j1
            for k in range(n):
                new_words.append({
                    "word": " " + tokens[j1 + k],
                    "start": round(t0 + (t1 - t0) * k / n, 3),
                    "end": round(t0 + (t1 - t0) * (k + 1) / n, 3),
                    "probability": 1.0,
                    "edited": True,
                })
        if not new_words:
            return False
        seg["words"] = new_words
        self._rebuild_text(seg)
        seg["edited"] = True
        self.log_edit("edit_text", segment=index, old=old_text, new=seg["text"].strip())
        return True

    def split_segment(self, index: int, word_index: int) -> bool:
        """Split before word `word_index`: words[:k] stay, words[k:] become a new segment (same speaker)."""
        seg = self.segments[index]
        words = seg.get("words") or []
        if not (0 < word_index < len(words)):
            return False
        second = {k: v for k, v in seg.items() if k not in ("words", "text", "id")}
        second["words"] = [dict(w) for w in words[word_index:]]
        seg["words"] = words[:word_index]
        self._rebuild_text(seg)
        self._rebuild_text(second)
        self.segments.insert(index + 1, second)
        self.log_edit("split_segment", segment=index, at=second["start"])
        return True

    def insert_segment(self, start: float, end: float, text: str, speaker: Optional[str] = None) -> int:
        """Add speech the transcription missed. Words are spread evenly over [start, end], marked as typed by a
        person (probability 1.0, edited). Inserted in time order; returns the new segment's index."""
        tokens = re.findall(r"\S+", text or "")
        if not tokens:
            raise ValueError("The new segment needs some text.")
        start = max(0.0, float(start))
        end = max(start + 0.1, float(end))
        n = len(tokens)
        words = [{"word": " " + t, "start": round(start + (end - start) * k / n, 3), "end": round(start + (end - start) * (k + 1) / n, 3),
                  "probability": 1.0, "edited": True} for k, t in enumerate(tokens)]
        seg: Dict = {"start": round(start, 3), "end": round(end, 3), "text": " " + " ".join(tokens), "words": words, "edited": True, "inserted": True}
        speaker = (speaker or "").strip()
        if speaker:
            seg["speaker"] = speaker
            seg["speaker_manual"] = True
            roster = self.roster()
            if speaker not in roster:
                self.data["speakers"] = roster + [speaker]
        idx = next((i for i, sg in enumerate(self.segments) if float(sg.get("start", 0.0)) > start), len(self.segments))
        self.segments.insert(idx, seg)
        self.log_edit("insert_segment", segment=idx, start=seg["start"], end=seg["end"], text=seg["text"].strip(), speaker=speaker or None)
        return idx

    def sync_text_with_words(self) -> int:
        changed = sync_text_with_words(self.segments)
        if changed:
            self.log_edit("sync_text_with_words", segments=changed,
                          note="segment text replaced by its timed words; Whisper's differing text kept as text_whisper")
        return changed

    def set_checked(self, index: int, checked: bool = True) -> bool:
        seg = self.segments[index]
        if bool(seg.get("checked")) == bool(checked):
            return False
        if checked:
            seg["checked"] = True
            seg["checked_at"] = now_iso()
        else:
            seg.pop("checked", None)
            seg.pop("checked_at", None)
        return True

    def checked_fraction(self) -> float:
        """Share of speech (by duration) in segments marked as checked."""
        total = done = 0.0
        for seg in self.segments:
            dur = max(0.1, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
            total += dur
            if seg.get("checked"):
                done += dur
        return done / total if total else 0.0

    def replace_range(self, start: float, end: float, new_segments: List[Dict], info: Dict) -> Tuple[int, int]:
        """Replace the segments whose midpoint lies in [start, end] by new_segments (already on this timeline).
        New segments take the speaker they overlap most among the replaced ones (unless info['speaker'] forces one)."""
        mid = lambda sg: (float(sg.get("start", 0.0)) + float(sg.get("end", 0.0))) / 2
        idx = [i for i, sg in enumerate(self.segments) if start <= mid(sg) <= end]
        old = [self.segments[i] for i in idx]
        forced = (info.get("speaker") or "").strip()
        for sg in new_segments:
            if forced:
                sg["speaker"], sg["speaker_manual"] = forced, True
            elif old:
                def overlap(o):
                    return min(float(o["end"]), float(sg["end"])) - max(float(o["start"]), float(sg["start"]))
                best = max(old, key=overlap)
                if best.get("speaker"):
                    sg["speaker"] = best["speaker"]
                    if best.get("speaker_manual"):
                        sg["speaker_manual"] = True
            sg["retranscribed"] = {k: info.get(k) for k in ("model", "language", "task", "backend")}
            sg["retranscribed"]["at"] = now_iso()
        at = idx[0] if idx else next((i for i, sg in enumerate(self.segments) if float(sg.get("start", 0.0)) > start), len(self.segments))
        for i in reversed(idx):
            del self.segments[i]
        self.segments[at:at] = new_segments
        if forced and forced not in self.roster():
            self.data["speakers"] = self.roster() + [forced]
        self.log_edit("retranscribe", start=round(start, 3), end=round(end, 3), **{k: info.get(k) for k in ("model", "language", "task", "backend")},
                      old_text=[(o.get("text") or "").strip() for o in old], new_text=[(n.get("text") or "").strip() for n in new_segments])
        return len(old), len(new_segments)

    def apply_speaker_labels(self, labels: List[Optional[str]], keep_manual: bool, info: Dict) -> Dict[str, int]:
        """Apply new speaker labels (one per segment, same order). New labels are mapped onto the names already in
        use wherever they overlap most, so 'Robin' stays 'Robin'; hand-assigned speakers are kept if asked."""
        from collections import defaultdict

        overlap: Dict[Tuple[str, str], float] = defaultdict(float)
        for seg, lab in zip(self.segments, labels):
            if lab and seg.get("speaker"):
                overlap[(lab, seg["speaker"])] += max(0.1, float(seg.get("end", 0)) - float(seg.get("start", 0)))
        mapping: Dict[str, str] = {}
        used = set()
        for (lab, name), _w in sorted(overlap.items(), key=lambda kv: -kv[1]):
            if lab not in mapping and name not in used:
                mapping[lab] = name
                used.add(name)
        taken = set(self.roster()) | used
        for lab in sorted({l for l in labels if l}):
            if lab not in mapping:
                name, k = lab, 2
                while name in taken:
                    name, k = f"{lab}_{k}", k + 1
                mapping[lab] = name
                taken.add(name)
        changed = kept = 0
        for seg, lab in zip(self.segments, labels):
            if not lab:
                continue
            if keep_manual and seg.get("speaker_manual"):
                kept += 1
                continue
            name = mapping[lab]
            if seg.get("speaker") != name:
                seg["speaker"] = name
                seg.pop("speaker_manual", None)
                changed += 1
        roster = self.roster()
        self.data["speakers"] = roster
        self.log_edit("rerun_speaker_detection", method=info.get("method"), num_speakers=info.get("num_speakers"), changed=changed,
                      kept_manual=kept, mapping=mapping)
        return {"changed": changed, "kept": kept, "speakers": len(set(mapping.values()))}

    def remove_empty_segments(self) -> int:
        kept, removed = drop_empty_segments(self.segments)
        if removed:
            self.result["segments"] = kept
            self.log_edit("remove_empty_segments", count=removed)
        return removed

    def delete_segment(self, index: int) -> bool:
        if not (0 <= index < len(self.segments)):
            return False
        seg = self.segments.pop(index)
        self.log_edit("delete_segment", segment=index, start=seg.get("start"), text=(seg.get("text") or "").strip(), speaker=seg.get("speaker"))
        return True

    def merge_with_next(self, index: int) -> bool:
        if not (0 <= index < len(self.segments) - 1):
            return False
        a, b = self.segments[index], self.segments[index + 1]
        if (a.get("words") or []) and (b.get("words") or []):
            a["words"] = list(a["words"]) + list(b["words"])
            self._rebuild_text(a)
        else:
            a["text"] = " " + " ".join(x for x in ((a.get("text") or "").strip(), (b.get("text") or "").strip()) if x)
            a["end"] = b.get("end", a.get("end"))
        if a.get("speaker") != b.get("speaker"):
            a["speaker_merged_from"] = b.get("speaker")
        del self.segments[index + 1]
        self.log_edit("merge_segments", segment=index, merged_speaker=b.get("speaker"))
        return True

    def set_segment_speaker(self, index: int, speaker: str) -> bool:
        seg = self.segments[index]
        speaker = speaker.strip()
        if not speaker or seg.get("speaker") == speaker:
            return False
        self.log_edit("set_speaker", segment=index, old=seg.get("speaker"), new=speaker)
        roster = self.roster()  # keep the speaker being replaced in the list, even if it now has no segments
        seg["speaker"] = speaker
        seg["speaker_manual"] = True
        if speaker not in roster:
            roster.append(speaker)
        self.data["speakers"] = roster
        return True

    def paragraphs(self) -> List[Dict]:
        """Speaker turns: consecutive segments by the same speaker joined into one paragraph."""
        out: List[Dict] = []
        for i, seg in enumerate(self.segments):
            text = (seg.get("text") or "").strip()
            if is_blank_text(text):
                continue
            spk = seg.get("speaker")
            if out and out[-1]["speaker"] == spk:
                out[-1]["text"] += " " + text
                out[-1]["end"] = float(seg.get("end", out[-1]["end"]))
                out[-1]["segments"].append(i)
            else:
                out.append({"speaker": spk, "start": float(seg.get("start", 0.0)), "end": float(seg.get("end", 0.0)), "text": text, "segments": [i]})
        return out

    # ----- exports -----
    def confidence_report(self) -> Dict:
        t = self.thresholds
        return RTFConfidenceWriter.analyze_confidence(self.result, t["high"], t["medium"], t["poor"])

    def export_all(self) -> Dict[str, Path]:
        formats = dict(self.DEFAULT_FORMATS, **(self.data.get("export_formats") or {}))
        paths = self.export_paths()
        segs = self.segments
        created: Dict[str, Path] = {}
        if formats.get("srt"):
            RTFConfidenceWriter.write_srt(segs, paths["srt"])
            created["srt"] = paths["srt"]
        if formats.get("vtt"):
            RTFConfidenceWriter.write_vtt(segs, paths["vtt"])
            created["vtt"] = paths["vtt"]
        if formats.get("txt"):
            RTFConfidenceWriter.write_txt(segs, paths["txt"])
            created["txt"] = paths["txt"]
        if formats.get("rtf"):
            t = self.thresholds
            sources = (self.data.get("provenance") or {}).get("inputs") or []
            source_name = Path(sources[0]["path"]).name if sources else self.base_name
            RTFConfidenceWriter.write_rtf_with_confidence(
                self.result, paths["rtf"], source_name, self.data.get("model", ""), t["high"], t["medium"], t["poor"]
            )
            created["rtf"] = paths["rtf"]
        if formats.get("para_rtf") or formats.get("para_txt"):
            paras = self.paragraphs()
            if formats.get("para_rtf"):
                RTFConfidenceWriter.write_paragraphs_rtf(paras, paths["para_rtf"], self.base_name)
                created["para_rtf"] = paths["para_rtf"]
            if formats.get("para_txt"):
                RTFConfidenceWriter.write_paragraphs_txt(paras, paths["para_txt"])
                created["para_txt"] = paths["para_txt"]
        return created
