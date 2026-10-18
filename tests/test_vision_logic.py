"""Meaningful offline regressions; no cameras, models, or third-party packages."""

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wash_dishes.model_setup import _download
from wash_dishes.vision import (
    MAX_ENROLLMENT_SAMPLES,
    SFACE_NAME,
    EnrollmentStore,
    IdentityConsensus,
    LocalVision,
    VisionError,
    box_iou,
    match_identity,
)


class IdentityTests(unittest.TestCase):
    def test_unknown_and_runner_up_margin(self):
        self.assertEqual(match_identity([1, 0], {}), (None, None))
        self.assertIsNone(match_identity([1, 0], {1: [[0, 1]]})[0])
        self.assertIsNone(match_identity([1, 0], {1: [[1, 0]], 2: [[0.98, 0.2]]})[0])
        self.assertEqual(match_identity([1, 0], {1: [[1, 0]], 2: [[0, 1]]})[0], 1)

    def test_dimension_change_and_invalid_vector(self):
        self.assertEqual(match_identity([1, 0], {1: [[1, 0, 0]]}), (None, None))
        for vector in ([], [0, 0], [math.nan, 1], [True, 1]):
            with self.assertRaises(VisionError):
                match_identity(vector, {})

    def test_consensus_cannot_cross_ambiguity_gaps_or_identity_change(self):
        consensus = IdentityConsensus(required_frames=3, max_gap_seconds=4)
        self.assertIsNone(consensus.observe(1, eligible=True, now=0))
        self.assertIsNone(consensus.observe(1, eligible=True, now=1))
        self.assertEqual(consensus.observe(1, eligible=True, now=2), 1)
        self.assertIsNone(consensus.observe(1, eligible=False, now=3))
        self.assertIsNone(consensus.observe(1, eligible=True, now=4))
        self.assertIsNone(consensus.observe(1, eligible=True, now=9))
        self.assertIsNone(consensus.observe(2, eligible=True, now=10))
        self.assertIsNone(consensus.observe(2, eligible=True, now=11))
        self.assertEqual(consensus.observe(2, eligible=True, now=12), 2)
        self.assertIsNone(consensus.observe(2, eligible=True, now=2))

    def test_invalid_analysis_resets_consensus_and_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            vision = LocalVision(Path(directory))
            for now in (0, 1, 2):
                vision._consensus.observe(1, eligible=True, now=now)
            with self.assertRaises(VisionError):
                vision.analyze(b"not a JPEG")
            self.assertIsNone(vision._consensus.observe(1, eligible=True, now=3))


