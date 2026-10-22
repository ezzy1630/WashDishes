"""Bounded detector history, separate from the household's transactional database.

Only detections are retained, never pictures or face features. A crash can lose the
pending five seconds; committed batches remain atomic. Empty frames matter too:
without them an absent dish and a stopped camera would look identical.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

KINDS = frozenset(("person", "plate", "bowl", "cup", "fork", "knife", "spoon", "pot", "pan"))


class Journal:
    def __init__(self, path: Path, *, max_fps: float = 4, batch_seconds: float = 5,
                 batch_size: int = 32):
        if not math.isfinite(max_fps) or max_fps <= 0 or not 0 < batch_seconds <= 60 or batch_size < 1:
            raise ValueError("Journal needs a positive rate and bounded batches.")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, timeout=2, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA cache_size=-1024")
        self.conn.execute("CREATE TABLE IF NOT EXISTS frames(at REAL PRIMARY KEY, detections TEXT NOT NULL) WITHOUT ROWID")
        self.conn.commit()
        self.max_fps, self.batch_seconds, self.batch_size = max_fps, batch_seconds, batch_size
        self.pending: list[tuple[float, str]] = []
        self.last_at: float | None = None
        self.last_flush = time.monotonic()
        self.closed = False

    def record(self, at: float, detections: Iterable[dict]) -> bool:
        """Queue one analyzed frame. No upsampling; duplicate/over-rate calls are skipped."""
        if not math.isfinite(at):
            raise ValueError("Frame time must be finite.")
        with self.lock:
            # Disk-full/locked failures must not turn an optional history into an
            # unbounded RAM queue. Preserve the failed batch and let the caller retry.
            if len(self.pending) >= self.batch_size:
                self.flush()
            if self.last_at is not None and abs(at - self.last_at) < 1 / self.max_fps - 1e-6:
                return False
            kept = []
            for detection in detections:
                kind, score, box = detection.get("kind"), detection.get("score"), detection.get("box")
                if not isinstance(kind, str) or kind not in KINDS or not isinstance(score, (int, float)) or not math.isfinite(score) or not .2 <= score <= 1:
                    continue
                if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in box):
                    continue
                x, y, w, h = (round(float(v), 4) for v in box)
                if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.0001 or y + h > 1.0001:
                    continue
                item = {"kind": kind, "score": round(score, 4), "box": [x, y, w, h]}
                if isinstance(detection.get("track_id"), int):
                    item["track_id"] = detection["track_id"]
                kept.append(item)
            self.pending.append((at, json.dumps(kept, separators=(",", ":"))))
            self.last_at = at
            if len(self.pending) >= self.batch_size or time.monotonic() - self.last_flush >= self.batch_seconds:
                self.flush()
            return True

    def flush(self) -> None:
        with self.lock:
            if self.pending:
                with self.conn:
                    self.conn.executemany("INSERT OR REPLACE INTO frames VALUES(?,?)", self.pending)
                self.pending.clear()
            self.last_flush = time.monotonic()

    def prune(self, now: float, buffer_hours: float) -> int:
        """Call from the existing minute-scale cleanup loop; pages are reused."""
        if not math.isfinite(now) or not math.isfinite(buffer_hours) or buffer_hours <= 0:
            raise ValueError("Retention needs a finite time and positive buffer hours.")
        with self.lock:
            self.flush()
            with self.conn:
                return self.conn.execute("DELETE FROM frames WHERE at < ?", (now - buffer_hours * 3600,)).rowcount

    def frames(self, t0: float, t1: float) -> Iterator[dict]:
        """Stream an ordered snapshot without holding the writer's lock during a search."""
        self.flush()
        connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            connection.execute("PRAGMA cache_size=-1024")
            for at, detections in connection.execute("SELECT at,detections FROM frames WHERE at>=? AND at<=? ORDER BY at", (t0, t1)):
                yield {"at": at, "detections": json.loads(detections)}
        finally:
            connection.close()

    def bounds(self) -> tuple[float, float] | None:
        with self.lock:
            self.flush()
            first = self.conn.execute("SELECT at FROM frames ORDER BY at LIMIT 1").fetchone()
            last = self.conn.execute("SELECT at FROM frames ORDER BY at DESC LIMIT 1").fetchone()
            return (first[0], last[0]) if first else None

    def close(self) -> None:
        with self.lock:
            if not self.closed:
                self.flush()
                self.conn.close()
                self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
