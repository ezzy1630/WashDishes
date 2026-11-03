"""Review priority, never responsibility or scoring. Uses retained metadata/features only."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from .remembered import EMBEDDING_SIZE, SIMILARITY_THRESHOLD, similarity, unit_embedding


def rate(capture: dict, *, has_filmstrip: bool, has_photo: bool, dish_confident: bool,
         remembered_near_match: bool, non_dish: bool = False, dish_likely: bool = False) -> dict:
    """Pure visit rating. One useful signal earns fair; strong needs all safeguards.

    person_boxes in a saved filmstrip are already filtered to people in reach by
    the filmstrip worker. A person here is context, never proof of responsibility.
    """
    arrival = capture.get('window', {}).get('arrival_recorded') is True
    person = any(frame.get('person_boxes') for frame in capture.get('frames', []))
    if non_dish:
        return {'evidence': 'weak', 'reason': 'Looks like a household object; check Not a dish'}
    if has_filmstrip and arrival and person and dish_confident and not remembered_near_match:
        return {'evidence': 'strong', 'reason': None}
    if (has_filmstrip and (arrival or person)) or ((has_filmstrip or has_photo) and (dish_confident or dish_likely)):
        return {'evidence': 'fair', 'reason': None}
    reasons = []
    if not has_filmstrip and arrival:
        reasons.append('Arrival photos unavailable')
    elif not arrival:
        reasons.append('Arrival not recorded')
    if has_filmstrip and not person:
        reasons.append('No one in the photos')
    return {'evidence': 'weak', 'reason': ' · '.join(reasons)}


@lru_cache(maxsize=4)
def _probe(path: Path, mtime: int, size: int) -> dict | None:
    try:
        value = json.loads(path.read_text())
        weights, bias = value['weights'], value['bias']
        threshold = value.get('proof_threshold', value['threshold'])
        if (len(weights) != EMBEDDING_SIZE or any(type(w) not in (int, float) or not math.isfinite(w) for w in weights)
                or type(bias) not in (int, float) or not math.isfinite(bias)
                or type(threshold) not in (int, float) or not 0 <= threshold <= 1):
            return None
        return {'weights': weights, 'bias': bias, 'threshold': threshold}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def kitchen_probe(root: Path) -> dict | None:
    """Read the adopted probe without importing/loading image inference libraries."""
    path = root / 'models' / 'dish-probe-kitchen.json'
    try:
        stat = path.stat()
        return _probe(path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def signals(embedding: str | None, probe: dict | None, memories: list[dict], area: dict | None) -> dict:
    """Pure scoring of saved features with the existing probe and recurrence cutoff."""
    try:
        vector = unit_embedding(json.loads(embedding)) if embedding else None
    except (ValueError, TypeError):
        vector = None
    if vector is None:
        return {'dish_confident': False, 'remembered_near_match': False}
    # sigmoid(logit) >= threshold, without overflow or a numpy/model dependency.
    confident = False
    if probe:
        logit = similarity(vector, probe['weights']) + probe['bias']
        probability = 1 / (1 + math.exp(-max(-700, min(700, logit))))
        confident = probability >= probe['threshold']
    near = bool(area and area['role'] in ('dirty', 'sink') and any(
        m['area_id'] == area['id'] and m['area_role'] == area['role']
        and similarity(vector, m['embedding']) >= SIMILARITY_THRESHOLD for m in memories))
    return {'dish_confident': confident, 'remembered_near_match': near}
