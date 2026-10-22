import json
import threading
from dataclasses import replace

import numpy as np
import pytest
from test_proofs import late_dish
from test_sequences import jpeg

from wash_dishes.proof_checks import ProofChecker, Thresholds, colour_similarity, crop_change, make_check
from wash_dishes.proofs import Proofs

BOX = [.4, .45, .15, .15]
PERSON = [.15, .15, .3, .7]
HIDDEN = [.35, .3, .3, .6]
BEFORE, PUTTING, LEFT, LATE = [jpeg(c) for c in ((0, 0, 200), (0, 200, 0), (200, 0, 0), (160, 0, 0))]
FOUND = {k: {"jpeg": raw, "at": at} for k, raw, at in zip(("before", "putting", "left"), (BEFORE, PUTTING, LEFT), (10, 20, 30))}
SHADED = jpeg((170, 0, 30))  # crop change .31 from LEFT: the same spot, a little different


class Verifier:
    def __init__(self):
        self.vectors = np.array([[0., 1.], [1., 0.], [1., 0.]])
        self.scored = (.9, True)

    def embed(self, crops):
        return self.vectors

    def features(self, image, box):
        return self.vectors[1]

    def score(self, vector):
        return self.scored


@pytest.fixture
def checker():
    detections = {PUTTING: [{"kind": "person", "box": PERSON}]}
    verifier = Verifier()
    checker = ProofChecker(lambda raw, box: detections.get(raw, []), verifier,
                           lambda _: {"label": "soda can", "margin": -.1})
    return checker, detections, verifier


def run(checker, **kwargs):
    return checker(1, kwargs.get("found", FOUND), kwargs.get("late", LATE), BOX, kwargs.get("late_box"))


def test_valid_sequence_retains_diagnostics_and_visible_person(checker):
    check, _, _ = checker
    result = run(check)
    assert result["putting"]["person_box"] == PERSON
    assert result["checks"]["late"]["colour"] > .95
    assert result["checks"]["left_embedding"] == [1., 0.]


@pytest.mark.parametrize("score", [(.2, False), None])
def test_dish_probe_rejection_and_unavailable_probe_do_not_pass(checker, score):
    check, _, verifier = checker
    verifier.scored = score
    assert isinstance(run(check), str)


def test_zero_shot_negative_can_veto_probe(checker):
    check, _, _ = checker
    check.negatives = lambda _: {"label": "soda can", "margin": .2}
    assert run(check) == "looks more like a soda can than a dish"


def test_clean_mat_excludes_dish_but_dirty_surface_does_not(checker):
    check, _, _ = checker
    area = {"id": "mat", "role": "clean", "box": BOX}
    check.drying_areas = lambda: [area]
    assert run(check) == "dish is in a drying or storage area"
    area["role"] = "dirty"
    assert isinstance(run(check), dict)


def test_drying_area_outline_does_not_exclude_neighboring_sink(checker):
    check, _, _ = checker
    area = {"id": "mat", "role": "clean", "box": [.3, .3, .5, .5],
            "outline": [[.6, .6], [.8, .6], [.8, .8], [.6, .8]]}
    check.drying_areas = lambda: [area]
    assert isinstance(run(check), dict)
    result = check.evaluate(1, FOUND, LATE, BOX, [.65, .65, .1, .1])
    assert result["reason"] == "dish is in a drying or storage area"
    assert result["checks"]["excluded_area"] == {"id": "mat", "stage": "late"}


def test_before_dish_vetoes_unless_the_spot_changed_a_lot(checker):
    # A pan set down on a bowl already in the sink is a new arrival; the same bowl in new light is not.
    check, detections, _ = checker
    detections[BEFORE] = detections[SHADED] = [{"kind": "bowl", "box": BOX}]
    assert isinstance(run(check), dict)
    assert run(check, found=FOUND | {"before": {"at": 10, "jpeg": SHADED}}) == "dish already there in the before picture"


def test_a_pan_held_over_the_spot_is_not_a_clear_before(checker):
    # Held over a bowl already there, the pan changes the spot as much as a new arrival does.
    check, detections, _ = checker
    detections[BEFORE] = [{"kind": "pot", "box": BOX}, {"kind": "person", "box": PERSON}]
    assert run(check) == "someone is handling something over the spot in the before picture"
    detections[BEFORE] = [{"kind": "person", "box": PERSON}]
    assert isinstance(run(check), dict)


