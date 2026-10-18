"""Learn how much each identity cue should count, from the names people gave sightings.

Each confirmed camera sighting is scored again as if it were new (its own samples left out), giving
per-cue evidence for every housemate. Cue weights are fitted to pick the confirmed person, pulled
towards 1.0 so a little data cannot swing them far. New weights are kept only when they pick the
right person more often than the weights in use, measured on sightings left out of the fit.
"""
from __future__ import annotations

import json
import math
import os

from .store import IDENTITY_WEIGHTS_FILE, Store, stamp

LAST_RUN_FILE = "identity-fit-last.json"
MIN_CASES = 40
FOLDS = 5
PRIOR = 0.05
STEPS, RATE, MAX_WEIGHT = 400, 0.05, 4.0

Case = tuple[int, dict[int, dict[str, float]]]


def cases(store: Store) -> list[Case]:
    """(named person, per-cue evidence by housemate) for every sighting a person named.

    Only people label samples: confirming an owner names them, and so does answering "who is this?"
    for a sighting whose dish was never confirmed. The camera naming an owner itself never does.
    """
    with store.lock:
        rows = store.conn.execute("""SELECT DISTINCT s.obligation_id id, s.resident_id FROM person_samples s JOIN obligations o ON o.id=s.obligation_id
            WHERE s.resident_id IS NOT NULL AND o.source='vision' AND o.lifecycle NOT IN ('dismissed','disputed') ORDER BY id""").fetchall()
    result = []
    for row in rows:
        cues, context, at = store.sighting_cues(row["id"])
        scored = {resident: features for resident, features, _ in store.cue_features(cues, at=at, context=context, exclude_obligation=row["id"])}
        if row["resident_id"] in scored and len(scored) > 1:
            result.append((row["resident_id"], scored))
    return result


def score(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(weights.get(cue, 1.0) * value for cue, value in features.items())


def accuracy(weights: dict[str, float], data: list[Case]) -> float | None:
    if not data:
        return None
    return sum(max(scored, key=lambda r: score(weights, scored[r])) == truth for truth, scored in data) / len(data)


def fit(data: list[Case]) -> dict[str, float]:
    """Softmax over housemates, maximising the confirmed person's share, by gradient descent."""
    cues = sorted({cue for _, scored in data for features in scored.values() for cue in features})
    weights = {cue: 1.0 for cue in cues}
    for _ in range(STEPS):
        gradient = {cue: 2 * PRIOR * (weights[cue] - 1.0) for cue in cues}
        for truth, scored in data:
            values = {r: score(weights, f) for r, f in scored.items()}
            top = max(values.values())
            total = sum(math.exp(v - top) for v in values.values())
            for resident, features in scored.items():
                share = math.exp(values[resident] - top) / total - (resident == truth)
                for cue, value in features.items():
                    gradient[cue] += share * value / len(data)
        weights = {cue: min(MAX_WEIGHT, max(0.0, weights[cue] - RATE * gradient[cue])) for cue in cues}
    return {cue: round(value, 3) for cue, value in weights.items()}


def cross_validated(data: list[Case], current: dict[str, float]) -> tuple[float, float]:
    """Accuracy on held-out sightings: weights fitted without them, and the current weights."""
    fitted_right = current_right = 0
    for fold in range(FOLDS):
        test = [case for index, case in enumerate(data) if index % FOLDS == fold]
        train = [case for index, case in enumerate(data) if index % FOLDS != fold]
        weights = fit(train)
        fitted_right += sum(max(s, key=lambda r: score(weights, s[r])) == t for t, s in test)
        current_right += sum(max(s, key=lambda r: score(current, s[r])) == t for t, s in test)
    return fitted_right / len(data), current_right / len(data)


def _write(path, report: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=1))
    temporary.chmod(0o600)
    os.replace(temporary, path)


def refit(store: Store) -> dict:
    """Fit, compare on held-out sightings, and adopt the new weights only if they do better.

    Every attempt is written to LAST_RUN_FILE for the dashboard, adopted or not.
    """
    data = cases(store)
    current = store.identity_weights()
    report = {"at": stamp(), "cases": len(data), "adopted": False}
    if data:
        # How often the top guess names the right person today, each sighting scored as if new.
        report["accuracy_now"] = round(accuracy(current, data), 3)
    if len(data) < MIN_CASES:
        report["reason"] = f"Needs {MIN_CASES} named sightings with appearance cues; has {len(data)}."
    else:
        fitted_accuracy, current_accuracy = cross_validated(data, current)
        report |= {"held_out_accuracy": round(fitted_accuracy, 3), "current_accuracy": round(current_accuracy, 3)}
        if fitted_accuracy <= current_accuracy:
            report["reason"] = "The refitted weights were not more accurate on held-out sightings."
        else:
            report |= {"adopted": True, "weights": fit(data)}
            _write(store.root / IDENTITY_WEIGHTS_FILE, report)
            store.event("identity_weights_updated", f"Identity cue weights relearned from {len(data)} confirmed sightings: "
                        f"right {fitted_accuracy:.0%} of held-out sightings, was {current_accuracy:.0%}")
    _write(store.root / LAST_RUN_FILE, report)
    return report


def add_faces(store: Store, face_of, clip_face_of=None) -> dict:
    """Add a face cue to saved sightings that have none, from their saved person crop, then merge in
    the face from each sighting's put-down clip.

    face_of(path) and clip_face_of(path) return an embedding or None. The face joins the sighting's
    capture (same time and area) and its name, so earlier answers teach faces too. Sightings already
    holding a face are skipped, and a clip already merged is not merged again.
    """
    with store.lock:
        rows = store.conn.execute("""SELECT s.obligation_id, s.created_at, s.context, s.evidence_id, s.resident_id, s.hint_resident_id, e.filename
            FROM person_samples s JOIN evidence e ON e.id=s.evidence_id WHERE s.cue='outfit' AND e.expired=0
            AND NOT EXISTS (SELECT 1 FROM person_samples f WHERE f.obligation_id=s.obligation_id AND f.created_at=s.created_at AND f.cue='face')""").fetchall()
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
    clips = 0
    if clip_face_of is not None:
        from .inventory import PUT_DOWN_CLIP
        with store.lock:
            videos = store.conn.execute("""SELECT e.obligation_id, e.id, e.filename FROM evidence e WHERE e.kind='clip' AND e.label=? AND e.expired=0
                AND EXISTS (SELECT 1 FROM person_samples s WHERE s.obligation_id=e.obligation_id)
                AND NOT EXISTS (SELECT 1 FROM person_samples f WHERE f.obligation_id=e.obligation_id AND f.cue='face' AND f.evidence_id=e.id)""", (PUT_DOWN_CLIP,)).fetchall()
        for video in videos:
            embedding = clip_face_of(store.root / "media" / video["filename"])
            clips += embedding is not None and store.merge_sighting_face(video["obligation_id"], embedding, video["id"])
    # Sightings still waiting for a name are re-scored with the new faces.
    refreshed = store.refresh_identity_suggestions()
    return {"sightings": len(rows), "faces_added": added, "clip_faces_merged": clips, "suggestions_changed": refreshed}
