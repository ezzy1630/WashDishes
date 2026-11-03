#!/usr/bin/env python3
"""Calibrate cached verifier features without training or loading another model.

The private manifest has key, instance (episode, shared across replay/source views),
group (visually established physical object), label, memory_area and proposal_area.
Vectors are the existing 512-dimensional contextual CLIP features in manifest order.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

NEGATIVE = {'not_a_dish', 'clean_or_drying', 'clean_return'}
POSITIVE = {'real_dirty_dish_left_out', 'older_dirty_target', 'audit_dirty_target'}


def calibrate(rows: list[dict], vectors: np.ndarray) -> dict:
    if vectors.shape != (len(rows), 512) or not np.isfinite(vectors).all():
        raise ValueError('Expected one finite CLIP vector per manifest row.')
    vectors = vectors.astype(float)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if (norms == 0).any():
        raise ValueError('Zero feature vector.')
    vectors = vectors / norms
    scores = vectors @ vectors.T
    negatives = [i for i, r in enumerate(rows) if r['label'] in NEGATIVE]
    positives = [i for i, r in enumerate(rows) if r['label'] in POSITIVE]

    def compatible(i, j):
        return rows[i]['memory_area'] is not None and rows[i]['memory_area'] == rows[j]['proposal_area']

    unsafe = [(float(scores[i, j]), rows[i]['key'], rows[j]['key']) for i in negatives for j in negatives + positives
              if compatible(i, j) and (j in positives or rows[i]['group'] != rows[j]['group'])]
    if not unsafe or not positives:
        raise ValueError('Calibration needs compatible negative and dirty controls.')
    bound = max(unsafe)
    pairs = [(i, j) for i in negatives for j in negatives
             if compatible(i, j) and rows[i]['group'] == rows[j]['group'] and rows[i]['instance'] != rows[j]['instance']]
    safe = [float(scores[i, j]) for i, j in pairs if scores[i, j] > bound[0]]
    if not safe:
        raise ValueError('No useful threshold with zero control vetoes.')
    threshold = math.ceil((bound[0] + min(safe)) / 2 * 1e6) / 1e6
    targets = sorted({j for _, j in pairs})
    coverage = [{'key': rows[j]['key'], 'group': rows[j]['group'],
                 'best': max(float(scores[i, j]) for i, k in pairs if k == j),
                 'vetoed': any(scores[i, j] >= threshold for i, k in pairs if k == j)} for j in targets]
    return {'threshold': threshold, 'unsafe_max': bound, 'next_same_object': min(safe),
            'negative_images': len(negatives), 'positive_images': len(positives),
            'real_dish_vetoes': sum(any(compatible(i, j) and scores[i, j] >= threshold for i in negatives) for j in positives),
            'loo_candidate_images': len(targets), 'loo_vetoed_images': sum(c['vetoed'] for c in coverage),
            'loo_pairs': len(pairs), 'loo_vetoed_pairs': sum(bool(scores[i, j] >= threshold) for i, j in pairs),
            'coverage': coverage}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--examples', type=Path, required=True)
    parser.add_argument('--vectors', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = calibrate(json.loads(args.examples.read_text()), np.load(args.vectors))
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
