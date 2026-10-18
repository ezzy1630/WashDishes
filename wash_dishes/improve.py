"""Retrain the kitchen dish detector and switch to it only when it does better on recorded truth.

Run weekly with live analysis stopped (training does not fit beside it on the Orin Nano). The
candidate and the detector in use both replay the same hand-labeled footage; the candidate replaces
it only if it finds at least as many left-out dishes and scores a higher F1, which with equal or
better recall means fewer false alarms. The replaced model is kept for one-step rollback.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Callable

from .store import Store, stamp

KITCHEN, PREVIOUS = "dishes-kitchen.pt", "dishes-kitchen.prev.pt"
STATE, HISTORY = "improve-state.json", "improve-history.jsonl"
# Fewer new labels than this rarely changes a model; skip the run rather than burn a night on it.
MIN_NEW_LABELS = 20


def summary(report: dict) -> dict:
    """Left-out-dish scores from an evaluation report, without the per-change lists."""
    return {k: v for k, v in report["left_out"].items() if k not in ("missed", "extra")}


def combine(reports: list[dict]) -> dict:
    """One report from several recorded periods: left-out dishes pooled, then scored once."""
    parts = [r["left_out"] for r in reports]
    truth, matched, predicted = (sum(p[k] for p in parts) for k in ("truth", "matched", "predicted"))
    precision = matched / predicted if predicted else None
    recall = matched / truth if truth else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return {"left_out": {"truth": truth, "matched": matched, "predicted": predicted, "precision": precision, "recall": recall, "f1": f1, "periods": len(parts)}}


def better(candidate: dict, current: dict) -> tuple[bool, str]:
    if (candidate["recall"] or 0) < (current["recall"] or 0):
        return False, "It found fewer of the dishes left out in the recorded footage."
    if (candidate["f1"] or 0) <= (current["f1"] or 0):
        return False, "It was not more accurate on the recorded footage."
    return True, "It found at least as many left-out dishes with fewer false alarms on the recorded footage."


def _digest(path: Path | None) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else "stock"


def _log(models: Path, entry: dict) -> None:
    with (models / HISTORY).open("a") as log:
        log.write(json.dumps(entry) + "\n")


def history(root: Path, limit: int = 12) -> list[dict]:
    path = Path(root) / "models" / HISTORY
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines[-limit:] if line.strip()][::-1]


def improve(root: Path, store: Store, labeled: int, *, train: Callable[[], Path], evaluate: Callable[[Path | None], dict], force: bool = False,
            truth_id: str = "", evaluate_stills: Callable[[Path | None], dict] | None = None) -> dict:
    """train() returns a candidate model path; evaluate(path or None for the model in use) returns a report.

    truth_id names the recorded truth used; the cached score of the model in use is reused only for the same truth.
    """
    models = Path(root) / "models"
    state_path = models / STATE
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    entry: dict = {"at": stamp(), "labeled_frames": labeled, "promoted": False}
    new = labeled - state.get("labeled_frames", 0)
    if not force and new < MIN_NEW_LABELS:
        entry["reason"] = f"Skipped: {new} newly labeled frames since the last run; needs {MIN_NEW_LABELS}."
        _log(models, entry)
        return entry
    current = models / KITCHEN if (models / KITCHEN).is_file() else None
    digest = _digest(current)
    baseline = state.get("baseline") if state.get("baseline_model") == digest and state.get("baseline_truth", "") == truth_id else None
    if baseline is None:
        baseline = summary(evaluate(None))
    candidate = Path(train())
    scored = summary(evaluate(candidate))
    promoted, reason = better(scored, baseline)
    if evaluate_stills is not None:
        before, after = evaluate_stills(None), evaluate_stills(candidate)
        entry["reserved_stills"] = {"current": before, "candidate": after}
        if before["frames"] or after["frames"]:
            same = before["snapshot"] == after["snapshot"] and before["frames"] == after["frames"]
            held_up = same and after["missed"] <= before["missed"] and after["extra"] <= before["extra"]
            if not held_up:
                promoted, reason = False, "Not adopted: the reserved picture check changed or missed more dishes / added more false outlines."
    entry |= {"current": baseline, "candidate": scored, "promoted": promoted, "reason": reason}
    if promoted:
        if current:
            shutil.copyfile(current, models / PREVIOUS)
        temporary = models / f".{KITCHEN}.tmp"
        shutil.copyfile(candidate, temporary)
        os.replace(temporary, models / KITCHEN)
        baseline, digest = scored, _digest(models / KITCHEN)
    state |= {"labeled_frames": labeled, "baseline": baseline, "baseline_model": digest, "baseline_truth": truth_id}
    state_path.write_text(json.dumps(state, indent=1))
    _log(models, entry)
    store.event("detector_retrained", ("New dish detector adopted. " if promoted else "Retrained dish detector not adopted. ") + reason)
    return entry


def rollback(root: Path, store: Store) -> None:
    models = Path(root) / "models"
    if not (models / PREVIOUS).is_file():
        raise ValueError("There is no previous kitchen detector to go back to.")
    os.replace(models / PREVIOUS, models / KITCHEN)
    (models / STATE).unlink(missing_ok=True)
    _log(models, {"at": stamp(), "promoted": True, "reason": "Rolled back to the previous kitchen detector by hand."})
    store.event("detector_rolled_back", "Kitchen dish detector rolled back to the previous version")
