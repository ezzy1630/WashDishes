"""A bounded, locally labeled image set for teaching the detector this particular kitchen.

Frames are kept only when the scene changes or something notable happens, pre-boxed by
the current detector, and corrected by a person in the dashboard. Nothing leaves the device.
"""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .store import Store, parse_stamp, stamp, utcnow
from .vision import KINDS, box_iou

STATUSES = ("unlabeled", "labeled", "skipped")
# Teacher boxes below this score are more often wrong than right; a person adds what it missed.
TEACHER_MIN_SCORE = 0.35


class Dataset:
    def __init__(self, root: Path, store: Store):
        self.root, self.store = Path(root) / "dataset", store
        self.images, self.labels = self.root / "images", self.root / "labels"
        self.lock = threading.RLock()
        self.last_signature: tuple | None = None
        self.last_saved = 0.0
        self._summary: tuple[tuple, dict] | None = None

    def _ready(self) -> bool:
        settings = self.store.settings()
        if not settings["dataset_enabled"] or settings["dataset_limit_gb"] <= 0:
            return False
        for folder in (self.root, self.images, self.labels):
            folder.mkdir(exist_ok=True, mode=0o700)
        return True

    def consider(self, jpeg: bytes, analysis: dict, by_area: dict, *, notable: str | None = None, observed_at: datetime | None = None) -> str | None:
        """Keep a frame when contents change, when an entry opens or closes, or every half hour."""
        now = time.monotonic()
        signature = tuple(sorted(by_area.items()))
        if notable and now - self.last_saved >= 10:
            reason = notable
        elif signature != self.last_signature and now - self.last_saved >= 60:
            reason = "Counter or sink contents changed"
        elif now - self.last_saved >= 1800:
            reason = "Periodic sample"
        else:
            return None
        if not self._ready():
            return None
        self.last_signature, self.last_saved = signature, now
        return self.save(jpeg, analysis, reason, observed_at=observed_at)

    def save(self, jpeg: bytes, analysis: dict, reason: str, *, observed_at: datetime | None = None, capture: dict | None = None) -> str:
        for folder in (self.root, self.images, self.labels):
            folder.mkdir(exist_ok=True, mode=0o700)
        frame_id = str(time.time_ns() // 1000)
        while (self.labels / f"{frame_id}.json").exists():
            frame_id = str(int(frame_id) + 1)
        boxes = [{"kind": d["kind"], "box": [round(v, 5) for v in d["box"]], "score": round(d.get("score", 0), 3)}
                 for d in analysis.get("detections", []) if d["kind"] in KINDS and d.get("score", 0) >= 0.3]
        width, height = analysis.get("size") or (None, None)
        label = {"id": frame_id, "created_at": stamp(observed_at), "reason": reason, "status": "unlabeled", "width": width, "height": height,
                 "boxes": boxes, "predicted": boxes}
        if capture is not None:
            reference = capture.get("purpose") == "reference"
            label.update(correction=not reference, capture=capture, status="skipped" if reference else "unlabeled")
        with self.lock:
            label["holdout"] = self.reserved(parse_stamp(label["created_at"]))
            (self.images / f"{frame_id}.jpg").write_bytes(jpeg)
            self._write(frame_id, label)
            self.enforce_budget()
        return frame_id

    def reserved(self, at: datetime) -> bool:
        path = self.root / "evaluation-windows.json"
        windows = json.loads(path.read_text()) if path.exists() else []
        return any(parse_stamp(start) <= at <= parse_stamp(end) for start, end in windows)

    def capture(self, jpeg: bytes, analysis: dict, *, observed_at: datetime, purpose: str, capture: dict) -> dict:
        """Freeze original predictions before a human sees/edits the labels."""
        if purpose not in ("training", "evaluation", "reference"):
            raise ValueError("Choose training, evaluation, or a search reference.")
        if self.store.settings()["dataset_limit_gb"] <= 0:
            raise ValueError("Set a training picture storage limit in Settings before saving a correction.")
        with self.lock:
            if purpose == "evaluation":
                # A fixed clock block leaks nearly identical frames across its
                # boundary. Reserve both sides of this capture, including future frames.
                start = observed_at - timedelta(minutes=15)
                end = observed_at + timedelta(minutes=15)
                self.hold_out([(start, end)])
            frame_id = self.save(jpeg, analysis, "Search reference" if purpose == "reference" else "Live correction",
                                 observed_at=observed_at, capture=capture | {"purpose": purpose})
            label = self.item(frame_id)
            return label

    def from_review(self, obligation_id: int, jpeg: bytes, box: list[float], kind: str, verdict: str, *, observed_at: datetime | None = None) -> str | None:
        """Queue the photo behind a Review answer for labeling, already corrected by that answer.

        "dish" keeps the box with the confirmed kind; "not_dish" removes it, which is exactly the
        mistake the detector needs to unlearn. The rest of the picture still needs a person's look,
        so the frame waits in the Training queue, first in line, rather than counting as labeled.
        """
        if verdict not in ("dish", "not_dish") or kind not in KINDS or not self._ready():
            return None
        with self.lock:
            if any(i.get("review", {}).get("obligation_id") == obligation_id for i in self._labels()):
                return None
            hinted = {"kind": kind, "box": [round(v, 5) for v in box], "score": 1.0}
            frame_id = self.save(jpeg, {"detections": [hinted] if verdict == "dish" else []},
                                 f"From Review: {'a ' + kind + ' confirmed' if verdict == 'dish' else 'marked not a dish'}", observed_at=observed_at)
            label = self.item(frame_id)
            label |= {"review": {"obligation_id": obligation_id, "verdict": verdict, "box": hinted["box"], "kind": kind}, "predicted": [hinted]}
            self._write(frame_id, label)
            return frame_id

    def _write(self, frame_id: str, label: dict) -> None:
        temporary = self.labels / f".{frame_id}.json.tmp"
        temporary.write_text(json.dumps(label))
        os.replace(temporary, self.labels / f"{frame_id}.json")

    def _labels(self) -> list[dict]:
        if not self.labels.exists():
            return []
        result = []
        for path in sorted(self.labels.glob("*.json")):
            try:
                result.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        return result

    def prelabel(self, detect, *, limit: int | None = None) -> int:
        """Pre-box unlabeled frames with the slower, stronger teacher so a person only corrects them."""
        done = 0
        for item in self._labels():
            if item["status"] != "unlabeled" or item.get("prelabel") == "teacher" or item.get("holdout") or item.get("correction"):
                continue
            path = self.images / f"{item['id']}.jpg"
            if not path.exists():
                continue
            boxes = [{"kind": d["kind"], "box": [round(v, 5) for v in d["box"]], "score": round(d["score"], 3)}
                     for d in detect(path.read_bytes()) if d["kind"] in KINDS and d["score"] >= TEACHER_MIN_SCORE]
            with self.lock:
                label = self.item(item["id"])
                if label["status"] != "unlabeled":
                    continue
                label.update(boxes=boxes, predicted=boxes, prelabel="teacher")
                self._write(item["id"], label)
            done += 1
            if limit is not None and done >= limit:
                break
        return done

    def hold_out(self, windows: list[tuple[datetime, datetime]]) -> int:
        """Keep frames from evaluation windows out of training, so the evaluation stays honest."""
        held = 0
        with self.lock:
            self.root.mkdir(exist_ok=True, mode=0o700)
            path = self.root / "evaluation-windows.json"
            existing = json.loads(path.read_text()) if path.exists() else []
            combined = sorted({tuple(w) for w in existing} | {(stamp(start), end.isoformat()) for start, end in windows})
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(combined))
            temporary.chmod(0o600)
            os.replace(temporary, path)
            for item in self._labels():
                inside = any(start <= datetime.fromisoformat(item["created_at"]) <= end for start, end in windows)
                if inside and not item.get("holdout"):
                    item["holdout"] = True
                    self._write(item["id"], item)
                    held += 1
        return held

    def sample_footage(self, folder: Path, *, every: float = 60, skip: list[tuple[datetime, datetime]] = ()) -> int:
        """Add one frame per interval from recorded segments, leaving out evaluation windows.

        A segment's modification time is when it finished recording, ten seconds after it began.
        """
        added, last = 0, None
        for segment in sorted(Path(folder).glob("*.ts"), key=lambda p: p.stat().st_mtime):
            ended = datetime.fromtimestamp(segment.stat().st_mtime).astimezone()
            if any(start <= ended <= end for start, end in skip) or (last and (ended - last).total_seconds() < every):
                continue
            with tempfile.TemporaryDirectory() as directory:
                frame = Path(directory) / "frame.jpg"
                subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "5", "-i", str(segment), "-frames:v", "1", "-q:v", "2", str(frame)], check=False)
                if not frame.exists():
                    continue
                jpeg = frame.read_bytes()
            import cv2
            import numpy as np
            image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            self.save(jpeg, {"detections": [], "size": [image.shape[1], image.shape[0]]}, "Sampled from recorded footage for training", observed_at=ended)
            last, added = ended, added + 1
        return added

    def enforce_budget(self) -> int:
        """Unlabeled frames go first, then skipped ones; human-labeled frames only as a last resort."""
        limit = int(self.store.settings()["dataset_limit_gb"] * 1_000_000_000)
        with self.lock:
            items = self._labels()
            sizes = {i["id"]: (self.images / f"{i['id']}.jpg").stat().st_size for i in items if (self.images / f"{i['id']}.jpg").exists()}
            used, removed = sum(sizes.values()), 0
            order = {"unlabeled": 0, "skipped": 1, "labeled": 3}
            for item in sorted(items, key=lambda i: (2 if i["status"] == "unlabeled" and (i.get("review") or i.get("correction")) else order.get(i["status"], 0), i["id"])):
                if used <= limit:
                    break
                used -= sizes.get(item["id"], 0)
                self.delete(item["id"])
                removed += 1
            return removed

    def delete(self, frame_id: str) -> None:
        self._check_id(frame_id)
        (self.images / f"{frame_id}.jpg").unlink(missing_ok=True)
        (self.labels / f"{frame_id}.json").unlink(missing_ok=True)

    @staticmethod
    def _check_id(frame_id: str) -> None:
        if not frame_id.isdecimal() or len(frame_id) > 20:
            raise ValueError("Unknown training frame.")

    def summary(self) -> dict:
        with self.lock:
            # Reading every label file took over 3 s on the Jetson and the dashboard asks every few
            # seconds. Every write is a rename into these folders and every delete an unlink, both of
            # which move the folder's mtime, so the counts are only recomputed when something changed.
            version = tuple(folder.stat().st_mtime_ns if folder.exists() else 0 for folder in (self.labels, self.images))
            if self._summary is None or self._summary[0] != version:
                items = self._labels()
                self._summary = (version, {
                    "counts": {status: sum(i["status"] == status for i in items) for status in STATUSES},
                    "from_review": sum(i["status"] == "unlabeled" and bool(i.get("review")) for i in items),
                    "labeled_boxes": sum(len(i["boxes"]) for i in items if i["status"] == "labeled"),
                    "used_bytes": sum(p.stat().st_size for p in self.images.glob("*.jpg")) if self.images.exists() else 0,
                    "corrections": sum(bool(i.get("correction")) for i in items),
                    "evaluation": frame_scores([i for i in items if i.get("holdout") and i.get("correction") and i["status"] == "labeled"])})
            settings = self.store.settings()
            return self._summary[1] | {"limit_bytes": int(settings["dataset_limit_gb"] * 1_000_000_000), "enabled": settings["dataset_enabled"], "kinds": list(KINDS)}

    def item(self, frame_id: str) -> dict:
        self._check_id(frame_id)
        path = self.labels / f"{frame_id}.json"
        if not path.exists():
            raise LookupError("Training frame not found.")
        return json.loads(path.read_text())

    def next(self, *, after: str | None = None, status: str = "unlabeled") -> dict | None:
        with self.lock:
            items = [i for i in self._labels() if i["status"] == status and (after is None or i["id"] > after)]
            # Frames behind Review answers are the detector's known mistakes and confirmations; label them first.
            items.sort(key=lambda i: (not (i.get("review") or i.get("correction")), i["id"]))
            return items[0] if items else None

    def image(self, frame_id: str) -> Path:
        self._check_id(frame_id)
        path = self.images / f"{frame_id}.jpg"
        if not path.exists():
            raise LookupError("Training frame not found.")
        return path

    def update(self, frame_id: str, boxes: list, status: str) -> dict:
        if status not in STATUSES:
            raise ValueError("Choose labeled, skipped, or unlabeled.")
        if not isinstance(boxes, list) or len(boxes) > 300:
            raise ValueError("A frame can have at most 300 boxes.")
        clean = []
        for box in boxes:
            if not isinstance(box, dict) or box.get("kind") not in KINDS:
                raise ValueError("Each box needs a supported kind.")
            values = box.get("box")
            if not isinstance(values, list) or len(values) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError("Each box needs x, y, width, and height.")
            x, y, w, h = (max(0.0, min(1.0, float(v))) for v in values)
            if w < 0.002 or h < 0.002:
                continue
            clean.append({"kind": box["kind"], "box": [round(x, 5), round(y, 5), round(min(w, 1 - x), 5), round(min(h, 1 - y), 5)]})
        with self.lock:
            label = self.item(frame_id)
            label.update(boxes=clean, status=status, labeled_at=stamp())
            self._write(frame_id, label)
            return label

    def export(self, target: Path | None = None, *, val_fraction: float = 0.15, seed: int = 7, include_teacher: bool = False) -> Path:
        """Write labeled frames in YOLO format, with a fixed validation split.

        With include_teacher, unreviewed teacher boxes are training data too. Validation uses
        human labels once there are ten; before that it only measures agreement with the teacher.
        """
        target = Path(target or self.root / "export")
        items = [i for i in self._labels() if not i.get("holdout")]
        labeled = [i for i in items if i["status"] == "labeled"]
        teacher = [i for i in items if include_teacher and i["status"] == "unlabeled" and i.get("prelabel") == "teacher"]
        if len(labeled) + len(teacher) < 10:
            raise ValueError(f"Label at least 10 frames before training ({len(labeled)} so far).")
        if target.exists():
            shutil.rmtree(target)
        pool = labeled if len(labeled) >= 10 else labeled + teacher
        random.Random(seed).shuffle(pool)
        validation = pool[:max(2, int(len(pool) * val_fraction))]
        held = {i["id"] for i in validation}
        for split, items in (("val", validation), ("train", [i for i in labeled + teacher if i["id"] not in held])):
            (target / "images" / split).mkdir(parents=True)
            (target / "labels" / split).mkdir(parents=True)
            for item in items:
                shutil.copyfile(self.images / f"{item['id']}.jpg", target / "images" / split / f"{item['id']}.jpg")
                lines = [f"{KINDS.index(b['kind'])} {b['box'][0] + b['box'][2] / 2:.6f} {b['box'][1] + b['box'][3] / 2:.6f} {b['box'][2]:.6f} {b['box'][3]:.6f}" for b in item["boxes"]]
                (target / "labels" / split / f"{item['id']}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        names = "\n".join(f"  {index}: {kind}" for index, kind in enumerate(KINDS))
        (target / "data.yaml").write_text(f"path: {target.resolve()}\ntrain: images/train\nval: images/val\nnames:\n{names}\n")
        (target / "exported_at").write_text(stamp(utcnow()))
        return target / "data.yaml"


def frame_scores(items: list[dict], predictions: dict[str, list[dict]] | None = None) -> dict:
    """Object presence at IoU .5; class accuracy is separate from finding a dish.

    This scores held-out stills, not handling, ownership or washing. By default it
    measures the immutable detector output captured before the labels were edited.
    """
    matched = truth = predicted = kind_correct = 0
    for item in items:
        actual = [b for b in item["boxes"] if b["kind"] != "person"]
        found = [b for b in (predictions[item["id"]] if predictions is not None else item["predicted"])
                 if b["kind"] != "person" and b.get("score", 1) >= .35]
        used_a, used_b = set(), set()
        for iou, i, j in sorted(((box_iou(a["box"], b["box"]), i, j) for i, a in enumerate(actual) for j, b in enumerate(found)), reverse=True):
            if iou >= .5 and i not in used_a and j not in used_b:
                used_a.add(i)
                used_b.add(j)
                kind_correct += actual[i]["kind"] == found[j]["kind"]
        truth += len(actual)
        predicted += len(found)
        matched += len(used_a)
    return {"frames": len(items), "truth": truth, "predicted": predicted, "matched": matched,
            "missed": truth - matched, "extra": predicted - matched,
            "precision": matched / predicted if predicted else None, "recall": matched / truth if truth else None,
            "f1": 2 * matched / (truth + predicted) if truth + predicted else None,
            "kind_accuracy": kind_correct / matched if matched else None}
