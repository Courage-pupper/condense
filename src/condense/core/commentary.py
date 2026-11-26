"""Commentary over time: what you wrote about a recording — comments, coding memos, segment notes — laid out along its
timeline in blocks of a few minutes (5 by default), as a spreadsheet with one video frame per block.

For thematic analysis this is a description grid: every block of the session gets a row, also the ones without a
comment yet (to fill in, or to see where nothing was written), and each comment sits in the block where it starts
with its own start and end (a comment may cover a few seconds or a quarter of an hour). For a project, all its
transcripts go into one sheet, with their Day, Group, Type and attributes as columns to filter by (and, if chosen,
a sheet per transcript as well).
"""
import csv
import io
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from condense.core import coding as cod

KINDS = {"comment": "Comment", "memo": "Coding memo", "note": "Note"}
BLOCK_MINUTES = (1, 2, 5, 10, 15, 30)
FRAME_W, FRAME_H = 192, 108          # pixels in the sheet
COLUMNS = ["Block", "Frame", "Start", "End", "Kind", "Code", "Speaker", "Passage", "Comment"]


def hms(t: float) -> str:
    t = max(0, int(round(float(t))))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}"


def duration_of(doc) -> float:
    ends = [float(s.get("end", 0.0)) for s in doc.segments[-30:]] or [0.0]
    return max([max(ends)] + [float(c.get("end", 0.0)) for c in cod.codings(doc)])


def items(doc, cb: Optional["cod.Codebook"] = None, include: Iterable[str] = tuple(KINDS)) -> List[Dict]:
    """Everything written about the recording, by time: comments, memos of codings, notes of segments."""
    include = set(include)
    out: List[Dict] = []
    for r in cod.resolve(doc):
        is_comment = r["code"] == cod.COMMENT
        if is_comment and "comment" not in include:
            continue
        if not is_comment and ("memo" not in include or not r.get("memo")):
            continue
        code = "" if is_comment else (cb.path_name(r["code"]) if cb is not None and cb.get(r["code"]) else str(r["code"]))
        out.append({"kind": KINDS["comment" if is_comment else "memo"], "start": float(r["start"]), "end": float(r["end"]),
                    "code": code, "speakers": ", ".join(cod.speakers_of(doc, r)),
                    "passage": "" if r.get("kind") == "time" or r.get("orphan") else cod.coded_text(doc, r),
                    "text": r.get("memo", ""), "by_time": r.get("kind") == "time"})
    if "note" in include:
        for seg in doc.segments:
            if seg.get("note"):
                out.append({"kind": KINDS["note"], "start": float(seg.get("start", 0.0)), "end": float(seg.get("end", 0.0)),
                            "code": "", "speakers": seg.get("speaker") or "", "passage": str(seg.get("text", "")).strip(),
                            "text": seg["note"], "by_time": False})
    return sorted(out, key=lambda it: (it["start"], it["end"], it["kind"]))


def blocks(duration: float, minutes: float) -> List[Tuple[float, float]]:
    step = max(10.0, float(minutes) * 60)
    out, a = [], 0.0
    while a < duration - 1e-6 or not out:
        out.append((a, min(a + step, max(duration, a + 1e-3))))
        a += step
    return out


def table(doc, minutes: float = 5, cb=None, include: Iterable[str] = tuple(KINDS)) -> List[Dict]:
    """The rows of one recording: per block its items (each in the block where it starts), or one empty row."""
    its = items(doc, cb, include)
    dur = max(duration_of(doc), max((it["end"] for it in its), default=0.0))
    bl = blocks(dur, minutes)
    rows: List[Dict] = []
    k = 0
    for bi, (a, b) in enumerate(bl):
        last = bi == len(bl) - 1
        inside = []
        while k < len(its) and (its[k]["start"] < b or last):
            inside.append(its[k])
            k += 1
        base = {"block": bi, "block_start": a, "block_end": b, "block_label": f"{hms(a)} – {hms(b)}"}
        if not inside:
            rows.append(dict(base, first=True, kind="", start=None, end=None, code="", speakers="", passage="", text=""))
        for j, it in enumerate(inside):
            rows.append(dict(base, first=j == 0, **it))
    return rows


def frame_jpeg(doc, t: float, width: int = FRAME_W, height: int = FRAME_H) -> Optional[bytes]:
    """The video frame at transcript time t as a small JPEG (None without a video or ffmpeg)."""
    from condense.core import videolink
    media = doc.find_video()
    if media is None or not shutil.which("ffmpeg"):
        return None
    pieces = doc.video_pieces() if doc.media_path("video") else None
    vt, _ = videolink.to_video_time(pieces, t)
    if vt is None:
        return None
    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{vt:.2f}", "-i", str(media),
                              "-an", "-sn", "-frames:v", "1", "-vf", vf, "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                             capture_output=True, timeout=30)
    except Exception:
        return None
    return res.stdout or None


