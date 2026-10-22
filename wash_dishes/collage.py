"""The proof picture sent to the house group chat: four photos of one dish, marked, with times.

Kept apart from proofs.py so the picture's design can change without touching how proofs are found.
It is read as a small image in a chat thread, so text is large, captions are short, and the photos
are cropped close enough that the dish is easy to find.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from datetime import timezone as utc
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .notify import timezone
from .sequences import _font
from .store import Store

STAGES = ("before", "putting", "left", "late")
# Part of every cached picture's name, so a new design is never hidden behind an old cached copy.
VERSION = 3

WIDTH, MARGIN, GAP = 1600, 32, 24
PANEL_W = (WIDTH - 2 * MARGIN - GAP) // 2
PANEL_H = PANEL_W * 3 // 4
CAPTION_H = 92
PAPER, INK, MUTED, DISH, SHADE = "#101214", "#f3f4f6", "#a7aeb6", "#ffd400", "#000000"
MIN_MARK = 64          # px; a cup across the room still gets a mark you can see on a phone
MIN_CROP = 0.36        # of the frame width, so the spot stays recognisable
# The camera's own clock and "tapo" logo (x, y, w, h of the frame). Each panel already says when it
# was taken, so the crop leaves these out whenever the dish and the person still fit.
CAMERA_MARKS = ((0.0, 0.0, 0.41, 0.05), (0.0, 0.89, 0.12, 0.10))
MONA = Path(__file__).parent / "static" / "fonts" / "MonaSans.woff2"


@cache
def _type(size: int, weight: int = 450) -> ImageFont.FreeTypeFont:
    """Mona Sans like the dashboard; the system sans where this Pillow cannot read WOFF2."""
    try:
        font = ImageFont.truetype(str(MONA), size)
        font.set_variation_by_axes([100, weight])
        return font
    except (OSError, ValueError):
        return _font(size, weight >= 600)


def _clock(at: float) -> str:
    return datetime.fromtimestamp(at, utc.utc).astimezone(timezone()).strftime("%-I:%M %p")


def _later(seconds: float) -> str:
    hours, minutes = int(seconds // 3600), int(seconds // 60)
    return f"{hours} hour{'s' * (hours != 1)} later" if hours else f"{minutes} min later"


# The dish check's words for the detector's cup, pot and pan.
SAME_KIND = {"mug": "cup", "drinking glass": "cup", "cooking pot": "pot", "frying pan": "pan"}


def dish_name(dishes: list[dict], looks_like: str | None = None) -> str:
    """"pan" for one pan, "2 cups, bowl" for several. One dish is named only if the dish check's
    second look agrees with the detector: in the sink the detector calls pans and lids "bowl", and a
    wrong word undermines the whole picture, so a disagreement just says "dish"."""
    if len(dishes) == 1 and dishes[0]["quantity"] == 1:
        kind = dishes[0]["kind"]
        return kind if looks_like is None or SAME_KIND.get(looks_like, looks_like) == kind else "dish"
    return Store.dish_description(dishes)


def place(location: str | None) -> str:
    # Area names carry setup notes such as "(visible portion)" that mean nothing in a chat.
    return re.sub(r"\s*\([^)]*\)", "", location or "").strip() or "Kitchen"


def _fit(draw: ImageDraw.ImageDraw, text: str, sizes: range, weight: int, width: int) -> tuple[str, ImageFont.FreeTypeFont]:
    """The largest size that fits on one line; at the smallest, the end is cut with an ellipsis."""
    for size in sizes:
        font = _type(size, weight)
        if draw.textlength(text, font=font) <= width:
            return text, font
    while len(text) > 1 and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…", font


def _title(draw: ImageDraw.ImageDraw, dish: str, name: str | None) -> tuple[str, ImageFont.FreeTypeFont]:
    """"Sam left this pan out", or "Pan left out" before anyone is named. A long name is shortened,
    never the part saying what was left."""
    if not name:
        return _fit(draw, f"{dish[:1].upper()}{dish[1:]} left out", range(84, 55, -4), 700, WIDTH - 2 * MARGIN)
    name = name.strip()
    rest = f" left {'this ' * (not re.match(r'[0-9]', dish) and ',' not in dish)}{dish} out"
    font = _type(56, 700)
    short, _ = _fit(draw, name[:1].upper() + name[1:], range(84, 55, -4), 700, max(200, WIDTH - 2 * MARGIN - int(draw.textlength(rest, font=font))))
    return _fit(draw, short + rest, range(84, 55, -4), 700, WIDTH - 2 * MARGIN)


def _crop(dish: list[list[float]], person: list[float] | None, aspect: float) -> tuple[float, float, float, float]:
    """One region for every panel: the dish, plus the head and shoulders of whoever put it down.

    Legs are left out on purpose; including the whole body shrinks the dish to a speck. When the
    person is too far away to fit as well, the dish wins."""
    boxes = list(dish)
    if person:
        x, y, w, h = person
        boxes.append([x, y, w, min(h, max(w, h * 0.4))])
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    w = min(1.0, max(MIN_CROP, (x1 - x0) * 1.3, (y1 - y0) * 1.3 * aspect))
    h = min(1.0, w / aspect)
    w = h * aspect
    left, top = (x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - h / 2
    dx0, dy0 = min(b[0] for b in dish), min(b[1] for b in dish)
    dx1, dy1 = max(b[0] + b[2] for b in dish), max(b[1] + b[3] for b in dish)
    left = max(min(left, dx0 - 0.02), dx1 + 0.02 - w)
    top = max(min(top, dy0 - 0.02), dy1 + 0.02 - h)
    left, top = min(max(left, 0.0), 1 - w), min(max(top, 0.0), 1 - h)
    return _clear_of_marks(left, top, w, h, boxes)


def _clear_of_marks(left: float, top: float, w: float, h: float, boxes: list[list[float]]) -> tuple[float, float, float, float]:
    """Slide the region the least distance that leaves out the camera's clock and logo, keeping
    everything it showed of the dish and the person; if no slide does, the marks stay in."""
    def shows(x0, y0, box):
        return x0 <= box[0] and y0 <= box[1] and box[0] + box[2] <= x0 + w and box[1] + box[3] <= y0 + h

    def marked(x0, y0):
        return any(x0 < x + mw and x < x0 + w and y0 < y + mh and y < y0 + h for x, y, mw, mh in CAMERA_MARKS)

    kept = [box for box in boxes if shows(left, top, box)]
    moves = [(0.0, 0.0)] + [move for x, y, mw, mh in CAMERA_MARKS
                            for move in ((x + mw - left, 0.0), (x - left - w, 0.0), (0.0, y + mh - top), (0.0, y - top - h))]
    for dx, dy in sorted(moves, key=lambda m: abs(m[0]) + abs(m[1])):
        x0, y0 = left + dx, top + dy
        if (-1e-9 <= x0 <= 1 - w + 1e-9 and -1e-9 <= y0 <= 1 - h + 1e-9 and not marked(x0, y0)
                and all(shows(x0, y0, box) for box in kept)):
            return x0, y0, x0 + w, y0 + h
    return left, top, left + w, top + h


def _mark(draw: ImageDraw.ImageDraw, rect: tuple[float, float, float, float], style: str) -> None:
    """solid: the dish. dashed: where it will be. corners: the spot, without covering whoever is there."""
    x0, y0, x1, y1 = rect
    for color, width in ((SHADE, 11), (DISH, 6)):
        if style == "solid":
            draw.rounded_rectangle(rect, radius=10, outline=color, width=width)
        elif style == "corners":
            arm = min(x1 - x0, y1 - y0) * 0.32 + 6
            for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
                draw.line([(cx, cy + dy * arm), (cx, cy), (cx + dx * arm, cy)], fill=color, width=width, joint="curve")
        else:
            for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
                length = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
                steps = max(3, round(length / 13)) | 1  # odd, so every side starts and ends on a dash
                for i in range(0, steps, 2):
                    s, e = i / steps, min(1.0, (i + 1) / steps)
                    draw.line([(a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s),
                               (a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e)], fill=color, width=width)


def render(meta: dict, pictures: dict[str, bytes], name: str | None) -> bytes:
    """2x2 photos with a short caption and time each; the dish marked in yellow in every panel.

    Optional meta: `person_box` keeps the person's head in the crop, `late_box` marks the dish where the
    late photo saw it, and `left_behind` captions the second photo as leaving the dish rather than
    putting it down (the moment found was them walking off from it)."""
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    title, title_font = _title(probe, meta["dish"], name)
    day = datetime.fromtimestamp(meta["putting_at"], utc.utc).astimezone(timezone())
    subtitle, sub_font = _fit(probe, f"{day:%a %b %-d} · {place(meta['location'])}", range(44, 35, -4), 450, WIDTH - 2 * MARGIN)
    head = MARGIN + 100 + 58 + 26
    row = PANEL_H + CAPTION_H
    canvas = Image.new("RGB", (WIDTH, head + 2 * row + GAP + MARGIN - 18), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.text((MARGIN, MARGIN + 92), title, font=title_font, fill=INK, anchor="ls")
    draw.text((MARGIN, MARGIN + 92 + 62), subtitle, font=sub_font, fill=MUTED, anchor="ls")

    with Image.open(io.BytesIO(pictures["putting"])) as first:
        aspect = PANEL_W / PANEL_H * first.height / first.width
    x0, y0, x1, y1 = _crop([meta["box"], *([meta["late_box"]] if meta.get("late_box") else [])], meta.get("person_box"), aspect)
    sx, sy = PANEL_W / (x1 - x0), PANEL_H / (y1 - y0)
    captions = {"before": "Before", "putting": "Leaving it" if meta.get("left_behind") else "Putting it down",
                "left": "Left there", "late": f"Still there {_later(meta['late_at'] - meta['left_at'])}"}
    styles = {"before": "dashed", "putting": "corners", "left": "solid", "late": "solid"}
    time_font = _type(46, 450)
    for i, key in enumerate(STAGES):
        px, py = MARGIN + (i % 2) * (PANEL_W + GAP), head + (i // 2) * (row + GAP)
        with Image.open(io.BytesIO(pictures[key])) as source:
            frame = source.convert("RGB")
        bounds = (round(x0 * frame.width), round(y0 * frame.height), round(x1 * frame.width), round(y1 * frame.height))
        canvas.paste(frame.crop(bounds).resize((PANEL_W, PANEL_H), Image.Resampling.LANCZOS), (px, py))
        bx, by, bw, bh = meta["late_box"] if key == "late" and meta.get("late_box") else meta["box"]
        cx, cy = px + (bx + bw / 2 - x0) * sx, py + (by + bh / 2 - y0) * sy
        half_w, half_h = max(bw * sx / 2 + 10, MIN_MARK / 2), max(bh * sy / 2 + 10, MIN_MARK / 2)
        rect = (max(px + 6, cx - half_w), max(py + 6, cy - half_h), min(px + PANEL_W - 6, cx + half_w), min(py + PANEL_H - 6, cy + half_h))
        if rect[0] < rect[2] and rect[1] < rect[3]:  # a late box far from the first one can fall outside
            _mark(draw, rect, styles[key])
        clock, base = _clock(meta[f"{key}_at"]), py + PANEL_H + 64
        room = PANEL_W - draw.textlength(clock, font=time_font) - 20
        caption, font = _fit(draw, captions[key], range(50, 37, -2), 650, int(room))
        draw.text((px, base), caption, font=font, fill=DISH if key == "late" else INK, anchor="ls")
        draw.text((px + draw.textlength(caption, font=font) + 20, base), clock, font=time_font, fill=MUTED, anchor="ls")
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=84, optimize=True, progressive=True)
    return out.getvalue()
