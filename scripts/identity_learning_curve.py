#!/usr/bin/env python3
"""Replay human names locally from an explicit DB snapshot; never contacts a camera or host."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wash_dishes.config import Config  # noqa: E402
from wash_dishes.identity_fit import evaluate, observations, refit  # noqa: E402
from wash_dishes.store import IDENTITY_WEIGHTS_FILE, Store  # noqa: E402


def backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def replay(snapshot: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    root = output / 'replay'
    Config(root).prepare()
    backup(snapshot / 'history.db', root / 'history.db')
    # Score the installed model separately; a cold-start replay cannot inherit future labels in its weights.
    weights = snapshot / IDENTITY_WEIGHTS_FILE
    if weights.exists():
        (root / IDENTITY_WEIGHTS_FILE).write_bytes(weights.read_bytes())
    store = Store(root)
    try:
        labels = sorted(observations(store), key=lambda r: (r['at'], r['id']))
        original = evaluate(store, leave_one_day_out=True)
        (root / IDENTITY_WEIGHTS_FILE).unlink(missing_ok=True)
        all_samples = [dict(r) for r in store.conn.execute('SELECT id,obligation_id,resident_id FROM person_samples WHERE resident_id IS NOT NULL')]
        with store.conn:
            store.conn.execute('UPDATE person_samples SET resident_id=NULL,hint_resident_id=NULL')
        milestones = sorted({n for n in (10, 20, 30, 40, 50, len(labels)) if n <= len(labels)})
        curve, online = [], []
        for count, row in enumerate(labels, 1):
            guess, score, _, _ = store.suggest_identity(row['cues'], at=row['at'], context=row['context'], exclude_obligation=row['id'])
            gate = store.identity_display_gate()
            shown = bool(gate['active'] and guess is not None and score is not None and score >= gate['threshold'])
            online.append({'name_count_before': count - 1, 'id': row['id'], 'guess': guess,
                           'correct': guess == row['truth'], 'shown': shown})
            with store.conn:
                store.conn.executemany('UPDATE person_samples SET resident_id=? WHERE id=?',
                    [(s['resident_id'], s['id']) for s in all_samples if s['obligation_id'] == row['id']])
            if count in milestones:
                fitted = refit(store)
                held = evaluate(store, leave_one_day_out=True)
                curve.append({'names': count, 'independent_cases': fitted['cases'], 'days': fitted['days'],
                    'held_out': {k: v for k, v in held.items() if k != 'rows'},
                    'display_gate': fitted['display_gate'], 'adopted': fitted['adopted'], 'reason': fitted.get('reason'),
                    'online_shown': sum(r['shown'] for r in online), 'online_cases': len(online)})
                print(json.dumps(curve[-1]), flush=True)
        result = {'snapshot_sha256': hashlib.sha256((snapshot / 'history.db').read_bytes()).hexdigest(),
                  'protocol': 'Cold-start uniform cue weights, never the installed weights trained on future prefix labels. Human truth replayed in capture order as simulated name order; actual historical entry times are not reconstructed. Only prefix labels are in the gallery. Refit at each milestone. Day-excluded held-out queries include abstentions; independent counts deduplicate captures and 30-minute same-person visits.',
                  'original_day_out': original, 'learning_curve': curve, 'online': online}
        (output / 'learning-curve.json').write_text(json.dumps(result, indent=2))
        return result
    finally:
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    replay(args.snapshot, args.output)
