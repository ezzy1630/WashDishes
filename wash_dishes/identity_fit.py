"""Human-only identity learning, day-held-out adoption and calibrated house guesses."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import timedelta
from zoneinfo import ZoneInfo

from .store import (
    AUTO_MIN_ACCURACY,
    AUTO_MIN_CHECKED,
    IDENTITY_WEIGHTS_FILE,
    Store,
    identity_prediction,
    stamp,
)

LAST_RUN_FILE = "identity-fit-last.json"
MIN_CASES = 40
PRIOR = 0.05
STEPS, RATE, MAX_WEIGHT = 400, 0.05, 4.0
# Fixed score bands, never probabilities. Calibration includes abstentions in coverage.
DISPLAY_THRESHOLDS = tuple(range(13))
Case = tuple[int, dict[int, dict[str, float]]]


def observations(store: Store, *, independent: bool = False) -> list[dict]:
    """Only human-labelled active sightings, never predictions or disputed/dismissed labels.

    A resident_id in person_samples is written only by human review. Legacy imported human
    labels remain usable even when they predate the immutable prediction events.
    """
    tz = ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))
    with store.lock:
        rows = store.conn.execute("""SELECT s.obligation_id id, min(s.resident_id) truth FROM person_samples s
            JOIN obligations o ON o.id=s.obligation_id JOIN residents r ON r.id=s.resident_id
            WHERE r.active=1 AND o.lifecycle NOT IN ('dismissed','disputed') AND o.attribution IS NOT 'camera'
            GROUP BY s.obligation_id HAVING count(DISTINCT s.resident_id)=1 ORDER BY s.obligation_id""").fetchall()
        result = []
        for row in rows:
            capture = store.sighting_cues(row['id'])
            if not capture:
                continue
            cues, context, at = capture
            result.append(dict(row) | {'cues': cues, 'context': context, 'at': at,
                'day': at.astimezone(tz).date().isoformat(),
                'fingerprint': hashlib.sha256(json.dumps(cues, sort_keys=True).encode()).hexdigest()})
    if not independent:
        return result
    groups = []
    for item in sorted(result, key=lambda r: (r['at'], r['id'])):
        group = next((g for g in reversed(groups) if item['fingerprint'] in g['fingerprints']
                      or (item['truth'] == g['truth'] and item['at'] - g['last'] < timedelta(minutes=30))), None)
        if group:
            group['fingerprints'].add(item['fingerprint'])
            group['last'] = item['at']
            group['conflict'] |= item['truth'] != group['truth']
        else:
            groups.append(item | {'last': item['at'], 'fingerprints': {item['fingerprint']}, 'conflict': False})
    return [g for g in groups if not g['conflict']]


def cases(store: Store) -> list[Case]:
    all_rows = observations(store)
    return [(r['truth'], {who: f for who, f, _ in store.cue_features(r['cues'], at=r['at'],
            context=r['context'], exclude_day=r['day'],
            exclude_obligations=[x['id'] for x in all_rows if x['fingerprint'] == r['fingerprint']])}) for r in observations(store, independent=True)]


def score(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(weights.get(cue, 1.0) * value for cue, value in features.items())


def accuracy(weights: dict[str, float], data: list[Case]) -> float | None:
    if not data:
        return None
    return sum(max(scored, key=lambda r: score(weights, scored[r]), default=None) == truth for truth, scored in data) / len(data)


def fit(data: list[Case]) -> dict[str, float]:
    """Softmax over housemates, maximising the confirmed person's share, by gradient descent."""
    cues = sorted({cue for _, scored in data for features in scored.values() for cue in features})
    weights = {cue: 1.0 for cue in cues}
    for _ in range(STEPS):
        gradient = {cue: 2 * PRIOR * (weights[cue] - 1.0) for cue in cues}
        for truth, scored in data:
            if not scored or truth not in scored:
                continue
            values = {r: score(weights, f) for r, f in scored.items()}
            top = max(values.values())
            total = sum(math.exp(v - top) for v in values.values())
            for resident, features in scored.items():
                share = math.exp(values[resident] - top) / total - (resident == truth)
                for cue, value in features.items():
                    gradient[cue] += share * value / len(data)
        weights = {cue: min(MAX_WEIGHT, max(0.0, weights[cue] - RATE * gradient[cue])) for cue in cues}
    return {cue: round(value, 3) for cue, value in weights.items()}


