"""Generate retained fixed-spot stories. Dry-run by default; explicit root required.

Resume with next_after_id. A new pass from zero revisits transient failures.
Only story jobs, new evidence rows and their images change; names, membership,
proof checks and scoring are never rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

from .dish_story import BUDGET
from .media import Media
from .rerender import ReadJournal
from .store import Store, stamp, utcnow
from .story_jobs import Stories


def run(root, *, apply=False, after_id=0, limit=3, max_seconds=90, now=None):
    if not 1 <= limit <= 100 or after_id < 0 or not BUDGET <= max_seconds <= 120:
        raise ValueError('Use limit 1–100, nonnegative after-id, and a 45–120 second budget.')
    root = Path(root).resolve()
    now = now or utcnow()
    # Dry runs never prepare a Store or change SQLite pragmas/schema.
    with sqlite3.connect((root/'history.db').as_uri()+'?mode=ro', uri=True) as conn:
        ids = [r[0] for r in conn.execute('''SELECT id FROM obligations WHERE id>?
            AND source IN ('vision','baseline') AND box IS NOT NULL AND archived_at IS NULL
            AND lifecycle NOT IN ('dismissed','disputed','cleared') AND due_at<=? AND created_at>=?
            ORDER BY id LIMIT ?''', (after_id, stamp(now), stamp(now-timedelta(hours=50)), limit+1))]
    result = {'root': str(root), 'dry_run': not apply, 'ids': ids[:limit], 'results': [],
              'next_after_id': ids[limit-1] if len(ids)>limit else None}
    if not apply:
        return result
    try:
        os.nice(15)
    except OSError:
        pass
    start = time.monotonic()
    store = Store(root)
    try:
        worker = Stories(store, Media(root, store), ReadJournal(root/'journal.db'))
        cursor = after_id
        for oid in ids[:limit]:
            if time.monotonic()-start > max_seconds-BUDGET:
                result['next_after_id'] = cursor
                break
            outcome = worker.generate(oid, now=now)
            result['results'].append(outcome)
            if outcome['status'] == 'busy':
                result['next_after_id'] = cursor
                break
            cursor = oid
    finally:
        store.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--after-id', type=int, default=0)
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--max-seconds', type=float, default=90)
    args = parser.parse_args()
    print(json.dumps(run(args.root, apply=args.apply, after_id=args.after_id, limit=args.limit,
                         max_seconds=args.max_seconds), indent=2))


if __name__ == '__main__':
    main()
