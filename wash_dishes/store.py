from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULT_SETTINGS, validate_settings
from .evidence import evidence_timeline

# Legacy ranking features remain useful for fit diagnostics. Displaying a guess additionally requires
# an absolute visual match and separation from the next resident; clothing never crosses local dates.
IDENTITY_CUES = {"face": (0.25, 8.0), "head": (0.78, 10.0), "outfit_today": (0.75, 10.0), "outfit": (0.88, 1.5)}
STATURE_SPREAD = 0.02
IDENTITY_WEIGHTS_FILE = "identity-weights.json"
# A dismissal with one of these notes means "the camera saw something that isn't a dish". Other
# dismissals (already there at startup, a different dish, system cleanups) say nothing about that.
NOT_A_DISH_NOTES = ("", "Not a dish")
UNCLAIMED_NOTE = "Gone before anyone knew whose it was"
# Closed as a real dish whose owner nobody could see: a dish for training, no one's for the scoreboard.
CANT_TELL_NOTE = "Can't tell whose it was"
# A dish picked up and not put down again in view within this time was carried away (inventory.MOVE_WINDOW).
CARRY_WINDOW = timedelta(minutes=30)
# An unnamed dish the camera has not seen for this long is gone; nobody names the owner of a dish that is no longer out.
UNPROVABLE_AFTER = timedelta(hours=24)
UNPROVABLE_NOTE = "Gone before anyone named whose it was"
# Closed by the camera after the deadline: the dish is no longer out, but it was out late.
TAKEN_LATE_NOTE = "Taken away after its deadline and not put down again in view. Still late; whether it was washed was not observed."
WASHED_LATE_NOTE = "Seen going through the sink onto a clean rack after its deadline. Still late; washing itself was not verified."
REMOVED_REVIEWED_NOTE = 'Removal confirmed by a person; washing was not observed.'
PUT_AWAY_NOTE = 'Placed in a drying or storage area. Washing itself was not observed.'
CAMERA_CLOSED_NOTES = (TAKEN_LATE_NOTE, WASHED_LATE_NOTE, REMOVED_REVIEWED_NOTE, PUT_AWAY_NOTE)
# Camera naming turns on only once human-checked suggestions are measured accurate enough, over the
# most recent checks so a drop in accuracy turns it off again. Every fifth eligible record stays a
# human spot check, because corrections of camera-named records alone would only ever record mistakes.
AUTO_MIN_CHECKED = 30
AUTO_MIN_ACCURACY = 0.9
AUTO_WINDOW = 50
AUTO_SPOT_CHECK_EVERY = 5
AUTO_MAX_AGE = timedelta(hours=24)
COLORS = ("#496855", "#596d8c", "#a07653", "#877294", "#a36969", "#56858a", "#7c8051", "#796a57", "#6b769e", "#927450")
DISH_KINDS = {"plate", "bowl", "cup", "mug", "glass", "pot", "pan", "fork", "knife", "spoon", "cutlery", "other"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_dishes(dishes: list) -> list[dict]:
    if not isinstance(dishes, list) or not 1 <= len(dishes) <= 12:
        raise ValueError("Add at least one dish, with at most twelve dish types.")
    combined: dict[str, int] = {}
    for dish in dishes:
        if not isinstance(dish, dict) or dish.get("kind") not in DISH_KINDS:
            raise ValueError("Select a supported dish type.")
        quantity = dish.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100:
            raise ValueError("Dish quantities must be whole numbers between 1 and 100.")
        kind = dish["kind"]
        combined[kind] = combined.get(kind, 0) + quantity
    if sum(combined.values()) > 100:
        raise ValueError("A dish event can contain at most 100 dishes.")
    return [{"kind": kind, "quantity": count} for kind, count in combined.items()]


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        self._weights_cache: tuple[float | None, dict[str, float]] = (None, {})
        self.conn = sqlite3.connect(root / "history.db", check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS residents(
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS resident_active_name ON residents(lower(name)) WHERE active=1;
            CREATE TABLE IF NOT EXISTS obligations(
                id INTEGER PRIMARY KEY, resident_id INTEGER REFERENCES residents(id),
                dishes TEXT NOT NULL, created_at TEXT NOT NULL, due_at TEXT NOT NULL,
                resolved_at TEXT, cleaner_id INTEGER REFERENCES residents(id),
                lifecycle TEXT NOT NULL DEFAULT 'open', confidence TEXT NOT NULL,
                source TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT, overdue_verified INTEGER NOT NULL DEFAULT 0,
                deadline_notified INTEGER NOT NULL DEFAULT 0, review_pending INTEGER NOT NULL DEFAULT 0,
                track_key TEXT, original_resident_id INTEGER REFERENCES residents(id));
            CREATE INDEX IF NOT EXISTS obligations_due ON obligations(lifecycle,due_at);
            CREATE INDEX IF NOT EXISTS obligations_track ON obligations(track_key);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY, obligation_id INTEGER REFERENCES obligations(id),
                created_at TEXT NOT NULL, event TEXT NOT NULL, description TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS evidence(
                id TEXT PRIMARY KEY, obligation_id INTEGER REFERENCES obligations(id),
                filename TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, label TEXT NOT NULL, expired INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS evidence_expiry ON evidence(expired,expires_at);
            CREATE TABLE IF NOT EXISTS evidence_sequences(
                obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id),
                fingerprint TEXT NOT NULL, image_id TEXT REFERENCES evidence(id),
                manifest TEXT NOT NULL, checks TEXT NOT NULL DEFAULT '{}', reviewed_at TEXT);
            CREATE TABLE IF NOT EXISTS incident_decisions(
                obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scene_state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS person_samples(
                id INTEGER PRIMARY KEY, obligation_id INTEGER REFERENCES obligations(id),
                resident_id INTEGER REFERENCES residents(id), created_at TEXT NOT NULL,
                embedding TEXT NOT NULL, evidence_id TEXT);
            CREATE INDEX IF NOT EXISTS person_samples_resident ON person_samples(resident_id);
            CREATE INDEX IF NOT EXISTS person_samples_obligation ON person_samples(obligation_id);
            -- Every dish the live inventory saw put down or picked up, to score it against hand-checked footage.
            CREATE TABLE IF NOT EXISTS dish_changes(at TEXT NOT NULL, area TEXT NOT NULL, change TEXT NOT NULL, kind TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS dish_changes_at ON dish_changes(at);
            -- What the second look saw for each new box; its label comes from what people later decided.
            CREATE TABLE IF NOT EXISTS dish_samples(
                id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, embedding TEXT NOT NULL, score REAL,
                obligation_id INTEGER REFERENCES obligations(id), label INTEGER);
            CREATE INDEX IF NOT EXISTS dish_samples_obligation ON dish_samples(obligation_id);
            -- Twice-daily look at every counter: what was out, and what a person said about it.
            CREATE TABLE IF NOT EXISTS kitchen_checks(
                id INTEGER PRIMARY KEY, slot TEXT NOT NULL UNIQUE, at TEXT, photo TEXT, dishes TEXT NOT NULL DEFAULT '[]',
                frame_id TEXT, answered_at TEXT, answer TEXT, status TEXT NOT NULL DEFAULT 'pending');
        """)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(obligations)")}
        for name, definition in (("timing_known", "INTEGER NOT NULL DEFAULT 1"), ("location", "TEXT NOT NULL DEFAULT ''"),
                                 ("suggested_resident_id", "INTEGER REFERENCES residents(id)"), ("suggestion_score", "REAL"),
                                 ("suggestion_correct", "INTEGER"), ("handler_key", "TEXT"),
                                 # NULL: named by a person. 'camera': named automatically. 'camera_reviewed': a person then confirmed or corrected it.
                                 ("attribution", "TEXT"),
                                 # Where the dish was in the "left unattended" photo: [x, y, w, h], for training from Review answers.
                                 ("box", "TEXT"), ("archived_at", "TEXT")):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE obligations ADD COLUMN {name} {definition}")
        resident_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(residents)")}
        # A personal link is shown once; only its hash is kept. notify_url is the person's own ntfy topic.
        for name, definition in (("link_hash", "TEXT"), ("notify_url", "TEXT NOT NULL DEFAULT ''")):
            if name not in resident_columns:
                self.conn.execute(f"ALTER TABLE residents ADD COLUMN {name} {definition}")
        sample_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(person_samples)")}
        for name, definition in (("cue", "TEXT NOT NULL DEFAULT 'outfit'"), ("context", "TEXT"), ("hint_resident_id", "INTEGER REFERENCES residents(id)")):
            if name not in sample_columns:
                self.conn.execute(f"ALTER TABLE person_samples ADD COLUMN {name} {definition}")
        if "capture" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(evidence)")}:
            self.conn.execute("ALTER TABLE evidence ADD COLUMN capture TEXT NOT NULL DEFAULT '{}'")
        self.conn.execute("PRAGMA user_version=7")
        self.conn.commit()
        (root / "history.db").chmod(0o600)

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def settings(self, *, private: bool = False) -> dict:
        with self.lock:
            settings = json.loads(json.dumps(DEFAULT_SETTINGS))
            rows = self.conn.execute("SELECT key,value FROM settings").fetchall()
            settings.update({row["key"]: json.loads(row["value"]) for row in rows if private or row["key"] != "camera"})
            return settings

    def set_settings(self, patch: dict) -> dict:
        validate_settings(patch)
        with self.lock, self.conn:
            for key, value in patch.items():
                self.conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            self._event(None, "settings_updated", "Household settings updated", {"keys": list(patch)})
        return self.settings()

    def set_camera(self, camera: dict) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO settings VALUES('camera',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(camera),))
            self.conn.execute("INSERT INTO settings VALUES('camera_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(camera["enabled"]),))
            self._event(None, "camera_configured", "Camera connection updated")

    def _event(self, obligation_id: int | None, event: str, description: str, data: dict | None = None, at: datetime | None = None) -> None:
        self.conn.execute("INSERT INTO events(obligation_id,created_at,event,description,data) VALUES(?,?,?,?,?)", (obligation_id, stamp(at), event, description, json.dumps(data or {})))

    def event(self, event: str, description: str, obligation_id: int | None = None, data: dict | None = None) -> None:
        with self.lock, self.conn:
            self._event(obligation_id, event, description, data)

    def events_after(self, event_id: int, kinds: tuple[str, ...], limit: int = 20) -> list[dict]:
        with self.lock:
            marks = ",".join("?" * len(kinds))
            return [dict(row) for row in self.conn.execute(f"SELECT * FROM events WHERE id>? AND event IN ({marks}) ORDER BY id LIMIT ?", (event_id, *kinds, limit))]

    def latest_event_id(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT coalesce(max(id),0) FROM events").fetchone()[0]

    def residents(self) -> list[dict]:
        with self.lock:
            result = []
            for row in self.conn.execute("SELECT * FROM residents ORDER BY active DESC,id"):
                item = {**dict(row), "active": bool(row["active"]), "has_link": bool(row["link_hash"])}
                del item["link_hash"]
                result.append(item)
            return result

    def _resident(self, resident_id: int | None, *, active: bool = True) -> sqlite3.Row | None:
        if resident_id is None:
            return None
        row = self.conn.execute("SELECT * FROM residents WHERE id=?", (resident_id,)).fetchone()
        if not row or (active and not row["active"]):
            raise ValueError("Select an active housemate.")
        return row

    def add_resident(self, name: str) -> dict:
        name = name.strip()
        if not name or len(name) > 60 or any(ord(char) < 32 for char in name):
            raise ValueError("Enter a name between 1 and 60 characters.")
        with self.lock, self.conn:
            count = self.conn.execute("SELECT count(*) FROM residents").fetchone()[0]
            if self.conn.execute("SELECT count(*) FROM residents WHERE active=1").fetchone()[0] >= 30:
                raise ValueError("At most 30 active housemates are supported.")
            try:
                cursor = self.conn.execute("INSERT INTO residents(name,color,created_at) VALUES(?,?,?)", (name, COLORS[count % len(COLORS)], stamp()))
            except sqlite3.IntegrityError:
                raise ValueError("An active housemate already has that name.") from None
            self._event(None, "resident_added", f"{name} joined the household")
            return dict(self._resident(cursor.lastrowid))

    def archive_resident(self, resident_id: int) -> None:
        with self.lock, self.conn:
            resident = self._resident(resident_id)
            self.conn.execute("UPDATE residents SET active=0 WHERE id=?", (resident_id,))
            self._event(None, "resident_archived", f"{resident['name']} archived; history retained")

    def create_obligation(self, resident_id: int | None, dishes: list, note: str = "", *, source: str = "manual", at: datetime | None = None, candidate: bool = False, track_key: str | None = None, reason: str = "", timing_known: bool = True, location: str = "", scene: list[dict] | None = None, handler_key: str | None = None, box: list[float] | None = None) -> dict:
        dishes = validate_dishes(dishes)
        if len(note) > 1000:
            raise ValueError("Notes can contain at most 1000 characters.")
        at = at or utcnow()
        if at > utcnow() + timedelta(minutes=1):
            raise ValueError("The deposit cannot be in the future.")
        with self.lock, self.conn:
            resident = self._resident(resident_id)
            deadline = at + timedelta(minutes=self.settings()["deadline_minutes"])
            confidence = "candidate" if candidate else ("confirmed" if resident else "unknown")
            cursor = self.conn.execute("""INSERT INTO obligations(resident_id,dishes,created_at,due_at,confidence,source,note,reason,track_key,original_resident_id,last_seen_at,timing_known,location,handler_key,box)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (resident_id, json.dumps(dishes), stamp(at), stamp(deadline), confidence, source, note.strip(), reason, track_key, resident_id, stamp(at), int(timing_known), location, handler_key,
                                                           json.dumps([round(v, 5) for v in box]) if box else None))
            obligation_id = cursor.lastrowid
            if scene is not None:
                for observed in scene:
                    if observed["key"] == track_key:
                        observed["obligation_id"] = obligation_id
                self.conn.execute("INSERT INTO scene_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(scene),))
            who = resident["name"] if resident else "Someone"
            description = ("Already present: " if not timing_known else "Possible unattended dish: ") + self.dish_description(dishes) if candidate else f"{who} left {self.dish_description(dishes)}"
            if location:
                description += f" · {location}"
            self._event(obligation_id, "dish_deposited", description, {"resident_id": resident_id, "confidence": confidence, "source": source}, at)
            return self.obligation(obligation_id)

    @staticmethod
    def dish_description(dishes: list[dict]) -> str:
        return ", ".join(f"{dish['quantity']} {dish['kind']}{'s' if dish['quantity'] != 1 else ''}" for dish in dishes)

    def _format(self, row: sqlite3.Row, now: datetime) -> dict:
        item = dict(row)
        if item["lifecycle"] != "open":
            status = item["lifecycle"]
        elif item["confidence"] != "confirmed" or item["review_pending"]:
            status = "review"
        else:
            status = "overdue" if item["timing_known"] and parse_stamp(item["due_at"]) <= now else ("pending" if item["timing_known"] else "review")
        item.update(status=status, dishes=json.loads(item["dishes"]), overdue_verified=bool(item["overdue_verified"]), timing_known=bool(item["timing_known"]),
                    closed_by_camera=item["lifecycle"] == "resolved" and item["reason"] in CAMERA_CLOSED_NOTES,
                    box=json.loads(item["box"]) if item.get("box") else None)
        item["followup_at"] = item["due_at"]
        if not item["timing_known"]:
            item["due_at"] = None
        item["evidence"] = [dict(e) | {"capture": json.loads(e["capture"]), "url": None if e["expired"] else f"/api/evidence/{e['id']}", "expired": bool(e["expired"])} for e in self.conn.execute("SELECT id,kind,created_at,expires_at,label,expired,capture FROM evidence WHERE obligation_id=? ORDER BY created_at", (item["id"],))]
        return item

    def obligation(self, obligation_id: int, *, now: datetime | None = None) -> dict:
        with self.lock:
            row = self.conn.execute("""SELECT o.*, r.name resident_name,c.name cleaner_name,s.name suggested_name FROM obligations o
                LEFT JOIN residents r ON r.id=o.resident_id LEFT JOIN residents c ON c.id=o.cleaner_id
                LEFT JOIN residents s ON s.id=o.suggested_resident_id WHERE o.id=?""", (obligation_id,)).fetchone()
            if not row:
                raise LookupError("Dish event not found.")
            item = self._format(row, now or utcnow())
            item["events"] = [dict(e) | {"data": json.loads(e["data"])} for e in self.conn.execute("SELECT * FROM events WHERE obligation_id=? ORDER BY id DESC", (obligation_id,))]
            names = {r["id"]: r["name"] for r in self.conn.execute("SELECT id,name FROM residents")}
            item["evidence_timeline"] = evidence_timeline(item, names, now or utcnow())
            return item

    def obligations(self, *, history: bool = False, status: str | None = None, resident_id: int | None = None, date: str | None = None, now: datetime | None = None) -> list[dict]:
        now = now or utcnow()
        params: list = []
        where = ["o.archived_at IS NULL"]
        if not history:
            where.append("o.lifecycle IN ('open','disputed')")
        if resident_id is not None:
            where.append("o.resident_id=?")
            params.append(resident_id)
        if date:
            tz = ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))
            start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
            where.append("o.created_at>=? AND o.created_at<?")
            params += [stamp(start), stamp(start + timedelta(days=1))]
        query = """SELECT o.*, r.name resident_name,c.name cleaner_name,s.name suggested_name FROM obligations o
            LEFT JOIN residents r ON r.id=o.resident_id LEFT JOIN residents c ON c.id=o.cleaner_id
            LEFT JOIN residents s ON s.id=o.suggested_resident_id"""
        if where:
            query += " WHERE " + " AND ".join(where)
        # Apply status before the history limit so a rare status is not hidden by recent rows.
        with self.lock:
            rows = self.conn.execute(query + " ORDER BY o.created_at DESC,o.id DESC", params).fetchall()
            result = [self._format(row, now) for row in rows]
            if status:
                result = [item for item in result if item["status"] == status]
            return result[:500] if history else result

    def archive_count(self) -> int:
        with self.lock:
            return self.conn.execute('SELECT count(*) FROM obligations WHERE archived_at IS NOT NULL').fetchone()[0]

    def archive_through(self, obligation_id: int) -> int:
        """Start a fresh feed without changing outcomes, labels, or retained evidence.

        Call with monitoring stopped so its in-memory scene cannot overwrite the reset.
        Existing objects become background until an observed physical change.
        """
        with self.lock, self.conn:
            count = self.conn.execute('UPDATE obligations SET archived_at=? WHERE id<=? AND archived_at IS NULL',
                                      (stamp(), obligation_id)).rowcount
            scene = self.load_scene()
            for item in scene:
                if item.get('obligation_id') is None or item['obligation_id'] <= obligation_id:
                    item.update(obligation_id=None, background=True, timing_known=False, placed_at=None)
            self.conn.execute('INSERT INTO scene_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(scene),))
            self._event(None, 'feed_archived', 'Previous records archived; labels and evidence retained', {'through': obligation_id, 'count': count})
            return count

    def action(self, obligation_id: int, action: str, *, resident_id: int | None = None, cleaner_id: int | None = None, note: str = "", resolved_at: datetime | None = None, now: datetime | None = None) -> dict:
        now = now or utcnow()
        if len(note) > 1000:
            raise ValueError("Notes can contain at most 1000 characters.")
        with self.lock, self.conn:
            return self._action_locked(obligation_id, action, resident_id=resident_id, cleaner_id=cleaner_id,
                                       note=note, resolved_at=resolved_at, now=now)

    def _action_locked(self, obligation_id: int, action: str, *, resident_id: int | None = None,
                       cleaner_id: int | None = None, note: str = '', resolved_at: datetime | None = None,
                       now: datetime) -> dict:
        """Caller owns the transaction, including when several review facts change together."""
        item = self.obligation(obligation_id, now=now)
        previous = {key: item[key] for key in ("resident_id", "lifecycle", "confidence", "resolved_at", "overdue_verified")}
        if action == "resolve":
            if item["lifecycle"] == "dismissed":
                raise ValueError("Reopen a dismissed event before resolving it.")
            when = resolved_at or now
            if when > now + timedelta(seconds=5) or when < parse_stamp(item["created_at"]):
                raise ValueError("Cleanup must fall between deposit time and now.")
            cleaner = self._resident(cleaner_id)
            late = item["confidence"] == "confirmed" and item["timing_known"] and when > parse_stamp(item["due_at"])
            self.conn.execute("UPDATE obligations SET lifecycle='resolved', resolved_at=?,cleaner_id=?,overdue_verified=?,review_pending=0,reason=? WHERE id=?", (stamp(when), cleaner_id, int(late), note.strip(), obligation_id))
            description = f"{self.dish_description(item['dishes'])} marked clean" + (f" by {cleaner['name']}" if cleaner else "")
        elif action in ("confirm", "reassign"):
            resident = self._resident(resident_id)
            if not resident:
                raise ValueError("Choose the responsible housemate to confirm ownership.")
            lifecycle = ("resolved" if item["resolved_at"] else "open") if item["lifecycle"] in ("disputed", "dismissed") else item["lifecycle"]
            camera_closed = item["lifecycle"] == "resolved" and item["reason"] in CAMERA_CLOSED_NOTES
            late = (bool(item["timing_known"] and item["resolved_at"] and parse_stamp(item["resolved_at"]) > parse_stamp(item["due_at"]))
                    if lifecycle == "resolved" and not camera_closed else item["overdue_verified"])
            # The camera photographs a dish still out after its deadline before anyone is named.
            # Keep the after-deadline sighting separately from the dirty-dish verdict.
            # Naming alone cannot trigger an accusation; the incident review gate owns that.
            photographed_late = (lifecycle == "open" or camera_closed) and not late and item["timing_known"] and self.conn.execute(
                "SELECT 1 FROM evidence WHERE obligation_id=? AND expired=0 AND label LIKE 'Matching dish still visible after the deadline%' AND created_at>=?",
                (obligation_id, item["due_at"])).fetchone() is not None
            late = late or photographed_late
            self.conn.execute("UPDATE obligations SET resident_id=?, confidence='confirmed',lifecycle=?,review_pending=0,overdue_verified=?,reason=?,attribution=CASE WHEN attribution IS NULL THEN NULL ELSE 'camera_reviewed' END WHERE id=?", (resident_id, lifecycle, int(late), note.strip(), obligation_id))
            if photographed_late:
                self._retain_review_evidence(obligation_id, now)
            # Each human decision labels the saved appearance of whoever handled the dish.
            self.conn.execute("UPDATE person_samples SET resident_id=? WHERE obligation_id=?", (resident_id, obligation_id))
            if item["suggested_resident_id"] is not None and item["suggestion_correct"] is None:
                self.conn.execute("UPDATE obligations SET suggestion_correct=? WHERE id=?", (int(item["suggested_resident_id"] == resident_id), obligation_id))
            description = f"Responsibility confirmed as {resident['name']}"
        elif action == "start_timer":
            if item["timing_known"] or item["lifecycle"] != "open":
                raise ValueError("Only an open backlog entry without a known deadline can start a timer.")
            due = now + timedelta(minutes=self.settings()["deadline_minutes"])
            self.conn.execute("UPDATE obligations SET timing_known=1,due_at=?,deadline_notified=0,overdue_verified=0 WHERE id=?", (stamp(due), obligation_id))
            description = "Backlog cleanup grace started by a person; original deposit time remains unknown"
        elif action == "dispute":
            self.conn.execute("UPDATE obligations SET lifecycle='disputed',reason=?,review_pending=1 WHERE id=?", (note.strip() or "Ownership or cleanup needs review", obligation_id))
            self.conn.execute("UPDATE person_samples SET resident_id=NULL,hint_resident_id=NULL WHERE obligation_id=?", (obligation_id,))
            description = "Dish event disputed; removed from named statistics"
        elif action == "dismiss":
            self.conn.execute("UPDATE obligations SET lifecycle='dismissed',reason=?,review_pending=0 WHERE id=?", (note.strip() or "Event dismissed after review", obligation_id))
            self.conn.execute("UPDATE person_samples SET resident_id=NULL,hint_resident_id=NULL WHERE obligation_id=?", (obligation_id,))
            description = "Dish event dismissed"
        elif action == "reopen":
            self.conn.execute("UPDATE obligations SET lifecycle='open',resolved_at=NULL,cleaner_id=NULL,overdue_verified=0,review_pending=0,reason=? WHERE id=?", (note.strip(), obligation_id))
            description = "Dish event reopened"
        else:
            raise ValueError("Choose resolve, confirm, reassign, dispute, dismiss, reopen, or start_timer.")
        self._event(obligation_id, action, description, {"before": previous, "note": note.strip(), "resident_id": resident_id}, now)
        if action == "dispute" or (action == "resolve" and late):
            self._retain_review_evidence(obligation_id, now)
        if action in ("confirm", "reassign", "dispute", "dismiss"):
            self._refresh_identity_suggestions()
        return self.obligation(obligation_id, now=now)

    def _retain_review_evidence(self, obligation_id: int, at: datetime) -> None:
        until = stamp(at + timedelta(days=self.settings()["review_days"]))
        self.conn.execute("UPDATE evidence SET expires_at=max(expires_at,?) WHERE obligation_id=? AND expired=0", (until, obligation_id))

    def record_seen(self, obligation_id: int, at: datetime | None = None, *, reliable: bool = True) -> None:
        """Only the matching visible dish track can provide continuing dirty evidence."""
        at = at or utcnow()
        with self.lock, self.conn:
            item = self.obligation(obligation_id, now=at)
            if item['archived_at']:
                return
            overdue = reliable and item["lifecycle"] == "open" and item["confidence"] == "confirmed" and not item["review_pending"] and item["timing_known"] and at >= parse_stamp(item["due_at"])
            decision = self.conn.execute('SELECT data FROM incident_decisions WHERE obligation_id=?', (obligation_id,)).fetchone()
            if decision and json.loads(decision[0]).get('condition') == 'clean':
                overdue = False
            self.conn.execute("UPDATE obligations SET last_seen_at=?,overdue_verified=max(overdue_verified,?) WHERE id=?", (stamp(at), int(overdue), obligation_id))
            if overdue and not item["overdue_verified"]:
                self._retain_review_evidence(obligation_id, at)

    def propose_cleanup(self, obligation_id: int, *, at: datetime | None = None) -> bool:
        with self.lock, self.conn:
            item = self.obligation(obligation_id)
            if item["lifecycle"] != "open" or item['archived_at']:
                return False
            lifecycle = 'resolved' if item['overdue_verified'] else 'cleared'
            self.conn.execute("UPDATE obligations SET lifecycle=?,resolved_at=?,review_pending=0,reason=? WHERE id=?", (lifecycle, stamp(at), PUT_AWAY_NOTE, obligation_id))
            self._event(obligation_id, "cleanup_candidate", "Put to dry or away; washing not verified", at=at)
            return True

    def scan_deadlines(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        with self.lock, self.conn:
            self._clear_removed(now)
            self._close_taken(now)
            self._retire_unprovable(now)
            rows = self.conn.execute("SELECT id FROM obligations WHERE archived_at IS NULL AND lifecycle='open' AND timing_known=1 AND deadline_notified=0 AND due_at<=?", (stamp(now),)).fetchall()
            for row in rows:
                self.conn.execute("UPDATE obligations SET deadline_notified=1 WHERE id=?", (row["id"],))
                self._event(row["id"], "deadline_passed", "Cleanup deadline passed; current dish state may need review", at=now)
            return len(rows)

    def _retire_unprovable(self, now: datetime) -> int:
        """Close unnamed dishes that have been gone for a day.

        Naming only happens while a dish is still out (household decision, 2026-10-20: nobody cares about a dish
        from days ago that is no longer there), so an unnamed one that is gone will never count.
        On 2026-10-15 thirty of them buried the few that could. Appearance labels are kept (no
        dismiss event, so nothing is learned as "not a dish"), and a person can still reopen or name one.
        """
        rows = self.conn.execute("""SELECT o.id FROM obligations o WHERE o.archived_at IS NULL AND o.lifecycle='open' AND o.resident_id IS NULL AND o.timing_known=1
            AND o.due_at<=? AND coalesce(o.last_seen_at,o.created_at)<=?""",
            (stamp(now), stamp(now - UNPROVABLE_AFTER))).fetchall()
        for row in rows:
            self.conn.execute("UPDATE obligations SET lifecycle='dismissed',review_pending=0,reason=? WHERE id=?", (UNPROVABLE_NOTE, row["id"]))
            self._event(row["id"], "retired_unprovable", UNPROVABLE_NOTE, at=now)
        return len(rows)

    def _close_taken(self, now: datetime) -> int:
        """Stop showing a dish as still out once the camera saw it go: carried off after its deadline,
        or carried through the sink onto a clean rack at any time.

        Before the deadline that is "taken away in time". After it the record is closed but stays
        late on the owner's record, just as if a person had marked it washed late. A reopen after the
        movement keeps the record open.
        """
        rows = self.conn.execute("""SELECT o.id, o.due_at, o.confidence, e.event, e.created_at moved_at FROM obligations o
            JOIN events e ON e.id=(SELECT max(id) FROM events WHERE obligation_id=o.id AND event IN ('dish_picked_up','dish_moved','cleanup_candidate'))
            WHERE o.archived_at IS NULL AND o.lifecycle='open' AND o.timing_known=1 AND e.created_at<=?
            AND (e.event='cleanup_candidate' OR (e.event='dish_picked_up' AND e.created_at>=o.due_at))
            AND NOT EXISTS (SELECT 1 FROM events r WHERE r.obligation_id=o.id AND r.event='reopen' AND r.id>e.id)""",
            (stamp(now - CARRY_WINDOW),)).fetchall()
        for row in rows:
            washed = row["event"] == "cleanup_candidate"
            if row["moved_at"] < row["due_at"]:
                self.conn.execute("UPDATE obligations SET lifecycle='cleared',review_pending=0,reason=? WHERE id=?",
                                  ("Seen going through the sink onto a clean rack before its deadline. Washing itself was not verified.", row["id"]))
                self._event(row["id"], "cleared_before_deadline", "Washed or put away before the deadline; no late dish recorded", {"moved_at": row["moved_at"]}, now)
                continue
            # Lateness still needs the after-deadline photo; closing the record never adds it.
            self.conn.execute("UPDATE obligations SET lifecycle='resolved',resolved_at=?,review_pending=0,reason=? WHERE id=?",
                              (row["moved_at"], WASHED_LATE_NOTE if washed else TAKEN_LATE_NOTE, row["id"]))
            self._event(row["id"], "taken_after_deadline", "No longer out; it was out past its deadline", {"moved_at": row["moved_at"]}, now)
        return len(rows)

    def _clear_removed(self, now: datetime) -> int:
        """Close dishes carried away before their deadline: nobody left them out past it.

        The dish's last movement must be a pickup before the deadline, with no put-down in view for
        the carry window since. Where it went is not observed, so this is never counted as washed,
        and a person reopening the record keeps it open.
        """
        rows = self.conn.execute("""SELECT o.id, e.created_at picked_at FROM obligations o
            JOIN events e ON e.id=(SELECT max(id) FROM events WHERE obligation_id=o.id AND event IN ('dish_picked_up','dish_moved'))
            WHERE o.archived_at IS NULL AND o.lifecycle='open' AND o.timing_known=1 AND e.event='dish_picked_up' AND e.created_at<o.due_at AND e.created_at<=?
            AND NOT EXISTS (SELECT 1 FROM events r WHERE r.obligation_id=o.id AND r.event='reopen' AND r.id>e.id)""",
            (stamp(now - CARRY_WINDOW),)).fetchall()
        for row in rows:
            self.conn.execute("UPDATE obligations SET lifecycle='cleared',review_pending=0,reason=? WHERE id=?",
                              ("Picked up before its deadline and not put down again in view. Where it went was not observed.", row["id"]))
            self._event(row["id"], "cleared_before_deadline", "Taken away before the deadline; no late dish recorded", {"picked_up_at": row["picked_at"]}, now)
        return len(rows)

    def confirmed_late_ids(self) -> set[int]:
        from .incidents import confirmed_late
        with self.lock:
            rows = self.conn.execute('''SELECT o.*,d.data decision,s.manifest FROM obligations o
                LEFT JOIN incident_decisions d ON d.obligation_id=o.id
                LEFT JOIN evidence_sequences s ON s.obligation_id=o.id
                WHERE o.archived_at IS NULL AND o.overdue_verified=1''').fetchall()
            return {r['id'] for r in rows if confirmed_late(dict(r), json.loads(r['decision'] or '{}'), json.loads(r['manifest'] or '{}'))}

    def statistics(self, now: datetime | None = None) -> list[dict]:
        now = now or utcnow()
        with self.lock:
            result = []
            late_ids = self.confirmed_late_ids()
            decisions = {r['obligation_id']: json.loads(r['data']) for r in self.conn.execute('SELECT * FROM incident_decisions')}
            for resident in self.residents():
                rows = self.conn.execute("SELECT * FROM obligations WHERE archived_at IS NULL AND resident_id=? AND confidence='confirmed' AND timing_known=1 AND lifecycle IN ('open','resolved')", (resident["id"],)).fetchall()
                # An unobserved outcome cannot be treated as an on-time success in a rate.
                eligible = [r for r in rows if r['id'] in late_ids or (r["lifecycle"] == "resolved" and r['reason'] not in CAMERA_CLOSED_NOTES)]
                overdue = sum(r['id'] in late_ids for r in eligible)
                cleaned_for_others = self.conn.execute("SELECT count(*) FROM obligations WHERE archived_at IS NULL AND cleaner_id=? AND resident_id!=? AND lifecycle='resolved' AND confidence='confirmed'", (resident["id"], resident["id"])).fetchone()[0]
                dish_count = lambda items: sum(d["quantity"] for r in items for d in json.loads(r["dishes"]))  # noqa: E731
                waiting = [r for r in rows if r["lifecycle"] == "open" and not r['review_pending'] and decisions.get(r['id'], {}).get('condition') == 'used']
                samples = self.conn.execute("SELECT count(*) FROM person_samples WHERE resident_id=?", (resident["id"],)).fetchone()[0]
                washed_own = sum(r["lifecycle"] == "resolved" and r["cleaner_id"] == resident["id"] for r in rows)
                result.append({"dishes_unwashed": dish_count(waiting), "dishes_late_now": dish_count([r for r in waiting if r['id'] in late_ids]),
                               "dishes_total": dish_count(rows), "dishes_overdue": dish_count([r for r in eligible if r['id'] in late_ids]),
                               "washed_own": washed_own, "appearance_samples": samples,
                               "resident_id": resident["id"], "name": resident["name"], "color": resident["color"], "uses": len(eligible), "observed_uses": len(rows), "overdue": overdue, "overdue_rate": overdue / len(eligible) if eligible else 0, "on_time": sum(r["lifecycle"] == "resolved" and not r["overdue_verified"] for r in eligible), "cleaned_for_others": cleaned_for_others, "pending_unverified": sum(r["lifecycle"] == "open" and parse_stamp(r["due_at"]) <= now and not r["overdue_verified"] for r in rows)})
            return result

    def activity(self, limit: int = 30) -> list[dict]:
        with self.lock:
            result = []
            for row in self.conn.execute("SELECT id,created_at,event,description,obligation_id FROM events WHERE obligation_id IS NULL OR obligation_id IN (SELECT id FROM obligations WHERE archived_at IS NULL) ORDER BY id DESC LIMIT ?", (limit,)):
                item = dict(row)
                evidence = self.conn.execute("SELECT id,label FROM evidence WHERE obligation_id=? AND kind='snapshot' AND expired=0 AND expires_at>? ORDER BY CASE WHEN label LIKE 'Person near%' THEN 0 ELSE 1 END,created_at LIMIT 1", (row["obligation_id"], stamp())).fetchone()
                item["snapshot_url"] = f"/api/evidence/{evidence['id']}" if evidence else None
                item["snapshot_label"] = evidence["label"] if evidence else None
                result.append(item)
            return result

    def load_scene(self) -> list[dict]:
        with self.lock:
            row = self.conn.execute("SELECT payload FROM scene_state WHERE id=1").fetchone()
            return json.loads(row["payload"]) if row else []

    def save_scene(self, dishes: list[dict]) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO scene_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(dishes),))

    def log_change(self, change: dict) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO dish_changes(at,area,change,kind) VALUES(?,?,?,?)", (change["at"], change["area"], change["change"], change["kind"]))

    def mark_watching(self, at: datetime) -> None:
        """Logging covers time from here on, even if nothing changes for a while."""
        self.log_change({"at": stamp(at), "area": "", "change": "watching", "kind": ""})

    def changes_between(self, start: datetime, end: datetime) -> list[dict] | None:
        """The live inventory's changes in a window, or None if logging began after the window started."""
        with self.lock:
            first = self.conn.execute("SELECT min(at) FROM dish_changes").fetchone()[0]
            if first is None or parse_stamp(first) > start:
                return None
            return [dict(row) for row in self.conn.execute("SELECT at,area,change,kind FROM dish_changes WHERE at>=? AND at<=? AND change IN ('added','removed') ORDER BY at",
                                                           (stamp(start), stamp(end)))]

    # Second look: what it saw, and what people decided

    def add_dish_sample(self, embedding: list[float], score: float | None, *, at: datetime | None = None) -> int:
        with self.lock, self.conn:
            return self.conn.execute("INSERT INTO dish_samples(created_at,embedding,score) VALUES(?,?,?)",
                                     (stamp(at), json.dumps([round(v, 5) for v in embedding]), score)).lastrowid

    def link_dish_sample(self, sample_id: int, obligation_id: int) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE dish_samples SET obligation_id=? WHERE id=?", (obligation_id, sample_id))

    def label_dish_sample(self, sample_id: int, dish: bool) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE dish_samples SET label=? WHERE id=?", (int(dish), sample_id))

    def labeled_dish_samples(self) -> list[dict]:
        """Samples with a person's verdict: given directly in a kitchen check, or implied by a Review answer.

        A named owner, a cleanup marked by a person, or "can't tell whose" all mean a real dish;
        "not a dish" means a mistake. Anything else (system closures, open questions) teaches nothing.
        """
        with self.lock:
            rows = self.conn.execute("""SELECT s.created_at, s.embedding, s.label, o.lifecycle, o.confidence, o.attribution, o.resolved_at,
                (SELECT json_extract(e.data,'$.note') FROM events e WHERE e.obligation_id=o.id AND e.event='dismiss' ORDER BY e.id DESC LIMIT 1) note
                FROM dish_samples s LEFT JOIN obligations o ON o.id=s.obligation_id ORDER BY s.id""").fetchall()
        result = []
        for row in rows:
            label = row["label"]
            if label is None and row["lifecycle"] is not None:
                note = (row["note"] or "").strip()
                if row["lifecycle"] == "dismissed":
                    label = 0 if note in NOT_A_DISH_NOTES else 1 if note == CANT_TELL_NOTE else None
                elif row["confidence"] == "confirmed" and row["attribution"] != "camera":
                    label = 1
            if label is not None:
                result.append({"created_at": row["created_at"], "embedding": json.loads(row["embedding"]), "label": int(label)})
        return result

    # Kitchen checks

    def check(self, slot: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM kitchen_checks WHERE slot=?", (slot,)).fetchone()
            return self._check(row) if row else None

    @staticmethod
    def _check(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["dishes"] = json.loads(item["dishes"])
        item["answer"] = json.loads(item["answer"]) if item["answer"] else None
        return item

    def save_check(self, slot: str, *, at: datetime | None = None, photo: str | None = None, dishes: list[dict] | None = None,
                   frame_id: str | None = None, status: str = "pending") -> dict:
        with self.lock, self.conn:
            self.conn.execute("""INSERT INTO kitchen_checks(slot,at,photo,dishes,frame_id,status) VALUES(?,?,?,?,?,?)
                ON CONFLICT(slot) DO UPDATE SET at=excluded.at,photo=excluded.photo,dishes=excluded.dishes,frame_id=excluded.frame_id,status=excluded.status""",
                              (slot, stamp(at) if at else None, photo, json.dumps(dishes or []), frame_id, status))
        return self.check(slot)

    def checks(self, limit: int = 14) -> list[dict]:
        with self.lock:
            return [self._check(row) for row in self.conn.execute("SELECT * FROM kitchen_checks ORDER BY slot DESC LIMIT ?", (limit,))]

    def check_by_id(self, check_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM kitchen_checks WHERE id=?", (check_id,)).fetchone()
            return self._check(row) if row else None

    def answer_check(self, check_id: int, verdicts: list[bool], missed: int) -> dict:
        with self.lock, self.conn:
            item = self.check_by_id(check_id)
            if not item or item["status"] not in ("pending", "answered"):
                raise LookupError("Kitchen check not found.")
            if len(verdicts) != len(item["dishes"]) or not 0 <= missed <= 50:
                raise ValueError("Answer every outlined dish, and give a missed count from 0 to 50.")
            for dish, verdict in zip(item["dishes"], verdicts):
                if dish.get("sample"):
                    self.conn.execute("UPDATE dish_samples SET label=? WHERE id=?", (int(verdict), dish["sample"]))
            self.conn.execute("UPDATE kitchen_checks SET answered_at=?,answer=?,status='answered' WHERE id=?",
                              (stamp(), json.dumps({"verdicts": [bool(v) for v in verdicts], "missed": int(missed)}), check_id))
        return self.check_by_id(check_id)

    def flag_continuity_gap(self, obligation_id: int) -> None:
        with self.lock, self.conn:
            item = self.obligation(obligation_id)
            # Which of two similar dishes this is only matters once someone answers for it.
            if item["lifecycle"] == "open" and not item["review_pending"] and item["resident_id"] is not None:
                self.conn.execute("UPDATE obligations SET review_pending=1,reason='Dish reappeared after an observation gap. Confirm that this is still the same dish before using its deadline evidence.' WHERE id=?", (obligation_id,))
                self._event(obligation_id, "dish_continuity_uncertain", "Dish visibility was interrupted; matching and outcome need review")

    def note_missing(self, obligation_id: int, location: str) -> None:
        """Gone when monitoring resumed: moved or cleaned unobserved. Never counts as washed."""
        with self.lock, self.conn:
            item = self.obligation(obligation_id)
            if item["lifecycle"] == "open" and not item["review_pending"]:
                self.conn.execute("UPDATE obligations SET review_pending=1,reason=? WHERE id=?",
                                  (f"Not at {location} when monitoring resumed; it may have been moved or cleaned while unobserved.", obligation_id))
                self._event(obligation_id, "dish_not_seen", f"Not visible at {location} after an observation gap; outcome unknown")

    def close_unclaimed(self, obligation_id: int) -> bool:
        """A dish already there when watching began, now gone, with nobody named: nothing anyone can answer."""
        with self.lock, self.conn:
            item = self.obligation(obligation_id)
            if item["lifecycle"] != "open" or item["timing_known"] or item["resident_id"] is not None:
                return False
        self.action(obligation_id, "dismiss", note=UNCLAIMED_NOTE)
        return True

    def unclaimed(self) -> list[int]:
        with self.lock:
            return [row[0] for row in self.conn.execute("SELECT id FROM obligations WHERE archived_at IS NULL AND lifecycle='open' AND timing_known=0 AND resident_id IS NULL")]

    def cleaned_today(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        tz = ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))
        start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.lock:
            rows = self.conn.execute("SELECT reason FROM obligations WHERE archived_at IS NULL AND lifecycle='resolved' AND resolved_at>=? AND resolved_at<?", (stamp(start), stamp(start + timedelta(days=1)))).fetchall()
            return sum(r['reason'] not in CAMERA_CLOSED_NOTES for r in rows)

    def add_evidence(self, obligation_id: int, evidence_id: str, filename: str, kind: str, label: str, *, at: datetime | None = None, expires: datetime | None = None, capture: dict | None = None) -> None:
        at = at or utcnow()
        item = self.obligation(obligation_id)
        settings = self.settings()
        days = settings["review_days" if item["status"] in ("review", "disputed", "overdue") else "evidence_days"]
        if kind == "snapshot":
            # Stills are small and are the lasting record; clips are the bulky part.
            days = max(days, settings["photo_days"])
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO evidence(id,obligation_id,filename,kind,created_at,expires_at,label,capture) VALUES(?,?,?,?,?,?,?,?)", (evidence_id, obligation_id, filename, kind, stamp(at), stamp(expires or at + timedelta(days=days)), label, json.dumps(capture or {})))

    def evidence(self, evidence_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            return dict(row) if row else None

    def all_evidence(self, *, include_expired: bool = False) -> list[dict]:
        with self.lock:
            where = "" if include_expired else " WHERE expired=0"
            return [dict(row) for row in self.conn.execute("SELECT * FROM evidence" + where + " ORDER BY created_at")]

    def expire_evidence(self, evidence_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE evidence SET expired=1 WHERE id=?", (evidence_id,))

    def track_obligation(self, track_key: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT id FROM obligations WHERE archived_at IS NULL AND track_key=? AND lifecycle='open' ORDER BY id DESC LIMIT 1", (track_key,)).fetchone()
            return self.obligation(row["id"]) if row else None

    def add_dish(self, obligation_id: int, kind: str, *, at: datetime | None = None) -> dict:
        """Grow one episode instead of opening a separate review card for every dish."""
        with self.lock, self.conn:
            item = self.obligation(obligation_id)
            dishes = validate_dishes([*item["dishes"], {"kind": kind, "quantity": 1}])
            self.conn.execute("UPDATE obligations SET dishes=? WHERE id=?", (json.dumps(dishes), obligation_id))
            self._event(obligation_id, "dish_added", f"Another {kind} left in the same episode", {"kind": kind}, at)
            return self.obligation(obligation_id)

    def handler_obligation(self, handler_key: str, since: datetime) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT id FROM obligations WHERE archived_at IS NULL AND handler_key=? AND lifecycle='open' AND created_at>=? ORDER BY id DESC LIMIT 1", (handler_key, stamp(since))).fetchone()
            return self.obligation(row["id"]) if row else None

    def add_person_sample(self, obligation_id: int, embedding: list[float], evidence_id: str | None = None, *, at: datetime | None = None,
                          cue: str = "outfit", context: str | None = None) -> int:
        with self.lock, self.conn:
            proof = self.conn.execute("""SELECT 1 FROM person_samples s JOIN evidence e ON e.id=s.evidence_id
                WHERE s.obligation_id=? AND e.label='Who put it down (from footage)' AND s.evidence_id IS NOT ?""",
                                     (obligation_id, evidence_id)).fetchone()
            if proof:
                raise ValueError("A proof's person sample can only be replaced by its put-down picture.")
            cursor = self.conn.execute("INSERT INTO person_samples(obligation_id,resident_id,created_at,embedding,evidence_id,cue,context) VALUES(?,NULL,?,?,?,?,?)",
                                       (obligation_id, stamp(at), json.dumps([round(v, 5) for v in embedding]), evidence_id, cue, context))
            return cursor.lastrowid

    def replace_putdown_samples(self, obligation_id: int, cues: dict[str, list[float]], evidence_id: str,
                               *, at: datetime, context: str | None = None) -> None:
        """Replace the capture atomically. A name on an older picture cannot label a new person."""
        rows = [(obligation_id, stamp(at), json.dumps([round(v, 5) for v in vector]), evidence_id, cue, context)
                for cue, vector in cues.items()]
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM person_samples WHERE obligation_id=?", (obligation_id,))
            self.conn.executemany("""INSERT INTO person_samples(obligation_id,resident_id,created_at,embedding,evidence_id,cue,context)
                VALUES(?,NULL,?,?,?,?,?)""", rows)

    def clear_unlabeled_samples(self, obligation_id: int) -> None:
        """Drop the camera's own captures for a dish when better ones (from the put-down footage) replace them."""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM person_samples WHERE obligation_id=? AND resident_id IS NULL", (obligation_id,))

    def merge_sighting_face(self, obligation_id: int, embedding: list[float], evidence_id: str) -> bool:
        """Join a face from a clip to the sighting's latest capture, averaged with a face already there.

        The face sample takes the clip's evidence id, so the same clip is never merged twice. Returns
        False when the sighting has no capture or this clip was already used.
        """
        with self.lock, self.conn:
            clip = self.conn.execute("SELECT kind FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if clip and clip["kind"] == "clip":
                return False
            # Old whole-visit clips have no link to the depicted handler. Never contaminate a proof.
            if self.conn.execute("SELECT 1 FROM evidence WHERE obligation_id=? AND label='Who put it down (from footage)'", (obligation_id,)).fetchone():
                return False
            capture = self.conn.execute("SELECT * FROM person_samples WHERE obligation_id=? AND cue!='face' ORDER BY id DESC LIMIT 1", (obligation_id,)).fetchone()
            if not capture:
                return False
            face = self.conn.execute("SELECT * FROM person_samples WHERE obligation_id=? AND created_at=? AND cue='face' ORDER BY id DESC LIMIT 1",
                                     (obligation_id, capture["created_at"])).fetchone()
            if face and face["evidence_id"] == evidence_id:
                return False
            if face:
                old = json.loads(face["embedding"])
                if len(old) == len(embedding):
                    merged = [a + b for a, b in zip(old, embedding)]
                    norm = math.sqrt(sum(v * v for v in merged)) or 1.0
                    embedding = [v / norm for v in merged]
                self.conn.execute("UPDATE person_samples SET embedding=?, evidence_id=? WHERE id=?",
                                  (json.dumps([round(v, 5) for v in embedding]), evidence_id, face["id"]))
            else:
                self.conn.execute("""INSERT INTO person_samples(obligation_id,resident_id,hint_resident_id,created_at,embedding,evidence_id,cue,context)
                    VALUES(?,?,?,?,?,?,'face',?)""", (obligation_id, capture["resident_id"], capture["hint_resident_id"], capture["created_at"],
                                                       json.dumps([round(v, 5) for v in embedding]), evidence_id, capture["context"]))
            return True

    def hint_samples(self, obligation_id: int, resident_id: int) -> None:
        """Compatibility for older callers: predictions are never teaching labels."""

    def suggest_identity(self, cues: dict[str, list[float]], *, at: datetime | None = None, context: str | None = None,
                         enrolled_faces: dict[int, list[list[float]]] | None = None, limit: int = 2000,
                         exclude_obligation: int | None = None, before: datetime | None = None) -> tuple[int | None, float | None, float | None, dict]:
        """Return a discriminating visual match, ranking diagnostics and its supported basis."""
        weights = self.identity_weights()
        features = self.cue_features(cues, at=at or utcnow(), context=context, enrolled_faces=enrolled_faces,
                                    limit=limit, exclude_obligation=exclude_obligation, before=before)
        ranked = sorted(((sum(weights.get(cue, 1.0) * value for cue, value in values.items()), resident, seen)
                         for resident, values, seen in features),
                        key=lambda r: r[0], reverse=True)
        if not ranked:
            return None, None, None, {}
        evidence, resident, seen = ranked[0]
        margin = evidence - ranked[1][0] if len(ranked) > 1 else None
        # A sum of weak, correlated cues is not independent evidence. Require a discriminating
        # visual match in its own right; a contradictory strong cue forces abstention.
        supported = {}
        for cue, minimum, separation in (("face", 0.55, 0.12), ("outfit_today", 0.94, 0.055), ("head", 0.96, 0.06)):
            scores = sorted(((s[cue], r) for r, _, s in features if cue in s), reverse=True)
            if scores and scores[0][0] >= minimum and (len(scores) == 1 or scores[0][0] - scores[1][0] >= separation):
                best_seen = next(s for r, _, s in features if r == scores[0][1])
                if cue == "outfit_today" and best_seen.get("outfit_colour", 1.0) < 0.94:
                    continue
                supported[cue] = scores[0][1]
        if not supported or len(set(supported.values())) != 1:
            return None, round(evidence, 3), None if margin is None else round(margin, 3), seen
        resident = next(iter(supported.values()))
        evidence, _, seen = next(row for row in ranked if row[1] == resident)
        other = [row[0] for row in ranked if row[1] != resident]
        margin = evidence - max(other) if other else None
        seen = {cue: seen[cue] for cue in supported}
        if "outfit_today" in supported:
            reference = self._outfit_reference(resident, cues["outfit"], at or utcnow(), exclude_obligation, before)
            if reference:
                seen["basis"] = reference
        return resident, round(evidence, 3), None if margin is None else round(margin, 3), seen

    def cue_features(self, cues: dict[str, list[float]], *, at: datetime, context: str | None = None,
                     enrolled_faces: dict[int, list[list[float]]] | None = None, limit: int = 2000,
                     exclude_obligation: int | None = None, before: datetime | None = None) -> list[tuple[int, dict[str, float], dict[str, float]]]:
        """How strongly each cue points at each housemate, before weighting: (resident, features, similarities).

        Face and head carry across days. An outfit is strong evidence only against sightings from the
        same day; only human-labelled sightings teach. Stature compares how high
        a head was when standing at the same area. exclude_obligation leaves one sighting's own samples
        out, so it can be scored as if new.
        """
        with self.lock:
            rows = self.conn.execute("""SELECT s.resident_id who, s.cue, s.context, s.created_at, s.embedding
                FROM person_samples s JOIN residents r ON r.id=s.resident_id JOIN obligations o ON o.id=s.obligation_id
                LEFT JOIN evidence e ON e.id=s.evidence_id
                WHERE r.active=1 AND o.lifecycle NOT IN ('dismissed','disputed') AND o.attribution IS NOT 'camera'
                AND NOT (s.cue='face' AND coalesce(e.kind,'')='clip')
                AND (? IS NULL OR s.obligation_id IS NOT ?) AND (? IS NULL OR s.created_at<?)
                ORDER BY s.id DESC LIMIT ?""", (exclude_obligation, exclude_obligation, stamp(before) if before else None,
                                               stamp(before) if before else None, limit)).fetchall()
        samples: dict[int, list] = {}
        for row in rows:
            samples.setdefault(row["who"], []).append(row)
        for resident in enrolled_faces or {}:
            samples.setdefault(resident, [])

        def similarity(vector: list[float], other: list[float]) -> float | None:
            if len(vector) != len(other) or not all(math.isfinite(v) for v in (*vector, *other)):
                return None
            norm = math.sqrt(sum(v * v for v in vector) * sum(v * v for v in other))
            return sum(a * b for a, b in zip(vector, other)) / norm if norm else None

        def top(vector: list[float], candidates: list[list[float]]) -> float | None:
            return max((s for s in (similarity(vector, c) for c in candidates) if s is not None), default=None)

        result = []
        for resident, own in samples.items():
            features: dict[str, float] = {}
            seen: dict[str, float] = {}

            def add(cue: str, value: float | None, factor: float = 1.0, floor: float = -4.0) -> None:
                if value is not None:
                    neutral, strength = IDENTITY_CUES[cue]
                    features[cue] = factor * max(floor, min(6.0, strength * (value - neutral)))
                    seen[cue] = round(value, 3)
            confirmed = own
            if cues.get("face"):
                faces = [json.loads(r["embedding"]) for r in confirmed if r["cue"] == "face"] + list((enrolled_faces or {}).get(resident, []))
                add("face", max((s for s in (similarity(cues["face"], f) for f in faces) if s is not None), default=None))
            if cues.get("head"):
                add("head", top(cues["head"], [json.loads(r["embedding"]) for r in confirmed if r["cue"] == "head"]))
            if cues.get("outfit"):
                # Reviewing older footage can supply labels from later sightings.
                tz = ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))
                recent = lambda r: at.astimezone(tz).date() == parse_stamp(r["created_at"]).astimezone(tz).date()  # noqa: E731
                outfits = [json.loads(r["embedding"]) for r in confirmed if r["cue"] == "outfit" and recent(r)]
                today = top(cues["outfit"], outfits)
                if today is not None:
                    add("outfit_today", today)
                    # The saved 592-value descriptor is 512 detector features plus two 40-bin
                    # colour histograms. Similar pose/background must not outweigh different clothes.
                    if len(cues["outfit"]) == 592:
                        closest = max((v for v in outfits if len(v) == 592),
                                      key=lambda v: similarity(cues["outfit"], v) or -1, default=None)
                        if closest is not None:
                            colour = similarity(cues["outfit"][-80:], closest[-80:])
                            seen["outfit_colour"] = colour if colour is not None else 0.0
            if cues.get("stature") and context:
                heights = [json.loads(r["embedding"])[0] for r in confirmed if r["cue"] == "stature" and r["context"] == context]
                if heights:
                    gap = abs(cues["stature"][0] - sum(heights) / len(heights))
                    features["stature"] = max(-1.0, 1.0 - (gap / STATURE_SPREAD) ** 2)
                    seen["stature"] = round(gap, 3)
            if seen:
                result.append((resident, features, seen))
        return result

    def _outfit_reference(self, resident_id: int, vector: list[float], at: datetime,
                          exclude_obligation: int | None, before: datetime | None) -> str | None:
        tz = ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))
        with self.lock:
            rows = self.conn.execute("""SELECT s.*, o.dishes FROM person_samples s JOIN obligations o ON o.id=s.obligation_id
                WHERE s.resident_id=? AND s.cue='outfit' AND o.lifecycle NOT IN ('dismissed','disputed')
                AND o.attribution IS NOT 'camera' AND s.obligation_id IS NOT ?""", (resident_id, exclude_obligation)).fetchall()
        candidates = []
        for row in rows:
            when = parse_stamp(row["created_at"])
            other = json.loads(row["embedding"])
            if when.astimezone(tz).date() != at.astimezone(tz).date() or (before and when >= before) or len(vector) != len(other):
                continue
            norm = math.sqrt(sum(v * v for v in vector) * sum(v * v for v in other))
            if norm:
                candidates.append((sum(a * b for a, b in zip(vector, other)) / norm, row["id"], row))
        if not candidates:
            return None
        row = max(candidates)[2]
        time = parse_stamp(row["created_at"]).astimezone(tz).strftime("%I:%M %p").lstrip("0")
        dish = json.loads(row["dishes"])[0]["kind"]
        return f"same outfit as the {time} {dish}"

    def identity_weights(self) -> dict[str, float]:
        """Per-cue weights learned from confirmed answers (identity_fit), or 1.0 for every cue."""
        path = self.root / IDENTITY_WEIGHTS_FILE
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if self._weights_cache[0] != mtime:
            try:
                self._weights_cache = (mtime, json.loads(path.read_text())["weights"])
            except (OSError, ValueError, KeyError):
                return {}
        return self._weights_cache[1]

    def sighting_cues(self, obligation_id: int) -> tuple[dict[str, list[float]], str | None, datetime] | None:
        """The cues from one sighting's latest capture, as they were when it was suggested."""
        with self.lock:
            samples = self.conn.execute("""SELECT s.* FROM person_samples s LEFT JOIN evidence e ON e.id=s.evidence_id
                WHERE s.obligation_id=? AND NOT (s.cue='face' AND coalesce(e.kind,'')='clip') ORDER BY s.id DESC""", (obligation_id,)).fetchall()
        if not samples:
            return None
        latest = samples[0]
        cues: dict[str, list[float]] = {}
        for sample in samples:
            if sample["created_at"] == latest["created_at"] and sample["context"] == latest["context"]:
                cues.setdefault(sample["cue"], json.loads(sample["embedding"]))
        return cues, latest["context"], parse_stamp(latest["created_at"])

    def refresh_identity_suggestions(self) -> int:
        with self.lock, self.conn:
            return self._refresh_identity_suggestions()

    def _refresh_identity_suggestions(self) -> int:
        """Re-score waiting camera sightings after a human changes the teaching examples.

        Suggestions never confirm ownership or label further teaching examples.
        Use each sighting's capture time so today's clothes do not identify yesterday.
        """
        with self.lock:
            rows = self.conn.execute("""SELECT s.*, o.suggested_resident_id, o.suggestion_score
                FROM person_samples s JOIN obligations o ON o.id=s.obligation_id LEFT JOIN evidence e ON e.id=s.evidence_id
                WHERE o.archived_at IS NULL AND o.lifecycle='open' AND o.confidence='candidate'
                AND NOT (s.cue='face' AND coalesce(e.kind,'')='clip')
                AND o.resident_id IS NULL ORDER BY s.id DESC""").fetchall()
            sightings: dict[int, list] = {}
            for row in rows:
                sightings.setdefault(row["obligation_id"], []).append(row)
            updated = 0
            for obligation_id, samples in sightings.items():
                latest = samples[0]
                cues = {}
                for sample in samples:
                    if sample["created_at"] == latest["created_at"] and sample["context"] == latest["context"]:
                        cues.setdefault(sample["cue"], json.loads(sample["embedding"]))
                resident, evidence, _, seen = self.suggest_identity(cues, at=parse_stamp(latest["created_at"]), context=latest["context"],
                                                                  exclude_obligation=obligation_id)
                if (resident, evidence) != (latest["suggested_resident_id"], latest["suggestion_score"]):
                    self._set_suggestion(obligation_id, resident, evidence, seen)
                    updated += 1
            return updated

    def set_suggestion(self, obligation_id: int, resident_id: int | None, score: float | None, cues: dict | None = None) -> None:
        with self.lock, self.conn:
            self._set_suggestion(obligation_id, resident_id, score, cues)

    def _set_suggestion(self, obligation_id: int, resident_id: int | None, score: float | None, cues: dict | None = None) -> None:
        # Caller owns the transaction; refreshed suggestions belong to the same
        # atomic decision as the human label that caused them.
        labels = {"face": "face", "head": "hair and head", "outfit_today": "today's outfit", "outfit": "a past outfit", "stature": "height"}
        self.conn.execute("UPDATE obligations SET suggested_resident_id=?, suggestion_score=? WHERE id=?", (resident_id, score, obligation_id))
        capture = self.sighting_cues(obligation_id)
        fingerprint = hashlib.sha256(json.dumps(capture[0], sort_keys=True).encode()).hexdigest() if capture else None
        # Unlike the mutable obligation fields this survives refreshes and corrections. Empty guesses
        # are recorded too, so an earlier visible guess cannot be credited after it was withdrawn.
        self._event(obligation_id, "identity_prediction", "Appearance guess recorded", {
            "resident_id": resident_id, "score": score, "fingerprint": fingerprint,
            "captured_at": stamp(capture[2]) if capture else None,
            "basis": (cues or {}).get("basis") or ", ".join(labels[c] for c in (cues or {}) if c in labels)})
        if resident_id is not None:
            name = self._resident(resident_id)["name"]
            basis = (cues or {}).get("basis") or ", ".join(labels[cue] for cue in (cues or {}) if cue in labels) or "appearance"
            self._event(obligation_id, "owner_suggested", f"Looks like {name} from {basis}; needs confirmation", {"resident_id": resident_id, "score": score, "cues": cues or {}})
            self._auto_attribute(obligation_id, resident_id, name)

    def suggestion_basis(self, ids: list[int]) -> dict[int, str]:
        """The basis of the current guess, never an old reason left after an abstention."""
        if not ids:
            return {}
        with self.lock:
            rows = self.conn.execute(f"""SELECT e.obligation_id, e.data, o.suggested_resident_id
                FROM events e JOIN obligations o ON o.id=e.obligation_id
                WHERE e.obligation_id IN ({','.join('?' for _ in ids)}) AND e.event='identity_prediction'
                AND e.id=(SELECT max(x.id) FROM events x WHERE x.obligation_id=e.obligation_id AND x.event='identity_prediction')""", ids).fetchall()
        return {r["obligation_id"]: d["basis"] for r in rows if (d := json.loads(r["data"])).get("basis")
                and d["resident_id"] is not None and d["resident_id"] == r["suggested_resident_id"]}

    def attribution_gate(self) -> dict:
        """Measure immutable guesses that appeared on ready cards before a human named them.

        Neither retrospective rescoring nor multiple dishes in the same person's visit can unlock
        automatic naming. Legacy suggestion_correct rows without a prediction snapshot are unproven.
        """
        with self.lock:
            has_proofs = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='proofs'").fetchone()
            rows = self.conn.execute("""SELECT e.*, p.created_at ready_at FROM events e
                JOIN obligations o ON o.id=e.obligation_id JOIN proofs p ON p.obligation_id=o.id
                WHERE e.event IN ('confirm','reassign','person_identified') AND o.attribution IS NULL
                AND o.lifecycle NOT IN ('dismissed','disputed') AND p.status='ready'
                AND e.created_at>=? ORDER BY e.id DESC""", (stamp(utcnow() - timedelta(days=30)),)).fetchall() if has_proofs else []
            cases, visited = [], set()
            for row in rows:
                oid = row["obligation_id"]
                if oid in visited:
                    continue
                visited.add(oid)
                truth = json.loads(row["data"]).get("resident_id")
                first = self.conn.execute("""SELECT min(id) FROM events WHERE obligation_id=?
                    AND event IN ('confirm','reassign','person_identified')""", (oid,)).fetchone()[0]
                prediction = self.conn.execute("""SELECT * FROM events WHERE obligation_id=? AND event='identity_prediction'
                    AND id<? ORDER BY id DESC LIMIT 1""", (oid, first)).fetchone()
                if not prediction or truth is None or row["ready_at"] > row["created_at"]:
                    continue
                data = json.loads(prediction["data"])
                if data.get("resident_id") is None or not data.get("fingerprint") or not data.get("captured_at"):
                    continue
                # It must have been a ready card during this prediction's lifetime, before the first label.
                first_time = self.conn.execute("SELECT created_at FROM events WHERE id=?", (first,)).fetchone()[0]
                if row["ready_at"] > first_time or parse_stamp(data["captured_at"]) < utcnow() - timedelta(days=30):
                    continue
                cases.append({"truth": truth, "correct": int(data["resident_id"] == truth),
                              "fingerprint": data["fingerprint"], "at": parse_stamp(data["captured_at"]), "checked": row["created_at"]})
            groups = []
            for case in sorted(cases, key=lambda c: c["at"]):
                group = next((g for g in reversed(groups) if case["fingerprint"] in g["fingerprints"]
                              or (case["truth"] == g["truth"] and case["at"] - g["at"] < timedelta(minutes=30))), None)
                if group:
                    group["correct"] = min(group["correct"], case["correct"])
                    group["fingerprints"].add(case["fingerprint"])
                    group["at"] = case["at"]
                    group["checked"] = max(group["checked"], case["checked"])
                else:
                    groups.append(case | {"fingerprints": {case["fingerprint"]}})
            recent = [g["correct"] for g in sorted(groups, key=lambda g: g["checked"], reverse=True)[:AUTO_WINDOW]]
            enabled = self.settings()["auto_attribute"]
        accuracy = sum(recent) / len(recent) if recent else None
        ready = len(recent) >= AUTO_MIN_CHECKED and accuracy is not None and accuracy >= AUTO_MIN_ACCURACY
        return {"enabled": enabled, "active": enabled and ready, "checked": len(recent), "correct": sum(recent), "accuracy": accuracy,
                "min_checked": AUTO_MIN_CHECKED, "min_accuracy": AUTO_MIN_ACCURACY}

    def _auto_attribute(self, obligation_id: int, resident_id: int, name: str) -> None:
        # Caller owns the transaction. Appearance samples stay unlabelled: the camera must not teach itself.
        if obligation_id % AUTO_SPOT_CHECK_EVERY == 0:
            return
        row = self.conn.execute("SELECT * FROM obligations WHERE id=?", (obligation_id,)).fetchone()
        if not (not row["archived_at"] and row["lifecycle"] == "open" and row["confidence"] == "candidate" and row["resident_id"] is None and row["source"] == "vision"
                and row["timing_known"] and not row["review_pending"] and parse_stamp(row["created_at"]) >= utcnow() - AUTO_MAX_AGE):
            return
        gate = self.attribution_gate()
        if not gate["active"]:
            return
        basis = f"its guesses were right {gate['correct']} of the last {gate['checked']} times a person checked"
        self.conn.execute("UPDATE obligations SET resident_id=?,original_resident_id=?,confidence='confirmed',attribution='camera',reason=? WHERE id=?",
                          (resident_id, resident_id, f"Named as {name} by the camera; {basis}. Dispute it if that's wrong.", obligation_id))
        self._event(obligation_id, "auto_attributed", f"Named as {name} by the camera ({basis})", {"resident_id": resident_id, "accuracy": gate["accuracy"], "checked": gate["checked"]})
        self._retain_review_evidence(obligation_id, utcnow())

    # Personal links: each housemate sees and can dispute only their own records.

    def create_link(self, resident_id: int) -> str:
        """A new personal link token; any earlier link for this person stops working."""
        token = secrets.token_urlsafe(24)
        with self.lock, self.conn:
            resident = self._resident(resident_id)
            self.conn.execute("UPDATE residents SET link_hash=? WHERE id=?", (_token_hash(token), resident_id))
            self._event(None, "personal_link_created", f"Personal link created for {resident['name']}; any earlier link was revoked")
        return token

    def resident_for_link(self, token: str) -> dict | None:
        if not token or len(token) > 64:
            return None
        with self.lock:
            row = self.conn.execute("SELECT id,name,color FROM residents WHERE active=1 AND link_hash=?", (_token_hash(token),)).fetchone()
            return dict(row) if row else None

    def set_resident_notify(self, resident_id: int, url: str) -> None:
        validate_settings({"notify_url": url})
        with self.lock, self.conn:
            self._resident(resident_id)
            self.conn.execute("UPDATE residents SET notify_url=? WHERE id=?", (url, resident_id))

    def personal_records(self, resident_id: int, *, days: int = 60, now: datetime | None = None) -> list[dict]:
        now = now or utcnow()
        since = stamp(now - timedelta(days=days))
        return [o for o in self.obligations(history=True, resident_id=resident_id, now=now)
                if o["created_at"] >= since and o["confidence"] == "confirmed" and o["lifecycle"] != "dismissed"]

    def dispute_own(self, resident_id: int, obligation_id: int, note: str) -> dict:
        note = note.strip()
        if not note:
            raise ValueError("Say what's wrong, so whoever reviews it knows.")
        with self.lock:
            item = self.obligation(obligation_id)
            if item["resident_id"] != resident_id or item["confidence"] != "confirmed":
                raise LookupError("Dish event not found.")
            if item["lifecycle"] == "disputed":
                raise ValueError("This one is already disputed.")
            return self.action(obligation_id, "dispute", note=f"Disputed by {item['resident_name']}: {note}")

    # How well the system is doing, for everyone to see.

    def scoreboard(self, *, weeks: int = 8, now: datetime | None = None) -> dict:
        """Weekly measures of whether the camera can be trusted, from people's own answers.

        False alarms: camera records a person dismissed as not a dish, against those confirmed as real.
        Identity: suggestions a person checked (camera-named records excluded, see attribution_gate).
        Camera names: how many a person later corrected. Disputes: raised, and how many changed the
        outcome (the record was then dismissed or given to someone else).
        """
        now = now or utcnow()
        tz = ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))
        today = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        first = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
        rows: list[dict] = []
        with self.lock:
            records = self.conn.execute("SELECT * FROM obligations WHERE created_at>=? AND source='vision'", (stamp(first),)).fetchall()
            decided = {row["obligation_id"]: row["n"] for row in self.conn.execute(
                "SELECT obligation_id, count(*) n FROM events WHERE event IN ('confirm','reassign','dismiss','resolve') AND created_at>=? GROUP BY obligation_id", (stamp(first),))}
            disputes = self.conn.execute("""SELECT d.obligation_id, d.created_at, EXISTS (SELECT 1 FROM events c WHERE c.obligation_id=d.obligation_id AND c.id>d.id
                AND (c.event='dismiss' OR (c.event IN ('reassign','confirm') AND json_extract(c.data,'$.before.resident_id') IS NOT (SELECT resident_id FROM obligations WHERE id=d.obligation_id)))) overturned
                FROM events d WHERE d.event='dispute' AND d.created_at>=?""", (stamp(first),)).fetchall()
            dismissed_as = {row["obligation_id"]: (row["note"] or "").strip() for row in self.conn.execute(
                "SELECT obligation_id, json_extract(data,'$.note') note FROM events WHERE event='dismiss' AND created_at>=? ORDER BY id", (stamp(first),))}
        for index in range(weeks):
            start, end = first + timedelta(days=7 * index), first + timedelta(days=7 * (index + 1))
            inside = lambda value: stamp(start) <= value < stamp(end)  # noqa: E731
            week = [r for r in records if inside(r["created_at"])]
            human = [r for r in week if r["id"] in decided]
            false = sum(r["lifecycle"] == "dismissed" and dismissed_as.get(r["id"], "") in NOT_A_DISH_NOTES for r in human)
            real = sum(r["lifecycle"] != "dismissed" and r["confidence"] == "confirmed" for r in human)
            checked = [r for r in week if r["suggestion_correct"] is not None and r["attribution"] is None]
            named = [r for r in week if r["attribution"] in ("camera", "camera_reviewed")]
            raised = [d for d in disputes if inside(d["created_at"])]
            rows.append({"week": start.date().isoformat(), "camera_records": len(week), "false_alarms": false, "real": real,
                         "false_alarm_rate": false / (false + real) if false + real else None,
                         "identity_checked": len(checked), "identity_correct": sum(r["suggestion_correct"] for r in checked),
                         "identity_accuracy": sum(r["suggestion_correct"] for r in checked) / len(checked) if checked else None,
                         "camera_named": len(named), "camera_corrected": sum(r["attribution"] == "camera_reviewed" and r["resident_id"] != r["original_resident_id"] for r in named),
                         "late_verified": sum(r["overdue_verified"] for r in week), "disputes": len(raised), "disputes_overturned": sum(d["overturned"] for d in raised)})
        return {"weeks": rows, "auto": self.attribution_gate()}

    def learning(self) -> dict:
        """How often appearance suggestions matched the person a human chose."""
        with self.lock:
            row = self.conn.execute("SELECT count(*) total, sum(suggestion_correct) correct FROM obligations WHERE suggestion_correct IS NOT NULL").fetchone()
            labeled = self.conn.execute("SELECT count(*) FROM person_samples WHERE resident_id IS NOT NULL").fetchone()[0]
            unlabeled = self.conn.execute("SELECT count(*) FROM person_samples WHERE resident_id IS NULL").fetchone()[0]
            named = self.conn.execute("SELECT count(*) FROM obligations WHERE attribution='camera'").fetchone()[0]
            return {"suggestions_checked": row["total"], "suggestions_correct": row["correct"] or 0,
                    "accuracy": (row["correct"] or 0) / row["total"] if row["total"] else None, "labeled_samples": labeled, "unlabeled_samples": unlabeled,
                    "auto": self.attribution_gate() | {"named": named}}
