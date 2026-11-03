"""Read full-resolution retained footage for presentation; never invent a stage."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from PIL import Image


class SourceFrames:
    def __init__(self, buffer: Path):
        from .filmstrip import footage_segments

        self.segments = footage_segments(buffer)
        self.cache = {}
        self.journal = buffer.parent / "journal.db"

    def at(self, at: float):
        from .filmstrip import _photo_at

        key = round(at, 3)
        if key not in self.cache:
            self.cache[key] = _photo_at(self.segments, at)
        photo = self.cache[key]
        # Segment mtimes estimate wall clocks. Never borrow across a gap or use a
        # distant keyframe as the saved stage; retain the original still instead.
        return photo if photo and abs(photo[0] - at) <= 0.75 else None

    def best(self, at: float | None, fallback: bytes) -> tuple[bytes, dict]:
        photo = self.at(at) if at is not None else None
        with Image.open(io.BytesIO(fallback)) as src:
            old_size = src.size
        if photo:
            with Image.open(io.BytesIO(photo[1])) as src:
                size = src.size
            if size[0] * size[1] > old_size[0] * old_size[1]:
                return photo[1], {
                    "source": "buffer",
                    "at": photo[0],
                    "timestamp_estimated": True,
                    "width": size[0],
                    "height": size[1],
                }
        return fallback, {"source": "saved_still", "width": old_size[0], "height": old_size[1]}

    def people(self, at: float, boxes: list) -> list:
        if not self.journal.is_file():
            return []
        from .putdown import _reach

        with sqlite3.connect(self.journal.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT detections FROM frames WHERE at BETWEEN ? AND ? ORDER BY abs(at-?) LIMIT 1",
                (at - 0.75, at + 0.75, at),
            ).fetchone()
        return (
            [
                d["box"]
                for d in json.loads(row[0])
                if d["kind"] == "person" and any(_reach(b, d["box"]) <= 0.1 for b in boxes)
            ]
            if row
            else []
        )
