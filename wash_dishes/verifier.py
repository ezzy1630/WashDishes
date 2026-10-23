"""A second look at each new dish: does the detector's box really hold a dish?

The detector's own confidence says almost nothing here: on this kitchen's labeled frames its
score could not separate real dishes from its mistakes (AUC 0.50), and neither could CLIP asked
zero-shot (0.55). A linear probe on CLIP image features, trained on this kitchen's labels,
could (0.79): kept 95% of dishes and dropped 57% of mistakes. So the probe is retrained nightly
from every label there is (hand-labeled frames, Review answers, kitchen checks) and is only
adopted when its held-out score is good enough.

Model files are prepared explicitly (`models verifier`); this module never downloads anything.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from .store import Store, stamp

CLIP_NAME = "clip-vit-b32-laion2b.safetensors"
PROBE_NAME = "dish-probe.json"
# A second probe learns only from the camera's own detections that people judged: the bottles, tubs
# and canisters this kitchen's detector keeps calling cups. The general probe passed all 176 such
# mistakes in the 2026-10-22 labels; this one, scored a day at a time on days it never saw, ranked
# a dish above a mistake 85% of the time. It only ever narrows what the general probe accepts.
KITCHEN_PROBE_NAME = "dish-probe-kitchen.json"
# Its samples are few and alike, so it needs less regularisation (day-out AUC .854 vs .833 at 3e-3).
KITCHEN_L2 = 1e-3
# The threshold keeps this share of real dishes in held-out checks; the rest of the trade is precision.
KEEP_DISHES = 0.95
# A proof picture is where a wrong dish costs most, so it must clear a stricter bar: keeping 80% of
# real dishes dropped 74% of the mistakes, against 44% at 95%.
KEEP_PROOF = 0.80
# Below these, a probe's held-out numbers are too noisy to trust over having none.
MIN_EXAMPLES = 60
MIN_AUC = 0.7
FOLDS = 5


def context_crop(image: Any, box: Sequence[float], scale: float = 2.0, minimum: float = 0.035) -> Any:
    """The dish with some of what's around it: a cup alone and a cup-shaped bottle look alike up close."""
    height, width = image.shape[:2]
    x, y, w, h = box
    cx, cy = (x + w / 2) * width, (y + h / 2) * height
    side = max(w * width, h * height, minimum * width) * scale
    left, top = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    right, bottom = int(min(width, cx + side / 2)), int(min(height, cy + side / 2))
    return image[top:bottom, left:right]


class Embedder:
    """CLIP ViT-B/32 image features, loaded lazily from the local models folder."""

    def __init__(self, root: Path):
        self.path = Path(root) / "models" / CLIP_NAME
        self.lock = threading.Lock()
        self._model = self._preprocess = self._device = None

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def _load(self) -> None:
        if self._model is not None:
            return
        import open_clip
        import torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained=str(self.path))
        model = model.eval().to(self._device)
        self._model = model.half() if self._device == "cuda" else model
        self._preprocess = preprocess

    def __call__(self, crops: list[Any]) -> np.ndarray:
        """Unit-length features, one row per BGR crop."""
        import cv2
        import torch
        from PIL import Image
        with self.lock:
            self._load()
            batch = torch.stack([self._preprocess(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))) for c in crops]).to(self._device)
            with torch.no_grad():
                features = self._model.encode_image(batch.half() if self._device == "cuda" else batch).float()
            return torch.nn.functional.normalize(features, dim=-1).cpu().numpy()


def fit_probe(X: np.ndarray, y: np.ndarray, *, l2: float = 3e-3, steps: int = 25) -> tuple[np.ndarray, float]:
    """L2-regularized logistic regression by Newton's method; small enough to solve exactly."""
    A = np.c_[X, np.ones(len(X))]
    w = np.zeros(A.shape[1])
    penalty = np.full(A.shape[1], l2)
    penalty[-1] = 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-np.clip(A @ w, -30, 30)))
        gradient = A.T @ (p - y) / len(y) + penalty * w
        hessian = (A * (p * (1 - p))[:, None]).T @ A / len(y) + np.diag(penalty + 1e-9)
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return w[:-1], float(w[-1])


def probability(X: np.ndarray, weights: np.ndarray, bias: float) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(X @ weights + bias, -30, 30)))


