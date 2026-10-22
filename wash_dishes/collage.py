"""The proof picture sent to the house group chat: four photos of one dish, boxed, with times.

Kept apart from proofs.py so the picture's design can change without touching how proofs are found.
"""
from __future__ import annotations

import io
from datetime import datetime
from datetime import timezone as utc

from PIL import Image, ImageDraw

from .notify import timezone
from .sequences import _font

STAGES = ("before", "putting", "left", "late")

WIDTH, GAP, MARGIN = 1600, 20, 36
PANEL_W = (WIDTH - 2 * MARGIN - GAP) // 2
PANEL_H = PANEL_W * 10 // 16
INK, PAPER, MUTED, DISH = "#111111", "#ffffff", "#5f6368", "#ffd400"


def _clock(at: float) -> str:
    return datetime.fromtimestamp(at, utc.utc).astimezone(timezone()).strftime("%-I:%M %p")


def _elapsed(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _crop(boxes: list[list[float]], aspect: float) -> tuple[float, float, float, float]:
    """One region for every panel: the dish and whoever put it down, with room around them."""
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    w = min(1.0, max(0.42, (x1 - x0) * 1.35, (y1 - y0) * 1.25 * aspect))
    h = min(1.0, w / aspect)
    w = h * aspect
    left = min(max((x0 + x1) / 2 - w / 2, 0.0), 1 - w)
    top = min(max((y0 + y1) / 2 - h / 2, 0.0), 1 - h)
    return left, top, left + w, top + h


def render(meta: dict, pictures: dict[str, bytes], name: str | None) -> bytes:
    """2x2 photos with one short line each, big enough to read on a phone; the dish boxed in yellow."""
    title_font, sub_font, time_font, label_font = _font(52, True), _font(32), _font(30, True), _font(30)
    dish = meta["dish"]
    title = f"{name.title()} left this {dish} out" if name else f"{dish.capitalize()} left out"
    late_after = meta["late_at"] - meta["putting_at"]
    subtitle = f"{meta['location']} · {datetime.fromtimestamp(meta['putting_at'], utc.utc).astimezone(timezone()):%a %b %-d}"
    head = MARGIN + 64 + 46 + 24
    row = PANEL_H + 74
    canvas = Image.new("RGB", (WIDTH, head + 2 * row + MARGIN - 10), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.text((MARGIN, MARGIN), title, font=title_font, fill=INK)
    draw.text((MARGIN, MARGIN + 66), subtitle, font=sub_font, fill=MUTED)
    with Image.open(io.BytesIO(pictures["putting"])) as probe:
        aspect = PANEL_W / PANEL_H * probe.height / probe.width
    x0, y0, x1, y1 = _crop([meta["box"], *([meta["person_box"]] if meta.get("person_box") else [])], aspect)
    labels = {"before": "Before", "putting": f"{name.title()} putting it down" if name else "Putting it down",
              "left": "Left there", "late": f"Still there {_elapsed(late_after)} later"}
    for i, key in enumerate(STAGES):
        px, py = MARGIN + (i % 2) * (PANEL_W + GAP), head + (i // 2) * row
        with Image.open(io.BytesIO(pictures[key])) as source:
            frame = source.convert("RGB")
        bounds = (round(x0 * frame.width), round(y0 * frame.height), round(x1 * frame.width), round(y1 * frame.height))
        canvas.paste(frame.crop(bounds).resize((PANEL_W, PANEL_H), Image.Resampling.LANCZOS), (px, py))
        bx, by, bw, bh = meta["late_box"] if key == "late" and meta.get("late_box") else meta["box"]
        sx, sy = PANEL_W / (x1 - x0), PANEL_H / (y1 - y0)
        pad = 10
        rect = (px + (bx - x0) * sx - pad, py + (by - y0) * sy - pad, px + (bx + bw - x0) * sx + pad, py + (by + bh - y0) * sy + pad)
        if key != "before":
            draw.rectangle(rect, outline=INK, width=9)
            draw.rectangle(rect, outline=DISH, width=5)
        clock = _clock(meta[f"{key}_at"])
        draw.text((px, py + PANEL_H + 14), clock, font=time_font, fill=INK)
        draw.text((px + draw.textlength(clock, font=time_font) + 16, py + PANEL_H + 14), labels[key], font=label_font, fill=INK)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()