def test_large_before_dish_cannot_hide_an_empty_spot_behind_low_iou(checker):
    check, detections, _ = checker
    detections[SHADED] = [{"kind": "pan", "box": [.2, .2, .6, .6]}]
    assert run(check, found=FOUND | {"before": {"at": 10, "jpeg": SHADED}}) == "dish already there in the before picture"


@pytest.mark.parametrize("cosine, changed, reason", [
    (1., True, "dish already there in the before picture"),
    (.87, False, "dish already there in the before picture"),
    (.87, True, None),
])
def test_semantically_same_before_crop_is_not_a_new_arrival(checker, cosine, changed, reason):
    check, _, verifier = checker
    verifier.vectors[0] = [cosine, (1 - cosine**2) ** .5]
    found = FOUND if changed else FOUND | {"before": {"at": 10, "jpeg": SHADED}}
    result = run(check, found=found)
    assert result == reason if reason else isinstance(result, dict)


def test_before_unchanged_crop_vetoes_even_when_detector_misses(checker):
    check, _, _ = checker
    found = FOUND | {"before": {"at": 10, "jpeg": LEFT}}
    assert run(check, found=found) == "dish already there in the before picture"


def test_hidden_before_cannot_establish_absence(checker):
    check, detections, _ = checker
    detections[BEFORE] = [{"kind": "person", "box": HIDDEN}]
    assert run(check) == "the spot is hidden in the before picture"


@pytest.mark.parametrize("person", [None, [.0, .0, .1, .1], HIDDEN])
def test_person_must_be_within_reach_and_not_hide_dish(checker, person):
    check, detections, _ = checker
    detections[PUTTING] = [] if person is None else [{"kind": "person", "box": person}]
    assert "no picture shows a person" in run(check)


def test_candidate_replaces_head_occlusion_and_ignores_unrelated_times(checker):
    check, detections, _ = checker
    detections[PUTTING] = [{"kind": "person", "box": HIDDEN}]
    better = jpeg((0, 150, 0))
    detections[better] = [{"kind": "person", "box": PERSON}, {"kind": "bowl", "box": BOX}]
    found = FOUND | {"candidates": [{"at": 999, "jpeg": better}, {"at": 25, "jpeg": better}]}
    result = run(check, found=found)
    assert result["putting"] == {"at": 25, "jpeg": better, "person_box": PERSON}


def test_two_possible_handlers_are_not_arbitrarily_assigned(checker):
    check, detections, _ = checker
    detections[PUTTING].append({"kind": "person", "box": [.55, .15, .3, .7]})
    assert isinstance(run(check), str)


def test_shoulder_edge_requires_visible_dish_and_can_be_rescued_by_candidate(checker):
    check, detections, _ = checker
    # Outside the central torso estimate, but entirely inside the person's rectangle.
    person = [.39, .2, .5, .7]
    detections[PUTTING] = [{"kind": "person", "box": person}]
    assert "no picture shows a person" in run(check)
    better = jpeg((0, 130, 0))
    detections[better] = [*detections[PUTTING], {"kind": "bowl", "box": BOX}]
    result = run(check, found=FOUND | {"candidates": [{"at": 25, "jpeg": better}]})
    assert result["putting"]["jpeg"] == better
    assert result["checks"]["putting"]["hidden_frames"] == 1


def test_different_late_embedding_rejected(checker):
    check, _, verifier = checker
    verifier.vectors[2] = [0., 1.]
    assert run(check) == "a different dish is in that spot later"


def test_same_embedding_different_colour_rejected(checker):
    check, _, _ = checker
    assert run(check, late=BEFORE) == "a different dish is in that spot later"


def test_busy_late_waits_instead_of_rejecting_an_occluded_crop(checker):
    check, detections, verifier = checker
    detections[LATE] = [{"kind": "person", "box": HIDDEN}]
    verifier.vectors[2] = [0., 1.]
    result = run(check)
    assert result["status"] == "waiting"
    assert result["checks"]["late"]["person_at_dish"]
    detections[LATE] = []
    verifier.vectors[2] = [1., 0.]
    assert "status" not in run(check)