def auc(scores: np.ndarray, y: np.ndarray) -> float:
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    positives, negatives = int(y.sum()), int(len(y) - y.sum())
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def train(X: np.ndarray, y: np.ndarray, *, l2: float = 3e-3) -> dict | None:
    """A probe with its held-out quality, or None when the labels can't support one yet.

    Folds are contiguous in time order, so frames from the same minute don't score each other.
    """
    positives, negatives = int(y.sum()), int(len(y) - y.sum())
    if min(positives, negatives) < MIN_EXAMPLES:
        return None
    held = np.zeros(len(y))
    for fold in np.array_split(np.arange(len(y)), FOLDS):
        rest = np.setdiff1d(np.arange(len(y)), fold)
        if len(set(y[rest])) < 2:
            return None
        held[fold] = probability(X[fold], *fit_probe(X[rest], y[rest], l2=l2))
    quality = auc(held, y)
    threshold, proof_threshold = (float(np.sort(held[y == 1])[int((1 - keep) * positives)]) for keep in (KEEP_DISHES, KEEP_PROOF))
    kept, shown = held >= threshold, held >= proof_threshold
    weights, bias = fit_probe(X, y, l2=l2)
    return {"weights": [round(float(v), 6) for v in weights], "bias": round(bias, 6), "threshold": round(threshold, 6),
            "proof_threshold": round(proof_threshold, 6), "examples": {"dish": positives, "not_dish": negatives},
            "held_out": {"auc": round(quality, 4), "dishes_kept": round(float(kept[y == 1].mean()), 4),
                         "mistakes_dropped": round(float((~kept[y == 0]).mean()), 4), "precision": round(float(y[kept].mean()), 4),
                         "proof_mistakes_dropped": round(float((~shown[y == 0]).mean()), 4)}}


class DishVerifier:
    """Scores a box; None until both CLIP and an adopted probe are present."""

    def __init__(self, root: Path, embedder: Callable[[list[Any]], np.ndarray] | None = None):
        self.root = Path(root)
        self.embed = embedder or Embedder(self.root)
        self.path = self.root / "models" / PROBE_NAME
        self.kitchen_path = self.root / "models" / KITCHEN_PROBE_NAME
        self._loaded: dict[Path, tuple[float, dict | None]] = {}

    def _load(self, path: Path) -> dict | None:
        """The probe saved at path, reread only when the file changes."""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._loaded.pop(path, None)
            return None
        if path not in self._loaded or self._loaded[path][0] != mtime:
            try:
                saved = json.loads(path.read_text())
                probe = saved | {"weights": np.asarray(saved["weights"], dtype=np.float32)}
            except (OSError, ValueError, KeyError):
                probe = None
            self._loaded[path] = (mtime, probe)
        return self._loaded[path][1]

    def probe(self) -> dict | None:
        return self._load(self.path)

    def kitchen(self) -> dict | None:
        return self._load(self.kitchen_path)

    @property
    def ready(self) -> bool:
        return getattr(self.embed, "available", True) and self.probe() is not None

    def features(self, image: Any, box: Sequence[float]) -> np.ndarray:
        return self.embed([context_crop(image, box)])[0]

    def score(self, vector: np.ndarray) -> tuple[float, bool] | None:
        probe = self.probe()
        if probe is None:
            return None
        value = _value(probe, vector)
        kitchen = self.kitchen()
        return value, value >= probe["threshold"] and (kitchen is None or _value(kitchen, vector) >= kitchen["threshold"])

    def proof(self, vector: np.ndarray) -> bool | None:
        """Sure enough it is a dish to show in a proof picture; None until a kitchen probe is adopted."""
        kitchen = self.kitchen()
        return None if kitchen is None else _value(kitchen, vector) >= kitchen.get("proof_threshold", kitchen["threshold"])


def _value(probe: dict, vector: np.ndarray) -> float:
    return float(probability(vector[None, :], probe["weights"], probe["bias"])[0])


def store_hook(verifier: DishVerifier, store: Store, *, threshold: float | None = None) -> Callable[[Any, list[float]], dict | None]:
    """Inventory's verify callable: score a new box and keep its features as a future training example.

    threshold overrides the probe's own, e.g. 0.0 to score everything without dropping any (for tuning).
    """
    def verify(image: Any, box: list[float]) -> dict | None:
        try:
            if not verifier.ready:
                return None
            vector = verifier.features(image, box)
            scored = verifier.score(vector)
        except Exception:
            # Optional: a missing package or a GPU hiccup means no second look, never a lost dish.
            return None
        if scored is None:
            return None
        value, dish = scored
        if threshold is not None:
            dish = value >= threshold
        sample = store.add_dish_sample(vector.tolist(), value)
        return {"dish": dish, "score": round(value, 4), "sample": sample}
    return verify


