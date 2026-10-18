from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from .activity import ActivityPhotos
from .store import Store, parse_stamp, stamp, utcnow


class Media:
    def __init__(self, root: Path, store: Store):
        self.root, self.store = root, store
        self.media = root / "media"
        self.buffer = root / "buffer"
        self.lock = threading.RLock()
        self.activity = ActivityPhotos(self)
        with store.lock, store.conn:
            store.conn.execute("""CREATE TABLE IF NOT EXISTS clip_jobs(
                id TEXT PRIMARY KEY, obligation_id INTEGER NOT NULL REFERENCES obligations(id),
                label TEXT NOT NULL, at TEXT NOT NULL, seconds INTEGER NOT NULL, ready_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                error TEXT, evidence_id TEXT REFERENCES evidence(id))""")
        # Called with (obligation_id, label, evidence_id, path) after each event clip is saved, outside the lock.
        self.clip_saved: Callable[[int, str, str, Path], None] | None = None

    @property
    def pending_clips(self) -> list[tuple]:
        with self.store.lock:
            return [(parse_stamp(r['ready_at']).timestamp(), r['obligation_id'], r['label'], parse_stamp(r['at']), r['seconds'])
                    for r in self.store.conn.execute("SELECT * FROM clip_jobs WHERE status='pending' ORDER BY ready_at")]

    def request_event_clip(self, obligation_id: int, label: str, *, at: datetime, seconds: int = 30) -> None:
        # Stable identifiers make producer retries and a crash after export idempotent.
        job_id = hashlib.sha256(f'{obligation_id}|{label}|{stamp(at)}|{seconds}'.encode()).hexdigest()[:32]
        with self.store.lock, self.store.conn:
            count = self.store.conn.execute("SELECT count(*) FROM clip_jobs WHERE status='pending'").fetchone()[0]
            state = 'pending' if count < 128 else 'failed'
            error = None if state == 'pending' else 'Capture queue full; original snapshot retained.'
            cursor = self.store.conn.execute("INSERT OR IGNORE INTO clip_jobs(id,obligation_id,label,at,seconds,ready_at,status,error) VALUES(?,?,?,?,?,?,?,?)",
                                            (job_id, obligation_id, label, stamp(at), seconds, stamp(at+timedelta(seconds=20)), state, error))
            if cursor.rowcount and error:
                self.store._event(obligation_id, 'clip_capture_failed', error, {'job_id': job_id})

    def capture_status(self) -> dict:
        with self.store.lock:
            rows = self.store.conn.execute("SELECT status,attempts FROM clip_jobs WHERE status!='saved'").fetchall()
            activity = self.store.conn.execute('SELECT count(*),max(at) FROM activity_photos').fetchone()
        return {'pending': sum(r['status'] == 'pending' for r in rows),
                'retrying': sum(r['status'] == 'pending' and r['attempts'] > 0 for r in rows),
                'failed': sum(r['status'] == 'failed' for r in rows),
                'activity_photos': activity[0], 'last_activity_photo_at': activity[1]}

    def flush_event_clips(self, *, now: datetime | None = None) -> None:
        now = now or utcnow()
        with self.store.lock:
            ready = self.store.conn.execute("SELECT * FROM clip_jobs WHERE status='pending' AND ready_at<=? ORDER BY ready_at LIMIT 4", (stamp(now),)).fetchall()
        for job in ready:
            at = parse_stamp(job['at'])
            evidence_id = self.save_recent_clip(job['obligation_id'], job['label'], job['seconds'], at=at, evidence_id=job['id'])
            with self.store.lock, self.store.conn:
                if evidence_id:
                    self.store.conn.execute("UPDATE clip_jobs SET status='saved',evidence_id=?,error=NULL WHERE id=?", (evidence_id, job['id']))
                else:
                    attempts = job['attempts'] + 1
                    expired = now - at > timedelta(hours=self.store.settings()['buffer_hours'])
                    failed = expired or attempts >= 5
                    error = 'Source video is unavailable or clip extraction failed; snapshot retained.'
                    self.store.conn.execute("UPDATE clip_jobs SET status=?,attempts=?,error=?,ready_at=? WHERE id=?",
                                            ('failed' if failed else 'pending', attempts, error, stamp(now+timedelta(seconds=20*2**attempts)), job['id']))
                    if failed:
                        self.store._event(job['obligation_id'], 'clip_capture_failed', error, {'job_id': job['id']})
            if evidence_id and self.clip_saved:
                self.clip_saved(job['obligation_id'], job['label'], evidence_id, self.safe_path(evidence_id + '.mp4'))

    def safe_path(self, filename: str) -> Path:
        path = self.media / filename
        if Path(filename).name != filename or path.is_symlink() or path.resolve().parent != self.media.resolve():
            raise ValueError("Invalid evidence path.")
        return path

    def save_snapshot(self, obligation_id: int, jpeg: bytes, label: str = "Dish deposit", *, at: datetime | None = None, capture: dict | None = None) -> str:
        if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > 8 * 1024 * 1024:
            raise ValueError("The camera did not provide a valid JPEG.")
        with self.lock:
            evidence_id = uuid.uuid4().hex
            filename = evidence_id + ".jpg"
            path = self.safe_path(filename)
            path.write_bytes(jpeg)
            path.chmod(0o600)
            try:
                self.store.add_evidence(obligation_id, evidence_id, filename, "snapshot", label, at=at, capture=capture)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            return evidence_id

    def save_check_photo(self, jpeg: bytes) -> str:
        """A kitchen check's frame; it belongs to the check, not to any one record."""
        if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > 8 * 1024 * 1024:
            raise ValueError("The camera did not provide a valid JPEG.")
        with self.lock:
            filename = f"check-{uuid.uuid4().hex}.jpg"
            path = self.safe_path(filename)
            path.write_bytes(jpeg)
            path.chmod(0o600)
            return filename

    def save_recent_clip(self, obligation_id: int, label: str = "Camera event", seconds: int = 30, *, at: datetime | None = None, evidence_id: str | None = None) -> str | None:
        """Completed segments are copied, so buffer rotation cannot invalidate evidence."""
        if evidence_id:
            existing = self.store.evidence(evidence_id)
            if existing and not existing['expired'] and self.safe_path(existing['filename']).is_file():
                return evidence_id
        if not shutil.which("ffmpeg"):
            return None
        with self.lock:
            anchor = (at or utcnow()).timestamp()
            # Exclude the newest file before selecting the event window: it may still be open.
            complete = sorted((p for p in self.buffer.glob("*.ts") if not p.is_symlink()), key=lambda p: p.stat().st_mtime)[:-1]
            segments = [p for p in complete if anchor - seconds - 10 <= p.stat().st_mtime <= anchor + 10]
            if not segments:
                return None
            evidence_id = evidence_id or uuid.uuid4().hex
            path = self.safe_path(evidence_id + ".mp4")
            listing = self.media / (evidence_id + ".concat")
            filenames = [str(segment.resolve()).replace("'", "'\\''") for segment in segments]
            listing.write_text("\n".join(f"file '{filename}'" for filename in filenames) + "\n")
            listing.chmod(0o600)
            try:
                subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-an", "-c:v", "copy", "-movflags", "+faststart", "-y", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True)
                path.chmod(0o600)
                times = [p.stat().st_mtime for p in segments]
                self.store.add_evidence(obligation_id, evidence_id, path.name, "clip", label, at=at,
                    capture={'requested_end': stamp(at), 'requested_seconds': seconds, 'timestamp_estimated': True,
                             'segments': len(segments), 'has_gaps': any(b-a > 15 for a, b in zip(times, times[1:])),
                             'covers_requested_window': times[0]-10 <= anchor-seconds and times[-1] >= anchor})
                return evidence_id
            except (subprocess.SubprocessError, OSError):
                path.unlink(missing_ok=True)
                return None
            finally:
                listing.unlink(missing_ok=True)

    def status(self) -> dict:
        def total(folder: Path) -> int:
            return sum(p.stat().st_size for p in folder.iterdir() if p.is_file() and not p.is_symlink())
        with self.lock:
            buffer_bytes, evidence_bytes = total(self.buffer), total(self.media)
            return {"used_bytes": buffer_bytes + evidence_bytes, "limit_bytes": int(self.store.settings()["storage_limit_gb"] * 1_000_000_000), "buffer_bytes": buffer_bytes, "evidence_bytes": evidence_bytes, "free_bytes": shutil.disk_usage(self.root).free}

    def cleanup(self) -> dict:
        with self.lock:
            now = utcnow()
            settings = self.store.settings()
            self.activity.cleanup(now=now)
            cutoff = now - timedelta(hours=settings["buffer_hours"])
            removed = 0
            # The active segment needs a grace interval, including under quota pressure.
            for path in self.buffer.glob("*.ts"):
                if path.is_symlink():
                    continue
                if path.stat().st_mtime < cutoff.timestamp() and path.stat().st_mtime < now.timestamp() - 30:
                    path.unlink(missing_ok=True)
                    removed += 1
            for evidence in self.store.all_evidence(include_expired=True):
                path = self.safe_path(evidence["filename"])
                if evidence["expired"] or parse_stamp(evidence["expires_at"]) <= now or not path.exists():
                    existed = path.exists()
                    path.unlink(missing_ok=True)
                    self.store.expire_evidence(evidence["id"])
                    removed += int(existed)
            status = self.status()
            target = status["limit_bytes"]
            # Reclaim oldest raw footage first, then retained evidence if the hard budget requires it.
            candidates = sorted((p for p in self.buffer.glob("*.ts") if not p.is_symlink() and p.stat().st_mtime < now.timestamp() - 30), key=lambda p: p.stat().st_mtime)
            used = status["used_bytes"]
            free = status["free_bytes"]
            reserve = min(1_000_000_000, max(100_000_000, target // 10))
            for path in candidates:
                if used <= target and free >= reserve:
                    break
                size = path.stat().st_size
                path.unlink(missing_ok=True)
                used -= size
                free += size
                removed += 1
            reclaimed = self.activity.cleanup(now=now, reclaim_bytes=max(0, used-target, reserve-free))
            used -= reclaimed
            free += reclaimed
            for evidence in self.store.all_evidence():
                if used <= target and free >= reserve:
                    break
                path = self.safe_path(evidence["filename"])
                size = path.stat().st_size if path.exists() else 0
                path.unlink(missing_ok=True)
                self.store.expire_evidence(evidence["id"])
                used -= size
                free += size
                removed += 1
            # Kitchen check photos are kept as long as other stills.
            photo_cutoff = (now - timedelta(days=settings["photo_days"])).timestamp()
            for path in self.media.glob("check-*.jpg"):
                if path.stat().st_mtime < photo_cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
            if removed:
                self.store.event("storage_cleanup", f"Removed {removed} expired or over-budget recording files")
            # Keep compact metadata for a year, retaining any open obligation and its audit trail.
            with self.store.lock, self.store.conn:
                old = stamp(now - timedelta(days=365))
                self.store.conn.execute("DELETE FROM events WHERE created_at<? AND (obligation_id IS NULL OR obligation_id IN (SELECT id FROM obligations WHERE lifecycle IN ('resolved','dismissed') AND created_at<?))", (old, old))
            return self.status() | {"removed_files": removed}


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    os.replace(temporary, path)
