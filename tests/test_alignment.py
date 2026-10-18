import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from wash_dishes.alignment import REANCHOR_SECONDS, STABLE_READINGS, ViewAlignment, shift_areas  # noqa: E402


def scene(dx=0, dy=0, brightness=1.0, seed=3):
    rng = np.random.default_rng(seed)
    base = cv2.GaussianBlur(rng.integers(0, 255, (400, 720), dtype=np.uint8), (0, 0), 3)
    for _ in range(40):
        x, y = rng.integers(0, 680), rng.integers(0, 360)
        cv2.rectangle(base, (int(x), int(y)), (int(x) + 40, int(y) + 30), int(rng.integers(0, 255)), -1)
    moved = np.roll(np.roll(base, dy, axis=0), dx, axis=1)
    return cv2.imencode(".jpg", np.clip(moved * brightness, 0, 255).astype(np.uint8))[1].tobytes()


def test_measures_a_small_nudge_under_different_light(tmp_path):
    view = ViewAlignment(tmp_path / "ref.npy")
    assert view.set_reference(scene())
    dx, dy, response = view.measure(scene(dx=-14, dy=10, brightness=0.6))
    assert dx == pytest.approx(-14 / 720, abs=0.003) and dy == pytest.approx(10 / 400, abs=0.003) and response > 0.05


def test_follows_a_confirmed_nudge_and_reports_a_large_move(tmp_path):
    view = ViewAlignment(tmp_path / "ref.npy")
    view.set_reference(scene())
    nudged = scene(dx=12, dy=-8)
    changes = [view.observe(nudged, now=2 * n) for n in range(STABLE_READINGS)]
    assert not any(changes[:-1]) and changes[-1] == pytest.approx((12 / 720, -8 / 400), abs=0.003) and view.settled
    assert ViewAlignment(tmp_path / "ref.npy").has_reference
    moved = scene(dx=120, dy=60)
    for n in range(STABLE_READINGS):
        view.observe(moved, now=100 + 2 * n)
    assert view.state == "moved" and not view.settled


def test_people_in_view_and_an_unreadable_view_never_move_or_pause_the_zones(tmp_path):
    view = ViewAlignment(tmp_path / "ref.npy")
    view.set_reference(scene())
    # Someone standing still in front of the camera reads as a steady shift; it is not measured.
    for n in range(10):
        assert view.observe(scene(dx=25, dy=10), now=2 * n, people=1) is None
    assert view.offset == (0.0, 0.0) and view.settled
    # Lights off: nothing readable for a long time. Tracking carries on, then re-anchors in place.
    blank = cv2.imencode(".jpg", np.zeros((400, 720), np.uint8))[1].tobytes()
    for now in range(100, 100 + REANCHOR_SECONDS + 10, 2):
        view.observe(blank, now=now)
        assert view.settled
    assert view.reanchors == 1 and view.offset == (0.0, 0.0)



def test_one_stray_reading_as_the_lights_go_out_does_not_pause_tracking(tmp_path):
    view = ViewAlignment(tmp_path / "ref.npy")
    view.set_reference(scene())
    # The camera switching to infrared gives one readable but wrong frame, then nothing readable.
    view.observe(scene(dx=25, dy=10), now=0)
    assert not view.settled
    blank = cv2.imencode(".jpg", np.zeros((400, 720), np.uint8))[1].tobytes()
    view.observe(blank, now=2)
    assert view.settled and view.offset == (0.0, 0.0)

def test_keeps_its_place_after_dark_when_the_daytime_reference_stops_matching(tmp_path):
    day = scene()
    night = lambda dx=0, dy=0: scene(dx, dy, seed=7)  # noqa: E731 - same kitchen, different light
    dusk = cv2.imencode(".jpg", cv2.addWeighted(cv2.imdecode(np.frombuffer(day, np.uint8), 0), 0.5,
                                                cv2.imdecode(np.frombuffer(night(), np.uint8), 0), 0.5, 0))[1].tobytes()
    view = ViewAlignment(tmp_path / "ref.npy")
    view.set_reference(day)
    for n in range(STABLE_READINGS):
        view.observe(dusk, now=2 * n)
    daytime_only = ViewAlignment(tmp_path / "day.npy")
    daytime_only.set_reference(day)
    assert daytime_only.measure(night())[2] < 0.05  # the reference alone cannot read the night view
    for now in range(20, 400, 2):
        view.observe(night(), now=now)
    assert view.state == "aligned" and view.settled and view.reanchors == 0
    # A restart at night picks up where it was. A shift only the anchor can read never moves the
    # zones: that is how spurious anchor readings walked them 12% off on 2026-10-14.
    view = ViewAlignment(tmp_path / "ref.npy")
    assert view.settled
    assert not any(view.observe(night(12, -8), now=2 * n) for n in range(3 * STABLE_READINGS))
    assert view.offset == (0.0, 0.0)


def test_a_wrong_saved_offset_is_corrected_by_the_reference(tmp_path):
    view = ViewAlignment(tmp_path / "ref.npy")
    view.set_reference(scene())
    view.offset = (-0.006, 0.097)
    view._set_anchor(ViewAlignment.features(scene()), 0)
    view = ViewAlignment(tmp_path / "ref.npy")
    assert view.offset == (-0.006, 0.097)
    changes = [view.observe(scene(), now=2 * n) for n in range(STABLE_READINGS)]
    assert changes[-1] == pytest.approx((0.006, -0.097), abs=0.003)
    assert view.offset == pytest.approx((0.0, 0.0), abs=0.003) and view.settled


def test_areas_shift_but_stay_inside_the_picture():
    areas = [{"id": "a", "box": [0.01, 0.5, 0.2, 0.2]}]
    assert shift_areas(areas, (-0.05, 0.02))[0]["box"] == pytest.approx([0.0, 0.52, 0.2, 0.2])
    assert shift_areas(areas, (0.0, 0.0)) is areas
