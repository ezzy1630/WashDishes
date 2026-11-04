"""Shared, phone-sized presentation of retained camera pixels.

Visual ranking never supplies identity, responsibility, or evidence admission.
"""

from __future__ import annotations

import io
from functools import cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat

WIDTH, MARGIN, PHOTO_H = 1080, 40, 560
PAPER, INK, MUTED, LINE = "#111316", "#e8eaed", "#a3aab2", "#3a4048"
DISH, BEFORE = "#ffe04f", "#a9c4ff"
VERSION = 2
MAX_BYTES = 350_000


@cache
def font(size: int, bold: bool = False):
    # Static faces have consistent metrics on both Pillow/FreeType builds. The
    # Jetson's variable WOFF2 rendering can overlap glyphs despite valid metrics.
    for name in (
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size=size)


def wrap(draw, text: str, size=40, *, bold=False, width=1000) -> list[str]:
    rows = []
    for word in text.split():
        if rows and draw.textlength(rows[-1] + " " + word, font=font(size, bold)) <= width:
            rows[-1] += " " + word
        else:
            # Long names/locations are still contained without shrinking the type.
            while draw.textlength(word, font=font(size, bold)) > width:
                end = len(word) - 1
                while end > 1 and draw.textlength(word[:end] + "–", font=font(size, bold)) > width:
                    end -= 1
                rows.append(word[:end] + "–")
                word = word[end:]
            rows.append(word)
    return rows


def jpeg(canvas: Image.Image) -> bytes:
    out = io.BytesIO()
    for quality in (86, 80, 74, 68, 60, 50, 40):
        out.seek(0)
        out.truncate()
        canvas.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        if out.tell() <= MAX_BYTES:
            return out.getvalue()
    raise ValueError("Evidence image exceeds the 350 KB budget.")


def region(boxes: list, picture: bytes, *, people=False) -> tuple:
    """Tight context around all actual boxes; include the whole head/torso for people."""
    with Image.open(io.BytesIO(picture)) as src:
        aspect = (WIDTH - 2 * MARGIN) / PHOTO_H * src.height / src.width
    if not boxes:
        return (0.0, 0.0, 1.0, 1.0)
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    pad = 0.04 if people else 0.025
    w = max(0.12, x1 - x0 + 2 * pad, (y1 - y0 + 2 * pad) * aspect)
    h = w / aspect
    # Expand, never shrink the required union when an edge meets the frame.
    if w >= 1 or h >= 1:
        return (max(0.0, x0 - pad), max(0.0, y0 - pad), min(1.0, x1 + pad), min(1.0, y1 + pad))
    x = min(max(0.0, (x0 + x1 - w) / 2), 1 - w)
    y = min(max(0.0, (y0 + y1 - h) / 2), 1 - h)
    return x, y, x + w, y + h


def crop(picture: bytes, bounds: tuple) -> Image.Image:
    with Image.open(io.BytesIO(picture)) as src:
        src = ImageOps.exif_transpose(src).convert("RGB")
        x0, y0, x1, y1 = bounds
        return src.crop(
            (
                int(x0 * src.width),
                int(y0 * src.height),
                max(1, int(x1 * src.width)),
                max(1, int(y1 * src.height)),
            )
        )


def render(title: str, subtitle: str, panels: list[dict], notes: list[str] = ()) -> bytes:
    """Stack only available photos, with 40px (14.4px at 390px) minimum text.

    Each panel: jpeg, label, time, boxes, optional region/people/expected. A shared
    region for dish stages lets the reader compare changes without reframing.
    """
    if not panels:
        raise ValueError("No retained photos to render.")
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    titles = wrap(probe, title, 56, bold=True)
    subtitles = wrap(probe, subtitle)
    note_rows = [row for text in notes for row in wrap(probe, text)]
    y = 32 + len(titles) * 66 + len(subtitles) * 50 + 28
    panel_rows = []
    for panel in panels:
        rows = wrap(probe, panel["label"], bold=True)
        panel_rows.append(rows)
    height = y + sum(PHOTO_H + (len(rows) + 1) * 50 + 32 for rows in panel_rows)
    height += len(note_rows) * 50 + (28 if note_rows else 0)
    canvas = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(canvas)
    ty = 32
    for row in titles:
        draw.text((MARGIN, ty), row, font=font(56, True), fill=INK)
        ty += 66
    for row in subtitles:
        draw.text((MARGIN, ty), row, font=font(40), fill=MUTED)
        ty += 50
    for panel, rows in zip(panels, panel_rows):
        boxes = panel.get("boxes") or []
        person_boxes = panel.get('person_boxes', [])
        bounds = panel.get("region") or region([*boxes, *person_boxes], panel["jpeg"], people=panel.get("people", False))
        photo = crop(panel["jpeg"], bounds)
        # Contain edge crops so no source pixels needed for the evidence are cut.
        photo = ImageOps.contain(photo, (WIDTH - 2 * MARGIN, PHOTO_H), Image.Resampling.LANCZOS)
        px, py = (WIDTH - photo.width) // 2, y + (PHOTO_H - photo.height) // 2
        canvas.paste(photo, (px, py))
        if not panel.get("people") or person_boxes:
            x0, y0, x1, y1 = bounds
            for (bx, by, bw, bh), person in [(b, False) for b in boxes] + [(b, True) for b in person_boxes]:
                sx, sy = photo.width / (x1 - x0), photo.height / (y1 - y0)
                rect = (
                    max(px + 3, px + (bx - x0) * sx - 6),
                    max(py + 3, py + (by - y0) * sy - 6),
                    min(px + photo.width - 3, px + (bx + bw - x0) * sx + 6),
                    min(py + photo.height - 3, py + (by + bh - y0) * sy + 6),
                )
                if rect[2] > rect[0] and rect[3] > rect[1]:
                    color = BEFORE if person or panel.get("expected") else DISH
                    draw.rectangle(rect, outline="#111316", width=9)
                    draw.rectangle(rect, outline=color, width=4)
        y += PHOTO_H + 10
        for row in rows:
            draw.text((MARGIN, y), row, font=font(40, True), fill=INK)
            y += 50
        draw.text((MARGIN, y), panel["time"], font=font(40), fill=MUTED)
        y += 72
    for row in note_rows:
        draw.text((MARGIN, y), row, font=font(40), fill=MUTED)
        y += 50
    return jpeg(canvas)


