#!/usr/bin/env python3
"""Generate the Gantz app icon (PNG + multi-size ICO) with no dependencies.

The artwork is rendered at 4x and downsampled, which gives clean edges at
every icon size.  Run from the repository root:

    python tools/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

TOP = (47, 111, 237)     # #2f6fed
BOTTOM = (29, 78, 216)   # #1d4ed8
WHITE = (255, 255, 255)
PANEL = (219, 234, 254)  # #dbeafe
ACCENT = (37, 99, 235)
SHADOW = (23, 55, 145)

SS = 4  # supersampling factor


def rounded_rect_alpha(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> float:
    """Return 1 inside a rounded rectangle, 0 outside (pixel centre sampling)."""
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    hw = (x1 - x0) / 2
    hh = (y1 - y0) / 2
    dx = abs(x - cx) - (hw - r)
    dy = abs(y - cy) - (hh - r)
    if dx < 0 and dy < 0:
        return 1.0
    dx = max(dx, 0.0)
    dy = max(dy, 0.0)
    return 1.0 if (dx * dx + dy * dy) <= r * r else 0.0


def render(size: int) -> bytes:
    """Render the icon at *size* pixels and return RGBA bytes."""
    big = size * SS
    px = bytearray(big * big * 4)

    # --- background rounded square with a vertical gradient
    inset = big * 0.02
    radius = big * 0.225
    for y in range(big):
        t = y / max(1, big - 1)
        r = round(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
        g = round(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
        b = round(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
        for x in range(big):
            a = rounded_rect_alpha(x + 0.5, y + 0.5, inset, inset, big - inset, big - inset, radius)
            if a:
                i = (y * big + x) * 4
                px[i:i + 4] = bytes((r, g, b, int(255 * a)))

    def put(x: int, y: int, colour: tuple[int, int, int], alpha: float = 1.0) -> None:
        if not (0 <= x < big and 0 <= y < big) or alpha <= 0:
            return
        i = (y * big + x) * 4
        dr, dg, db, da = px[i:i + 4]
        sa = alpha * 255
        na = sa + da * (1 - alpha)
        if na <= 0:
            return
        px[i] = int((colour[0] * sa + dr * da * (1 - alpha)) / na)
        px[i + 1] = int((colour[1] * sa + dg * da * (1 - alpha)) / na)
        px[i + 2] = int((colour[2] * sa + db * da * (1 - alpha)) / na)
        px[i + 3] = int(na)

    def shape(kind: str, x0: float, y0: float, x1: float, y1: float,
              colour: tuple[int, int, int], radius_frac: float = 0.06,
              alpha: float = 1.0) -> None:
        r = big * radius_frac
        for y in range(int(y0 * big), int(y1 * big) + 1):
            for x in range(int(x0 * big), int(x1 * big) + 1):
                if rounded_rect_alpha(x + 0.5, y + 0.5, x0 * big, y0 * big, x1 * big, y1 * big, r):
                    if kind == "circle":
                        continue
                    put(x, y, colour, alpha)

    # --- folder motif ------------------------------------------------------
    # subtle drop shadow
    shape("rect", 0.20, 0.34, 0.82, 0.76, SHADOW, 0.055, 0.30)
    # folder tab
    shape("rect", 0.20, 0.27, 0.47, 0.40, WHITE, 0.05)
    # folder back
    shape("rect", 0.20, 0.33, 0.80, 0.72, WHITE, 0.055)
    # folder front panel
    shape("rect", 0.20, 0.44, 0.80, 0.74, PANEL, 0.05)
    shape("rect", 0.20, 0.44, 0.80, 0.50, WHITE, 0.012, 0.55)

    # three documents peeking out of the folder
    for index, offset in enumerate((0.0, 0.035, 0.07)):
        x0 = 0.28 + offset
        x1 = 0.52 + offset
        y0 = 0.20 + offset * 0.7
        y1 = 0.47
        shape("rect", x0, y0, x1, y1, WHITE, 0.02)
        for line in range(3):
            ly = y0 + 0.055 + line * 0.045
            shape("rect", x0 + 0.03, ly, x1 - 0.05, ly + 0.014, ACCENT, 0.004, 0.75)

    # a small circular badge with a clean downward arrow
    bx0, by0, bx1, by1 = 0.60, 0.54, 0.88, 0.82
    bcx = (bx0 + bx1) / 2 * big
    bcy = (by0 + by1) / 2 * big
    br = (bx1 - bx0) / 2 * big
    for y in range(int(by0 * big), int(by1 * big) + 1):
        for x in range(int(bx0 * big), int(bx1 * big) + 1):
            if (x + 0.5 - bcx) ** 2 + (y + 0.5 - bcy) ** 2 <= br * br:
                put(x, y, ACCENT)
    # stem
    for y in range(int(0.583 * big), int(0.663 * big)):
        for x in range(int(0.723 * big), int(0.757 * big)):
            put(x, y, WHITE)
    # head: triangle pointing down
    top = 0.660
    bottom = 0.762
    for y in range(int(top * big), int(bottom * big)):
        t = (y - top * big) / max(1, (bottom - top) * big)
        half = 0.052 * big * (1 - t)
        for x in range(int(0.74 * big - half), int(0.74 * big + half) + 1):
            put(x, y, WHITE)

    # --- downsample --------------------------------------------------------
    out = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            for sy in range(SS):
                for sx in range(SS):
                    i = ((y * SS + sy) * big + (x * SS + sx)) * 4
                    r += px[i]
                    g += px[i + 1]
                    b += px[i + 2]
                    a += px[i + 3]
            n = SS * SS
            j = (y * size + x) * 4
            out[j:j + 4] = bytes((r // n, g // n, b // n, a // n))
    return bytes(out)


def png_bytes(size: int) -> bytes:
    raw = bytearray()
    pixels = render(size)
    for y in range(size):
        raw.append(0)  # filter: none
        raw.extend(pixels[y * size * 4:(y + 1) * size * 4])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def ico_bytes(sizes: list[int]) -> bytes:
    images = [png_bytes(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    payload = b""
    for size, data in zip(sizes, images):
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        payload += data
    return header + entries + payload


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    png = png_bytes(256)
    (ASSETS / "gantz.png").write_bytes(png)
    (ASSETS / "gantz.ico").write_bytes(ico_bytes([16, 24, 32, 48, 64, 128, 256]))
    print(f"wrote {ASSETS / 'gantz.png'} ({len(png):,} bytes)")
    print(f"wrote {ASSETS / 'gantz.ico'}")


if __name__ == "__main__":
    main()