class GeometryTests(unittest.TestCase):
    def test_iou(self):
        self.assertAlmostEqual(box_iou([0, 0, 1, 1], [0.5, 0, 1, 1]), 1 / 3)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = EnrollmentStore(self.root / "faces")
        self.vector = [1.0] + [0.0] * 127

    def test_private_normalized_bounded_persistence_and_clear(self):
        self.assertEqual(self.store.read(1), [])
        self.assertEqual(self.store.append(1, [2.0] + [0.0] * 127), 1)
        self.assertEqual(self.store.read(1), [self.vector])
        self.assertEqual(os.stat(self.root / "faces/1.json").st_mode & 0o777, 0o600)
        for _ in range(MAX_ENROLLMENT_SAMPLES - 1):
            self.store.append(1, self.vector)
        with self.assertRaises(VisionError):
            self.store.append(1, self.vector)
        self.assertEqual(len(self.store.read(1)), MAX_ENROLLMENT_SAMPLES)
        self.store.clear(1)
        self.assertEqual(self.store.read(1), [])

    def test_atomic_failure_preserves_old_enrollment(self):
        self.store.append(1, self.vector)
        with patch("wash_dishes.vision.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.store.append(1, self.vector)
        self.assertEqual(self.store.read(1), [self.vector])
        self.assertEqual(list((self.root / "faces").glob(".enrollment-*")), [])

    def test_invalid_ids_and_corrupt_model_or_embedding_rejected(self):
        for resident in (0, -1, True, "../1"):
            with self.assertRaises(VisionError):
                self.store.read(resident)
        self.store.directory.mkdir()
        path = self.store.directory / "1.json"
        for payload in (
            {"version": 1, "model": "other", "vectors": [self.vector]},
            {"version": 1, "model": SFACE_NAME, "vectors": [[math.nan] * 128]},
            {"version": 1, "model": SFACE_NAME, "vectors": [[0] * 128]},
            [],
        ):
            path.write_text(json.dumps(payload))
            with self.assertRaises(VisionError):
                self.store.read(1)

    def test_models_and_optional_dependencies_are_lazy(self):
        vision = LocalVision(self.root)
        self.assertFalse(vision.status()["ready"])
        self.assertEqual(vision.count_enrollment(1), 0)
        self.assertIsNone(vision._detector)
        self.assertIsNone(vision._face_detector)

    def test_enrollment_rejects_multiple_or_unusable_faces(self):
        vision = LocalVision(self.root)
        vision._image = lambda jpeg: object()
        vision._embedding = lambda image, face: self.vector
        vision._usable_face = lambda face: face == "usable"
        for faces in ([], ["usable", "usable"], ["unusable"]):
            vision._faces = lambda image, faces=faces: faces
            with self.assertRaises(VisionError):
                vision.enroll(1, b"image handled by fake decoder")
            self.assertEqual(vision.count_enrollment(1), 0)
        vision._faces = lambda image: ["usable"]
        self.assertEqual(vision.enroll(1, b"image handled by fake decoder"), 1)


class AdapterTests(unittest.TestCase):
    def test_optional_detail_model_is_used_only_for_sink_areas(self):
        with tempfile.TemporaryDirectory() as directory:
            vision = LocalVision(Path(directory))
            calls = []
            dish = {"kind": "pan", "score": 0.8, "box": [0.3, 0.6, 0.1, 0.1]}

            def detect(box, margin=0.5, *, detail=False):
                calls.append((margin, detail))
                return [dish]

            vision.detect_region = detect
            sink = {"id": "sink", "role": "sink", "box": [0.2, 0.5, 0.3, 0.3]}
            self.assertEqual(vision.detect_area(sink), [dish])
            self.assertEqual(calls, [(0.5, False)])
            vision.detail_model_path.parent.mkdir()
            vision.detail_model_path.write_bytes(b"optional weights")
            calls.clear()
            self.assertEqual(vision.detect_area(sink), [dish, dish | {"detail_area": "sink"}])
            self.assertEqual(calls, [(0.5, False), (0.1, True)])
            calls.clear()
            vision.detect_area({**sink, "role": "dirty"})
            self.assertEqual(calls, [(0.5, False)])

    def test_detected_multiple_people_and_external_faces_cannot_assign_resident(self):
        with tempfile.TemporaryDirectory() as directory:
            vision = LocalVision(Path(directory))
            vector = [1.0] + [0.0] * 127
            vision._store.append(1, vector)
            import numpy
            vision._image = lambda jpeg: numpy.zeros((100, 100, 3), dtype=numpy.uint8)
            vision._load_detector = lambda: None
            face = [20, 20, 60, 60]
            vision._faces = lambda image: [face]
            vision._usable_face = lambda value: True
            vision._embedding = lambda image, value: vector

            class Values:
                def __init__(self, values):
                    self.values = values
                def cpu(self):
                    return self
                def tolist(self):
                    return self.values

            def set_people(boxes):
                result = SimpleNamespace(
                    names={0: "person"},
                    boxes=SimpleNamespace(xyxy=Values(boxes), conf=Values([0.9] * len(boxes)), cls=Values([0] * len(boxes))),
                )
                vision._detector = SimpleNamespace(predict=lambda **kwargs: [result])

            set_people([[0, 0, 100, 100]])
            self.assertIsNone(vision.analyze(b"fake JPEG")["resident_id"])
            self.assertIsNone(vision.analyze(b"fake JPEG")["resident_id"])
            self.assertEqual(vision.analyze(b"fake JPEG")["resident_id"], 1)
            set_people([[0, 0, 100, 100], [20, 20, 80, 80]])
            ambiguous = vision.analyze(b"fake JPEG")
            self.assertEqual(ambiguous["person_count"], 2)
            self.assertIsNone(ambiguous["resident_id"])
            self.assertIsNone(ambiguous["identity_score"])
            set_people([[0, 0, 10, 10]])
            self.assertIsNone(vision.analyze(b"fake JPEG")["resident_id"])
            set_people([[0, 0, 100, 100]])
            self.assertIsNone(vision.analyze(b"fake JPEG")["resident_id"])

    def test_a_kitchen_model_only_looks_at_areas(self):
        import numpy

        class Values(list):
            def cpu(self):
                return self
            def tolist(self):
                return list(self)

        def detector(kind):
            result = SimpleNamespace(names={0: kind}, boxes=SimpleNamespace(xyxy=Values([[10, 10, 30, 30]]), conf=Values([0.9]), cls=Values([0])))
            return SimpleNamespace(predict=lambda **kwargs: [result])

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "models").mkdir()
            (Path(directory) / "models" / "dishes-kitchen.pt").write_bytes(b"weights")
            vision = LocalVision(Path(directory))
            self.assertEqual(vision.dish_model_path.name, "dishes-kitchen.pt")
            vision._image = lambda jpeg: numpy.zeros((100, 100, 3), dtype=numpy.uint8)
            vision._load_detector = lambda: None
            vision._detector, vision._dish_detector = detector("person"), detector("plate")
            self.assertEqual([d["kind"] for d in vision.analyze(b"fake JPEG")["detections"]], ["person"])
            self.assertEqual([d["kind"] for d in vision.detect_region([0.0, 0.0, 1.0, 1.0])], ["plate"])
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(LocalVision(Path(directory)).dish_model_path)

    def test_region_keeps_a_full_frame_dish_when_the_crop_misses_it(self):
        import numpy

        with tempfile.TemporaryDirectory() as directory:
            vision = LocalVision(Path(directory))
            image = numpy.zeros((100, 100, 3), dtype=numpy.uint8)
            dish = {"kind": "pan", "score": 0.8, "box": [0.3, 0.6, 0.1, 0.1]}
            vision.last_image = image
            vision.last_detections = [dish]
            vision._detect = lambda *args, **kwargs: []
            self.assertEqual(vision.detect_region([0.2, 0.5, 0.3, 0.3]), [dish])
            self.assertEqual(vision.detect_region([0.7, 0.1, 0.1, 0.1]), [])


class ModelSetupTests(unittest.TestCase):
    def test_corrupt_download_cannot_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.onnx"
            target.write_bytes(b"old model")
            import io
            with patch("wash_dishes.model_setup.urlopen", return_value=io.BytesIO(b"corrupt")):
                with self.assertRaises(VisionError):
                    _download("https://example.invalid/model", target, "0" * 64, size=7)
            self.assertEqual(target.read_bytes(), b"old model")
            self.assertEqual(list(target.parent.glob(".model-download-*")), [])

    def test_verified_asset_reused_without_network(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.onnx"
            target.write_bytes(b"verified model")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            with patch("wash_dishes.model_setup.urlopen", side_effect=AssertionError("network")):
                self.assertEqual(_download("https://example.invalid/model", target, digest, size=14), target)


if __name__ == "__main__":
    unittest.main()
