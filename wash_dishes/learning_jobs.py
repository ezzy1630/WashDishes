"""Serialized, low-priority candidate training. Live models change only after the outer gate.

Adapters use existing trainer functions in a private root containing training rows only.
The independently supplied evaluation rows never reach those trainer stores.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

from . import learning
from .store import IDENTITY_WEIGHTS_FILE, Store, parse_stamp, stamp

ARTIFACTS = {'probe': 'models/dish-probe-kitchen.json', 'identity': IDENTITY_WEIGHTS_FILE,
             'detector': 'models/dishes-kitchen.pt'}


@contextmanager
def exclusive(root):
    path = Path(root) / 'learning.lock'
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def gate(before, after):
    """Require a paired gain larger than two standard errors, and no recall loss."""
    if len(before) != len(after) or len(after) < 20:
        return False, 'Needs at least 20 paired held-out examples.'
    differences = [float(a) - float(b) for a, b in zip(after, before)]
    if any(not math.isfinite(v) for v in differences):
        return False, 'Invalid evaluation.'
    gain = sum(differences) / len(differences)
    variance = sum((v - gain) ** 2 for v in differences) / (len(differences) - 1)
    noise = 2 * math.sqrt(variance / len(differences))
    return gain > max(.02, noise), f'Paired gain {gain:.4f}; required > {max(.02, noise):.4f}.'


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.name + '.learning-tmp')
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def rollback(store, model, *, reason='Operator rollback'):
    if model not in ARTIFACTS:
        raise ValueError('Unknown model.')
    with exclusive(store.root) as acquired:
        if not acquired:
            raise ValueError('A learning job is running. Try again after it finishes.')
        target = store.root / ARTIFACTS[model]
        previous = target.with_name(target.name + '.learning-previous')
        absent = target.with_name(target.name + '.learning-was-absent')
        if previous.exists():
            atomic_copy(previous, target)
        elif absent.exists():
            target.unlink(missing_ok=True)
        else:
            raise ValueError('No previous model is available.')
        with store.lock, store.conn:
            store.conn.execute('INSERT INTO learning_runs(started_at,finished_at,status,details) VALUES(?,?,?,?)',
                               (stamp(), stamp(), 'rolled_back', json.dumps({'model': model, 'reason': reason})))
    return {'ok': True}


def _promote(store, model, candidate):
    target = store.root / ARTIFACTS[model]
    previous = target.with_name(target.name + '.learning-previous')
    absent = target.with_name(target.name + '.learning-was-absent')
    if target.exists():
        atomic_copy(target, previous)
        absent.unlink(missing_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        absent.touch(mode=0o600)
        previous.unlink(missing_ok=True)
    atomic_copy(candidate, target)
    return target, previous, absent


def run(store, *, busy, adapters=None, day=None):
    """Call only during the quiet-hour window. One persisted claim per night.

    busy is rechecked before EVERY model; unknown camera state must return True.
    Trainer failures are isolated, logged, and never replace an installed model.
    """
    day = day or stamp()[:10]
    with exclusive(store.root) as acquired:
        if not acquired:
            return {'status': 'locked'}
        with store.lock, store.conn:
            if store.conn.execute('SELECT 1 FROM learning_schedule WHERE day=?', (day,)).fetchone():
                return {'status': 'already_ran'}
            status = 'skipped_busy' if busy() else 'running'
            cursor = store.conn.execute('INSERT INTO learning_runs(started_at,status) VALUES(?,?)', (stamp(), status))
            run_id = cursor.lastrowid
            store.conn.execute('INSERT INTO learning_schedule VALUES(?,?)', (day, run_id))
            rows = learning.truth(store)
        train, test = learning.split(rows)
        details = {'train_ids': [r['id'] for r in train], 'test_ids': [r['id'] for r in test],
                   'truth_count': len(rows), 'train_count': len(train), 'test_count': len(test), 'models': {}}
        # The manifest is an audit receipt, not an input capable of bypassing consensus.
        folder = store.root / 'learning' / str(run_id)
        folder.mkdir(parents=True, mode=0o700)
        (folder / 'truth.json').write_text(json.dumps({'train': train, 'test': test}, indent=1))
        if status != 'skipped_busy':
            for model, adapter in (adapters or {m: Candidate(store, folder, m) for m in ARTIFACTS}).items():
                if busy():
                    details['models'][model] = {'status': 'skipped_busy'}
                    continue
                if not train or not test:
                    details['models'][model] = {'status': 'insufficient_truth'}
                    continue
                try:
                    previous = store.root / (ARTIFACTS[model] + '.learning-previous')
                    current = store.root / ARTIFACTS[model]
                    result = adapter(train, test)
                    if previous.exists() and hasattr(adapter, 'verify'):
                        old_score = adapter.verify(previous, test)
                        regressed, _ = gate(result['before'], old_score)
                        if regressed:
                            atomic_copy(previous, current)
                            details['models'][model] = {'status': 'rolled_back', 'before_accuracy': sum(result['before']) / len(result['before']), 'after_accuracy': sum(old_score) / len(old_score), 'reason': 'Current model regressed against its predecessor on later reserved truth.'}
                            continue
                    adopt, reason = gate(result['before'], result['after'])
                    positives = result.get('positive_indices', [])
                    if positives and sum(result['after'][i] for i in positives) < sum(result['before'][i] for i in positives):
                        adopt, reason = False, 'Rejected: fewer held-out dishes/people recognized.'
                    result['reason'] = reason
                    result['before_accuracy'] = sum(result['before']) / len(result['before']) if result['before'] else None
                    result['after_accuracy'] = sum(result['after']) / len(result['after']) if result['after'] else None
                    result['status'] = 'rejected'
                    if adopt and not busy():
                        # An undo/conflict entered during training invalidates the candidate.
                        with store.lock:
                            fresh = {(r['id'], r['label_id']) for r in learning.truth(store)}
                            if any((r['id'], r['label_id']) not in fresh for r in train + test):
                                raise ValueError('Teaching truth changed during training; candidate discarded.')
                            target, previous, absent = _promote(store, model, Path(result['candidate']))
                        # Read back/evaluate the installed artifact. Regression immediately restores it.
                        try:
                            readback = adapter.verify(target, test)
                            if readback != result['after']:
                                raise ValueError('Installed model differs from its evaluated candidate.')
                        except Exception:
                            if previous.exists():
                                atomic_copy(previous, target)
                            elif absent.exists():
                                target.unlink(missing_ok=True)
                            raise
                        result['status'] = 'adopted'
                        result['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
                    details['models'][model] = result
                except Exception as exc:
                    details['models'][model] = {'status': 'failed', 'reason': str(exc)}
            states = {r['status'] for r in details['models'].values()}
            status = 'adopted' if 'adopted' in states else 'rolled_back' if 'rolled_back' in states else 'rejected'
        with store.lock, store.conn:
            store.conn.execute('UPDATE learning_runs SET finished_at=?,status=?,details=? WHERE id=?',
                               (stamp(), status, json.dumps(details), run_id))
        return {'id': run_id, 'status': status, 'details': details}


class Candidate:
    def __init__(self, store, folder, model):
        self.store, self.folder, self.model = store, folder, model
        self.test_rows = []

    def __call__(self, train, test):
        if self.model == 'detector':
            from .learning_nightly import camera_active
            if camera_active():
                raise ValueError('Detector training needs a camera-off memory window; live models remain loaded.')
        if len({r['evidence_id'] for r in test}) < 20:
            raise ValueError('Needs 20 independent reserved captures before spending a night on training.')
        root = self.folder / self.model
        root.mkdir(mode=0o700)
        staged = Store(root)
        try:
            # Copy assets only, never a live database with hidden or held-out labels.
            (root / 'models').mkdir(exist_ok=True)
            for asset in (self.store.root / 'models').glob('*'):
                if not asset.is_file():
                    continue
                target = root / 'models' / asset.name
                if asset.suffix == '.json':
                    shutil.copyfile(asset, target)
                elif self.model == 'detector':
                    # Base weights are immutable inputs; trainers replace candidate targets atomically.
                    target.symlink_to(asset.resolve())
            if (self.store.root / IDENTITY_WEIGHTS_FILE).exists():
                shutil.copyfile(self.store.root / IDENTITY_WEIGHTS_FILE, root / IDENTITY_WEIGHTS_FILE)
            for resident in self.store.residents():
                with staged.conn:
                    staged.conn.execute('INSERT INTO residents(id,name,color,active,created_at) VALUES(?,?,?,?,?)',
                                        tuple(resident[k] for k in ('id', 'name', 'color', 'active', 'created_at')))
            if self.model == 'probe':
                from .verifier import refit_kitchen
                seen = set()
                for row in train:
                    if row['obligation_id'] in seen:
                        continue
                    sample = self._probe(row)
                    if sample:
                        seen.add(row['obligation_id'])
                        staged.conn.execute('INSERT INTO dish_samples(created_at,embedding,label) VALUES(?,?,?)', sample)
                staged.conn.commit()
                refit_kitchen(root, staged)
            elif self.model == 'identity':
                # Store's identity fitting needs actual cue captures, never a name alone.
                from .identity_fit import refit
                ids = {r['obligation_id']: r for r in train
                       if r['task'] == 'identity' and r['answer'] in ('me', 'name')}
                with staged.conn, self.store.lock:
                    for oid, teaching in ids.items():
                        resident = teaching['value']['resident_id']
                        for table in ('obligations', 'person_samples'):
                            original = self.store.conn.execute('SELECT * FROM obligations WHERE id=?', (oid,)).fetchall() if table == 'obligations' else self.store.conn.execute('SELECT * FROM person_samples WHERE obligation_id=? AND evidence_id=?', (oid, teaching['evidence_id'])).fetchall()
                            for source in original:
                                values = dict(source)
                                if table == 'person_samples':
                                    values['resident_id'], values['evidence_id'] = resident, None
                                columns = ','.join('"' + k + '"' for k in values)
                                staged.conn.execute(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in values)})', tuple(values.values()))
                refit(staged)
            else:
                from PIL import Image

                from .dataset import Dataset
                from .model_setup import train_detector
                from .vision import KINDS as detector_kinds
                data = Dataset(root, staged)
                for row in train:
                    value = row['value']
                    if row['task'] == 'frame':
                        evidence = self.store.evidence(row['evidence_id'])
                        fid = data.save((self.store.root / 'media' / evidence['filename']).read_bytes(), {}, 'Complete operator frame')
                        data.update(fid, [{**b, 'kind':'spoon' if b['kind']=='utensil' else b['kind']} for b in value], 'labeled')
                        continue
                    kind = value['kind'] if row['task'] == 'missed' else value if row['task'] == 'kind' else None
                    if kind == 'utensil':
                        kind = 'spoon'
                    if kind not in detector_kinds and kind != 'not_dish':
                        continue
                    box = value['box'] if row['task'] == 'missed' else json.loads(row['box'] or 'null')
                    if not box:
                        continue
                    evidence = self.store.evidence(row['evidence_id'])
                    with Image.open(self.store.root / 'media' / evidence['filename']) as im:
                        x, y, w, h = box
                        crop = im.crop((x * im.width, y * im.height, (x + w) * im.width, (y + h) * im.height))
                        # Only this crop is exhaustively labelled; unknown surroundings never become negatives.
                        import io
                        out = io.BytesIO()
                        crop.convert('RGB').save(out, 'JPEG')
                    fid = data.save(out.getvalue(), {}, 'Independent consensus crop')
                    data.update(fid, [] if kind == 'not_dish' else [{'kind': kind, 'box': [0, 0, 1, 1]}], 'labeled')
                trained = train_detector(root, data.export(), epochs=40)
                atomic_copy(Path(trained['model']), root / ARTIFACTS['detector'])
            candidate = root / ARTIFACTS[self.model]
            if not candidate.exists():
                raise ValueError('Trainer produced no candidate; more independent examples needed.')
            self.test_rows = test
            current = self.store.root / ARTIFACTS[self.model]
            before, after = self.verify(current, test), self.verify(candidate, test)
            return {'candidate': str(candidate), 'before': before, 'after': after, 'positive_indices': self.positive_indices}
        finally:
            staged.close()

    def _probe(self, row):
        if row['task'] not in ('present', 'kind'):
            return None
        with self.store.lock:
            sample = self.store.conn.execute('SELECT created_at,embedding FROM dish_samples WHERE obligation_id=? AND abs(julianday(created_at)-julianday(?))<1.0/1440 ORDER BY abs(julianday(created_at)-julianday(?)) LIMIT 1', (row['obligation_id'], row['captured_at'], row['captured_at'])).fetchone()
        if not sample:
            return None
        return (sample['created_at'], sample['embedding'], int(row['value'] not in ('no', 'not_dish')))

    def verify(self, path, rows):
        self.positive_indices = []
        if self.model == 'probe':
            import numpy as np

            from .verifier import probability
            probe = json.loads(path.read_text()) if path.exists() else None
            scores, seen = [], set()
            for row in rows:
                if row['obligation_id'] in seen:
                    continue
                sample = self._probe(row)
                if sample:
                    seen.add(row['obligation_id'])
                    expected = sample[2]
                    if expected:
                        self.positive_indices.append(len(scores))
                    predicted = bool(probability(np.array([json.loads(sample[1])]), np.array(probe['weights']), probe['bias'])[0] >= probe['threshold']) if probe else True
                    scores.append(int(predicted == expected))
            return scores
        if self.model == 'identity':
            from .identity_fit import accuracy
            # Score with a training-only gallery. Later held-out labels never enter cue_features.
            root = self.folder / 'identity'
            staged = Store(root)
            try:
                weights = json.loads(path.read_text()).get('weights', {}) if path.exists() else {}
                scored = []
                for row in rows:
                    if row['task'] != 'identity' or row['answer'] not in ('me', 'name'):
                        continue
                    with self.store.lock:
                        samples = self.store.conn.execute('SELECT * FROM person_samples WHERE obligation_id=? AND evidence_id=? ORDER BY id DESC', (row['obligation_id'], row['evidence_id'])).fetchall()
                    capture = ({s['cue']: json.loads(s['embedding']) for s in samples}, samples[0]['context'], parse_stamp(samples[0]['created_at'])) if samples else None
                    if capture:
                        cues, context, at = capture
                        features = {r: f for r, f, _ in staged.cue_features(cues, at=at, context=context)}
                        self.positive_indices.append(len(scored))
                        scored.append(accuracy(weights, [(row['value']['resident_id'], features)]))
                return scored
            finally:
                staged.close()
        from .vision import LocalVision, box_iou
        vision = LocalVision(self.store.root, dish_model_path=path if path.exists() else None)
        scores = []
        for row in rows:
            if row['task'] not in ('kind', 'missed', 'frame'):
                continue
            evidence = self.store.evidence(row['evidence_id'])
            jpeg = (self.store.root / 'media' / evidence['filename']).read_bytes()
            analysis = vision.analyze(jpeg)
            if row['task'] == 'frame':
                from .dataset import frame_scores
                score = frame_scores([{'id': str(row['id']), 'boxes': row['value'], 'predicted': analysis.get('detections', [])}])
                self.positive_indices.append(len(scores))
                scores.append(score['f1'] or 0)
                continue
            box = row['value']['box'] if row['task'] == 'missed' else json.loads(row['box'] or 'null')
            if not box:
                continue
            kind = row['value']['kind'] if row['task'] == 'missed' else row['value']
            matches = [d for d in analysis.get('detections', []) if box_iou(box, d['box']) >= .5]
            if kind != 'not_dish':
                self.positive_indices.append(len(scores))
            scores.append(int(not matches) if kind == 'not_dish' else int(any(d['kind'] == kind for d in matches)))
        return scores
