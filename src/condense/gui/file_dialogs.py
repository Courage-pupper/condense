"""File and folder pickers.

macOS and Windows: Tk's dialogs already are the system's own. Linux: Tk's are the old Motif-style ones (no sidebar,
no drives), so Condense asks the desktop's file chooser instead — GTK through `zenity` (GNOME, Ubuntu, Mint …) or
KDE's through `kdialog` — with external drives, bookmarks, recent places and search. If neither is installed, or the
native dialog fails, Tk's dialog is used. CONDENSE_FILE_DIALOG=tk|zenity|kdialog chooses explicitly.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path
from tkinter import filedialog
from typing import List, Optional, Sequence, Tuple

from condense.platform import PLATFORM


def native_backend() -> str:
    forced = os.environ.get("CONDENSE_FILE_DIALOG", "").strip().lower()
    if forced == "tk":
        return "tk"
    if forced in ("zenity", "kdialog"):
        return forced if shutil.which(forced) else "tk"
    if PLATFORM != "linux":
        return "tk"
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")).lower()
    if "kde" in desktop and shutil.which("kdialog"):
        return "kdialog"
    if shutil.which("zenity"):
        return "zenity"
    if shutil.which("kdialog"):
        return "kdialog"
    return "tk"


def _patterns(filetypes: Sequence[Tuple[str, str]]) -> List[Tuple[str, List[str]]]:
    """Tk filetypes -> [(label, patterns)] with upper-case variants (cameras write .MP4; GTK filters are case-sensitive)."""
    out = []
    for label, pats in filetypes or []:
        items: List[str] = []
        for p in str(pats).split():
            p = "*" if p in ("*.*", "*") else p
            for v in (p, p.upper()) if p != "*" else (p,):
                if v not in items:
                    items.append(v)
        out.append((label, items))
    return out


def _start_dir(initialdir: Optional[str]) -> str:
    """The folder to open in: the given one if it still exists (an external drive may have been unplugged), else home."""
    if initialdir and Path(initialdir).is_dir():
        return str(initialdir)
    return str(Path.home())


def native_command(backend: str, kind: str, title: str, initialdir: Optional[str],
                   filetypes: Sequence[Tuple[str, str]] = ()) -> List[str]:
    """kind: 'files' (several), 'file' (one) or 'dir'."""
    start = _start_dir(initialdir)
    if backend == "zenity":
        cmd = ["zenity", "--file-selection", f"--title={title}", "--modal", f"--filename={start.rstrip('/')}/"]
        if kind == "dir":
            cmd.append("--directory")
        if kind == "files":
            cmd += ["--multiple", "--separator=\n"]
        if kind != "dir":
            cmd += [f"--file-filter={label} | {' '.join(pats)}" for label, pats in _patterns(filetypes)]
        return cmd
    # kdialog
    if kind == "dir":
        return ["kdialog", "--title", title, "--getexistingdirectory", start]
    filters = "\n".join(f"{' '.join(pats)}|{label}" for label, pats in _patterns(filetypes)) or "*"
    cmd = ["kdialog", "--title", title, "--getopenfilename", start, filters]
    if kind == "files":
        cmd += ["--multiple", "--separate-output"]
    return cmd


class FileDialogsMixin:
    def _run_native_dialog(self, cmd: List[str]) -> Optional[str]:
        """Run the desktop's chooser while keeping this window alive (redrawing, queue progress). Returns its output,
        '' if cancelled, or None if it could not be used (then Tk's dialog is shown)."""
        if self.__dict__.get("_native_dialog_open"):
            return ""
        self._native_dialog_open = True
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace")
            while proc.poll() is None:
                try:
                    self.update()
                except Exception:
                    pass
                time.sleep(0.03)
            out, err = proc.communicate()
            if proc.returncode == 0:
                return out
            if proc.returncode == 1:  # Cancel / closed (zenity and kdialog)
                return ""
            self._append_log(f"The {cmd[0]} file dialog failed ({(err or '').strip()[:200] or proc.returncode}); using the basic one.", "WARN")
            return None
        except OSError:
            return None
        finally:
            self._native_dialog_open = False

    def _ask_open_files(self, title: str, initialdir: Optional[str], filetypes) -> Tuple[str, ...]:
        backend = native_backend()
        if backend != "tk":
            out = self._run_native_dialog(native_command(backend, "files", title, initialdir, filetypes))
            if out is not None:
                return tuple(line.strip() for line in out.splitlines() if line.strip())
        return tuple(filedialog.askopenfilenames(title=title, initialdir=_start_dir(initialdir), filetypes=filetypes) or ())

    def _ask_open_file(self, title: str, initialdir: Optional[str], filetypes) -> str:
        backend = native_backend()
        if backend != "tk":
            out = self._run_native_dialog(native_command(backend, "file", title, initialdir, filetypes))
            if out is not None:
                return out.strip().splitlines()[0] if out.strip() else ""
        return filedialog.askopenfilename(title=title, initialdir=_start_dir(initialdir), filetypes=filetypes) or ""

    def _ask_directory(self, title: str, initialdir: Optional[str]) -> str:
        backend = native_backend()
        if backend != "tk":
            out = self._run_native_dialog(native_command(backend, "dir", title, initialdir))
            if out is not None:
                return out.strip().splitlines()[0] if out.strip() else ""
        return filedialog.askdirectory(title=title, initialdir=_start_dir(initialdir)) or ""
