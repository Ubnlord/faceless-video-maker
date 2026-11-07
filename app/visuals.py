"""Procedural background generation.

Every scene gets a real, generated image - no stock footage, no external
assets, no network. The images are built with Pillow and then animated by
ffmpeg's ``zoompan`` filter, which is what gives the video its motion.
"""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter

RGB = Tuple[int, int, int]

# name -> (top colour, bottom colour, accent colour)
PALETTES: Dict[str, Tuple[RGB, RGB, RGB]] = {
    "aurora":   ((10, 14, 32), (58, 32, 110), (124, 92, 255)),
    "midnight": ((4, 6, 14), (16, 24, 48), (86, 156, 255)),
    "ember":    ((18, 6, 8), (92, 26, 20), (255, 122, 60)),
    "forest":   ((5, 16, 12), (16, 58, 44), (72, 220, 150)),
    "ocean":    ((3, 12, 24), (10, 52, 84), (64, 196, 232)),
    "sunset":   ((24, 8, 30), (120, 40, 70), (255, 150, 120)),
    "mono":     ((8, 8, 10), (34, 34, 40), (200, 200, 210)),
    "royal":    ((12, 8, 34), (48, 24, 96), (168, 120, 255)),
}

BACKGROUND_NAMES: List[str] = list(PALETTES.keys())


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    return (
        int(_lerp(a[0], b[0], t)),
        int(_lerp(a[1], b[1], t)),
        int(_lerp(a[2], b[2], t)),
    )


def _vertical_gradient(size: Tuple[int, int], top: RGB, bottom: RGB) -> Image.Image:
    w, h = size
    # Build a 1px wide gradient then stretch it - far cheaper than per pixel work.
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    for y in range(h):
        px[0, y] = _lerp_rgb(top, bottom, y / max(1, h - 1))
    return strip.resize((w, h), Image.BILINEAR)


def _add_glow(img: Image.Image, cx: int, cy: int, radius: int, colour: RGB, strength: float) -> None:
    """Add a soft radial light source."""
    w, h = img.size
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(glow)
    steps = 14
    for i in range(steps, 0, -1):
        r = int(radius * i / steps)
        alpha = strength * (1 - i / steps) ** 1.6
        c = (int(colour[0] * alpha), int(colour[1] * alpha), int(colour[2] * alpha))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    glow = glow.filter(ImageFilter.GaussianBlur(radius / 3.2))
    # Screen blend keeps highlights from washing out.
    base = img.load()
    gl = glow.load()
    for y in range(h):
        for x in range(w):
            b = base[x, y]
            g = gl[x, y]
            base[x, y] = (
                min(255, b[0] + g[0]),
                min(255, b[1] + g[1]),
                min(255, b[2] + g[2]),
            )


def _add_particles(img: Image.Image, rng: random.Random, colour: RGB, count: int) -> None:
    w, h = img.size
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for _ in range(count):
        x = rng.randint(0, w)
        y = rng.randint(0, h)
        r = rng.randint(2, max(3, w // 90))
        a = rng.uniform(0.15, 0.75)
        draw.ellipse(
            [x - r, y - r, x + r, y + r],
            fill=(int(colour[0] * a), int(colour[1] * a), int(colour[2] * a)),
        )
    layer = layer.filter(ImageFilter.GaussianBlur(max(1, w // 400)))
    base = img.load()
    lp = layer.load()
    for y in range(h):
        for x in range(w):
            b = base[x, y]
            l = lp[x, y]
            base[x, y] = (
                min(255, b[0] + l[0]),
                min(255, b[1] + l[1]),
                min(255, b[2] + l[2]),
            )


def _add_waves(img: Image.Image, rng: random.Random, colour: RGB, lines: int) -> None:
    w, h = img.size
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i in range(lines):
        base_y = int(h * (0.15 + 0.7 * i / max(1, lines - 1)))
        amp = rng.uniform(h * 0.01, h * 0.05)
        freq = rng.uniform(1.0, 3.0)
        phase = rng.uniform(0, math.tau)
        a = rng.uniform(0.10, 0.35)
        pts = []
        for x in range(0, w + 1, max(2, w // 160)):
            y = base_y + amp * math.sin(freq * math.tau * x / w + phase)
            pts.append((x, y))
        draw.line(pts, fill=(int(colour[0] * a), int(colour[1] * a), int(colour[2] * a)), width=max(2, w // 300))
    layer = layer.filter(ImageFilter.GaussianBlur(max(1, w // 500)))
    base = img.load()
    lp = layer.load()
    for y in range(h):
        for x in range(w):
            b = base[x, y]
            l = lp[x, y]
            base[x, y] = (
                min(255, b[0] + l[0]),
                min(255, b[1] + l[1]),
                min(255, b[2] + l[2]),
            )


def _add_vignette(img: Image.Image, strength: float = 0.55) -> None:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    steps = 40
    for i in range(steps):
        t = i / steps
        inset_x = int(w * 0.5 * t)
        inset_y = int(h * 0.5 * t)
        val = int(255 * (1 - strength * (1 - t) ** 2))
        draw.ellipse([inset_x, inset_y, w - inset_x, h - inset_y], fill=val)
    mask = mask.filter(ImageFilter.GaussianBlur(w // 12))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    img.paste(Image.composite(img, dark, mask), (0, 0))


def generate_background(
    out_path: Path,
    width: int,
    height: int,
    palette: str = "aurora",
    seed: int = 0,
    variant: int = 0,
) -> Path:
    """Render one background image and write it to ``out_path``."""
    top, bottom, accent = PALETTES.get(palette, PALETTES["aurora"])
    rng = random.Random(seed * 7919 + variant * 104729)

    img = _vertical_gradient((width, height), top, bottom)

    # Two or three light sources, placed off centre for a cinematic feel.
    for _ in range(rng.randint(2, 3)):
        cx = rng.randint(int(width * 0.05), int(width * 0.95))
        cy = rng.randint(int(height * 0.05), int(height * 0.75))
        radius = rng.randint(int(width * 0.35), int(width * 0.85))
        _add_glow(img, cx, cy, radius, accent, rng.uniform(0.35, 0.7))

    _add_waves(img, rng, accent, rng.randint(3, 6))
    _add_particles(img, rng, accent, rng.randint(60, 140))
    _add_vignette(img, 0.5)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


def palette_for_index(base: str, index: int) -> str:
    """Rotate through palettes so consecutive scenes look different."""
    if base in PALETTES:
        names = [base] + [n for n in BACKGROUND_NAMES if n != base]
    else:
        names = BACKGROUND_NAMES
    return names[index % len(names)]
