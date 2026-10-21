"""Ready-to-send proof pictures for dishes still out after their deadline, made without anyone asking.

A dish qualifies once the camera has photographed it still there after its deadline. The proof worker
then finds the moment it was put down in the rolling recording (putdown.py) and keeps four pictures:
the empty spot, the person putting it down, the dish left there, and the late photo. A proof is shown
only while the dish is still out: once it is cleared, resolved or no longer seen, someone dealt with it
and there is nothing left to send.
"""
from __future__ import annotations

import io
import json
import shutil
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import timezone as utc
from pathlib import Path

from PIL import Image, ImageDraw

from .notify import timezone
from .sequences import _font
from .store import Store, parse_stamp, stamp, utcnow

LATE_LABEL = "Matching dish still visible after the deadline%"
STILL_OUT = timedelta(minutes=30)    # unseen this long means it was taken away
FOOTAGE = timedelta(hours=20)        # the recording keeps 24 h; older put-downs are gone
KEEP = timedelta(days=7)
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


class Proofs:
    def __init__(self, root: Path, store: Store, media):
        self.dir = Path(root) / "proofs"
        self.store, self.media = store, media
        self.lock = threading.Lock()
        with store.lock, store.conn:
            # One row per dish ever considered, so nothing is traced twice. status: ready (shown while
            # the dish is out), untraced (footage shows no put-down), rejected (marked wrong by a person).
            store.conn.execute("""CREATE TABLE IF NOT EXISTS proofs(obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id),
                status TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}')""")

    # Dishes still out after their deadline with a late photo and no proof attempt yet. Dishes the camera
    # only found already sitting there (timing_known=0, e.g. after a restart) are included: finding their
    # put-down in the footage is exactly what this is for.
    WAITING = """FROM obligations o JOIN evidence e ON e.obligation_id=o.id AND e.kind='snapshot' AND e.expired=0 AND e.label LIKE ? AND e.created_at>=o.due_at
        WHERE o.archived_at IS NULL AND o.lifecycle='open' AND o.box IS NOT NULL AND o.due_at<=?
          AND o.created_at>=? AND coalesce(o.last_seen_at,o.created_at)>=?
          AND NOT EXISTS (SELECT 1 FROM proofs p WHERE p.obligation_id=o.id)"""

    def _waiting_args(self, now: datetime) -> tuple:
        return LATE_LABEL, stamp(now), stamp(now - FOOTAGE), stamp(now - STILL_OUT)

    def candidate(self, now: datetime | None = None) -> dict | None:
        now = now or utcnow()
        with self.store.lock:
            row = self.store.conn.execute(f"""SELECT o.id, o.created_at, o.box, e.id late_id, e.filename late_file, e.created_at late_at, e.capture late_capture
                {self.WAITING} ORDER BY o.created_at, e.created_at LIMIT 1""", self._waiting_args(now)).fetchone()
        return dict(row) if row else None

    def progress(self, now: datetime | None = None) -> dict:
        """What is in the pipeline, so an empty page can say whether anything is coming."""
        now = now or utcnow()
        with self.store.lock:
            checking = self.store.conn.execute(f"SELECT count(DISTINCT o.id) {self.WAITING}", self._waiting_args(now)).fetchone()[0]
            upcoming = self.store.conn.execute("""SELECT count(*) FROM obligations WHERE archived_at IS NULL AND lifecycle='open' AND box IS NOT NULL
                AND due_at>? AND coalesce(last_seen_at,created_at)>=?""", (stamp(now), stamp(now - STILL_OUT))).fetchone()[0]
        return {"checking": checking, "upcoming": upcoming}

    def _save(self, obligation_id: int, status: str, data: dict) -> None:
        with self.store.lock, self.store.conn:
            self.store.conn.execute("INSERT OR REPLACE INTO proofs(obligation_id,status,created_at,data) VALUES(?,?,?,?)",
                                    (obligation_id, status, stamp(), json.dumps(data)))

    def run_once(self, find: Callable, identify: Callable | None = None, now: datetime | None = None) -> int | None:
        """Trace the oldest waiting dish. `find(box, logged, until)` is putdown.find_putdown bound to the
        recording; `identify(obligation_id, jpeg, dish_box, at)` describes whoever is putting it down and
        returns their box, or None when no person is near the dish (then it is not proof of anyone)."""
        item = self.candidate(now)
        if item is None:
            return None
        oid, box = item["id"], json.loads(item["box"])
        late_at = parse_stamp(item["late_at"]).timestamp()
        found = find(box, parse_stamp(item["created_at"]).timestamp(), late_at)
        if not isinstance(found, dict):
            self._save(oid, "untraced", {"reason": found or "no put-down found"})
            return oid
        person_box = None
        if identify is not None:
            person_box = identify(oid, found["putting"]["jpeg"], box, datetime.fromtimestamp(found["putting"]["at"], utc.utc))
            if person_box is None:
                self._save(oid, "untraced", {"reason": "nobody near the dish in the put-down picture"})
                return oid
        folder = self.dir / str(oid)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for key in ("before", "putting", "left"):
            (folder / f"{key}.jpg").write_bytes(found[key]["jpeg"])
        shutil.copyfile(self.media.safe_path(item["late_file"]), folder / "late.jpg")
        late_box = (json.loads(item["late_capture"] or "{}") or {}).get("box")
        self._save(oid, "ready", {"box": box, "person_box": person_box, "late_box": late_box,
                                  **{f"{k}_at": found[k]["at"] for k in ("before", "putting", "left")}, "late_at": late_at})
        return oid

    def items(self, now: datetime | None = None) -> list[dict]:
        """Proofs of dishes still out right now, newest put-down first."""
        now = now or utcnow()
        with self.store.lock:
            rows = self.store.conn.execute("""SELECT p.obligation_id id, p.data, o.dishes, o.location, o.resident_id, o.confidence,
                    o.suggested_resident_id, r.name resident_name, g.name guess_name
                FROM proofs p JOIN obligations o ON o.id=p.obligation_id
                LEFT JOIN residents r ON r.id=o.resident_id LEFT JOIN residents g ON g.id=o.suggested_resident_id
                WHERE p.status='ready' AND o.archived_at IS NULL AND o.lifecycle='open' AND coalesce(o.last_seen_at,o.created_at)>=?""",
                (stamp(now - STILL_OUT),)).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["data"])
            named = row["confidence"] == "confirmed" and row["resident_id"] is not None
            out.append({"id": row["id"], "dish": _singular(self.store.dish_description(json.loads(row["dishes"]))), "location": row["location"],
                        "put_down_at": stamp(datetime.fromtimestamp(data["putting_at"], utc.utc)),
                        "late_at": stamp(datetime.fromtimestamp(data["late_at"], utc.utc)),
                        "resident_id": row["resident_id"] if named else None, "resident_name": row["resident_name"] if named else None,
                        "guess_id": row["suggested_resident_id"], "guess_name": row["guess_name"],
                        "image_url": f"/api/proofs/{row['id']}/image?v={row['resident_id'] if named else 0}"})
        return sorted(out, key=lambda p: p["put_down_at"], reverse=True)

    def image(self, obligation_id: int) -> bytes:
        item = next((p for p in self.items() if p["id"] == obligation_id), None)
        if item is None:
            raise LookupError("This dish is no longer out.")
        folder = self.dir / str(obligation_id)
        cached = folder / f"collage-{item['resident_id'] or 0}.jpg"
        with self.lock:
            if not cached.exists():
                with self.store.lock:
                    data = json.loads(self.store.conn.execute("SELECT data FROM proofs WHERE obligation_id=?", (obligation_id,)).fetchone()["data"])
                pictures = {key: (folder / f"{key}.jpg").read_bytes() for key in STAGES}
                meta = data | {"dish": item["dish"], "location": item["location"] or "Kitchen"}
                tmp = cached.with_suffix(".tmp")
                tmp.write_bytes(render(meta, pictures, item["resident_name"]))
                tmp.replace(cached)
            return cached.read_bytes()

    def reject(self, obligation_id: int) -> None:
        with self.store.lock, self.store.conn:
            if not self.store.conn.execute("UPDATE proofs SET status='rejected' WHERE obligation_id=? AND status='ready'", (obligation_id,)).rowcount:
                raise LookupError("No proof for this dish.")
        shutil.rmtree(self.dir / str(obligation_id), ignore_errors=True)

    def cleanup(self, now: datetime | None = None) -> None:
        """Pictures are kept a week; the row stays so the dish is never traced again."""
        now = now or utcnow()
        with self.store.lock:
            old = [r[0] for r in self.store.conn.execute("SELECT obligation_id FROM proofs WHERE created_at<?", (stamp(now - KEEP),))]
        for oid in old:
            shutil.rmtree(self.dir / str(oid), ignore_errors=True)


def _singular(description: str) -> str:
    # "1 cup" reads as data; one dish is just "cup".
    return description[2:] if description.startswith("1 ") and "," not in description else description
