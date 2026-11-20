"""Colours, fonts, markers and other GUI constants."""
import sys
from pathlib import Path

THEME = {
    "bg_root": "#0f1117",
    "bg_panel": "#171a22",
    "bg_card": "#1e222c",
    "bg_input": "#11141b",
    "fg_primary": "#e8eaef",
    "fg_secondary": "#9aa3b2",
    "fg_dim": "#6b7280",
    "accent": "#e11d48",
    "accent_hover": "#be123c",
    "ok": "#10b981",
    "warn": "#f59e0b",
    "err": "#ef4444",
    "border": "#2a3040",
}

# Fonts follow the real OS (missing families fall back to Tk's default, which is not monospaced on Linux)
if sys.platform == "darwin":
    FONT_UI, FONT_MONO = "Helvetica Neue", "Menlo"
elif sys.platform.startswith("win"):
    FONT_UI, FONT_MONO = "Segoe UI", "Consolas"
else:
    FONT_UI, FONT_MONO = "DejaVu Sans", "DejaVu Sans Mono"  # installed on Ubuntu/Debian/Fedora by default
# name -> (family, base size, weight). Every widget uses these named fonts, so View > Larger/Smaller Text
# (⌘+ / ⌘− / ⌘0) resizes the whole window live.
FONT_SPECS = {
    "body": (FONT_UI, 10, "normal"),
    "body_bold": (FONT_UI, 10, "bold"),
    "small": (FONT_UI, 9, "normal"),
    "small_bold": (FONT_UI, 9, "bold"),
    "title": (FONT_UI, 13, "bold"),
    "section": (FONT_UI, 9, "bold"),
    "mono": (FONT_MONO, 10, "normal"),
    "mono_small": (FONT_MONO, 9, "normal"),
    "mono_small_bold": (FONT_MONO, 9, "bold"),
    "transcript": (FONT_UI, 12, "normal"),
    "marker": (FONT_UI, 12, "normal"),
}
FONT_SLANTS = {"marker": "italic"}

# Transcription markers: (menu label, text, Ctrl+Shift key). {t} = current playback time.
MARKERS = [
    ("Laughter", "[laughs]", "L"),
    ("Inaudible (with time)", "[inaudible {t}]", "I"),
    ("Unclear word", "[unclear: ]", "U"),
    ("Crosstalk", "[crosstalk]", "C"),
    ("Pause", "[pause]", "P"),
    ("German", "[German]", "G"),
    ("Non-verbal / gesture", "[gestures]", "N"),
]
SPEAKER_COLORS = ["#a78bfa", "#38bdf8", "#f472b6", "#34d399", "#fbbf24", "#60a5fa", "#fb7185", "#a3e635"]

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".ts", ".m4v", ".mts"}
MEDIA_FILETYPES = [("Video Files", "*.mp4 *.mkv *.mov *.webm *.avi *.flv *.ts *.m4v *.mts"), ("All Files", "*.*")]

WHISPER_LANGUAGES = [
    "auto (Auto-Detect)", "de (German)", "en (English)", "fr (French)", "es (Spanish)", "it (Italian)",
    "nl (Dutch)", "pt (Portuguese)", "pl (Polish)", "tr (Turkish)", "ru (Russian)", "uk (Ukrainian)",
    "ar (Arabic)", "zh (Chinese)", "ja (Japanese)", "ko (Korean)", "sv (Swedish)", "da (Danish)",
]
