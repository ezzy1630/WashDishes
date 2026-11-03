"""Near-exact recurrence matching on the verifier's existing contextual CLIP features."""
from __future__ import annotations

import math
from collections.abc import Sequence

# Measured on Lane D's labelled photos and Lane G's replay crops. Midpoint between
# the largest incompatible/dirty match (.940419) and next same-object match (.941272).
# See docs/remembered-objects.md for coverage and the bounded calibration contract.
SIMILARITY_THRESHOLD = 0.940846
MAX_EXEMPLARS = 64
EMBEDDING_SIZE = 512


def unit_embedding(values: Sequence[float]) -> list[float]:
    if len(values) != EMBEDDING_SIZE or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Expected finite CLIP ViT-B/32 features.')
    length = math.sqrt(sum(v * v for v in values))
    if length == 0:
        raise ValueError('Expected nonzero CLIP features.')
    return [v / length for v in values]


def similarity(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
