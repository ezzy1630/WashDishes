"""A conservative review hint on tight CLIP crops; never an admission or scoring gate."""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from .remembered import EMBEDDING_SIZE, saved_embedding, similarity


@lru_cache(maxsize=4)
def _read(data: bytes) -> dict | None:
    # Keyed on content: an in-place rewrite can keep the same size and mtime tick.
    try:
        value = json.loads(data)
        weights, bias = value['weights'], value['bias']
        low, high = value['threshold'], value['retain_threshold']
        if (len(weights) != EMBEDDING_SIZE
                or any(type(w) not in (int, float) or not math.isfinite(w) for w in weights)
                or type(bias) not in (int, float) or not math.isfinite(bias)
                or type(low) not in (int, float) or type(high) not in (int, float)
                or not 0 <= low < high <= 1):
            return None
        return {'weights': weights, 'bias': bias, 'threshold': low, 'retain_threshold': high}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def probe(root: Path) -> dict | None:
    """Only an explicitly adopted operator model is active; otherwise abstain.

    No image/model inference, downloads or writes happen on the house API path.
    The bundled calibration candidate increased review burden and is not adopted.
    """
    path = root / 'models' / 'dish-review-probe.json'
    try:
        return _read(path.read_bytes())
    except OSError:
        return None


def signals(embedding: str | None, model: dict | None) -> dict:
    result = {'non_dish': False, 'dish_likely': False}
    if not model or not embedding:
        return result
    try:
        vector = saved_embedding(embedding)
    except (ValueError, TypeError):
        return result
    logit = similarity(vector, model['weights']) + model['bias']
    p = 1 / (1 + math.exp(-max(-700, min(700, logit))))
    return {'non_dish': p < model['threshold'], 'dish_likely': p >= model['retain_threshold']}
