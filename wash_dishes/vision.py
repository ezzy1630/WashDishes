"""Local, optional vision. Observations are review proposals, never proof of washing.

The application must serialize calls to a LocalVision instance. Model preparation is
an explicit CLI operation; this module never downloads models or installs packages.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Sequence

KINDS = ("person", "plate", "bowl", "cup", "fork", "knife", "spoon", "pot", "pan")
DETECTOR_NAME = "dishes-world.pt"
# A detector fine-tuned on this kitchen's counters, used only for the per-area pass when present.
# People and full frames stay with the stock detector: the kitchen labels have few people in them.
DISH_DETECTOR_NAMES = ("dishes-kitchen.engine", "dishes-kitchen.pt")
YUNET_NAME = "face_detection_yunet_2023mar.onnx"
SFACE_NAME = "face_recognition_sface_2021dec.onnx"
# InsightFace's ArcFace (buffalo_l w600k_r50), used instead of SFace when present. On this kitchen's camera, looking
# down at faces 50-130 px wide, SFace scored other people as high as the same person (2026-10-13).
ARCFACE_NAME = "face_recognition_arcface_r50.onnx"
ARCFACE_TEMPLATE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))
# Faces seen from the ceiling camera score 0.6-0.9 with YuNet; enrollment photos keep the stricter 0.9.
SIGHTING_FACE_SCORE = 0.7
MAX_ENROLLMENT_SAMPLES = 20
MAX_JPEG_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
_MAX_ENROLLMENT_BYTES = 2 * 1024 * 1024


class VisionError(RuntimeError):
    """A user-correctable local model, dependency, image, or enrollment error."""


def _resident_id(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise VisionError("Resident IDs must be positive integers.")
    return value


def _unit_vector(values: Sequence[float]) -> list[float]:
    if not values or len(values) > 4096:
        raise VisionError("Invalid face embedding dimensions.")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise VisionError("Face embeddings must contain finite numeric values.")
    norm = math.sqrt(math.fsum(float(v) ** 2 for v in values))
    if norm <= 1e-12:
        raise VisionError("The face model returned an empty embedding.")
    return [float(v) / norm for v in values]


def match_identity(
    vector: Sequence[float],
    enrollments: dict[int, list[list[float]]],
    *,
    threshold: float = 0.55,
    margin: float = 0.10,
) -> tuple[int | None, float | None]:
    """Reject weak/ambiguous matches; similarity is not a calibrated probability.

    These conservative starting thresholds still need kitchen validation. Enrollment
    samples with different dimensions cannot match the currently loaded face model.
    """
    query = _unit_vector(vector)
    scores: list[tuple[float, int]] = []
    for resident, samples in enrollments.items():
        compatible = [sample for sample in samples if len(sample) == len(query)]
        if compatible:
            score = max(math.fsum(a * b for a, b in zip(query, sample)) for sample in compatible)
            scores.append((max(-1.0, min(1.0, score)), resident))
    if not scores:
        return None, None
    scores.sort(reverse=True)
    score, resident = scores[0]
    second = scores[1][0] if len(scores) > 1 else -1.0
    if score < threshold or score - second < margin:
        return None, score
    return resident, score


class IdentityConsensus:
    """Require consecutive eligible frames; unknown/multiple-person frames reset it."""

    def __init__(self, required_frames: int = 3, max_gap_seconds: float = 4.0):
        if required_frames < 2 or max_gap_seconds <= 0:
            raise ValueError("Identity consensus needs at least two frames and a positive gap.")
        self.required_frames = required_frames
        self.max_gap_seconds = max_gap_seconds
        self.reset()

    def reset(self) -> None:
        self._resident: int | None = None
        self._frames = 0
        self._last_time: float | None = None

    def observe(self, resident: int | None, *, eligible: bool, now: float | None = None) -> int | None:
        timestamp = time.monotonic() if now is None else now
        if not eligible or resident is None:
            self.reset()
            return None
        gap_ok = (
            self._last_time is not None
            and 0 <= timestamp - self._last_time <= self.max_gap_seconds
        )
        self._frames = self._frames + 1 if self._resident == resident and gap_ok else 1
        self._resident = resident
        self._last_time = timestamp
        return resident if self._frames >= self.required_frames else None


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection-over-union for normalized x/y/width/height boxes."""
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right = min(a[0] + a[2], b[0] + b[2])
    bottom = min(a[1] + a[3], b[1] + b[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = a[2] * a[3] + b[2] * b[3] - intersection
    return intersection / union if union > 0 else 0.0


class _PersonBoxes:
    """The minimal Boxes-like view ByteTrack reads: conf, cls, xywh, and boolean indexing."""

    def __init__(self, np: Any, xyxy: Any, conf: Any):
        self.np, self.xyxy, self.conf = np, xyxy.reshape(-1, 4), conf.reshape(-1)
        self.cls = np.zeros_like(self.conf)
        self.xywh = np.column_stack([(self.xyxy[:, 0] + self.xyxy[:, 2]) / 2, (self.xyxy[:, 1] + self.xyxy[:, 3]) / 2,
                                     self.xyxy[:, 2] - self.xyxy[:, 0], self.xyxy[:, 3] - self.xyxy[:, 1]]) if len(self.conf) else np.zeros((0, 4))

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, index: Any) -> _PersonBoxes:
        return _PersonBoxes(self.np, self.xyxy[index], self.conf[index])


def merge_focus(full: list[dict], focused: list[dict], focus: Sequence[float]) -> list[dict]:
    """Prefer the high-resolution crop inside the focus area; keep full-frame dishes it missed."""
    x, y, w, h = focus
    merged = list(focused)
    for detection in full:
        bx, by, bw, bh = detection["box"]
        inside = x <= bx + bw / 2 <= x + w and y <= by + bh / 2 <= y + h
        if not inside or all(box_iou(detection["box"], other["box"]) < 0.3 for other in focused if other["kind"] != "person"):
            merged.append(detection)
    return merged


def merge_detections(detections: list[dict]) -> list[dict]:
    """One box per object across overlapping looks, including renamed dishes."""
    kept: list[dict] = []
    for detection in sorted(detections, key=lambda d: -d["score"]):
        if all(box_iou(detection["box"], other["box"]) < 0.5 for other in kept):
            kept.append(detection)
    return kept


class EnrollmentStore:
    """Private numeric JSON, with bounded reads and atomic replacement."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, resident: int) -> Path:
        return self.directory / f"{_resident_id(resident)}.json"

    def read(self, resident: int) -> list[list[float]]:
        path = self._path(resident)
        if not path.exists():
            return []
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_ENROLLMENT_BYTES:
                raise ValueError("unsafe or oversized file")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or payload.get("model") != SFACE_NAME:
                raise ValueError("unrecognized face model or format")
            vectors = payload["vectors"]
            if not isinstance(vectors, list) or len(vectors) > MAX_ENROLLMENT_SAMPLES:
                raise ValueError("invalid sample count")
            if any(not isinstance(vector, list) or not 64 <= len(vector) <= 4096 for vector in vectors):
                raise ValueError("invalid vector shape")
            normalized = [_unit_vector(vector) for vector in vectors]
            if normalized and any(len(vector) != len(normalized[0]) for vector in normalized):
                raise ValueError("inconsistent vector dimensions")
            return normalized
        except (OSError, ValueError, TypeError, KeyError, AttributeError, VisionError) as error:
            raise VisionError(f"Invalid enrollment for resident {resident}; clear it and enroll again.") from error

    def all(self) -> dict[int, list[list[float]]]:
        if not self.directory.exists():
            return {}
        return {
            int(path.stem): self.read(int(path.stem))
            for path in self.directory.glob("*.json")
            if path.stem.isdecimal() and int(path.stem) > 0
        }

    def append(self, resident: int, vector: Sequence[float]) -> int:
        target = self._path(resident)
        normalized = _unit_vector(vector)
        if not 64 <= len(normalized) <= 4096:
            raise VisionError("The face model returned unsupported embedding dimensions.")
        samples = self.read(resident)
        if len(samples) >= MAX_ENROLLMENT_SAMPLES:
            raise VisionError(f"Enrollment is limited to {MAX_ENROLLMENT_SAMPLES} samples per resident.")
        if samples and len(samples[0]) != len(normalized):
            raise VisionError("Face model dimensions changed; clear this enrollment before adding samples.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".enrollment-", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"version": 1, "model": SFACE_NAME, "vectors": [*samples, normalized]}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return len(samples) + 1

    def clear(self, resident: int) -> None:
        self._path(resident).unlink(missing_ok=True)


def _import_yolo_offline(config_directory: Path | None = None) -> Any:
    # Apply before import and to cached imports so embedding/export code cannot pip-install.
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ["YOLO_OFFLINE"] = "true"
    try:
        if config_directory is not None:
            config_directory.mkdir(parents=True, exist_ok=True)
            os.environ["YOLO_CONFIG_DIR"] = str(config_directory.resolve())
        from ultralytics import YOLO, utils
        from ultralytics.utils import checks
        utils.AUTOINSTALL = checks.AUTOINSTALL = False
        utils.ONLINE = checks.ONLINE = False
        return YOLO
    except Exception as error:
        raise VisionError("Local detector dependencies are unavailable. Install the optional vision requirements.") from error


class LocalVision:
    """Optional local detector and face adapter; identity remains a review candidate."""

    def __init__(self, root: Path, model_path: Path | None = None, dish_model_path: Path | None = None):
        self.root = Path(root)
        self.model_path = Path(model_path) if model_path is not None else self.root / "models" / DETECTOR_NAME
        self.dish_model_path = Path(dish_model_path) if dish_model_path is not None else next(
            (path for path in (self.root / "models" / name for name in DISH_DETECTOR_NAMES) if path.is_file()), None)
        self._dish_detector: Any = None
        self._detail_detector: Any = None
        self.detail_model_path = self.root / "models" / "dishes-teacher.pt"
        self._store = EnrollmentStore(self.root / "faces")
        self._people: Any = None
        self._consensus = IdentityConsensus()
        self._embedder: Any = None
        self._references: dict[str, tuple[float, Any]] = {}
        self.last_image: Any = None
        self.last_detections: list[dict] = []
        self._cv: Any = None
        self._np: Any = None
        self._detector: Any = None
        self._face_detector: Any = None
        self._face_recognizer: Any = None
        self._sighting_faces: Any = None
        self._arcface: Any = None
        self._clip_faces: Any = None
        # Clips are read on the media thread while frames are analysed on another; one ArcFace net serves both.
        self._face_lock = threading.Lock()
        self._device = "cpu"
        self._gpu_unavailable = False
        self._load_error: str | None = None

    def status(self) -> dict[str, Any]:
        paths = [self.model_path, self.root / "models" / YUNET_NAME, self.root / "models" / SFACE_NAME]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            return {"ready": False, "message": "Local models missing: " + ", ".join(missing) + ". Run model setup first."}
        if self.model_path.suffix.lower() not in (".pt", ".engine"):
            return {"ready": False, "message": "Detector must be a local .pt checkpoint or TensorRT .engine."}
        try:
            missing_dependencies = [name for name in ("numpy", "cv2", "ultralytics") if importlib.util.find_spec(name) is None]
        except (ImportError, ValueError):
            missing_dependencies = ["vision"]
        if missing_dependencies:
            return {"ready": False, "message": "Optional vision dependencies missing: " + ", ".join(missing_dependencies)}
        if self._load_error:
            return {"ready": False, "message": self._load_error}
        message = "Local vision available; dish and identity observations require review."
        if self._detector is not None:
            message = ("NVIDIA GPU unavailable; running slower CPU inference. Check the Jetson GPU."
                       if self._gpu_unavailable else f"Local vision running on {self._device}.")
        return {"ready": True, "message": message, "device": self._device if self._detector is not None else None}

    def count_enrollment(self, resident_id: int) -> int:
        return len(self._store.read(resident_id))

    def reset_observations(self) -> None:
        """Require fresh identity and dish observations after any monitoring gap."""
        self._consensus.reset()
        self._people = None
        self.last_detections = []

    def clear_enrollment(self, resident_id: int) -> None:
        self._store.clear(resident_id)
        self._consensus.reset()

    def _load_faces(self) -> None:
        if self._face_detector is not None:
            return
        for name in (YUNET_NAME, SFACE_NAME):
            if not (self.root / "models" / name).is_file():
                raise VisionError(f"Face model missing: {name}. Run model setup first.")
        try:
            import cv2
            import numpy
            self._cv, self._np = cv2, numpy
            self._face_detector = cv2.FaceDetectorYN.create(
                str(self.root / "models" / YUNET_NAME), "", (320, 320), 0.9, 0.3, 5000
            )
            self._face_recognizer = cv2.FaceRecognizerSF.create(str(self.root / "models" / SFACE_NAME), "")
            self._sighting_faces = cv2.FaceDetectorYN.create(
                str(self.root / "models" / YUNET_NAME), "", (320, 320), SIGHTING_FACE_SCORE, 0.3, 5000
            )
            self._clip_faces = cv2.FaceDetectorYN.create(
                str(self.root / "models" / YUNET_NAME), "", (320, 320), SIGHTING_FACE_SCORE, 0.3, 5000
            )
            if (self.root / "models" / ARCFACE_NAME).is_file():
                self._arcface = cv2.dnn.readNetFromONNX(str(self.root / "models" / ARCFACE_NAME))
        except Exception as error:
            self._face_detector = self._face_recognizer = self._sighting_faces = self._clip_faces = self._arcface = None
            raise VisionError("Unable to load YuNet/SFace. Install compatible OpenCV vision dependencies and valid models.") from error

    def _load_detector(self) -> None:
        if self._detector is not None:
            return
        status = self.status()
        if not status["ready"]:
            raise VisionError(status["message"])
        try:
            YOLO = _import_yolo_offline(self.root / "vision-settings")
            import torch
            available = torch.cuda.is_available()
            self._device = os.getenv("WASH_DISHES_VISION_DEVICE", "cuda:0" if available else "cpu")
            self._gpu_unavailable = bool(torch.version.cuda) and not available and not os.getenv("WASH_DISHES_VISION_DEVICE")
            if self.model_path.suffix.lower() == ".engine" and not torch.cuda.is_available():
                raise VisionError("TensorRT inference requires a supported NVIDIA GPU. Use the local .pt model on this Mac.")
            detector = self._open_detector(YOLO, self.model_path)
            if self.dish_model_path is not None:
                self._dish_detector = self._open_detector(YOLO, self.dish_model_path)
            self._detector = detector
        except Exception as error:
            message = str(error) if isinstance(error, VisionError) else "Unable to load the local detector; check vision dependencies, model integrity, and device support."
            self._load_error = message
            raise VisionError(message) from error

    def _image(self, jpeg: bytes) -> Any:
        if not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= MAX_JPEG_BYTES:
            raise VisionError("Provide a JPEG image no larger than 10 MiB.")
        if not jpeg.startswith(b"\xff\xd8"):
            raise VisionError("The enrollment/analysis image must be JPEG.")
        self._load_faces()
        image = self._cv.imdecode(self._np.frombuffer(jpeg, dtype=self._np.uint8), self._cv.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise VisionError("The JPEG image could not be decoded.")
        if image.shape[0] * image.shape[1] > MAX_IMAGE_PIXELS:
            raise VisionError("The JPEG image exceeds the 20 megapixel analysis limit.")
        return image

    def _faces(self, image: Any) -> Any:
        self._face_detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = self._face_detector.detect(image)
        return [] if faces is None else faces

    def _usable_face(self, face: Any) -> bool:
        # Small/profile/blurred faces need calibration; a size floor rejects unusable crops now.
        return bool(self._np.isfinite(face).all() and face[2] >= 60 and face[3] >= 60 and face[-1] >= 0.9)

    def _embedding(self, image: Any, face: Any) -> list[float]:
        if self._arcface is None:
            aligned = self._face_recognizer.alignCrop(image, face)
            return _unit_vector(self._face_recognizer.feature(aligned).reshape(-1).astype(float).tolist())
        cv, np = self._cv, self._np
        landmarks = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
        transform, _ = cv.estimateAffinePartial2D(landmarks, np.asarray(ARCFACE_TEMPLATE, dtype=np.float32), method=cv.LMEDS)
        aligned = cv.warpAffine(image, transform, (112, 112))
        with self._face_lock:
            self._arcface.setInput(cv.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True))
            embedding = self._arcface.forward().reshape(-1).astype(float).tolist()
        return _unit_vector(embedding)

    def clip_face(self, path: Path) -> list[float] | None:
        """Legacy whole-visit clips lack a person anchor; they cannot supply identity evidence."""
        return None

    def putdown_face(self, buffer: Path, at: float, jpeg: bytes, person_box: Sequence[float],
                     outfit: list[float]) -> list[float] | None:
        """Look six seconds either side, requiring the same body and outfit in every used frame.

        An unrelated lone face elsewhere in a visit is not the handler. At most six nearby frames
        are examined, with gaps, overlapping people and conflicting faces causing abstention.
        """
        from .putdown import photos, segments

        frames = [frame for segment in segments(buffer) if segment[0] <= at + 6 and segment[1] >= at - 6
                  for frame in photos(segment) if 0.5 < abs(frame[0] - at) <= 6]
        chosen = {}
        for offset in (-6, -4, -2, 2, 4, 6):
            frame = min(frames, key=lambda f: abs(f[0] - at - offset), default=None)
            if frame and abs(frame[0] - at - offset) <= 1.2:
                chosen[frame[0]] = frame[1]
        faces = []
        # Include the anchor's face so a different person's clearer face cannot replace it.
        anchor = self._person_crop(self._image(jpeg), person_box)
        face = self.sighting_face(anchor)
        if face is not None:
            faces.append(face)
        for frame in chosen.values():
            people = self.people(frame)
            candidates = []
            for person in people:
                if box_iou(person_box, person["box"]) < 0.35:
                    continue
                # Overlapping bodies defeat a crop-based association; do not choose the larger face.
                if any(other is not person and box_iou(other["box"], person["box"]) > 0.15 for other in people):
                    continue
                crop = self._person_crop(self._image(frame), person["box"])
                vector = self._appearance(crop, ((0.12, 0.55), (0.55, 1.0)), 256)
                if len(vector) == len(outfit):
                    candidates.append((sum(a * b for a, b in zip(vector, outfit)), crop))
            candidates.sort(key=lambda c: c[0], reverse=True)
            if not candidates or candidates[0][0] < 0.94 or (len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.055):
                continue
            face = self.sighting_face(candidates[0][1])
            if face is not None:
                if any(sum(a * b for a, b in zip(face, previous)) < 0.55 for previous in faces):
                    return None
                faces.append(face)
        return _unit_vector([sum(values) for values in zip(*faces)]) if faces else None

    @staticmethod
    def _person_crop(image: Any, box: Sequence[float]) -> Any:
        height, width = image.shape[:2]
        if len(box) != 4 or not all(math.isfinite(v) for v in box):
            raise VisionError("Invalid person box.")
        x, y, w, h = box
        if w <= 0 or h <= 0:
            raise VisionError("Invalid person box.")
        crop = image[max(0, int(y * height)):min(height, int((y + h) * height)),
                     max(0, int(x * width)):min(width, int((x + w) * width))]
        if crop.size == 0 or crop.shape[0] < 32 or crop.shape[1] < 16:
            raise VisionError("The person is too small to describe.")
        return crop

    def sighting_face(self, crop: Any) -> list[float] | None:
        """The face of the person in a sighting crop, if one is clear enough to compare.

        The camera looks down, so the face sits in the upper half of the body box and is often only
        50 px wide. Multiple faces are ambiguous; never select another person's larger face.
        """
        self._load_faces()
        cv = self._cv
        # At the bottom frame edge a person box can contain just head and shoulders. A fixed half
        # would cut that face in two. Width bounds the plausible head region of a truncated body.
        upper = crop[:max(1, int(max(crop.shape[0] * 0.55, crop.shape[1] * 0.9)))]
        scale = max(1.0, 640 / max(crop.shape[:2]))
        if scale > 1:
            upper = cv.resize(upper, None, fx=scale, fy=scale, interpolation=cv.INTER_CUBIC)
        if upper.shape[0] < 40 or upper.shape[1] < 40:
            return None
        self._sighting_faces.setInputSize((upper.shape[1], upper.shape[0]))
        _, faces = self._sighting_faces.detect(upper)
        usable = [f for f in ([] if faces is None else faces) if self._usable_sighting_face(upper, f, scale)]
        if len(usable) != 1:
            return None
        return self._embedding(upper, usable[0])

    def _usable_sighting_face(self, head: Any, face: Any, scale: float = 1.0) -> bool:
        """Reject hand-shaped false detections, profiles and dark patches inside the head crop."""
        if len(face) != 15 or not self._np.isfinite(face).all() or face[-1] < 0.85:
            return False
        x, y, w, h = map(float, face[:4])
        if min(w, h) < 40 * scale or x < 0 or y < 0 or x + w > head.shape[1] or y + h > head.shape[0]:
            return False
        points = self._np.asarray(face[4:14]).reshape(5, 2)
        if any(not (x <= px <= x + w and y <= py <= y + h) for px, py in points):
            return False
        eyes = sorted(points[:2], key=lambda p: p[0])
        mouth = sorted(points[3:], key=lambda p: p[0])
        nose = points[2]
        eye_gap = eyes[1][0] - eyes[0][0]
        eye_y = (eyes[0][1] + eyes[1][1]) / 2
        mouth_y = (mouth[0][1] + mouth[1][1]) / 2
        if (eye_gap < w * 0.2 or abs(eyes[0][1] - eyes[1][1]) > h * 0.2
                or abs(nose[0] - (eyes[0][0] + eyes[1][0]) / 2) > eye_gap * 0.35
                or not eye_y < nose[1] < mouth_y or mouth_y - eye_y < h * 0.2
                or mouth[1][0] - mouth[0][0] < w * 0.15):
            return False
        patch = head[int(y):int(y + h), int(x):int(x + w)]
        grey = self._cv.cvtColor(patch, self._cv.COLOR_BGR2GRAY)
        return bool(grey.mean() >= 40 and grey.std() >= 12)

    def enroll(self, resident_id: int, jpeg: bytes) -> int:
        _resident_id(resident_id)
        try:
            if self.count_enrollment(resident_id) >= MAX_ENROLLMENT_SAMPLES:
                raise VisionError(f"Enrollment is limited to {MAX_ENROLLMENT_SAMPLES} samples per resident.")
            image = self._image(jpeg)
            faces = self._faces(image)
            if len(faces) != 1 or not self._usable_face(faces[0]):
                raise VisionError("Enrollment needs exactly one clearly visible face, at least 60 pixels wide and tall.")
            count = self._store.append(resident_id, self._embedding(image, faces[0]))
            self._consensus.reset()
            return count
        except VisionError:
            raise
        except Exception as error:
            raise VisionError("Local face enrollment failed. Check the image, face models, and writable local data directory.") from error

    @staticmethod
    def _limit_inference_threads(predictor: Any) -> None:
        import cv2
        import torch
        # YOLO resets CPU threads during predictor setup. Apply the limit after
        # that setup, including each lazily loaded crop/teacher model. Two threads
        # used less CPU and ran faster on saved Jetson frames than six.
        if predictor.device.type == "cpu" and torch.get_num_threads() > 2:
            torch.set_num_threads(2)
        if cv2.getNumThreads() > 2:
            cv2.setNumThreads(2)

    @staticmethod
    def _open_detector(YOLO: Any, path: Path) -> Any:
        detector = YOLO(str(path.resolve()), task="detect")
        names = detector.names
        labels = {str(name).lower() for name in (names.values() if isinstance(names, dict) else names)}
        missing = set(KINDS) - labels
        if missing:
            raise VisionError(f"Detector vocabulary missing in {path.name}: " + ", ".join(sorted(missing)) + ". Prepare the dish vocabulary or supply matching custom weights.")
        detector.add_callback("on_predict_start", LocalVision._limit_inference_threads)
        return detector

    def _imgsz(self) -> int:
        return int(os.getenv("WASH_DISHES_IMGSZ", "1280" if self._device.startswith("cuda") else "640"))

    def _detect(self, image: Any, offset: Sequence[float] = (0, 0, 1, 1), *, dishes: bool = False, detail: bool = False) -> list[dict[str, Any]]:
        """Detections in normalized full-image coordinates; offset is the crop's normalized x/y/width/height."""
        detector, path = (self._dish_detector, self.dish_model_path) if dishes and self._dish_detector is not None else (self._detector, self.model_path)
        if detail:
            if self._detail_detector is None:
                self._detail_detector = self._open_detector(_import_yolo_offline(self.root / "vision-settings"), self.detail_model_path)
            detector, path = self._detail_detector, self.detail_model_path
        # FP16 roughly halves GPU inference for .pt checkpoints; TensorRT engines carry their own precision.
        precision = {"quantize": 16} if self._device.startswith("cuda") and path.suffix.lower() == ".pt" else {}
        imgsz = self._imgsz()
        if detail:
            imgsz = min(imgsz, getattr(detector, "overrides", {}).get("imgsz", 640))
        elif dishes:
            # Small contextual crops lost pans when stretched to the full-frame
            # size. Respect both the checkpoint's training size and crop detail.
            trained_size = getattr(detector, "overrides", {}).get("imgsz", imgsz)
            imgsz = min(imgsz, trained_size, 32 * math.ceil(max(image.shape[:2]) / 32))
        result = detector.predict(source=image, device=self._device, imgsz=imgsz, conf=0.25, max_det=128, agnostic_nms=True, verbose=False, **precision)[0]
        detections = []
        if result.boxes is None:
            return detections
        height, width = image.shape[:2]
        ox, oy, ow, oh = offset
        for raw_box, score, class_id in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()):
            kind = str(result.names[int(class_id)]).lower()
            if kind not in KINDS or not math.isfinite(score) or len(raw_box) != 4 or not all(math.isfinite(v) for v in raw_box):
                continue
            x1, y1, x2, y2 = raw_box
            x1, x2 = ox + ow * max(0.0, min(1.0, x1 / width)), ox + ow * max(0.0, min(1.0, x2 / width))
            y1, y2 = oy + oh * max(0.0, min(1.0, y1 / height)), oy + oh * max(0.0, min(1.0, y2 / height))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append({"kind": kind, "score": max(0.0, min(1.0, float(score))), "box": [x1, y1, x2 - x1, y2 - y1]})
        return detections

    def _track_people(self, people: list[dict[str, Any]], width: int, height: int) -> None:
        """ByteTrack keeps a walking person's identity through brief misses; IDs never imply who it is."""
        if self._people is None:
            from ultralytics.trackers.byte_tracker import BYTETracker
            from ultralytics.utils import IterableSimpleNamespace
            self._people = BYTETracker(IterableSimpleNamespace(tracker_type="bytetrack", track_high_thresh=0.35, track_low_thresh=0.1, new_track_thresh=0.4,
                                                               track_buffer=20, match_thresh=0.8, fuse_score=True))
        import numpy as np
        xyxy = np.array([[d["box"][0] * width, d["box"][1] * height, (d["box"][0] + d["box"][2]) * width, (d["box"][1] + d["box"][3]) * height] for d in people], dtype=np.float32)
        conf = np.array([d["score"] for d in people], dtype=np.float32)
        for detection in people:
            detection["track_id"] = None
        for row in self._people.update(_PersonBoxes(np, xyxy, conf)):
            index = int(row[-1])
            if 0 <= index < len(people):
                people[index]["track_id"] = 1_000_000 + int(row[4])

    def analyze(self, jpeg: bytes, *, focus: Sequence[float] | None = None) -> dict[str, Any]:
        try:
            image = self._image(jpeg)
            self._load_detector()
            self.last_image = image
            height, width = image.shape[:2]
            detections = self._detect(image)
            if focus is not None:
                fx, fy, fw, fh = focus
                left, top = int(fx * width), int(fy * height)
                right, bottom = int(math.ceil((fx + fw) * width)), int(math.ceil((fy + fh) * height))
                # Only worth a second pass when the crop is meaningfully smaller than the frame.
                if (right - left) * (bottom - top) < 0.7 * width * height:
                    crop = image[top:bottom, left:right]
                    focused = [d for d in self._detect(crop, (left / width, top / height, (right - left) / width, (bottom - top) / height)) if d["kind"] != "person"]
                    detections = merge_focus([d for d in detections if d["kind"] != "person"], focused, focus) + [d for d in detections if d["kind"] == "person"]
            people = [d for d in detections if d["kind"] == "person"]
            self._track_people(people, width, height)
            candidate, score, eligible = None, None, False
            if len(people) == 1:
                # Faces are only usable for a lone person, so search inside that person's box rather than the whole frame.
                x, y, w, h = people[0]["box"]
                left, top = int(x * width), int(y * height)
                region = image[top:int(math.ceil((y + h) * height)), left:int(math.ceil((x + w) * width))]
                faces = self._faces(region) if region.shape[0] >= 60 and region.shape[1] >= 60 else []
                eligible = len(faces) == 1 and self._usable_face(faces[0])
                # Matching needs enrolled faces; without any, an ArcFace pass per frame would only slow analysis.
                if eligible and self._store.all():
                    candidate, score = match_identity(self._embedding(region, faces[0]), self._store.all())
            resident = self._consensus.observe(candidate, eligible=eligible)
            self.last_detections = [d for d in detections if d["kind"] != "person"]
            return {"detections": detections, "resident_id": resident, "identity_score": score, "person_count": len(people), "size": [width, height]}
        except Exception as error:
            self.reset_observations()
            if isinstance(error, VisionError):
                raise
            raise VisionError("Local inference failed. Check model compatibility and the selected vision device.") from error

    def detect_region(self, box: Sequence[float], margin: float = 0.5, *, detail: bool = False) -> list[dict[str, Any]]:
        """Dishes in one part of the last analyzed frame, at full resolution, in full-frame coordinates."""
        image = self.last_image
        if image is None:
            return []
        height, width = image.shape[:2]
        x, y, w, h = box
        left, top = max(0, int((x - margin * w) * width)), max(0, int((y - margin * h) * height))
        right, bottom = min(width, int(math.ceil((x + w + margin * w) * width))), min(height, int(math.ceil((y + h + margin * h) * height)))
        if right - left < 16 or bottom - top < 16:
            return []
        offset = (left / width, top / height, (right - left) / width, (bottom - top) / height)
        focused = [d for d in self._detect(image[top:bottom, left:right], offset, dishes=True, detail=detail) if d["kind"] != "person"]
        full = [d for d in self.last_detections
                if offset[0] <= d["box"][0] + d["box"][2] / 2 <= offset[0] + offset[2]
                and offset[1] <= d["box"][1] + d["box"][3] / 2 <= offset[1] + offset[3]]
        return merge_detections(focused + full)

    def detect_area(self, area: dict) -> list[dict[str, Any]]:
        found = self.detect_region(area["box"])
        # Nested sink cookware needed a tighter, stronger view. Keep that slower
        # pass off the per-frame path; never download a missing optional model.
        if area["role"] == "sink" and self.detail_model_path.is_file():
            # The caller filters detail results to this area before global NMS,
            # so a detail false positive elsewhere cannot suppress a primary box.
            found += [d | {"detail_area": area["id"]}
                      for d in self.detect_region(area["box"], margin=0.1, detail=True)]
        return found

    # Appearance: who handled a dish, learned from human-confirmed examples.

    def _embedding_model(self) -> Any:
        if self._embedder is None:
            if self.model_path.suffix.lower() == ".pt":
                self._embedder = self._detector
            else:
                YOLO = _import_yolo_offline(self.root / "vision-settings")
                self._embedder = YOLO(str((self.root / "models" / DETECTOR_NAME).resolve()), task="detect")
        return self._embedder

    def _colour(self, image: Any, bands: Sequence[tuple[float, float]]) -> list[float]:
        """Hue/saturation histogram per horizontal band, plus a grey ramp for white, black and grey."""
        cv, np = self._cv, self._np
        hsv = cv.cvtColor(image, cv.COLOR_BGR2HSV)
        rows, colour = hsv.shape[0], []
        for top, bottom in bands:
            part = hsv[int(rows * top):max(int(rows * top) + 1, int(rows * bottom))]
            hue, sat, val = part[..., 0].ravel(), part[..., 1].ravel(), part[..., 2].ravel()
            vivid = (sat >= 50) & (val >= 40)
            hist = np.histogram2d(hue[vivid], sat[vivid], bins=(12, 3), range=((0, 180), (50, 256)))[0].ravel()
            grey = np.histogram(val[~vivid], bins=4, range=(0, 256))[0]
            combined = np.concatenate([hist, grey]).astype(float)
            colour.extend(np.sqrt(combined / max(1.0, combined.sum())).tolist())
        return _unit_vector(colour) if any(colour) else [0.0] * len(colour)

    def _appearance(self, crop: Any, bands: Sequence[tuple[float, float]], imgsz: int) -> list[float]:
        features = self._embedding_model().embed(source=[crop], imgsz=imgsz, device=self._device, verbose=False)[0]
        deep = _unit_vector(features.float().cpu().reshape(-1).tolist())
        return _unit_vector([0.75 * v for v in deep] + [0.66 * v for v in self._colour(crop, bands)])

    def people(self, jpeg: bytes) -> list[dict[str, Any]]:
        """Person detections in any saved picture, without touching live tracking state."""
        image = self._image(jpeg)
        self._load_detector()
        return [d for d in self._detect(image) if d["kind"] == "person"]

    def describe_person(self, jpeg: bytes, box: Sequence[float]) -> tuple[dict[str, list[float]], bytes]:
        """Identity cues for one sighting, plus a JPEG crop for review.

        outfit: the whole body (clothing), useful within a day. head: hair and head from above, which
        lasts. face: only when one is large and frontal enough. stature: where the top of the head was,
        comparable between sightings at the same area.
        """
        image = self._image(jpeg)
        cv = self._cv
        self._load_detector()
        x, y, w, h = box
        crop = self._person_crop(image, box)
        cues = {"outfit": self._appearance(crop, ((0.12, 0.55), (0.55, 1.0)), 256)}
        head = crop[:max(16, int(crop.shape[0] * 0.24))]
        if head.shape[0] >= 16 and head.shape[1] >= 16:
            cues["head"] = self._appearance(head, ((0.0, 0.5), (0.5, 1.0)), 128)
        face = self.sighting_face(crop)
        if face is not None:
            cues["face"] = face
        if y > 0.01:
            # A head cut off by the frame edge says nothing about height.
            cues["stature"] = [round(float(y), 4)]
        ok, encoded = cv.imencode(".jpg", crop, [cv.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise VisionError("Unable to encode the person crop.")
        return cues, encoded.tobytes()

    def enrolled_faces(self) -> dict[int, list[list[float]]]:
        return self._store.all()

    # Zone fullness: change against a photo of the zone when it was empty.

    def _zone_texture(self, image: Any, box: Sequence[float]) -> Any:
        cv, np = self._cv, self._np
        height, width = image.shape[:2]
        x, y, w, h = box
        crop = image[int(y * height):int((y + h) * height), int(x * width):int((x + w) * width)]
        grey = cv.cvtColor(crop, cv.COLOR_BGR2GRAY).astype(np.float32)
        scale = 160 / max(1, grey.shape[1])
        grey = cv.resize(grey, (160, max(8, int(grey.shape[0] * scale))), interpolation=cv.INTER_AREA)
        grey = cv.GaussianBlur(grey, (3, 3), 0)
        # Subtracting a wide blur removes slow lighting changes and keeps edges that dishes add.
        return grey - cv.GaussianBlur(grey, (0, 0), 9)

    def capture_reference(self, jpeg: bytes, area_id: str, box: Sequence[float]) -> None:
        image = self._image(jpeg)
        directory = self.root / "references"
        directory.mkdir(exist_ok=True, mode=0o700)
        self._np.save(directory / f"{area_id}.npy", self._zone_texture(image, box))
        self._references.pop(area_id, None)

    def has_reference(self, area_id: str) -> bool:
        return (self.root / "references" / f"{area_id}.npy").is_file()

    def occupancy(self, areas: list[dict], people: list[Sequence[float]]) -> dict[str, float | None]:
        """Fraction of each referenced zone that no longer looks like its empty photo; None when blocked."""
        if self.last_image is None:
            return {}
        np, result = self._np, {}
        for area in areas:
            path = self.root / "references" / f"{area['id']}.npy"
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
            cached = self._references.get(area["id"])
            if cached is None or cached[0] != mtime:
                cached = self._references[area["id"]] = (mtime, np.load(path))
            reference = cached[1]
            current = self._zone_texture(self.last_image, area["box"])
            if current.shape != reference.shape:
                result[area["id"]] = None
                continue
            changed = np.abs(current - reference) > 10
            mask = np.ones(changed.shape, dtype=bool)
            x, y, w, h = area["box"]
            for px, py, pw, ph in people:
                left, right = int((px - x) / w * mask.shape[1]), int((px + pw - x) / w * mask.shape[1])
                top, bottom = int((py - y) / h * mask.shape[0]), int((py + ph - y) / h * mask.shape[0])
                mask[max(0, top):max(0, bottom), max(0, left):max(0, right)] = False
            visible = mask.mean()
            result[area["id"]] = None if visible < 0.5 else round(float((changed & mask).sum() / max(1, mask.sum())), 3)
        return result