def _write(path, report: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=1))
    temporary.chmod(0o600)
    os.replace(temporary, path)


def summary(predictions: list[dict], protocol: str) -> dict:
    guessed = [r for r in predictions if r['guess'] is not None]
    correct = sum(r['truth'] == r['guess'] for r in guessed)
    return {'cases': len(predictions), 'guesses': len(guessed), 'correct': correct,
            'accuracy': correct / len(predictions) if predictions else None,
            'coverage': len(guessed) / len(predictions) if predictions else None,
            'protocol': protocol, 'abstentions': len(predictions) - len(guessed),
            'precision': correct / len(guessed) if guessed else None, 'rows': predictions}


def evaluate(store: Store, *, past_only: bool = False, leave_one_day_out: bool = False,
             enrolled_faces: dict[int, list[list[float]]] | None = None) -> dict:
    """Capture-time replay or day-excluded gallery; all human cases include abstentions."""
    if past_only and leave_one_day_out:
        raise ValueError('Choose capture-time or leave-one-day-out evaluation.')
    predictions = []
    data = observations(store)
    for r in data:
        guess, value, _, _ = store.suggest_identity(r['cues'], at=r['at'], context=r['context'],
            exclude_obligation=r['id'], before=r['at'] if past_only else None,
            exclude_day=r['day'] if leave_one_day_out else None, enrolled_faces=enrolled_faces,
            exclude_obligations=[x['id'] for x in data if x['fingerprint'] == r['fingerprint']])
        predictions.append({'id': r['id'], 'truth': r['truth'], 'guess': guess, 'score': value})
    return summary(predictions, 'leave_one_local_day_out' if leave_one_day_out else 'past_only' if past_only else 'leave_one_sighting_out')


def day_excluded(store: Store, data: list[dict], current: dict[str, float]) -> tuple[dict, dict]:
    """Fit weights without the test day AND remove its labels from training galleries.

    Each training observation also excludes its own day. Repeated dishes/crops cannot increase
    the evaluation count. Both models use the real discriminating-cue/abstention policy.
    """
    candidate_rows, current_rows = [], []
    all_observations = observations(store)
    for day in sorted({r['day'] for r in data}):
        test_fingerprints = {r['fingerprint'] for r in all_observations if r['day'] == day}
        test_ids = [r['id'] for r in all_observations if r['day'] == day or r['fingerprint'] in test_fingerprints]
        train = [(r['truth'], {who: features for who, features, _ in store.cue_features(
            r['cues'], at=r['at'], context=r['context'], exclude_day=r['day'],
            exclude_obligations=test_ids + [x['id'] for x in all_observations if x['fingerprint'] == r['fingerprint']])})
            for r in data if r['day'] != day and r['id'] not in test_ids]
        weights = fit(train)
        for r in (r for r in data if r['day'] == day):
            features = store.cue_features(r['cues'], at=r['at'], context=r['context'], exclude_day=day,
                exclude_obligations=[x['id'] for x in all_observations if x['fingerprint'] == r['fingerprint']])
            for model, rows in ((weights, candidate_rows), (current, current_rows)):
                guess, value, _, _ = identity_prediction(features, model)
                rows.append({'id': r['id'], 'truth': r['truth'], 'guess': guess, 'score': value})
    return summary(candidate_rows, 'nested_leave_one_local_day_out'), summary(current_rows, 'leave_one_local_day_out')


def display_gate(evaluation: dict) -> dict:
    """Broadest fixed score band with 30 independent checks and at least 90% precision."""
    for threshold in DISPLAY_THRESHOLDS:
        rows = [r for r in evaluation['rows'] if r['guess'] is not None and r['score'] is not None and r['score'] >= threshold]
        correct = sum(r['truth'] == r['guess'] for r in rows)
        if len(rows) >= AUTO_MIN_CHECKED and correct / len(rows) >= AUTO_MIN_ACCURACY:
            return {'threshold': threshold, 'checked': len(rows), 'correct': correct,
                    'precision': correct / len(rows), 'coverage': len(rows) / evaluation['cases']}
    return {'threshold': None, 'checked': 0, 'correct': 0, 'precision': None, 'coverage': 0}


