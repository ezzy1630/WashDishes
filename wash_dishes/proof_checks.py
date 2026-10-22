"""Check saved proof frames using the live detector and dish verifier, without tracking people.

`make_check(engine, verifier, store, lock=runtime.lock)` reuses both loaded models. The caller
must retain that same verifier when wiring inventory.store_hook; constructing another CLIP
embedder here would waste the Jetson's remaining memory. Similar crops are supporting evidence,
not proof of uninterrupted possession, washing status, or a person's identity.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from .config import monitoring_areas
from .inventory import rests_in
from .store import Store
from .verifier import DishVerifier, context_crop
from .vision import box_iou

POSITIVE = ("plate", "bowl", "cup", "mug", "drinking glass", "cooking pot", "frying pan", "fork", "knife", "spoon")
# Detector kinds and second-look labels. The detector called #917's frying pan a bowl.
COOKWARE = ("pan", "pot", "cooking pot", "frying pan")
NEGATIVE = ("soda can", "bottle", "jar", "plastic food container", "spice container", "cardboard box", "towel", "sponge", "cutting board")


@dataclass(frozen=True)
class Thresholds:
    # Calibration: 54 dishes in five separate hourly label groups had tight-crop negative
    # margin p99=.0323; allow .01 plus rounding. The larger context sample (3,680 positives)
    # had p95=.0356. The adopted probe retains its own independently fitted threshold.
    negative_margin: float = 0.05
    # Twenty labeled matching left/late pairs reached cosine .707 and colour .180: retain
    # them with .025/.03 margins. This small replay sample is calibration, NOT an
    # independent accuracy test, and does not establish recall on good complete proofs.
    same_cosine: float = 0.68
    same_colour: float = 0.15
    # Existing labeled-box matching uses .30; live attribution uses .10 reach. Inspected
    # torso failures covered >=.708 of the target, versus <=.213 full-person coverage in
    # the two visible drying placements. .60 leaves a .10 margin; it is not a pose model.
    overlap: float = 0.30
    reach: float = 0.10
    body_cover: float = 0.60
    # Context cosine for two different-object pairs and one ambiguous pair reached .909; a .06
    # margin avoids teaching a whole location is invalid after one rejected object.
    remembered_cosine: float = 0.97
    remembered_overlap: float = 0.50


def _image(jpeg: bytes) -> np.ndarray:
    import cv2
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("could not read a proof picture")
    return image


def _box(box: list) -> bool:
    return (len(box) == 4 and all(np.isfinite(v) for v in box) and min(box[:2]) >= 0
            and min(box[2:]) > 0 and box[0] + box[2] <= 1.001 and box[1] + box[3] <= 1.001)


def _crop(image: np.ndarray, box: list) -> np.ndarray:
    h, w = image.shape[:2]
    x, y, bw, bh = box
    return image[int(y*h):max(int(y*h)+1, int((y+bh)*h)), int(x*w):max(int(x*w)+1, int((x+bw)*w))]


def colour_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Histogram intersection in chromaticity, so a lamp's brightness is mostly removed."""
    def histogram(image):
        pixels = image.reshape(-1, 3).astype(np.float32) + 1
        chroma = pixels[:, :2] / pixels.sum(axis=1, keepdims=True)
        hist = np.histogram2d(chroma[:, 0], chroma[:, 1], bins=12, range=((0, 1), (0, 1)))[0]
        return hist / hist.sum()
    return float(np.minimum(histogram(a), histogram(b)).sum())


def _coverage(box: list, other: list) -> float:
    x, y, w, h = box
    px, py, pw, ph = other
    return max(0, min(x+w, px+pw)-max(x, px)) * max(0, min(y+h, py+ph)-max(y, py)) / (w*h)


def _reach(box: list, person: list) -> float:
    x, y, w, h = box
    px, py, pw, ph = person
    return max(px-x-w/2, 0, x+w/2-px-pw) + max(py-y-h/2, 0, y+h/2-py-ph)


def _body(person: list) -> list:
    # Hands may legitimately cover the dish at the edge of a person's detection. The central
    # head/torso cannot stand in for a visible put-down. This is geometry, not a pose estimate.
    x, y, w, h = person
    return [x + .2*w, y, .6*w, .8*h]


