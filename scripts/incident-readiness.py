#!/usr/bin/env python3
"""Read-only incident coverage receipt. Measures saved facts, never household accuracy."""
import argparse
import json
import sqlite3
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--root', type=Path, required=True, help='Exact household data root containing history.db')
args = p.parse_args()
conn = sqlite3.connect(f'file:{args.root.resolve() / "history.db"}?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT o.id,o.source,o.confidence,o.lifecycle,o.resident_id,o.overdue_verified,s.manifest,s.checks FROM obligations o LEFT JOIN evidence_sequences s ON s.obligation_id=o.id').fetchall()
current = [r for r in rows if r['lifecycle'] == 'open']
def complete(r):
    return bool(r['manifest'] and json.loads(r['manifest'])['complete'])
def reviewed(r):
    checks = json.loads(r['checks'] or '{}')
    return complete(r) and all(checks.get(k) is True for k in ('before', 'using', 'leaving', 'later'))
checks = [r[0] for r in conn.execute('SELECT suggestion_correct FROM obligations WHERE suggestion_correct IS NOT NULL AND attribution IS NULL ORDER BY id DESC LIMIT 50')]
report = {'data_root': str(args.root.resolve()), 'records': len(rows), 'open': len(current),
          'open_baseline_unknown_deposit': sum(r['source'] == 'baseline' for r in current),
          'open_four_stages_captured': sum(complete(r) for r in current),
          'open_four_stages_reviewed': sum(reviewed(r) for r in current),
          'named_verified_late': sum(r['confidence'] == 'confirmed' and r['overdue_verified'] and r['lifecycle'] not in ('dismissed','disputed') for r in rows),
          'identity_checked': len(checks), 'identity_accuracy': sum(checks)/len(checks) if checks else None,
          'live_acceptance': 'UNVERIFIED: requires held-out, human-labeled household events including missed incidents. Saved records alone cannot measure recall.'}
if conn.execute("SELECT 1 FROM sqlite_master WHERE name='clip_jobs'").fetchone():
    report['clip_jobs'] = {r[0]: r[1] for r in conn.execute('SELECT status,count(*) FROM clip_jobs GROUP BY status')}
print(json.dumps(report, indent=2))
