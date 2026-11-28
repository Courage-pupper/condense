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


def _coverage(x: int, y: int, w: int, h: int, r: float) -> float:
    """How much of pixel (x, y) lies inside a w×h rectangle with corners of radius r (0 … 1)."""
    inside = 0
    for sy in range(SUPER):
        for sx in range(SUPER):
            px, py = x + (sx + 0.5) / SUPER, y + (sy + 0.5) / SUPER
            cx = min(max(px, r), w - r)  # the nearest point of the inner rectangle
            cy = min(max(py, r), h - r)
            if (px - cx) ** 2 + (py - cy) ** 2 <= r * r:
                inside += 1
    return inside / (SUPER * SUPER)


def png(fill: str, alpha: float = 1.0, side: int = SIDE, radius: float = RADIUS) -> bytes:
    """A side×side PNG: a rounded rectangle in fill, transparent outside it."""
    rgb = _hex(fill)
    rows = []
    for y in range(side):
        row = bytearray([0])  # filter: none
        for x in range(side):
            a = int(round(255 * alpha * _coverage(x, y, side, side, radius)))
            row += bytes((*rgb, a))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def photo(master, fill: str, alpha: float = 1.0):
    import tkinter as tk
    return tk.PhotoImage(master=master, data=base64.b64encode(png(fill, alpha)).decode("ascii"), format="png")


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
        looks["Play"] = looks["Tool"]
        for name, (normal, hover, pressed, disabled) in looks.items():
            element = f"Round{name}.background"
            style.element_create(element, "image", normal, ("disabled", disabled), ("pressed", pressed), ("active", hover),
                                 border=RADIUS + 1, padding=0, sticky="nsew")
            style.layout(f"{name}.TButton", [(element, {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        return {"looks": looks, "clear": clear}
    except Exception:  # noqa: BLE001 — no images (an old Tk, a test's stand-in): square buttons
        return None