class ProofChecker:
    def __init__(self, detect: Callable, verifier: DishVerifier, negatives: Callable,
                 store: Store | None = None, *, thresholds: Thresholds = Thresholds(),
                 drying_areas: Callable | None = None):
        self.detect, self.verifier, self.negatives = detect, verifier, negatives
        self.store, self.thresholds = store, thresholds
        self.drying_areas = drying_areas

    def embedding(self, jpeg: bytes, box: list) -> list[float]:
        return self.verifier.features(_image(jpeg), box).tolist()

    def _remembered(self, obligation_id: int, vector: np.ndarray, box: list) -> str | None:
        if self.store is None:
            return None
        with self.store.lock:
            # Proofs owns this table; the factory can also be used before Proofs is constructed.
            if not self.store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='proof_rejections'").fetchone():
                return None
            item = self.store.conn.execute("SELECT location FROM obligations WHERE id=?", (obligation_id,)).fetchone()
            if item is None:
                return None
            rows = self.store.conn.execute("SELECT * FROM proof_rejections WHERE reason='not_a_dish' AND location IS ? ORDER BY created_at DESC",
                                           (item["location"],)).fetchall()
        for row in rows:
            if box_iou(box, json.loads(row["box"])) < self.thresholds.remembered_overlap:
                continue
            old = np.asarray(json.loads(row["embedding"]), dtype=np.float32)
            if old.shape != vector.shape:
                continue
            if float(vector @ old) >= self.thresholds.remembered_cosine:
                zone = self.store.settings().get("timezone", "America/Los_Angeles")
                when = datetime.fromisoformat(row["created_at"]).astimezone(ZoneInfo(zone)).strftime("%-I:%M %p")
                return f"looks like the thing marked not a dish at {when}"
        return None

    def _kinds(self, obligation_id: int) -> list[str]:
        if self.store is None:
            return []
        with self.store.lock:
            row = self.store.conn.execute("SELECT dishes FROM obligations WHERE id=?", (obligation_id,)).fetchone()
        return [d["kind"] for d in json.loads(row["dishes"])] if row else []

    def __call__(self, obligation_id: int, found: dict, late_jpeg: bytes, box: list, late_box: list | None) -> dict | str:
        result = self.evaluate(obligation_id, found, late_jpeg, box, late_box)
        return result["reason"] if result.get("status") == "rejected" else result

    def evaluate(self, obligation_id: int, found: dict, late_jpeg: bytes, box: list, late_box: list | None) -> dict:
        """The full measurements, including failed gates, for private replay evaluation."""
        late_box = late_box if late_box is not None else box
        if not _box(box) or not _box(late_box):
            return {"status": "rejected", "reason": "the dish box is outside the picture", "checks": {}}
        if not found["before"]["at"] < found["putting"]["at"] <= found["left"]["at"]:
            return {"status": "rejected", "reason": "the proof pictures are out of order", "checks": {}}
        try:
            images = [_image(found[k]["jpeg"]) for k in ("before", "left")] + [_image(late_jpeg)]
        except ValueError as error:
            return {"status": "rejected", "reason": str(error), "checks": {}}
        # The probe was trained on broad context. Matching needs a tighter view: otherwise
        # two different pans inherit the similarity of the same stove behind them.
        left = self.verifier.features(images[1], box)
        vectors = self.verifier.embed([context_crop(images[1], box, scale=1.2, minimum=0),
                                       context_crop(images[2], late_box, scale=1.2, minimum=0)])
        score = self.verifier.score(left)
        negative = self.negatives(vectors[0])
        remembered = self._remembered(obligation_id, left, box)
        checks = {"left_embedding": left.tolist(), "probe": score[0] if score else None, "negative": negative,
                  "looks_like": negative.get("looks_like"),
                  "remembered": remembered, "thresholds": vars(self.thresholds)}
        reasons = []
        if remembered:
            reasons.append(remembered)
        # Use the configured clean/storage surfaces, including cloth mats. Position in a
        # sink or face-down appearance alone cannot establish that something was washed.
        areas = self.drying_areas() if self.drying_areas else []
        excluded = next(({"id": a["id"], "stage": stage} for stage, b in (("left", box), ("late", late_box))
                         for a in areas if a["role"] in ("clean", "storage") and rests_in(b, a)), None)
        checks["excluded_area"] = excluded
        if excluded:
            reasons.append("dish is in a drying or storage area")
        # A dish belongs to whoever used it, not whoever later moved it. A pan or pot is used on the
        # stove, so it counts only with the person the visits show cooking with it (visits.py).
        cookware = negative.get("looks_like") in COOKWARE or any(k in COOKWARE for k in self._kinds(obligation_id))
        if cookware and found.get("use") != "cooking":
            reasons.append("a pan or pot counts only with whoever cooked with it, and that isn't shown")
        if score is None:
            reasons.append("the dish checker is not ready")
        elif not score[1]:
            reasons.append("the left picture does not look like a dish")
        if negative["margin"] > self.thresholds.negative_margin:
            reasons.append(f"looks more like a {negative['label']} than a dish")

        choices = []
        hidden_frames = 0
        for candidate in [found["putting"], *found.get("candidates", [])]:
            if not found["before"]["at"] < candidate["at"] <= found["left"]["at"]:
                continue
            try:
                _image(candidate["jpeg"])
            except ValueError:
                continue
            detections = self.detect(candidate["jpeg"], box)
            people = [d["box"] for d in detections if d["kind"] == "person"
                      and _reach(box, d["box"]) <= self.thresholds.reach]
            if len(people) != 1:
                continue
            cover = max((_coverage(box, _body(d["box"])) for d in detections if d["kind"] == "person"), default=0)
            visible = max((box_iou(box, d["box"]) for d in detections if d["kind"] != "person"), default=0)
            person_cover = max((_coverage(box, d["box"]) for d in detections if d["kind"] == "person"), default=0)
            # A bounding box cannot locate the shoulder precisely. At the body's edges,
            # require an actual dish detection if the person covers most of the spot.
            # A visible dish beside an outstretched arm can still pass this check.
            if (cover >= self.thresholds.body_cover
                    or (person_cover >= self.thresholds.body_cover and visible < self.thresholds.overlap)):
                hidden_frames += 1
                continue
            choices.append((visible, -cover, candidate["at"], candidate | {"person_box": people[0]}))
        putting = max(choices, key=lambda c: c[:3])[-1] if choices else None
        checks["putting"] = {"usable_frames": len(choices), "hidden_frames": hidden_frames, "at": putting["at"] if putting else None}
        if putting is None:
            reasons.append("no picture shows a person at the dish without their body hiding it")

        late_detections = self.detect(late_jpeg, late_box)
        busy = any(_reach(late_box, d["box"]) <= self.thresholds.reach for d in late_detections if d["kind"] == "person")
        cosine = float(vectors[0] @ vectors[1])
        colour = colour_similarity(_crop(images[1], box), _crop(images[2], late_box))
        checks["late"] = {"cosine": cosine, "colour": colour, "person_at_dish": busy}
        # A dish that moved since is matched along the way by visits.py; its two spots look different.
        if not busy and not found.get("moves") and (cosine < self.thresholds.same_cosine or colour < self.thresholds.same_colour):
            reasons.append("a different dish is in that spot later")
        checks["failures"] = reasons
        if reasons:
            return {"status": "rejected", "reason": reasons[0], "checks": checks}
        result = {"checks": checks, "putting": putting}
        if busy:
            result.update(status="waiting", reason="someone is still at the dish; waiting for a clear later picture")
        return result


