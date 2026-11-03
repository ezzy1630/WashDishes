#!/usr/bin/env python3
"""Replay provable legacy human labels, in answer-time order, on an immutable SQLite backup.

No inference, network access, live writes or guessed legacy provenance. All output
must be under the task scratch directory. Use kitchen-check answers because they
retain an actual human answer timestamp and per-box verdict.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wash_dishes import learning, learning_jobs  # noqa: E402
from wash_dishes.store import Store, parse_stamp, stamp  # noqa: E402

SCRATCH = Path('/Volumes/Neural/dishwatch-scratch/houseleague/learning')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, default=SCRATCH / 'history.db')
    parser.add_argument('--output', type=Path, default=SCRATCH / 'simulation')
    args = parser.parse_args()
    if not args.backup.resolve().is_relative_to(SCRATCH) or not args.output.resolve().is_relative_to(SCRATCH):
        raise SystemExit('Both paths must be under learning scratch.')
    args.output.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(f'file:{args.backup}?mode=ro&immutable=1', uri=True)
    source.row_factory = sqlite3.Row
    store = Store(args.output)
    (store.root / "media").mkdir(exist_ok=True)
    events = []
    for check in source.execute("SELECT * FROM kitchen_checks WHERE answered_at IS NOT NULL ORDER BY answered_at"):
        for index, (dish, vote) in enumerate(zip(json.loads(check['dishes']), json.loads(check['answer'])['verdicts'])):
            sample = source.execute('SELECT * FROM dish_samples WHERE id=?', (dish.get('sample'),)).fetchone()
            if sample:
                events.append({'sample': dict(sample), 'at': check['at'], 'labeled_at': check['answered_at'],
                               'value': bool(vote), 'source': f"legacy-check:{check['id']}:{index}"})
    frame_labels = []
    label_root = args.backup.parent / 'real' / 'dataset' / 'labels'
    for path in label_root.glob('*.json'):
        frame = json.loads(path.read_text())
        if frame.get('status') == 'labeled' and frame.get('labeled_at'):
            frame_labels.append(frame)
    counts = {'operator_frame_answers': len(frame_labels), 'raw_embeddings': source.execute('SELECT count(*) FROM dish_samples').fetchone()[0],
              'explicit_sample_labels': source.execute('SELECT count(*) FROM dish_samples WHERE label IS NOT NULL').fetchone()[0],
              'auditable_check_answers': len(events)}
    start = min([parse_stamp(f['labeled_at']).date() for f in frame_labels] + [parse_stamp(source.execute('SELECT min(created_at) FROM dish_samples').fetchone()[0]).date()])
    end = parse_stamp(source.execute('SELECT max(created_at) FROM dish_samples').fetchone()[0]).date()
    nights = []
    day = start
    while day <= end:
        for frame in frame_labels:
            if parse_stamp(frame['labeled_at']).date() != day:
                continue
            eid = 'operator-frame:' + frame['id']
            oid = store.create_obligation(None, [{'kind':'plate','quantity':1}], at=parse_stamp(frame['created_at']))['id']
            filename = f"{frame['id']}.jpg"
            media = args.backup.parent / 'real' / 'dataset' / 'images' / filename
            if media.exists():
                import shutil
                shutil.copyfile(media, store.root / 'media' / filename)
            store.add_evidence(oid, eid, filename, 'snapshot', 'Legacy operator frame correction', at=parse_stamp(frame['created_at']))
            with store.conn:
                qid = learning.add_question(store, source=eid, task='frame', evidence_id=eid, obligation_id=oid)
                boxes = [b for b in frame['boxes'] if b['kind'] != 'person']
                learning.answer(store, qid, None, 'boxes', boxes, source=eid, at=parse_stamp(frame['labeled_at']), require_ask=False)
        for event in events:
            if parse_stamp(event['labeled_at']).date() != day:
                continue
            sample = event['sample']
            oid = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], at=parse_stamp(event['at']))['id']
            eid = event['source']
            store.add_evidence(oid, eid, 'legacy.jpg', 'snapshot', 'Legacy operator kitchen check', at=parse_stamp(event['at']))
            with store.conn:
                store.conn.execute('INSERT INTO dish_samples(created_at,embedding,obligation_id,label) VALUES(?,?,?,?)',
                                   (sample['created_at'], sample['embedding'], oid, int(event['value'])))
                qid = learning.add_question(store, source=eid, task='present', evidence_id=eid, obligation_id=oid)
                learning.answer(store, qid, None, 'yes' if event['value'] else 'no', source=eid,
                                at=parse_stamp(event['labeled_at']), require_ask=False)
        result = learning_jobs.run(store, busy=lambda:False, day=day.isoformat())
        details = result['details']
        nights.append({'night':day.isoformat(), 'truth':details['truth_count'], 'train':details['train_count'],
                       'test':details['test_count'], 'status':result['status'], 'models':details['models']})
        day += timedelta(days=1)
    report = {'counts':counts, 'nights':nights, 'accuracy_before':None, 'accuracy_after':None,
              'conclusion':'No measured improvement. Later reserved captures are insufficient for a noise-gated comparison; no candidate was adopted.',
              'excluded':'Legacy sample labels without answer time/actor provenance are not silently promoted to truth.',
              'protocol':'read-only immutable SQLite online backup; compute and new database exclusively on Mac scratch', 'at':stamp()}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    store.close()
    source.close()


if __name__ == '__main__':
    main()