def _safe_text(v):
    """Text that starts like a formula stays text (in Excel and in a CSV opened by it)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def _session_cells(sess: Dict, extra: Sequence[str]) -> List:
    lv = dict(sess.get("levels") or [])
    return [lv.get(n, "") for n in sess.get("level_names") or []] + [sess.get("type", ""), sess.get("name", "")] + \
        [(sess.get("attributes") or {}).get(a, "") for a in extra]


def _row_cells(r: Dict) -> List:
    return [r["block_label"], None, hms(r["start"]) if r.get("start") is not None else "",
            hms(r["end"]) if r.get("end") is not None else "", r.get("kind", ""), r.get("code", ""), r.get("speakers", ""),
            r.get("passage", ""), r.get("text", "")]


def export(sessions: Sequence[Dict], target: Path, minutes: float = 5, cb=None, include: Iterable[str] = tuple(KINDS),
           frames: bool = True, per_sheet: bool = False, attributes: Sequence[str] = (),
           progress: Optional[Callable[[str], None]] = None, stop=None) -> Dict:
    """Write the commentary of sessions to target (.xlsx, or .csv: no frames). A session: {"path" or "doc", "name",
    "type", "levels": [(level, value)], "level_names", "attributes"}. Returns {"rows", "frames", "sessions", "path"}."""
    from condense.core.transcript import TranscriptDocument
    target = Path(target)
    include = tuple(include)
    level_names: List[str] = []
    for s in sessions:
        for n, _v in s.get("levels") or []:
            if n not in level_names:
                level_names.append(n)
    head = level_names + ["Type", "Session"] + list(attributes) + COLUMNS
    as_csv = target.suffix.lower() == ".csv"
    if as_csv:
        head = [h for h in head if h != "Frame"]
    stats = {"rows": 0, "frames": 0, "sessions": 0, "path": target}
    tables = []
    for n, s in enumerate(sessions):
        if stop is not None and stop.is_set():
            break
        doc = s.get("doc") or TranscriptDocument.load(Path(s["path"]))
        if progress:
            progress(f"Reading {n + 1}/{len(sessions)}: {s.get('name') or doc.base_name}")
        s = dict(s, level_names=level_names, name=s.get("name") or doc.base_name)
        tables.append((s, doc, table(doc, minutes, cb, include)))
    if as_csv:
        with open(target, "w", newline="", encoding="utf-8-sig") as fh:  # (BOM: Excel reads umlauts right)
            w = csv.writer(fh)
            w.writerow(head)
            for s, _doc, rows in tables:
                for r in rows:
                    cells = _session_cells(s, attributes) + _row_cells(r)
                    del cells[len(cells) - len(COLUMNS) + 1]  # no Frame column
                    w.writerow([_safe_text(c) for c in cells])
                    stats["rows"] += 1
                stats["sessions"] += 1
        return stats
    return _write_xlsx(tables, target, head, attributes, frames, per_sheet, progress, stop, stats)


def _write_xlsx(tables, target: Path, head, attributes, frames: bool, per_sheet: bool, progress, stop, stats) -> Dict:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    try:
        from openpyxl.drawing.image import Image as XLImage
        import PIL  # noqa: F401  (openpyxl needs Pillow for pictures)
    except Exception:
        XLImage = None
    wb = Workbook()
    sheets = [("Commentary", tables)]
    if per_sheet and len(tables) > 1:
        used = {"Commentary"}
        for s, doc, rows in tables:
            name = "".join(ch for ch in s["name"] if ch not in '[]:*?/\\')[:28] or "Session"
            base, k = name, 2
            while name in used:
                name = f"{base[:25]} {k}"
                k += 1
            used.add(name)
            sheets.append((name, [(s, doc, rows)]))
    frame_col = head.index("Frame") + 1
    widths = {"Block": 19, "Frame": 28, "Start": 9, "End": 9, "Kind": 12, "Code": 22, "Speaker": 16, "Passage": 50,
              "Comment": 60, "Session": 26, "Type": 11}
    cache: Dict[Tuple[str, float], Optional[bytes]] = {}
    n_frames_total = sum(len({r["block"] for r in rows}) for _s, _d, rows in tables) if frames and XLImage else 0
    done = 0
    shade = PatternFill("solid", fgColor="F3F4F6")
    for si, (title, part) in enumerate(sheets):
        ws = wb.active if si == 0 else wb.create_sheet()
        ws.title = title
        ws.append(head)
        for c in ws[1]:
            c.font = Font(bold=True)
        for k, h in enumerate(head, start=1):
            ws.column_dimensions[get_column_letter(k)].width = widths.get(h, 14)
        ws.freeze_panes = "A2"
        ws.page_setup.orientation = "landscape"  # printed: one page wide, as many long as it takes
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.print_title_rows = "1:1"
        row_no = 1
        for s, doc, rows in part:
            for r in rows:
                if stop is not None and stop.is_set():
                    break
                row_no += 1
                cells = _session_cells(s, attributes) + _row_cells(r)
                for k, v in enumerate(cells, start=1):
                    cell = ws.cell(row=row_no, column=k, value=v)
                    if isinstance(v, str) and v[:1] == "=":
                        cell.data_type = "s"  # a comment that starts with "=" is text, not a formula
                    cell.alignment = Alignment(vertical="top", wrap_text=head[k - 1] in ("Passage", "Comment", "Code"))
                    if r["block"] % 2:
                        cell.fill = shade  # every other block shaded: the blocks are easy to tell apart
                if r["first"] and frames and XLImage is not None:
                    key = (str(doc.path), r["block_start"])
                    if key not in cache:
                        mid = (r["block_start"] + r["block_end"]) / 2
                        cache[key] = frame_jpeg(doc, mid)
                        done += 1
                        if progress and (done % 5 == 0 or done == n_frames_total):
                            progress(f"Video frames {done}/{n_frames_total}")
                    data = cache[key]
                    if data:
                        img = XLImage(io.BytesIO(data))
                        img.width, img.height = FRAME_W, FRAME_H
                        ws.add_image(img, f"{get_column_letter(frame_col)}{row_no}")
                        ws.row_dimensions[row_no].height = FRAME_H * 0.75 + 4  # points
                        if si == 0:
                            stats["frames"] += 1
                if si == 0:
                    stats["rows"] += 1
            if si == 0:
                stats["sessions"] += 1
        ws.auto_filter.ref = f"A1:{get_column_letter(len(head))}{max(1, row_no)}"
    if progress:
        progress("Saving the spreadsheet…")
    wb.save(target)
    return stats


def can_write_xlsx() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except Exception:
        return False
