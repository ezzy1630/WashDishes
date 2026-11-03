"""Recover transient arrival failures. Dry-run by default; no models or service startup.

Use --after-id from next_after_id to resume a bounded pass. Start a new pass at
zero to revisit backoff/decode failures. The service uses the same process claim.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

from .filmstrip import KEEP, TIMEOUT, Filmstrips, footage_segments
from .media import Media
from .rerender import ReadJournal
from .store import Store, parse_stamp, stamp, utcnow


def inventory(root: Path, *, after_id=0, limit=3, now=None):
    now = now or utcnow()
    with sqlite3.connect((root / 'history.db').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""SELECT o.id,o.created_at,m.visit_id FROM filmstrip_jobs f
            JOIN obligations o ON o.id=f.obligation_id
            LEFT JOIN arrival_visit_members m ON m.obligation_id=o.id
            LEFT JOIN arrival_visits v ON v.id=m.visit_id
            WHERE o.id>? AND (f.status='retry' OR f.reason=?)
            AND o.archived_at IS NULL AND o.lifecycle NOT IN ('dismissed','disputed')
            AND o.created_at>=? AND (v.id IS NULL OR o.id=(SELECT min(obligation_id) FROM arrival_visit_members WHERE visit_id=v.id))
            ORDER BY o.id LIMIT ?""", (after_id, TIMEOUT, stamp(now-KEEP), limit+1)).fetchall()
    segments = footage_segments(root / 'buffer') if rows else []
    actions = [{'obligation_id': r['id'], 'visit_id': r['visit_id'],
                'retained': any(a <= parse_stamp(r['created_at']).timestamp() < b for a,b,_ in segments)}
               for r in rows[:limit]]
    return actions, len(rows) > limit


def run(root: Path, *, apply=False, limit=3, after_id=0, max_seconds=90, now=None):
    if limit < 1 or after_id < 0 or not 1 <= max_seconds <= 120:
        raise ValueError('Use a positive limit, nonnegative after-id and a 1–120 second budget.')
    now = now or utcnow()
    root = root.resolve()
    start = time.monotonic()
    actions, more = inventory(root, after_id=after_id, limit=limit, now=now)
    result = {'root': str(root), 'dry_run': not apply, 'actions': actions, 'results': [],
              'next_after_id': actions[-1]['obligation_id'] if more else None}
    if not apply:
        return result
    try:
        os.nice(15)
    except OSError:
        pass
    store = Store(root)
    try:
        worker = Filmstrips(store, Media(root, store), ReadJournal(root / 'journal.db'))
        cursor = after_id
        for action in actions:
            # Reserve a full worker budget. Never start a second search while
            # the running worker owns the claim, or load duplicate vision models.
            if time.monotonic()-start > max_seconds-30:
                result['next_after_id'] = cursor
                break
            if not action['retained']:
                outcome = {'status': 'skipped', 'reason': 'First sighting footage is no longer retained.'}
            else:
                outcome = worker.generate(action['obligation_id'], now=now)
            result['results'].append(action | outcome)
            if outcome['status'] == 'busy':
                result['next_after_id'] = cursor
                break
            cursor = action['obligation_id']
    finally:
        store.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--after-id', type=int, default=0)
    parser.add_argument('--max-seconds', type=float, default=90)
    args = parser.parse_args()
    print(json.dumps(run(args.root, apply=args.apply, limit=args.limit, after_id=args.after_id, max_seconds=args.max_seconds), indent=2))


if __name__ == '__main__':
    main()
