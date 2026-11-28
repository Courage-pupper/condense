"""Rounded buttons for ttk. The clam theme draws only square buttons, so each look (quiet, red, the chosen part of a
switch) is a small rounded rectangle drawn here as a PNG with an alpha channel: smooth (anti-aliased) corners over
any background. ttk stretches its middle and keeps the corners (an image element with a border)."""
import base64
import struct
import zlib
from typing import Dict, Optional, Tuple

RADIUS = 5
SIDE = 2 * RADIUS + 4  # the image: two corners and a few pixels between them that ttk stretches
SUPER = 4  # samples per pixel and direction, for the edge's coverage


def _hex(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _coverage(x: int, y: int, box: Tuple[float, float, float, float], r: float) -> float:
    """How much of pixel (x, y) lies inside box (x0, y0, x1, y1) with corners of radius r (0 … 1)."""
    x0, y0, x1, y1 = box
    inside = 0
    for sy in range(SUPER):
        for sx in range(SUPER):
            px, py = x + (sx + 0.5) / SUPER, y + (sy + 0.5) / SUPER
            if not (x0 <= px <= x1 and y0 <= py <= y1):
                continue
            cx = min(max(px, x0 + r), x1 - r)  # the nearest point of the inner rectangle
            cy = min(max(py, y0 + r), y1 - r)
            if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
                inside += 1
    return inside / (SUPER * SUPER)


def png(fill: str, alpha: float = 1.0, side: int = SIDE, radius: float = RADIUS, height: Optional[int] = None,
        box: Optional[Tuple[float, float, float, float]] = None) -> bytes:
    """A side×height PNG (square by default): a rounded rectangle (the whole image, or box) in fill, transparent
    outside it."""
    height = height or side
    box = box or (0, 0, side, height)
    rgb = _hex(fill)
    rows = []
    for y in range(height):
        row = bytearray([0])  # filter: none
        for x in range(side):
            a = int(round(255 * alpha * _coverage(x, y, box, radius)))
            row += bytes((*rgb, a))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def photo(master, fill: str, alpha: float = 1.0, **shape):
    import tkinter as tk
    return tk.PhotoImage(master=master, data=base64.b64encode(png(fill, alpha, **shape)).decode("ascii"), format="png")


# ttk fills a widget with its style's background before drawing the images: for the corners to blend into what a
# button sits on, each surface gets its own variant of the style with that background ("On171a22.Tool.TButton").
_style = None
_made = set()


def _bg_of(widget) -> Optional[str]:
    for opt in ("bg", "background"):
        try:
            v = str(widget.cget(opt))
        except Exception:  # noqa: BLE001
            continue
        if v.startswith("#") and len(v) == 7:
            return v.lower()
    return None


def style_for(parent, base: str) -> str:
    """The button style to use on parent: base ("Tool", "SegOn.TButton" …), on parent's background colour."""
    name = base if base.endswith(".TButton") else base + ".TButton"
    if _style is None:
        return name
    bg = _bg_of(parent)
    if bg is None:
        return name
    key = f"On{bg[1:]}.{name}"
    if key not in _made:
        _style.configure(key, background=bg)
        _style.map(key, background=[("disabled", bg), ("pressed", bg), ("active", bg), ("!active", bg)])
        _made.add(key)
    return key


def base_style(style: str) -> str:
    """"On171a22.SegOn.TButton" → "SegOn.TButton"."""
    return style.split(".", 1)[1] if style.startswith("On") and style.count(".") >= 2 else style


KNOB = 12  # the slider's round knob; its line is 3 px


def install_scale(style, master, theme: Dict[str, str]) -> Optional[Dict]:
    """A slider as a thin line with a small round knob (ttk's Horizontal.TScale)."""
    T = theme
    try:
        # the line: rounded ends and a middle that ttk stretches (the image must be wider than its two ends)
        line = photo(master, "#4a5165", side=KNOB + 8, height=KNOB, box=(0, KNOB / 2 - 1.5, KNOB + 8, KNOB / 2 + 1.5), radius=1.5)
        knob = photo(master, "#c8ccd6", side=KNOB, height=KNOB, radius=KNOB / 2)
        knob_on = photo(master, "#ffffff", side=KNOB, height=KNOB, radius=KNOB / 2)
        style.element_create("Thin.Horizontal.Scale.trough", "image", line, border=(4, 0), sticky="ew")
        style.element_create("Thin.Horizontal.Scale.slider", "image", knob, ("pressed", knob_on), ("active", knob_on))
        style.layout("Horizontal.TScale", [("Thin.Horizontal.Scale.trough", {"sticky": "nswe", "children": [
            ("Thin.Horizontal.Scale.slider", {"side": "left", "sticky": ""})]})])
        return {"line": line, "knob": knob, "knob_on": knob_on}
    except Exception:  # noqa: BLE001 — Tk's own slider then
        return None


def install(style, master, theme: Dict[str, str]) -> Optional[Dict]:
    """Rounded backgrounds for the button styles. Returns the images (keep them: Tk forgets an image nobody holds), or
    None where images can't be made (then the buttons stay square, as before)."""
    T = theme
    try:
        clear = photo(master, T["bg_soft"], 0.0)
        looks = {  # style: (normal, hover, pressed, disabled)
            "Tool": (photo(master, T["bg_soft"]), photo(master, T["bg_soft_hover"]), photo(master, T["bg_selected"]),
                     photo(master, T["bg_soft"], 0.55)),
            "Primary": (photo(master, T["accent"]), photo(master, T["accent_hover"]), photo(master, T["accent_hover"]),
                        photo(master, T["bg_card"])),
            # a switch: the parts are plain text, the chosen one sits in a pill
            "Seg": (clear, photo(master, T["bg_soft"]), photo(master, T["bg_soft_hover"]), clear),
            "SegOn": (photo(master, T["bg_selected"]),) * 4,
        }
        looks["Play"] = looks["Check"] = looks["CheckOn"] = looks["Tool"]
        for name, (normal, hover, pressed, disabled) in looks.items():
            element = f"Round{name}.background"
            style.element_create(element, "image", normal, ("disabled", disabled), ("pressed", pressed), ("active", hover),
                                 border=RADIUS + 1, padding=0, sticky="nsew")
            style.layout(f"{name}.TButton", [(element, {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        global _style
        _style = style
        return {"looks": looks, "clear": clear}
    except Exception:  # noqa: BLE001 — no images (an old Tk, a test's stand-in): square buttons
        return None
