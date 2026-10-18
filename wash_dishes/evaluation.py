"""Replay recorded footage through the live pipeline and score it against hand-labeled truth.

Truth is a JSON file: {"from": ISO, "to": ISO, "changes": [{"start": ISO, "at": ISO, "area": id,
"added": [kind], "removed": [kind]}]}, one entry per visit to an area that left it different; "start"
is optional and "at" is when the visit ended. Scores count single dishes: a prediction matches a true
change in the same direction, in the same or an overlapping area, within the time tolerance of the visit. Kind and exact area are reported separately, since a dish put
down and called the wrong kind is still a dish found.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .alignment import ViewAlignment, shift_areas
from .config import Config, monitoring_areas
from .inventory import Inventory
from .store import Store, parse_stamp
from .vision import LocalVision, box_iou

TOLERANCE_SECONDS = 90


class _NoActivityPhotos:
    def save(self, *args, **kwargs) -> None:
        pass

    def recent(self, *args, **kwargs) -> list:
        return []


class _NoMedia:
    """Replays keep no evidence files."""

    activity = _NoActivityPhotos()

    def save_snapshot(self, *args, **kwargs) -> str:
        return uuid.uuid4().hex

    def request_event_clip(self, *args, **kwargs) -> None:
        pass


def frames(folder: Path, start: datetime, end: datetime, fps: float) -> Iterator[tuple[datetime, bytes]]:
    """Full-resolution JPEG frames with capture times; a segment's mtime is when it finished."""
    found = sorted((datetime.fromtimestamp(p.stat().st_mtime, timezone.utc), p) for p in Path(folder).glob("*.ts"))
    for ended, path in found:
        if not start - timedelta(seconds=10) <= ended <= end + timedelta(seconds=10):
            continue
        data = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-vf", f"fps={fps}", "-q:v", "3",
                               "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"], capture_output=True).stdout
        images, position = [], 0
        while (begin := data.find(b"\xff\xd8", position)) >= 0 and (finish := data.find(b"\xff\xd9", begin + 2)) >= 0:
            images.append(data[begin:finish + 2])
            position = finish + 2
        for index, jpeg in enumerate(images):
            at = ended - timedelta(seconds=(len(images) - index) / fps)
            if start <= at <= end:
                yield at, jpeg


def replay(footage: Path, root: Path, areas: list[dict], start: datetime, end: datetime, *, fps: float = 2.0,
           detector: Path | None = None, dish_detector: Path | None = None, verify: bool = True, verify_threshold: float | None = None) -> list[dict]:
    """Dish changes the current pipeline finds in the footage, using an isolated scratch database.

    The second look runs as it does live, when its model is adopted; verify_threshold 0 scores
    every change without dropping any, so thresholds can be compared on one replay.
    """
    vision = LocalVision(root, detector, dish_detector)
    with tempfile.TemporaryDirectory() as scratch:
        config = Config(Path(scratch) / "live")
        config.prepare()
        store = Store(config.root)
        try:
            store.set_settings({"areas": areas})
            reference = Path(scratch) / "view-reference.npy"
            if (root / "view-reference.npy").exists():
                shutil.copyfile(root / "view-reference.npy", reference)
            alignment = ViewAlignment(reference)
            inventory = Inventory(store, _NoMedia())
            if verify:
                from .verifier import DishVerifier, store_hook
                inventory.verify = store_hook(DishVerifier(root), store, threshold=verify_threshold)
            aligned_at = None
            for at, jpeg in frames(footage, start, end, fps):
                analysis = vision.analyze(jpeg)
                if aligned_at is None or (at - aligned_at).total_seconds() >= 2:
                    aligned_at = at
                    change = alignment.observe(jpeg, now=at.timestamp(), people=sum(d["kind"] == "person" for d in analysis["detections"]))
                    if change:
                        inventory.shift(*change)
                if alignment.settled:
                    inventory.process(analysis, vision.last_image, jpeg, shift_areas(areas, alignment.offset), "replay", at,
                                      vision.detect_area)
            return list(inventory.changes)
        finally:
            store.close()


def _related(a: str | None, b: str | None, areas: list[dict]) -> bool:
    if a == b:
        return True
    boxes = {area["id"]: area["box"] for area in areas}
    return a in boxes and b in boxes and box_iou(boxes[a], boxes[b]) > 0