def test_late_box_is_used_for_crop_and_detector(checker):
    check, _, _ = checker
    boxes = []
    original = check.detect
    check.detect = lambda jpeg, box: boxes.append(box) or original(jpeg, box)
    late_box = [.6, .7, .15, .15]
    run(check, late_box=late_box)
    assert boxes[-1] == late_box


def test_brightness_is_not_an_arrival_or_a_colour_mismatch():
    a = np.full((10, 10, 3), [40, 50, 70], np.uint8)
    assert crop_change(a, a*2) < .01
    assert colour_similarity(a, a*2) > .9


def test_rejection_memory_survives_restart_but_not_different_spot_or_object(store, media, checker):
    check, _, verifier = checker
    check.store = store
    item = late_dish(store, media)
    proofs = Proofs(store.root, store, media)
    proofs._save(item["id"], "ready", {"box": BOX, "checks": {"left_embedding": [1., 0.]}})
    proofs.reject(item["id"], "not_a_dish")
    assert store.obligation(item["id"])["lifecycle"] == "dismissed"
    new = late_dish(store, media)
    restarted = ProofChecker(check.detect, verifier, check.negatives, store)
    assert "marked not a dish at" in restarted(new["id"], FOUND, LATE, BOX, None)
    restarted.thresholds = replace(Thresholds(), remembered_cosine=1.01)
    assert isinstance(restarted(new["id"], FOUND, LATE, BOX, None), dict)
    assert check._remembered(new["id"], np.array([0., 1.]), BOX) is None
    assert check._remembered(new["id"], np.array([1., 0.]), [.7, .7, .1, .1]) is None
    with store.lock, store.conn:
        store.conn.execute("UPDATE obligations SET location='Another counter' WHERE id=?", (new["id"],))
    assert check._remembered(new["id"], np.array([1., 0.]), BOX) is None


def test_wrong_photos_memory_does_not_teach_that_the_dish_is_not_a_dish(store, media, checker):
    check, _, _ = checker
    item = late_dish(store, media)
    proofs = Proofs(store.root, store, media)
    proofs._save(item["id"], "ready", {"box": BOX, "checks": {"left_embedding": [1., 0.]}})
    proofs.reject(item["id"])
    assert store.obligation(item["id"])["lifecycle"] == "open"
    check.store = store
    assert isinstance(run(check), dict)
    row = store.conn.execute('SELECT * FROM proof_rejections').fetchone()
    assert row['reason'] == 'wrong_photos' and json.loads(row['embedding']) == [1., 0.]


def test_factory_reuses_engine_and_verifier_without_live_tracking(store, checker):
    check, _, verifier = checker
    class Engine:
        def _image(self, jpeg):
            return np.zeros((100, 100, 3), np.uint8)

        def _load_detector(self):
            pass

        def _detect(self, image, offset=None):
            return [{"kind": "person", "box": PERSON}] if offset is None else []
    shared = Engine()
    result = make_check(shared, verifier, store, lock=threading.RLock())
    assert result.verifier is verifier
    assert result.detect(LEFT, BOX) == [{"kind": "person", "box": PERSON}]
    mat = {"id": "mat", "role": "clean", "box": BOX}
    shared_areas = make_check(shared, verifier, store, lock=threading.RLock(), drying_areas=lambda: [mat])
    assert mat in shared_areas.drying_areas()
    assert any(a["role"] == "clean" for a in result.drying_areas())


@pytest.mark.parametrize("box", [[0, 0, 0, .1], [-.1, 0, .1, .1], [0, 0, float('nan'), .1]])
def test_invalid_boxes_fail_closed(checker, box):
    check, _, _ = checker
    assert isinstance(check(1, FOUND, LATE, box, None), str)


def test_corrupt_picture_is_a_reason_not_a_worker_stopping_exception(checker):
    check, _, _ = checker
    assert run(check, late=b'not a jpeg') == 'could not read a proof picture'
    corrupt = FOUND | {'putting': {'at': 20, 'jpeg': b'not a jpeg'}}
    assert 'no picture shows a person' in run(check, found=corrupt)
    assert isinstance(run(check, found=corrupt | {'candidates': [FOUND['putting']]}), dict)