def refit(store: Store) -> dict:
    """Nightly fit-identity timer: adopt only strict day-held-out improvement, never on training fit.

    Hold the gallery lock until publication; a concurrent undo cannot calibrate retracted labels.
    The separate timer process reads a SQLite snapshot, so validate the revision again before writing.
    """
    with store.lock:
        revision = store.identity_revision()
        data = observations(store, independent=True)
        current = store.identity_weights()
        candidate, baseline = day_excluded(store, data, current)
        report = {'at': stamp(), 'cases': len(data), 'days': len({r['day'] for r in data}), 'adopted': False,
                  'held_out_accuracy': candidate['accuracy'], 'current_accuracy': baseline['accuracy']}
        measured = baseline
        if len(data) < MIN_CASES:
            report['reason'] = f'Needs {MIN_CASES} independent named sightings with appearance cues; has {len(data)}.'
        elif report['days'] < 3:
            report['reason'] = 'Needs at least three local days for day-excluded fitting and evaluation.'
        elif candidate['correct'] <= baseline['correct']:
            report['reason'] = 'The refitted weights were not more accurate on day-excluded human sightings.'
        else:
            report['weights'] = fit(cases(store))
            report['adopted'] = True
            measured = candidate
        if revision != store.identity_revision():
            return report | {'adopted': False, 'reason': 'Human labels changed during fitting; retry next night.'}
        # SQLite write lock makes revision check + publication atomic against other processes' reviews.
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            if revision != store.identity_revision():
                return report | {'adopted': False, 'reason': 'Human labels changed during fitting; retry next night.'}
            if report['adopted']:
                _write(store.root / IDENTITY_WEIGHTS_FILE, report)
                store._event(None, 'identity_weights_updated', 'Identity weights improved on day-excluded human sightings.', report)
            report['revision'] = store.identity_revision()
            report['display_gate'] = display_gate(measured)
            report['suggestions_day_out'] = {k: v for k, v in measured.items() if k != 'rows'}
            _write(store.root / LAST_RUN_FILE, report)
        return report


def add_faces(store: Store, face_of, clip_face_of=None) -> dict:
    """Rebuild from the named body crop. Preserve but never reuse unanchored clip face rows.

    clip_face_of is retained for old callers; whole-visit clips cannot establish who the face belongs to.
    """
    with store.lock:
        rows = store.conn.execute("""SELECT s.obligation_id, s.created_at, s.context, s.evidence_id, s.resident_id, s.hint_resident_id, e.filename
            FROM person_samples s JOIN evidence e ON e.id=s.evidence_id WHERE s.cue='outfit' AND e.expired=0
            AND NOT EXISTS (SELECT 1 FROM person_samples f LEFT JOIN evidence fe ON fe.id=f.evidence_id
                WHERE f.obligation_id=s.obligation_id AND f.created_at=s.created_at AND f.cue='face'
                AND coalesce(fe.kind,'')!='clip')""").fetchall()
    added = 0
    for row in rows:
        embedding = face_of(store.root / "media" / row["filename"])
        if embedding is None:
            continue
        with store.lock, store.conn:
            store.conn.execute("""INSERT INTO person_samples(obligation_id,resident_id,hint_resident_id,created_at,embedding,evidence_id,cue,context)
                VALUES(?,?,?,?,?,?,'face',?)""", (row["obligation_id"], row["resident_id"], row["hint_resident_id"], row["created_at"],
                                                   json.dumps([round(v, 5) for v in embedding]), row["evidence_id"], row["context"]))
        added += 1
    # Sightings still waiting for a name are re-scored with the new faces.
    refreshed = store.refresh_identity_suggestions()
    return {"sightings": len(rows), "faces_added": added, "clip_faces_merged": 0, "suggestions_changed": refreshed}