def examples(root: Path, store: Store, detect: Callable[[bytes], tuple[Any, list[dict]]], embed: Callable[[list[Any]], np.ndarray],
             *, holdout: Sequence[tuple[datetime, datetime]] = ()) -> tuple[np.ndarray, np.ndarray]:
    """Every label there is, as (features, dish or not), in time order.

    Hand-labeled frames: the detector runs on each, and a box a person also drew is a dish, one
    nowhere near any is a mistake. Keep original false boxes the labeler removed too: a bottle
    missed on this training pass is still a useful negative for the next lighting change.
    An explicit Review answer labels just that box, even before the whole frame is annotated.
    Frames held out for evaluation, and anything inside a holdout window, stay out.
    """
    from .vision import box_iou
    holdout = _windows(root, holdout)
    rows: list[tuple[str, np.ndarray, int]] = []
    labels = Path(root) / "dataset" / "labels"
    for path in sorted(labels.glob("*.json")) if labels.exists() else []:
        try:
            item = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        at = datetime.fromisoformat(item["created_at"])
        review = item.get('review', {})
        reviewed = review.get('verdict') in ('dish', 'not_dish') and review.get('box')
        if (item.get("status") != "labeled" and not reviewed) or item.get("holdout") or any(start <= at <= end for start, end in holdout):
            continue
        image_path = Path(root) / "dataset" / "images" / f"{item['id']}.jpg"
        if not image_path.exists():
            continue
        truth = [b["box"] for b in item["boxes"] if b["kind"] != "person"]
        image, found = detect(image_path.read_bytes())
        judged = []
        if item.get('status') != 'labeled':
            # The rest of an unfinished frame is unknown, not negative training data.
            judged.append((review['box'], int(review['verdict'] == 'dish')))
        else:
            for detection in found:
                if detection['kind'] == 'person':
                    continue
                overlap = max((box_iou(detection["box"], t) for t in truth), default=0.0)
                if overlap >= 0.3 or overlap < 0.1:
                    judged.append((detection["box"], int(overlap >= 0.3)))
            for original in item.get('predicted', []):
                box = original['box']
                if (original['kind'] != 'person'
                        and max((box_iou(box, t) for t in truth), default=0.0) < .1
                        and max((box_iou(box, b) for b, _ in judged), default=0.0) < .3):
                    judged.append((box, 0))
        if judged:
            vectors = embed([context_crop(image, box) for box, _ in judged])
            rows.extend((item["created_at"], vector, label) for vector, (_, label) in zip(vectors, judged))
    for sample in store.labeled_dish_samples():
        at = datetime.fromisoformat(sample["created_at"])
        if not any(start <= at <= end for start, end in holdout):
            rows.append((sample["created_at"], np.asarray(sample["embedding"], dtype=np.float32), sample["label"]))
    rows.sort(key=lambda row: row[0])
    if not rows:
        return np.zeros((0, 512), np.float32), np.zeros(0)
    return np.stack([row[1] for row in rows]), np.array([row[2] for row in rows], dtype=float)


def _windows(root: Path, holdout: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """The given holdout windows plus the evaluation windows recorded with the dataset."""
    path = Path(root) / "dataset" / "evaluation-windows.json"
    saved = json.loads(path.read_text()) if path.exists() else []
    return [*holdout, *((datetime.fromisoformat(start), datetime.fromisoformat(end)) for start, end in saved)]


def kitchen_examples(root: Path, store: Store, *, holdout: Sequence[tuple[datetime, datetime]] = ()) -> tuple[np.ndarray, np.ndarray]:
    """The camera's own detections that people judged, as (features, dish or not), in time order.

    Their features were kept when each was first seen, so no model is loaded: this fits in a small
    process beside live analysis, where a refit over the labeled frames did not.
    """
    holdout = _windows(root, holdout)
    rows = sorted([(s["created_at"], np.asarray(s["embedding"], dtype=np.float32), s["label"]) for s in store.labeled_dish_samples()
                   if not any(start <= datetime.fromisoformat(s["created_at"]) <= end for start, end in holdout)], key=lambda row: row[0])
    if not rows:
        return np.zeros((0, 512), np.float32), np.zeros(0)
    return np.stack([row[1] for row in rows]), np.array([row[2] for row in rows], dtype=float)


def _adopt(path: Path, probe: dict | None, y: np.ndarray) -> dict:
    """Save a probe only if it holds up on data it didn't see."""
    if probe is None:
        return {"adopted": False, "reason": f"Not enough labels yet: {int(y.sum())} dishes and {int(len(y) - y.sum())} mistakes; {MIN_EXAMPLES} of each are needed."}
    if probe["held_out"]["auc"] < MIN_AUC:
        return {"adopted": False, "reason": f"Held-out AUC {probe['held_out']['auc']} is below {MIN_AUC}.", "held_out": probe["held_out"]}
    probe["trained_at"] = stamp()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(probe))
    os.replace(temporary, path)
    return {"adopted": True, "examples": probe["examples"], "held_out": probe["held_out"], "threshold": probe["threshold"]}


def refit(root: Path, store: Store, detect: Callable[[bytes], tuple[Any, list[dict]]], embed: Callable[[list[Any]], np.ndarray],
          *, holdout: Sequence[tuple[datetime, datetime]] = ()) -> dict:
    """Train on everything labeled; adopt only a probe that holds up on data it didn't see."""
    X, y = examples(root, store, detect, embed, holdout=holdout)
    return _adopt(Path(root) / "models" / PROBE_NAME, train(X, y), y)


def refit_kitchen(root: Path, store: Store, *, holdout: Sequence[tuple[datetime, datetime]] = ()) -> dict:
    """Retrain the kitchen probe from people's answers about the camera's detections."""
    X, y = kitchen_examples(root, store, holdout=holdout)
    return _adopt(Path(root) / "models" / KITCHEN_PROBE_NAME, train(X, y, l2=KITCHEN_L2), y)
