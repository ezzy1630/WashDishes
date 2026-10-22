from types import SimpleNamespace

import pytest

from wash_dishes.vision import LocalVision


def test_face_quality_rejects_the_hand_dark_and_outside_person_failure_shapes(tmp_path):
    np = pytest.importorskip('numpy')
    cv = pytest.importorskip('cv2')
    engine = LocalVision(tmp_path)
    engine._cv, engine._np = cv, np
    head = np.random.default_rng(1).integers(50, 220, (120, 120, 3), dtype=np.uint8)
    # x,y,w,h; eyes, nose, mouth corners; score. All geometry in the anchored head crop.
    face = np.array([20, 15, 70, 90, 38, 45, 72, 45, 55, 64, 40, 85, 70, 85, .98], dtype=float)
    assert engine._usable_sighting_face(head, face)
    hand = face.copy()
    hand[4:14] = [30, 30, 33, 32, 80, 28, 27, 32, 30, 35]
    assert not engine._usable_sighting_face(head, hand)
    assert not engine._usable_sighting_face(np.zeros_like(head), face)
    outside = face.copy()
    outside[0] = 90
    assert not engine._usable_sighting_face(head, outside)
    profile = face.copy()
    profile[8] = 78
    assert not engine._usable_sighting_face(head, profile)
    assert not engine._usable_sighting_face(head, face, scale=2)
    low_score = face.copy()
    low_score[-1] = .75
    assert not engine._usable_sighting_face(head, low_score)


def test_unanchored_visit_clip_never_supplies_a_face(tmp_path):
    assert LocalVision(tmp_path).clip_face(tmp_path / 'whole-visit.mp4') is None


def test_nearby_face_search_ignores_frames_outside_six_seconds(tmp_path, monkeypatch):
    from wash_dishes import footage
    engine = LocalVision(tmp_path)
    monkeypatch.setattr(footage, 'segments', lambda _: [(0, 20, tmp_path / 'clip.ts')])
    monkeypatch.setattr(footage, 'photos', lambda _: [(2, b'early'), (18, b'late')])
    engine._image = lambda _: SimpleNamespace()
    engine._person_crop = lambda *_: object()
    engine.sighting_face = lambda _: None
    engine.people = lambda _: pytest.fail('Must not inspect an out-of-window frame')
    assert engine.putdown_face(tmp_path, 10, b'anchor', [.1, .1, .2, .5], [1, 0]) is None


def test_putdown_identity_uses_supplied_box_and_discards_stale_training_on_failure(store, tmp_path):
    import threading

    from wash_dishes.runtime import Runtime
    from wash_dishes.store import utcnow

    at = utcnow()
    item = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], candidate=True, at=at)
    store.add_person_sample(item['id'], [0, 1], at=at)
    alex = store.add_resident('Alex')['id']
    store.action(item['id'], 'confirm', resident_id=alex)
    chosen = [.3, .2, .2, .6]
    seen = []

    def describe(jpeg, box):
        seen.append(box)
        return {'outfit': [1, 0]}, b'\xff\xd8crop'

    engine = SimpleNamespace(describe_person=describe, enrolled_faces=lambda: {}, putdown_face=lambda *args: None,
                             people=lambda _: pytest.fail('Supplied reachable person must be used'))
    def save(oid, crop, label, *, at, capture):
        assert capture['person_box'] == chosen
        store.add_evidence(oid, 'putdown', 'putdown.jpg', 'snapshot', label, at=at, capture=capture)
        return 'putdown'

    runtime = Runtime.__new__(Runtime)
    runtime.lock = threading.RLock()
    runtime.local_vision = lambda: engine
    runtime.store = store
    runtime.media = SimpleNamespace(buffer=tmp_path, save_snapshot=save)
    assert runtime._identify_putdown(item['id'], b'picture', [.4, .6, .1, .1], at, person_box=chosen) == chosen
    assert seen == [chosen]
    assert store.sighting_cues(item['id'])[0] == {'outfit': [1, 0]}
    assert store.conn.execute('SELECT resident_id FROM person_samples').fetchone()[0] is None
    engine.describe_person = lambda *_: (_ for _ in ()).throw(ValueError('unusable crop'))
    runtime._identify_putdown(item['id'], b'picture', [.4, .6, .1, .1], at, person_box=chosen)
    assert store.sighting_cues(item['id']) is None
    assert store.obligation(item['id'])['suggested_resident_id'] is None
