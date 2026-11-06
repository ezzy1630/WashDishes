"""Bounded, resumable story jobs. Never rewrite naming units or human decisions."""
from __future__ import annotations

import fcntl
import json
import time
from datetime import timedelta

from . import dish_story
from .media import atomic_write
from .store import parse_stamp, stamp, utcnow
from .story_pose import PoseRanker

RETAINED = timedelta(hours=50)
RETRY = timedelta(minutes=5)
KEEP = timedelta(days=7)


class Stories:
    def __init__(self, store, media, journal):
        self.store, self.media, self.journal = store, media, journal
        self.pose = PoseRanker(store.root / "models" / "yolo26n-pose.pt", store.root / "vision-settings")
        with store.lock, store.conn:
            store.conn.execute('''CREATE TABLE IF NOT EXISTS dish_story_jobs(
                obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id),
                status TEXT NOT NULL, attempted_at TEXT NOT NULL, reason TEXT,
                evidence_id TEXT REFERENCES evidence(id))''')

    def candidates(self, *, now=None, after_id=0, limit=1):
        now = now or utcnow()
        with self.store.lock:
            return [r[0] for r in self.store.conn.execute('''SELECT o.id FROM obligations o
                LEFT JOIN dish_story_jobs j ON j.obligation_id=o.id
                WHERE o.id>? AND o.source IN ('vision','baseline') AND o.box IS NOT NULL
                AND o.archived_at IS NULL AND o.lifecycle NOT IN ('dismissed','disputed','cleared')
                AND o.created_at>=? AND o.due_at<=?
                AND (j.obligation_id IS NULL OR (j.status='retry' AND j.attempted_at<=?))
                ORDER BY o.id LIMIT ?''',
                (after_id, stamp(now-RETAINED), stamp(now), stamp(now-RETRY), limit))]

    def generate(self, oid, *, now=None, budget=dish_story.BUDGET):
        now = now or utcnow()
        with (self.store.root / '.filmstrip.lock').open('a') as claim:
            try:
                fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'obligation_id': oid, 'status': 'busy'}
            with self.store.lock:
                prior = self.store.conn.execute('SELECT * FROM dish_story_jobs WHERE obligation_id=?', (oid,)).fetchone()
                if prior and (prior['status'] != 'retry' or parse_stamp(prior['attempted_at'])+RETRY > now):
                    return dict(prior)
                item = self.store.obligation(oid)
                # Untimed startup inventory still has an observation-based followup.
                item = item | {'due_at': item.get('followup_at') or item.get('due_at')}
            if not item.get('due_at') or parse_stamp(item['due_at']) > now:
                return {'obligation_id': oid, 'status': 'pending', 'reason': 'Deadline has not passed.'}
            if item.get('archived_at') or item['lifecycle'] in ('dismissed','disputed','cleared'):
                return {'obligation_id': oid, 'status': 'skipped', 'reason': 'Dish is no longer eligible.'}
            started = time.monotonic()
            found = dish_story.select(self.journal, self.media.buffer, item, now=now,
                                      deadline=started+budget-1, pose=self.pose)
            eid = None
            reason = found if isinstance(found, str) else None
            # A just-passed deadline may not have a usable clear frame yet.
            transient = reason == dish_story.TIMEOUT or (reason and now < parse_stamp(item['due_at'])+timedelta(minutes=30))
            status = 'retry' if transient else 'unavailable' if reason else 'saved'
            if not reason:
                eid = f'dish-story-v{dish_story.VERSION}-{oid}'
                filename = eid+'.jpg'
                picture = dish_story.render(item, found)
                atomic_write(self.media.safe_path(filename), picture)
                try:
                    with self.store.lock, self.store.conn:
                        current = self.store.obligation(oid)
                        if (current['box'] != item['box'] or current['followup_at'] != item['due_at']
                                or current.get('archived_at') or current['lifecycle'] in ('dismissed','disputed','cleared')):
                            reason = 'Dish box, deadline or eligibility changed during search; retry pending.'
                            eid, status = None, 'retry'
                        else:
                            self.store.conn.execute('''INSERT INTO evidence
                                (id,obligation_id,filename,kind,created_at,expires_at,label,capture)
                                VALUES(?,?,?,'filmstrip',?,?,'dish story',?)''',
                                (eid, oid, filename, stamp(now), stamp(now+KEEP), json.dumps(dish_story.metadata(found))))
                        # Evidence and its terminal job are one transaction, including
                        # after a crash. Recovery cannot collide with an orphan row.
                        self._record(oid, status, now, reason, eid)
                    if not eid:
                        self.media.safe_path(filename).unlink(missing_ok=True)
                except Exception:
                    self.media.safe_path(filename).unlink(missing_ok=True)
                    raise
            else:
                with self.store.lock, self.store.conn:
                    self._record(oid, status, now, reason, eid)
            return {'obligation_id': oid, 'status': status, 'reason': reason, 'evidence_id': eid,
                    'seconds': round(time.monotonic()-started, 3)}

    def _record(self, oid, status, now, reason, eid):
        self.store.conn.execute('''INSERT INTO dish_story_jobs VALUES(?,?,?,?,?)
            ON CONFLICT(obligation_id) DO UPDATE SET status=excluded.status,
            attempted_at=excluded.attempted_at,reason=excluded.reason,evidence_id=excluded.evidence_id''',
            (oid, status, stamp(now), reason, eid))

    def run_once(self, *, now=None):
        now = now or utcnow()
        ids = self.candidates(now=now)
        if ids:
            self.generate(ids[0], now=now)
            return ids[0]
        return None