def score(predicted: list[dict], truth: dict, areas: list[dict], *, roles: tuple[str, ...] | None = None,
          tolerance: float = TOLERANCE_SECONDS) -> dict:
    role = {area["id"]: area["role"] for area in areas}
    expected = [{"start": parse_stamp(c.get("start", c["at"])), "at": parse_stamp(c["at"]), "area": c["area"], "change": change, "kind": kind}
                for c in truth["changes"] for change in ("added", "removed") for kind in c.get(change, [])]
    found = [{**p, "at": parse_stamp(p["at"])} for p in predicted]
    if roles:
        expected = [e for e in expected if role.get(e["area"]) in roles]
        found = [p for p in found if role.get(p["area"]) in roles]
    # Within the tolerance, prefer pairings that agree on kind and area, then the closest in time.
    pairs = sorted((e["kind"] != p["kind"], e["area"] != p["area"], gap, n, m)
                   for n, e in enumerate(expected) for m, p in enumerate(found)
                   if e["change"] == p["change"] and _related(e["area"], p["area"], areas)
                   and (gap := max(0.0, (e["start"] - p["at"]).total_seconds(), (p["at"] - e["at"]).total_seconds())) <= tolerance)
    used_e, used_p, matched = set(), set(), []
    for _, _, _, n, m in pairs:
        if n not in used_e and m not in used_p:
            used_e.add(n)
            used_p.add(m)
            matched.append((expected[n], found[m]))
    precision = len(matched) / len(found) if found else None
    recall = len(matched) / len(expected) if expected else None
    return {"truth": len(expected), "predicted": len(found), "matched": len(matched), "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision and recall else 0.0,
            "kind_accuracy": sum(e["kind"] == p["kind"] for e, p in matched) / len(matched) if matched else None,
            "area_accuracy": sum(e["area"] == p["area"] for e, p in matched) / len(matched) if matched else None,
            "missed": [{"at": e["at"].isoformat(), "area": e["area"], "change": e["change"], "kind": e["kind"]} for n, e in enumerate(expected) if n not in used_e],
            "extra": [{"at": p["at"].isoformat(), "area": p["area"], "change": p["change"], "kind": p["kind"]} for m, p in enumerate(found) if m not in used_p]}


def evaluate(config: Config, store: Store, truth_path: Path, footage: Path | None, *, fps: float = 2.0, detector: Path | None = None,
             dish_detector: Path | None = None, reuse: Path | None = None, out: Path | None = None, verify: bool = True,
             verify_threshold: float | None = None) -> dict:
    """Replay and score; with reuse, score a previous report's predictions again (after correcting the truth).

    A replay takes about half the footage's length, so its predictions are written to out before
    scoring; a later failure can then be rescored with reuse.
    """
    truth = json.loads(Path(truth_path).read_text())
    start, end = parse_stamp(truth["from"]), parse_stamp(truth["to"])
    areas = monitoring_areas(store.settings())
    if reuse:
        previous = json.loads(Path(reuse).read_text())
        # An earlier report may cover a longer window; score only what falls in this one.
        predicted = [c for c in previous["predicted"] if start <= parse_stamp(c["at"]) <= end]
        detector = previous["detector"]
    else:
        predicted = replay(footage, config.root, areas, start, end, fps=fps, detector=detector, dish_detector=dish_detector,
                           verify=verify, verify_threshold=verify_threshold)
        if dish_detector:
            detector = f"{detector or 'current'} + {dish_detector}"
        if out:
            Path(out).write_text(json.dumps({"detector": str(detector or "current"), "predicted": predicted}, indent=1))
    left_out = ("dirty", "sink")
    deployed = store.changes_between(start, end)
    return {"window": [truth["from"], truth["to"]], "detector": str(detector or "current"),
            "changes": score(predicted, truth, areas),
            "left_out": score(predicted, truth, areas, roles=left_out),
            # What the running system itself saw, from its change log; None before that log existed.
            "deployed_left_out": None if deployed is None else score(deployed, truth, areas, roles=left_out),
            "predicted": predicted}


def evaluate_frames(root: Path, store: Store, *, dish_detector: Path | None = None) -> dict:
    """Score a detector candidate on reserved, fully human-corrected stills.

    Runs offline with watching stopped, as the existing improvement command does.
    Both models use the same area/full-frame adapter and second-look verifier.
    """
    import hashlib

    from .dataset import Dataset, frame_scores
    from .inventory import area_for
    from .verifier import DishVerifier
    from .vision import LocalVision, merge_detections
    dataset = Dataset(root, store)
    items = [i for i in dataset._labels() if i.get('correction') and i.get('holdout') and i['status'] == 'labeled']
    receipt = hashlib.sha256()
    for item in items:
        receipt.update(json.dumps({'id': item['id'], 'boxes': item['boxes'], 'capture': item.get('capture')}, sort_keys=True).encode())
        receipt.update(dataset.image(item['id']).read_bytes())
    predictions = {}
    if items:
        vision = LocalVision(root, dish_model_path=dish_detector)
        verifier = DishVerifier(root)
        for item in items:
            analysis = vision.analyze(dataset.image(item['id']).read_bytes())
            areas = item['capture']['areas']
            found = merge_detections(analysis['detections'] + [d for area in areas for d in vision.detect_area(area)
                                     if not d.get('detail_area') or d['detail_area'] == (area_for(d['box'], areas) or {}).get('id')])
            accepted = []
            for detection in found:
                if detection['kind'] == 'person' or detection['score'] < .35:
                    continue
                verdict = verifier.score(verifier.features(vision.last_image, detection['box'])) if verifier.ready else None
                if verdict is None or verdict[1]:
                    accepted.append(detection)
            predictions[item['id']] = accepted
    return frame_scores(items, predictions) | {'snapshot': receipt.hexdigest(), 'scope': 'reserved_stills_area_detector_and_verifier'}
