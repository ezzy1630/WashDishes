"""Ready-to-send proof pictures for dishes still out after their deadline, made without anyone asking.

A dish qualifies once the camera has photographed it still there after its deadline. The proof worker
then follows it back through the rolling recording to whoever last used it (visits.py) and keeps four
pictures: the spot before, that person with the dish, the dish left there, and the late photo. A proof
is shown only while the dish is still out: once it is cleared, resolved or no longer seen, someone dealt
with it and there is nothing left to send.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import timezone as utc
from pathlib import Path
from typing import Protocol

from .collage import STAGES, dish_name, render
from .store import Store, parse_stamp, stamp, utcnow

LATE_LABEL = "Matching dish still visible after the deadline%"
STILL_OUT = timedelta(minutes=30)    # unseen this long means it was taken away
FOOTAGE = timedelta(hours=20)        # the recording keeps 24 h; older put-downs are gone
KEEP = timedelta(days=7)
RETRY = timedelta(minutes=5)
# Running out only costs a picture, never adds a wrong one; a longer budget only delays later dishes.
TRACE_SECONDS = 1200
# What a trace found besides its pictures, kept so a waiting proof and its collage still know it.
TRACE_KEYS = ("method", "use", "stay", "moves")


class Finder(Protocol):
    def __call__(self, box: list[float], logged: float, until: float, *, deadline: float | None = None) -> dict | str: ...


class Proofs:
    def __init__(self, root: Path, store: Store, media, check: Callable | None = None):
        self.dir = Path(root) / "proofs"
        self.store, self.media = store, media
        self.lock = threading.Lock()
        self.check = check
        with store.lock, store.conn:
            # Waiting retains its trace while a newer late photo is requested. Terminal failures
            # stay recorded so a bad sequence is not traced repeatedly.
            store.conn.execute("""CREATE TABLE IF NOT EXISTS proofs(obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id),
                status TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}')""")
            store.conn.execute("""CREATE TABLE IF NOT EXISTS proof_rejections(obligation_id INTEGER PRIMARY KEY,
                reason TEXT NOT NULL, created_at TEXT NOT NULL, location TEXT, box TEXT NOT NULL, embedding TEXT NOT NULL)""")

    # Dishes still out after their deadline with a late photo and no proof attempt yet. Dishes the camera
    # only found already sitting there (timing_known=0, e.g. after a restart) are included: finding their
    # put-down in the footage is exactly what this is for.
    WAITING = """FROM obligations o JOIN evidence e ON e.obligation_id=o.id AND e.kind='snapshot' AND e.expired=0 AND e.label LIKE ? AND e.created_at>=o.due_at
        WHERE o.archived_at IS NULL AND o.lifecycle='open' AND o.box IS NOT NULL AND o.due_at<=?
          AND o.created_at>=? AND coalesce(o.last_seen_at,o.created_at)>=?
          AND NOT EXISTS (SELECT 1 FROM proofs p WHERE p.obligation_id=o.id AND
              (p.status!='waiting' OR json_extract(p.data,'$.retry_at')>?
               OR json_extract(p.data,'$.late_at')>=unixepoch(e.created_at)))"""

    def _waiting_args(self, now: datetime) -> tuple:
        return LATE_LABEL, stamp(now), stamp(now - FOOTAGE), stamp(now - STILL_OUT), stamp(now)

    def candidate(self, now: datetime | None = None) -> dict | None:
        now = now or utcnow()
        with self.store.lock:
            row = self.store.conn.execute(f"""SELECT o.id, o.created_at, o.box, e.id late_id, e.filename late_file, e.created_at late_at, e.capture late_capture
                {self.WAITING} ORDER BY o.created_at, o.id, e.created_at DESC, e.id DESC LIMIT 1""", self._waiting_args(now)).fetchone()
        return dict(row) if row else None

    def progress(self, now: datetime | None = None) -> dict:
        """What is in the pipeline, so an empty page can say whether anything is coming."""
        now = now or utcnow()
        with self.store.lock:
            checking = self.store.conn.execute(f"SELECT count(DISTINCT o.id) {self.WAITING}", self._waiting_args(now)).fetchone()[0]
            upcoming = self.store.conn.execute("""SELECT count(*) FROM obligations WHERE archived_at IS NULL AND lifecycle='open' AND box IS NOT NULL
                AND due_at>? AND coalesce(last_seen_at,created_at)>=?""", (stamp(now), stamp(now - STILL_OUT))).fetchone()[0]
        return {"checking": checking, "upcoming": upcoming}

    def refresh_waiting(self, jpeg: bytes, inventory: list[dict], at: datetime) -> int:
        """Call after live inventory.process. Only a match from THIS observation can be a late photo.

        Ordinary deadline capture happens once. A waiting proof needs another matched observation,
        not an arbitrary camera snapshot or a cached inventory entry. One new photo per retry is enough.
        """
        observed = stamp(at)
        with self.store.lock:
            due = {r["obligation_id"] for r in self.store.conn.execute("""SELECT p.obligation_id FROM proofs p
                JOIN obligations o ON o.id=p.obligation_id
                WHERE p.status='waiting' AND json_extract(p.data,'$.retry_at')<=?
                  AND o.lifecycle='open' AND o.archived_at IS NULL AND o.last_seen_at=? AND o.due_at<=?
                  AND NOT EXISTS(SELECT 1 FROM evidence e WHERE e.obligation_id=o.id AND e.expired=0
                    AND e.kind='snapshot' AND e.label LIKE ? AND unixepoch(e.created_at)>json_extract(p.data,'$.late_at'))""",
                (observed, observed, observed, LATE_LABEL))}
        captured = 0
        for item in inventory:
            oid = item.get("obligation_id")
            if (oid not in due or item.get("last_seen_at") != observed or item.get("ignored")
                    or item.get("unseen_since") or item.get("role") not in ("dirty", "sink") or not item.get("box")):
                continue
            self.media.save_snapshot(oid, jpeg, "Matching dish still visible after the deadline; proof retry", at=at,
                                     capture={"stage": "later", "box": item["box"], "box_kind": "candidate_item"})
            due.remove(oid)
            captured += 1
        return captured

    def _save(self, obligation_id: int, status: str, data: dict) -> None:
        with self.store.lock, self.store.conn:
            self.store.conn.execute("INSERT OR REPLACE INTO proofs(obligation_id,status,created_at,data) VALUES(?,?,?,?)",
                                    (obligation_id, status, stamp(), json.dumps(data)))

    def run_once(self, find: Finder, identify: Callable | None = None, now: datetime | None = None,
                 *, check: Callable | None = None, identify_selected: Callable | None = None) -> int | None:
        """Trace the oldest waiting dish. `find(box, logged, until, deadline=)` returns the pictures and the
        spot (`box`) where its user had it, or why nobody can be named.
        `identify(obligation_id, jpeg, dish_box, at)` describes whoever is with the dish and
        returns their box, or None when no person is near the dish (then it is not proof of anyone).
        `identify_selected` receives the checker's person_box keyword AFTER the checked
        proof is saved, so immutable identity predictions can read its readiness. Waiting
        retains the trace and needs both the retry interval and a newer matching late photo."""
        now = now or utcnow()
        if check is not None:
            self.check = check
        item = self.candidate(now)
        if item is None:
            return None
        oid, box = item["id"], json.loads(item["box"])
        late_at = parse_stamp(item["late_at"]).timestamp()
        folder = self.dir / str(oid)
        with self.store.lock:
            previous = self.store.conn.execute("SELECT data FROM proofs WHERE obligation_id=? AND status='waiting'", (oid,)).fetchone()
        found = None
        if previous:
            saved = json.loads(previous["data"])
            try:
                found = {k: {"at": saved[f"{k}_at"], "jpeg": (folder / f"{k}.jpg").read_bytes()}
                         for k in ("before", "putting", "left")}
                found |= {"box": saved["box"], **{k: saved[k] for k in TRACE_KEYS if k in saved}}
            except (OSError, KeyError):
                found = None
        try:
            late_box = (json.loads(item["late_capture"] or "{}") or {}).get("box")
            late_jpeg = self.media.safe_path(item["late_file"]).read_bytes()
            if found is None:
                found = find(box, parse_stamp(item["created_at"]).timestamp(), late_at,
                             deadline=time.monotonic() + TRACE_SECONDS)
            # The pictures show where its user had the dish, which may be far from where it is now.
            checked = (found or "no put-down found") if not isinstance(found, dict) else (
                self.check(oid, found, late_jpeg, found.get("box", box), late_box) if self.check else {})
        except Exception as error:
            # The oldest dish is always tried first: footage or a photo that cannot be read
            # must close its attempt, or every later dish waits behind it for good.
            self._save(oid, "untraced", {"reason": f"could not read the footage or photos ({type(error).__name__})"})
            shutil.rmtree(folder, ignore_errors=True)
            return oid
        if isinstance(checked, str):
            self._save(oid, "untraced", {"reason": checked})
            shutil.rmtree(folder, ignore_errors=True)
            return oid
        box = found.get("box", box)
        trace = {k: found[k] for k in TRACE_KEYS if k in found}
        found = found | {k: checked[k] for k in ("before", "putting", "left") if k in checked}
        waiting = checked.get("status") == "waiting"
        person_box = found["putting"].get("person_box")
        args = (oid, found["putting"]["jpeg"], box, datetime.fromtimestamp(found["putting"]["at"], utc.utc))
        if identify is not None and identify_selected is None and not waiting:
            identified = identify(*args)
            if identified is None:
                self._save(oid, "untraced", {"reason": "nobody near the dish in the put-down picture"})
                return oid
            person_box = person_box or identified
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for key in ("before", "putting", "left"):
            (folder / f"{key}.jpg").write_bytes(found[key]["jpeg"])
        (folder / "late.jpg").write_bytes(late_jpeg)
        data = {"box": box, "person_box": person_box, "late_box": late_box, "checks": checked.get("checks", {}),
                **{f"{k}_at": found[k]["at"] for k in ("before", "putting", "left")}, "late_at": late_at, **trace}
        if waiting:
            data.update(reason=checked["reason"], retry_at=stamp(now + RETRY))
        self._save(oid, "waiting" if waiting else "ready", data)
        if identify_selected is not None and not waiting:
            # Identity is optional to a checked proof. If describing someone fails, keep the
            # valid unnamed pictures; the prediction must observe their actual ready state.
            identified = identify_selected(*args, person_box=person_box)
            if identified is None:
                self._save(oid, "untraced", {"reason": "nobody near the dish in the put-down picture"})
                shutil.rmtree(folder, ignore_errors=True)
            elif person_box is None:
                data["person_box"] = identified
                self._save(oid, "ready", data)
        return oid

    def items(self, now: datetime | None = None) -> list[dict]:
        """Proofs of dishes still out right now, newest put-down first."""
        now = now or utcnow()
        with self.store.lock:
            rows = self.store.conn.execute("""SELECT p.obligation_id id, p.data, o.dishes, o.location, o.resident_id, o.confidence,
                    o.suggested_resident_id, r.name resident_name, g.name guess_name
                FROM proofs p JOIN obligations o ON o.id=p.obligation_id
                LEFT JOIN residents r ON r.id=o.resident_id LEFT JOIN residents g ON g.id=o.suggested_resident_id
                WHERE p.status='ready' AND o.archived_at IS NULL AND o.lifecycle='open' AND coalesce(o.last_seen_at,o.created_at)>=?
                  -- A dish picked up and not seen since is gone from the counter: someone dealt with it, so
                  -- its card goes now, not when the record closes. Seen again, it comes back.
                  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.id=(SELECT max(id) FROM events WHERE obligation_id=o.id
                      AND event IN ('dish_picked_up','dish_moved')) AND e.event='dish_picked_up'
                      AND e.created_at>=coalesce(o.last_seen_at,o.created_at))""",
                (stamp(now - STILL_OUT),)).fetchall()
        out = []
        basis = self.store.suggestion_basis([r["id"] for r in rows]) if hasattr(self.store, "suggestion_basis") else {}
        for row in rows:
            data = json.loads(row["data"])
            named = row["confidence"] == "confirmed" and row["resident_id"] is not None
            out.append({"id": row["id"], "dish": dish_name(json.loads(row["dishes"]), data.get("checks", {}).get("looks_like")), "location": row["location"],
                        "put_down_at": stamp(datetime.fromtimestamp(data["putting_at"], utc.utc)),
                        "late_at": stamp(datetime.fromtimestamp(data["late_at"], utc.utc)),
                        "resident_id": row["resident_id"] if named else None, "resident_name": row["resident_name"] if named else None,
                        "guess_id": row["suggested_resident_id"], "guess_name": row["guess_name"],
                        "guess_basis": basis.get(row["id"]),
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

    def reject(self, obligation_id: int, reason: str = "wrong_photos") -> None:
        if reason not in {"not_a_dish", "wrong_photos"}:
            raise ValueError("Choose not_a_dish or wrong_photos.")
        with self.store.lock, self.store.conn:
            row = self.store.conn.execute("""SELECT p.data,o.location FROM proofs p JOIN obligations o ON o.id=p.obligation_id
                WHERE p.obligation_id=? AND p.status='ready'""", (obligation_id,)).fetchone()
            if row is None:
                raise LookupError("No proof for this dish.")
            data = json.loads(row["data"])
        embedding = data.get("checks", {}).get("left_embedding")
        if embedding is None and self.check is not None and hasattr(self.check, "embedding"):
            embedding = self.check.embedding((self.dir / str(obligation_id) / "left.jpg").read_bytes(), data["box"])
        now = utcnow()
        with self.store.lock, self.store.conn:
            data.update(reason=reason, rejected_at=stamp(now))
            if not self.store.conn.execute("UPDATE proofs SET status='rejected',data=? WHERE obligation_id=? AND status='ready'",
                                           (json.dumps(data), obligation_id)).rowcount:
                raise LookupError("No proof for this dish.")
            if embedding is not None:
                self.store.conn.execute("INSERT OR REPLACE INTO proof_rejections VALUES(?,?,?,?,?,?)",
                                        (obligation_id, reason, stamp(now), row["location"], json.dumps(data["box"]), json.dumps(embedding)))
            if reason == "not_a_dish":
                self.store._action_locked(obligation_id, "dismiss", note="Not a dish", now=now)
        shutil.rmtree(self.dir / str(obligation_id), ignore_errors=True)

    def cleanup(self, now: datetime | None = None) -> None:
        """Pictures are kept a week; the row stays so the dish is never traced again."""
        now = now or utcnow()
        with self.store.lock:
            old = [r[0] for r in self.store.conn.execute("SELECT obligation_id FROM proofs WHERE created_at<?", (stamp(now - KEEP),))]
        for oid in old:
            shutil.rmtree(self.dir / str(oid), ignore_errors=True)