def _signature(frame: dict) -> list[int]:
    boxes = frame.get("person_boxes") or frame.get("dish_boxes") or []
    patch = crop(frame["jpeg"], region(boxes, frame["jpeg"], people=bool(frame.get("person_boxes"))))
    return list(patch.convert("L").resize((24, 24)).tobytes())


def person_quality(frame: dict) -> float:
    """Prefer visible frontal faces, then large, detailed head crops. No identities."""
    score = 0.0
    for x, y, w, h in frame.get("person_boxes", []):
        patch = crop(frame["jpeg"], (max(0, x), max(0, y), min(1, x + w), min(1, y + min(h, w * 1.5))))
        gray = patch.convert("L")
        from PIL import ImageFilter

        sharp = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).stddev[0]
        face = False
        try:
            import numpy as np

            cascade = _face_cascade()
            if cascade is not None:
                thumb = ImageOps.contain(gray, (320, 320))
                face = (
                    len(
                        cascade.detectMultiScale(
                            np.asarray(thumb), scaleFactor=1.15, minNeighbors=4, minSize=(20, 20)
                        )
                    )
                    > 0
                )
        except ImportError:
            pass
        score = max(
            score,
            (10 if face else 0) + min(2, patch.width * patch.height / 50_000) + min(1, sharp / 50),
        )
    return score


def person_changed(picture: bytes, reference: bytes, box: list) -> bool:
    """Reject a journal person box that only depicts the unchanged background.

    The reference must have no journal person over this box. This is only a stale
    box veto, never a new person detection. Compare structure as well as raw
    brightness so day/night exposure changes cannot turn the counter into a
    person. A blown-out head patch is not readable naming context.
    """
    x, y, w, h = box
    head = crop(picture, (x, y, min(1, x+w), min(1, y+min(h,w*.8)))).convert('L').resize((24, 24))
    if sum(v > 230 for v in head.tobytes()) > .25 * 24 * 24:
        return False
    bounds = (x, y, min(1, x+w), min(1, y+min(h,w*2)))
    before = crop(picture, bounds).convert('L').resize((32, 48))
    after = crop(reference, bounds).convert('L').resize((32, 48))
    if ImageStat.Stat(ImageChops.difference(before, after)).mean[0] < 6:
        return False
    a, b = ImageStat.Stat(before), ImageStat.Stat(after)
    scale = a.stddev[0] * b.stddev[0]
    if scale > 1:
        correlation = sum((p-a.mean[0])*(q-b.mean[0]) for p,q in zip(before.tobytes(), after.tobytes())) / (32*48*scale)
        if correlation >= .8:
            return False
    return True


@cache
def _face_cascade():
    import cv2

    directory = getattr(getattr(cv2, "data", None), "haarcascades", None)
    if not directory:
        return None
    path = Path(directory) / "haarcascade_frontalface_default.xml"
    if not path.exists():
        return None
    cascade = cv2.CascadeClassifier(str(path))
    return None if cascade.empty() else cascade


def distinct_people(frames: list[dict], *, count=2) -> list[dict]:
    """Rank within a known arrival window and drop visually near-identical views."""
    chosen, signatures = [], []
    ranked = sorted((f for f in frames if f.get("person_boxes")), key=person_quality, reverse=True)
    # Do not let a large foreground visitor erase simultaneous people in reach.
    group = max(ranked, key=lambda f: len(f["person_boxes"]), default=None)
    if group and len(group["person_boxes"]) > 1:
        ranked.remove(group)
        ranked.insert(0, group)
    for frame in ranked:
        signature = _signature(frame)
        if any(sum(abs(a - b) for a, b in zip(signature, old)) / len(old) < 9 for old in signatures):
            continue
        chosen.append(frame)
        signatures.append(signature)
        if len(chosen) == count:
            break
    return sorted(chosen, key=lambda f: f["at"])
