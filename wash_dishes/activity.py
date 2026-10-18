"""Event-time originals, retained independently of the rolling video and tracker RAM.

These are handling candidates, not ownership decisions. Only the inventory's
same-dish checks may attach them to a record. Unlinked candidates are bounded;
attached copies follow the normal evidence retention policy.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from .store import parse_stamp, stamp, utcnow

if TYPE_CHECKING:
    from .media import Media


class ActivityPhotos:
    MAX_ENTRIES = 512
    MAX_BYTES = 512 * 1024 * 1024

    def __init__(self, media: Media):
        self.media, self.store = media, media.store
        with self.store.lock, self.store.conn:
            self.store.conn.execute('''CREATE TABLE IF NOT EXISTS activity_photos(
                id TEXT PRIMARY KEY, area_id TEXT NOT NULL, at TEXT NOT NULL,
                frame TEXT NOT NULL, before_frame TEXT, metadata TEXT NOT NULL)''')
            self.store.conn.execute('CREATE INDEX IF NOT EXISTS activity_area_time ON activity_photos(area_id,at)')

    def save(self, area: dict, generation: str, visitor: dict) -> None:
        """Commit originals before returning to the camera loop, even without a record."""
        metadata = {k: visitor[k] for k in ('box', 'track', 'dishes')}
        metadata.update(area_box=area['box'], generation=generation)
        for key in ('at', 'before_at', 'visit_started'):
            metadata[key] = stamp(visitor[key]) if visitor.get(key) else None
        filenames = []
        with self.media.lock:
            try:
                for key in ('frame', 'before'):
                    jpeg = visitor.get(key)
                    if not jpeg:
                        filenames.append(None)
                        continue
                    if not jpeg.startswith(b'\xff\xd8') or len(jpeg) > 8 * 1024 * 1024:
                        raise ValueError('The camera did not provide a valid JPEG.')
                    name = 'activity-' + uuid.uuid4().hex + '.jpg'
                    filenames.append(name)
                    path = self.media.safe_path(name)
                    # A committed row always refers to a completely written original.
                    with path.open('xb') as output:
                        os.chmod(path, 0o600)
                        output.write(jpeg)
                        output.flush()
                        os.fsync(output.fileno())
                with self.store.lock, self.store.conn:
                    self.store.conn.execute('INSERT INTO activity_photos VALUES(?,?,?,?,?,?)',
                                            (uuid.uuid4().hex, area['id'], stamp(visitor['at']),
                                             *filenames, json.dumps(metadata)))
            except Exception:
                for name in filenames:
                    if name:
                        self.media.safe_path(name).unlink(missing_ok=True)
                raise
            self.cleanup(now=visitor['at'])

    def recent(self, area: dict, at) -> list[dict]:
        # A long recording gap cannot establish that an earlier dish is the same
        # one. Keep the originals, but only restore the existing 30-minute window.
        with self.media.lock, self.store.lock:
            rows = self.store.conn.execute('''SELECT * FROM activity_photos
                WHERE area_id=? AND at>=? AND at<=? ORDER BY at DESC LIMIT 48''',
                (area['id'], stamp(at-timedelta(minutes=30)), stamp(at))).fetchall()
            visitors = []
            for row in reversed(rows):
                data = json.loads(row['metadata'])
                if data['area_box'] != area['box']:
                    continue
                try:
                    data['frame'] = self.media.safe_path(row['frame']).read_bytes()
                    data['before'] = self.media.safe_path(row['before_frame']).read_bytes() if row['before_frame'] else None
                except FileNotFoundError:
                    continue
                for key in ('at', 'before_at', 'visit_started'):
                    data[key] = parse_stamp(data[key]) if data.get(key) else None
                # Tracker IDs are local to a camera run, never identities across restarts.
                data['track'] = f"saved:{data['generation']}:{data['track']}"
                data['recovered'] = True
                visitors.append(data)
            return visitors

    def cleanup(self, *, now=None, reclaim_bytes=0) -> int:
        now = now or utcnow()
        cutoff = stamp(now-timedelta(hours=24))
        with self.media.lock, self.store.lock, self.store.conn:
            rows = self.store.conn.execute('SELECT * FROM activity_photos ORDER BY at').fetchall()
            sizes = {r['id']: sum(self.media.safe_path(n).stat().st_size for n in (r['frame'], r['before_frame'])
                                 if n and self.media.safe_path(n).exists()) for r in rows}
            total, count, removed = sum(sizes.values()), len(rows), 0
            for row in rows:
                if row['at'] >= cutoff and count <= self.MAX_ENTRIES and total <= self.MAX_BYTES and removed >= reclaim_bytes:
                    break
                self.store.conn.execute('DELETE FROM activity_photos WHERE id=?', (row['id'],))
                for name in (row['frame'], row['before_frame']):
                    if name:
                        self.media.safe_path(name).unlink(missing_ok=True)
                total -= sizes[row['id']]
                removed += sizes[row['id']]
                count -= 1
            # Interrupted writes have no committed row; don't let them leak space.
            referenced = {n for row in self.store.conn.execute('SELECT frame,before_frame FROM activity_photos') for n in row if n}
            for path in self.media.media.glob('activity-*.jpg'):
                if path.name not in referenced and path.stat().st_mtime < now.timestamp()-60:
                    removed += path.stat().st_size
                    path.unlink(missing_ok=True)
            return removed