def make_check(engine, verifier: DishVerifier, store: Store, *, lock, thresholds: Thresholds = Thresholds(),
               drying_areas: Callable | None = None) -> ProofChecker:
    """Use runtime.lock for detector serialization; CLIP's existing embedder owns its own lock.

    No analyze(), face models, tracker updates, or second CLIP instance. A focused detector pass
    catches small dishes beside a person, using the same loaded detector rather than the teacher.
    Clean/storage areas are read afresh from settings. An optional drying_areas callback supplies
    additional area dictionaries (same schema), e.g. a cloth mat outside the rack outlines.
    """
    def detect(jpeg, box):
        with lock:
            image = _image(jpeg)
            engine._load_detector()
            detections = engine._detect(image)
            h, w = image.shape[:2]
            x, y, bw, bh = box
            x1, y1 = max(0, int((x-bw)*w)), max(0, int((y-bh)*h))
            x2, y2 = min(w, int((x+2*bw)*w)), min(h, int((y+2*bh)*h))
            crop = image[y1:y2, x1:x2]
            if min(crop.shape[:2]) >= 16:
                focused = engine._detect(crop, (x1/w, y1/h, (x2-x1)/w, (y2-y1)/h))
                detections.extend(d for d in focused if d["kind"] != "person")
            return detections

    text_features = None

    def negatives(vector):
        nonlocal text_features
        if text_features is None:
            import open_clip
            import torch
            embed = verifier.embed
            with embed.lock, torch.no_grad():
                embed._load()
                tokens = open_clip.get_tokenizer("ViT-B-32")([f"a photo of a {s} on a kitchen counter." for s in POSITIVE + NEGATIVE]).to(embed._device)
                features = embed._model.encode_text(tokens).float()
                text_features = torch.nn.functional.normalize(features, dim=-1).cpu().numpy()
        scores = text_features @ vector
        negative = int(np.argmax(scores[len(POSITIVE):]))
        return {"label": NEGATIVE[negative], "margin": float(scores[len(POSITIVE)+negative] - scores[:len(POSITIVE)].max()),
                "looks_like": POSITIVE[int(np.argmax(scores[:len(POSITIVE)]))]}

    def areas():
        return [*monitoring_areas(store.settings()), *(drying_areas() if drying_areas else [])]

    return ProofChecker(detect, verifier, negatives, store, thresholds=thresholds, drying_areas=areas)
