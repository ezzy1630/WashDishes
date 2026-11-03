"""Offline, reproducible tight-crop review calibration. Never opens a live DB for writes.

Set TMPDIR/XDG_CACHE_HOME outside the checkout. `features` needs local CLIP weights;
`train` reads development labels only; `measure` copies the SQLite snapshot into OUT.
Photos, features, copied DBs and measurement output are private scratch artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from wash_dishes import review_evidence, review_objects
from wash_dishes.house import House
from wash_dishes.house_auth import HouseConfig
from wash_dishes.inventory import area_for
from wash_dishes.media import Media
from wash_dishes.store import Store
from wash_dishes.verifier import fit_probe, probability


def truth(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate truth IDs')
    for row in rows:
        split = 'holdout' if int(hashlib.sha256(str(row['id']).encode()).hexdigest(), 16) % 2 else 'dev'
        if row['split'] != split:
            raise ValueError('Truth split differs from frozen SHA-256 parity')
    return rows


def feature_map(path, rows):
    with np.load(path, allow_pickle=False) as saved:
        ids, vectors = saved['ids'].tolist(), saved['vectors']
    if (vectors.shape != (len(ids), 512) or len(ids) != len(set(ids))
            or set(ids) != {r['id'] for r in rows} or not np.isfinite(vectors).all()):
        raise ValueError('Expected one finite 512-dimensional vector per truth record')
    return dict(zip(ids, vectors))


def features(args, rows):
    import cv2
    import torch

    from wash_dishes.verifier import Embedder, context_crop

    torch.set_num_threads(4)
    embed = Embedder(args.data)
    embed._load()
    if torch.backends.mps.is_available():
        embed._device = 'mps'
        embed._model = embed._model.to('mps')
    vectors = []
    for start in range(0, len(rows), 16):
        crops = []
        for row in rows[start:start + 16]:
            view = row['views'][0]
            filename = args.data / 'media' / view['filename']
            if hashlib.sha256(filename.read_bytes()).hexdigest() != view['sha256']:
                raise ValueError(f"Photo changed for record {row['id']}")
            image = cv2.imread(str(filename))
            crops.append(context_crop(image, view['box'], scale=1.2, minimum=0))
        vectors.extend(embed(crops))
        print(f'{min(start + 16, len(rows))}/{len(rows)}', flush=True)
    with args.out.open('xb') as file:
        np.savez_compressed(file, ids=np.array([r['id'] for r in rows]), vectors=np.array(vectors))


def train(args, rows):
    vectors = feature_map(args.features, rows)
    dev = [r for r in rows if r['split'] == 'dev']
    x = np.array([vectors[r['id']] for r in dev])
    # Uncertain dirtiness does not teach "not a dish". Protect every uncertain view.
    y = np.array([r['label'] != 'non_dish' for r in dev], int)
    held = np.zeros(len(dev))
    for fold in np.array_split(np.arange(len(dev)), 5):
        rest = np.setdiff1d(np.arange(len(dev)), fold)
        balanced = np.r_[rest[y[rest] == 1], np.tile(rest[y[rest] == 0], 8)]
        held[fold] = probability(x[fold], *fit_probe(x[balanced], y[balanced], l2=.001))
    balanced = np.r_[np.where(y == 1)[0], np.tile(np.where(y == 0)[0], 8)]
    weights, bias = fit_probe(x[balanced], y[balanced], l2=.001)
    result = {'weights': weights.round(7).tolist(), 'bias': round(bias, 7),
              'threshold': .35, 'retain_threshold': .65, 'crop_scale': 1.2, 'minimum': 0,
              'model': 'CLIP ViT-B-32 laion2b',
              'truth_sha256': hashlib.sha256(args.truth.read_bytes()).hexdigest(),
              'training_split': 'sha256(record ID) integer mod 2 == 0',
              'training_examples': {'protected': int(y.sum()), 'non_dish': int((y == 0).sum())},
              'negative_repeats': 8, 'l2': .001}
    # These thresholds belong to this calibration, not an automatic adoption gate.
    # New labels require fresh evaluation; do not overwrite an adopted probe here.
    with args.out.open('x') as file:
        file.write(json.dumps(result, indent=2))
    dirty = [p for r, p in zip(dev, held) if r['label'] == 'dirty']
    print(json.dumps({'dev_records': len(dev), 'dirty_oof_min': min(dirty),
                      'protected_oof_min': float(held[y == 1].min()), 'output': str(args.out)}))


def measure(args, rows):
    args.out.mkdir()  # Refuse to overwrite another run or an adopted data root.
    source = sqlite3.connect(f'file:{args.data / "history.db"}?mode=ro', uri=True)
    copied = sqlite3.connect(args.out / 'history.db')
    source.backup(copied)
    copied.close()
    source.close()
    for directory in ['models', 'media']:
        if directory == 'models' and args.candidate_model:
            (args.out / directory).mkdir()
            for model in (args.data / directory).iterdir():
                if model.name != 'dish-review-probe.json':
                    (args.out / directory / model.name).symlink_to(model.resolve())
            (args.out / directory / 'dish-review-probe.json').write_bytes(args.candidate_model.read_bytes())
        else:
            (args.out / directory).symlink_to((args.data / directory).resolve(), target_is_directory=True)
    store = Store(args.out)
    original_start = store.conn.execute('SELECT start_week FROM league_start WHERE id=1').fetchone()
    if args.history_start:
        with store.conn:
            store.conn.execute('UPDATE league_start SET start_week=? WHERE id=1', (args.history_start,))
    house = House(store, Media(args.out, store), HouseConfig(public_origin='https://offline.invalid'))
    weeks = args.weeks
    before = {w: house.week(w, None) for w in weeks}
    vectors = feature_map(args.features, rows)
    # Backfill only the isolated copy, reusing the exact source-photo features.
    with store.conn:
        for oid, vector in vectors.items():
            store.conn.execute('UPDATE dish_samples SET tight_embedding=? WHERE id=(SELECT max(id) FROM dish_samples WHERE obligation_id=?)',
                               (json.dumps(vector.round(5).tolist()), oid))
    after = {w: house.week(w, None) for w in weeks}
    probe = review_evidence.kitchen_probe(store.root)
    object_probe = review_objects.probe(store.root)
    areas = store.settings()['areas']
    samples = {r['obligation_id']: dict(r) for r in store.conn.execute('SELECT * FROM dish_samples ORDER BY id')}
    memories = [dict(r) for r in store.conn.execute('SELECT m.* FROM remembered_objects m JOIN dish_reviews r ON r.event_id=m.event_id')]
    for memory in memories:
        memory['embedding'] = json.loads(memory['embedding'])
    records = []
    residents = store.residents()
    for row in rows:
        raw = dict(store.conn.execute('SELECT * FROM obligations WHERE id=?', (row['id'],)).fetchone())
        raw['dishes'] = json.loads(raw['dishes'])
        raw['human_named'] = False
        photo = house.evidence_for([row['id']])
        dish = house.dish(raw, residents, before[weeks[-1]]['rules'], actor=None, closed=False, evidence=photo)
        sample = samples.get(row['id'], {})
        base = review_evidence.signals(sample.get('embedding'), probe, memories,
                                       area_for(json.loads(raw['box']), areas) if raw['box'] else None)
        new = base | review_objects.signals(sample.get('tight_embedding'), object_probe)
        def rating(signals):
            return house.visits([raw], [dish], photo, residents, {}, actor=None, signals={row['id']: signals})[0]['evidence']
        records.append({k: row[k] for k in ['id', 'split', 'label']} | {'before': rating(base), 'after': rating(new)})
    cohort = {}
    for split in ['dev', 'holdout']:
        cohort[split] = {}
        for label in sorted({r['label'] for r in rows}):
            selected = [r for r in records if r['split'] == split and r['label'] == label]
            cohort[split][label] = {'total': len(selected),
                'default_before': sum(r['before'] != 'weak' for r in selected),
                'default_after': sum(r['after'] != 'weak' for r in selected),
                'newly_weak': [r['id'] for r in selected if r['before'] != 'weak' and r['after'] == 'weak'],
                'still_weak': [r['id'] for r in selected if r['after'] == 'weak']}
    counts = {w: {'visits': len(before[w]['visits']), 'default_before': before[w]['clear_count'],
                  'default_after': after[w]['clear_count']} for w in weeks}
    result = {'at': datetime.now(timezone.utc).isoformat(), 'source_sha256': hashlib.sha256((args.data / 'history.db').read_bytes()).hexdigest(),
              'truth_sha256': hashlib.sha256(args.truth.read_bytes()).hexdigest(),
              'model_sha256': hashlib.sha256(args.candidate_model.read_bytes()).hexdigest() if args.candidate_model else None,
              'original_league_start': original_start[0] if original_start else None,
              'history_start_projection': args.history_start, 'weeks': counts, 'cohort': cohort, 'records': records}
    for name, value in [('measurement', result), ('weeks-before', before), ('weeks-after', after)]:
        (args.out / f'{name}.json').write_text(json.dumps(value, indent=2))
    store.conn.close()
    print(json.dumps({'weeks': counts, 'cohort': cohort}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['features', 'train', 'measure'])
    parser.add_argument('--truth', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--features', type=Path)
    parser.add_argument('--candidate-model', type=Path, help='Opt into a candidate on the isolated measurement copy only')
    parser.add_argument('--weeks', nargs='+', default=['2026-10-26', '2026-11-02'])
    parser.add_argument('--history-start', help='Project a pre-start week on the isolated copy only')
    args = parser.parse_args()
    if args.data and args.out.resolve().is_relative_to(args.data.resolve()):
        parser.error('Output must be outside the input snapshot')
    if args.command in ('features', 'measure') and not args.data:
        parser.error('--data required')
    if args.command in ('train', 'measure') and not args.features:
        parser.error('--features required')
    {'features': features, 'train': train, 'measure': measure}[args.command](args, truth(args.truth))


if __name__ == '__main__':
    main()
