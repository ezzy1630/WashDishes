import json
import shutil
import sqlite3
import subprocess

import pytest

from wash_dishes import putdown
from wash_dishes.journal import Journal
from wash_dishes.putdown import JOURNAL_TOO_NEW, Arrival, journal_arrival

BOX = [.4, .6, .08, .07]
DISH = {"kind": "pan", "score": .8, "box": BOX}
PERSON = {"kind": "person", "score": .8, "box": [.35, .2, .2, .6], "track_id": 1}


def frame(at, dish=False, people=()):
    return {"at": at, "detections": ([DISH] if dish else []) + list(people)}


def test_roundtrip_filters_and_rounds_without_retaining_face_features(tmp_path):
    path = tmp_path / "journal.db"
    with Journal(path) as journal:
        raw = DISH | {"box": [.412345, .6, .08, .07], "embedding": [1, 2]}
        assert journal.record(1, [raw, PERSON, DISH | {"score": .19}, DISH | {"kind": "bottle"}, DISH | {"box": [0, 0, -1, 1]}])
        result = list(journal.frames(0, 2))
        assert result == [{"at": 1, "detections": [{"kind": "pan", "score": .8, "box": [.4123, .6, .08, .07]}, PERSON]}]
        assert raw["box"][0] == .412345
    with Journal(path) as reopened:
        assert list(reopened.frames(0, 2)) == result
        reopened.close()  # Shutdown and the context manager may both close it.


def test_throttle_batches_and_keeps_empty_frames(tmp_path):
    path = tmp_path / "journal.db"
    with Journal(path, max_fps=4, batch_size=3) as journal:
        assert journal.record(100, [])
        assert not journal.record(100.1, [DISH])
        assert journal.record(100.25, [DISH])
        with sqlite3.connect(path) as reader:
            assert reader.execute("SELECT count(*) FROM frames").fetchone()[0] == 0
        assert journal.record(100.5, [])
        with sqlite3.connect(path) as reader:
            assert reader.execute("SELECT count(*) FROM frames").fetchone()[0] == 3
        assert list(journal.frames(100, 100.25))[0]["detections"] == []


def test_timed_flush_prune_restart_and_backfill_out_of_order(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr("wash_dishes.journal.time.monotonic", lambda: clock[0])
    path = tmp_path / "journal.db"
    with Journal(path) as journal:
        journal.record(7200, [DISH])
        clock[0] = 6
        journal.record(7201, [])
        assert not journal.pending
        journal.record(0, [])
        journal.record(3600, [])
        assert journal.prune(7200, 1) == 1
        assert journal.bounds() == (3600, 7201)
    with Journal(path) as journal:
        assert [f["at"] for f in journal.frames(0, 10000)] == [3600, 7200, 7201]


def test_crash_rollback_preserves_prior_batch(tmp_path):
    path = tmp_path / "journal.db"
    with Journal(path) as journal:
        journal.record(1, [])
        journal.flush()
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO frames VALUES(?,?)", (2, json.dumps([DISH])))
        conn.close()  # Simulate an uncommitted writer disappearing.
        assert [f["at"] for f in journal.frames(0, 3)] == [1]


def test_failed_writes_keep_a_bounded_retry_batch(tmp_path, monkeypatch):
    with Journal(tmp_path / "journal.db", batch_size=2) as journal:
        original = journal.flush
        def fail():
            raise sqlite3.OperationalError("disk full")
        monkeypatch.setattr(journal, "flush", fail)
        journal.record(1, [DISH])
        for at in range(2, 20):
            with pytest.raises(sqlite3.OperationalError):
                journal.record(at, [DISH])
            assert len(journal.pending) == 2
        monkeypatch.setattr(journal, "flush", original)
        journal.flush()
        assert [f["at"] for f in journal.frames(0, 20)] == [1, 2]


def test_read_snapshot_does_not_block_writer(tmp_path):
    with Journal(tmp_path / "journal.db") as journal:
        for at in range(10):
            journal.record(at, [])
        reading = journal.frames(0, 100)
        assert next(reading)["at"] == 0
        journal.record(11, [DISH])
        journal.flush()
        assert [f["at"] for f in reading] == list(range(1, 10))
        assert len(list(journal.frames(0, 100))) == 11


def test_arrival_tolerates_flicker_and_does_not_move_to_cooking():
    sequence = [frame(t, t >= 20 and t not in range(40, 46), [PERSON] if 70 <= t < 130 else []) for t in range(150)]
    arrival = journal_arrival(sequence, BOX, 149, family="vessel")
    assert isinstance(arrival, Arrival)
    assert arrival.before == 19 and arrival.appeared == 20 and arrival.clear == 20
    assert not arrival.left_behind


def test_final_continuous_presence_after_actual_removal():
    sequence = [frame(t, 10 <= t < 30 or t >= 60) for t in range(90)]
    arrival = journal_arrival(sequence, BOX, 89)
    assert isinstance(arrival, Arrival)
    assert arrival.before == 59 and arrival.appeared == 60


def test_class_flip_while_cooking_is_not_an_empty_spot():
    sequence = [frame(t, t >= 20) for t in range(150)]
    for item in sequence[40:90]:
        item["detections"] = [DISH | {"kind": "bowl"}]
    arrival = journal_arrival(sequence, BOX, 149, family="vessel")
    assert isinstance(arrival, Arrival) and arrival.appeared == 20


def test_patch_witness_bridges_detector_dropout_but_not_actual_removal():
    sequence = [frame(t, 20 <= t < 40 or t >= 90) for t in range(110)]
    unchanged = journal_arrival(sequence, BOX, 109, absence_is_clear=lambda _: False)
    removed = journal_arrival(sequence, BOX, 109, absence_is_clear=lambda _: True)
    assert unchanged.appeared == 20
    assert removed.appeared == 90
    assert "cannot resolve" in journal_arrival(sequence, BOX, 109, absence_is_clear=lambda _: None)


def test_person_occlusion_preserves_existing_dish():
    sequence = [frame(t, 10 <= t < 30 or t >= 400, [PERSON] if 30 <= t < 400 else []) for t in range(420)]
    arrival = journal_arrival(sequence, BOX, 419)
    assert isinstance(arrival, Arrival)
    assert arrival.appeared == 10
    assert not arrival.left_behind  # Cooking/obscuring an already-present pan is not a new arrival.


def test_long_initial_occlusion_is_departure_not_invented_putdown():
    sequence = [frame(t, t >= 400, [PERSON] if 20 <= t < 400 else []) for t in range(420)]
    arrival = journal_arrival(sequence, BOX, 419)
    assert isinstance(arrival, Arrival)
    assert arrival.before == 19 and arrival.appeared == 400
    assert arrival.left_behind and arrival.hidden_seconds == 383
    assert arrival.clear == 403  # Three seconds without the person confirms departure.


def test_person_changes_during_long_occlusion_are_not_attributed():
    sequence = [frame(t, t >= 400, [PERSON | {"track_id": 1 if t < 200 else 2}] if 20 <= t < 400 else []) for t in range(420)]
    assert "different or untracked" in journal_arrival(sequence, BOX, 419)


def test_missed_seated_person_frame_does_not_invent_empty_before():
    sequence = [frame(t, t >= 400, [PERSON] if t < 400 and t != 350 else []) for t in range(420)]
    assert journal_arrival(sequence, BOX, 419) == JOURNAL_TOO_NEW


def test_partial_dish_detection_during_long_occlusion_still_checks_person_continuity():
    sequence = [frame(t, t >= 30, [PERSON | {"track_id": 1 if t < 200 else 2}] if 20 <= t < 400 else []) for t in range(420)]
    assert "different or untracked" in journal_arrival(sequence, BOX, 419)


def test_missing_recording_never_counts_as_an_empty_spot():
    sequence = [frame(t) for t in range(20)] + [frame(t, True) for t in range(400, 420)]
    assert "gap" in journal_arrival(sequence, BOX, 419)


def test_already_present_and_single_detection_are_not_arrivals():
    assert journal_arrival([frame(t, True) for t in range(20)], BOX, 19) == JOURNAL_TOO_NEW
    assert isinstance(journal_arrival([frame(t, t == 19) for t in range(20)], BOX, 19), str)


def test_matching_coarse_family_and_nearby_object_size():
    assert putdown._matches(DISH | {"kind": "pot"}, BOX, "vessel")
    assert not putdown._matches(DISH | {"kind": "cup"}, BOX, "vessel")
    assert not putdown._matches(DISH | {"box": [.2, .3, .6, .6]}, BOX, None)
    assert not putdown._matches(DISH | {"box": [.5, .6, .08, .07]}, BOX, None)


def test_body_cover_penalizes_head_and_torso_not_extended_reach():
    assert putdown._body_cover(BOX, PERSON["box"]) > .3
    assert putdown._body_cover(BOX, [.2, .2, .22, .5]) < .3
    assert putdown._reach(BOX, [.2, .2, .22, .5]) < .1


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_time_is_rejected(tmp_path, value):
    with Journal(tmp_path / "journal.db") as journal:
        with pytest.raises(ValueError):
            journal.record(value, [])


def test_full_rate_decoder_keeps_frames_between_keyframes_and_real_times(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    video = tmp_path / "sample.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=15",
                    "-t", "3", "-c:v", "libx264", "-threads", "1", "-g", "30", "-f", "mpegts", str(video)], check=True)
    result = putdown.decoded_photos((100, 103, video), t0=100.5, t1=101.1)
    assert len(result) == 9
    assert result[0][0] == pytest.approx(100 + 8 / 15, abs=.001)
    assert result[-1][0] == pytest.approx(100 + 16 / 15, abs=.001)
    assert all(jpeg.startswith(b"\xff\xd8") for _, jpeg in result)


def scene(dish):
    import io

    from PIL import Image, ImageDraw
    image = Image.new("RGB", (400, 300), "gray")
    draw = ImageDraw.Draw(image)
    draw.line((160, 180, 192, 201), fill="white", width=2)
    if dish:
        draw.ellipse((162, 181, 190, 199), fill="black", outline="white", width=3)
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


@pytest.mark.parametrize("detection_delay", [0, 4])
def test_finder_returns_ordered_real_frames_candidates_and_departure_metadata(tmp_path, monkeypatch, detection_delay):
    person = PERSON | {"box": [.2, .2, .22, .5]}
    empty, dish = scene(False), scene(True)
    sequence = [frame(t, t >= 20 + detection_delay, [person] if 18 <= t <= 20 else []) for t in range(50)]
    with Journal(tmp_path / "journal.db") as journal:
        for f in sequence:
            journal.record(f["at"], f["detections"])
        monkeypatch.setattr(putdown, "segments", lambda _: [(0, 50, tmp_path / "clip.ts")])
        monkeypatch.setattr(putdown, "samples", lambda *args: [putdown.Sample(t, putdown._jpeg_patch(dish if t >= 20 else empty, BOX)) for t in range(50)])
        monkeypatch.setattr(putdown, "decoded_photos", lambda _, **kw: [(t / 4, dish if t / 4 >= 20 else empty)
                                                                     for t in range(200) if kw["t0"] <= t / 4 <= kw["t1"]])
        result = putdown.find_putdown_journal(journal, tmp_path, BOX, 30, 49)
        assert isinstance(result, dict)
        assert result["method"] == "journal" and result["candidates"]
        assert result["before"]["at"] < result["putting"]["at"] < result["left"]["at"]
        assert result["before"]["jpeg"] == empty and result["left"]["jpeg"] == dish
        assert result["before"]["at"] < 20  # Never relabel a detector's late negative as empty.
        assert result["putting"]["person_box"] == person["box"]
        assert all(result["left"]["at"] - 5 < c["at"] < result["left"]["at"] for c in result["candidates"])
        assert result["putting_action"] == "putting it down"
        checked_people = []
        def nobody(jpeg):
            checked_people.append(jpeg)
            return []
        rejected = putdown.find_putdown_journal(journal, tmp_path, BOX, 30, 49, people=nobody)
        assert isinstance(rejected, str)  # An empty exact-person result can never produce a proof.
        assert len(checked_people) <= 14  # Two clear views, then a bounded candidate budget.


def test_patch_rewinds_minutes_late_recognition_to_the_actual_reach(tmp_path, monkeypatch):
    person = PERSON | {"box": [.2, .2, .22, .5]}
    empty, dish = scene(False), scene(True)
    with Journal(tmp_path / "journal.db") as journal:
        for t in range(300):
            journal.record(t, frame(t, t >= 200, [person] if 18 <= t <= 20 else [])["detections"])
        monkeypatch.setattr(putdown, "segments", lambda _: [(0, 300, tmp_path / "clip.ts")])
        monkeypatch.setattr(putdown, "samples", lambda *args: [putdown.Sample(t, putdown._jpeg_patch(dish if t >= 20 else empty, BOX)) for t in range(300)])
        monkeypatch.setattr(putdown, "decoded_photos", lambda _, **kw: [(t / 4, dish if t / 4 >= 20 else empty)
                                                                     for t in range(1200) if kw["t0"] <= t / 4 <= kw["t1"]])
        result = putdown.find_putdown_journal(journal, tmp_path, BOX, 230, 299)
        assert isinstance(result, dict), result
        assert result["before"]["at"] < 20
        assert abs(result["putting"]["at"] - 20) <= 3
        assert result["rewind_patch_checks"] > 0


def test_finder_does_not_call_detector_for_missing_history(tmp_path):
    with Journal(tmp_path / "journal.db") as journal:
        assert putdown.find_putdown_journal(journal, tmp_path, BOX, 30, 50) == JOURNAL_TOO_NEW


def test_earlier_clear_view_prevents_uncovering_an_old_dish_becoming_arrival(tmp_path, monkeypatch):
    person = PERSON | {"box": [.2, .2, .22, .5]}
    empty, dish = scene(False), scene(True)
    def picture(t):
        return dish if t >= 20 and not 100 <= t < 140 else empty
    with Journal(tmp_path / "journal.db") as journal:
        for t in range(-100, 300):
            nearby = [person] if 18 <= t <= 20 or 130 <= t <= 140 else []
            journal.record(t, frame(t, t >= 200, nearby)["detections"])
        monkeypatch.setattr(putdown, "segments", lambda _: [(-100, 300, tmp_path / "clip.ts")])
        monkeypatch.setattr(putdown, "samples", lambda *args: [putdown.Sample(t, putdown._jpeg_patch(picture(t), BOX)) for t in range(-100, 300)])
        monkeypatch.setattr(putdown, "decoded_photos", lambda _, **kw: [(t / 4, picture(t / 4))
                                                                     for t in range(-400, 1200) if kw["t0"] <= t / 4 <= kw["t1"]])
        result = putdown.find_putdown_journal(journal, tmp_path, BOX, 230, 299)
        assert isinstance(result, dict), result
        assert abs(result["putting"]["at"] - 20) <= 3
        assert result["before"]["at"] < 20


def test_finder_rejects_missing_video_even_when_journal_has_an_arrival(tmp_path, monkeypatch):
    person = PERSON | {"box": [.2, .2, .22, .5]}
    empty, dish = scene(False), scene(True)
    with Journal(tmp_path / "journal.db") as journal:
        for t in range(50):
            journal.record(t, frame(t, t >= 20, [person] if 18 <= t <= 20 else [])["detections"])
        monkeypatch.setattr(putdown, "segments", lambda _: [(0, 18, tmp_path / "before.ts"), (24, 50, tmp_path / "after.ts")])
        monkeypatch.setattr(putdown, "samples", lambda seg, *args: [putdown.Sample(t, putdown._jpeg_patch(dish if t >= 20 else empty, BOX))
                                                                  for t in range(int(seg[0]), int(seg[1]))])
        monkeypatch.setattr(putdown, "decoded_photos", lambda *a, **k: pytest.fail("A known recording gap must stop refinement"))
        result = putdown.find_putdown_journal(journal, tmp_path, BOX, 30, 49)
        assert isinstance(result, str) and "recording has a gap" in result


def test_long_occlusion_finder_selects_departure_and_preserves_caption(tmp_path, monkeypatch):
    person = PERSON | {"box": [.2, .2, .25, .5]}
    empty, dish = scene(False), scene(True)
    with Journal(tmp_path / "journal.db") as journal:
        for t in range(450):
            journal.record(t, frame(t, t >= 400, [person] if 20 <= t < 400 else [])["detections"])
        monkeypatch.setattr(putdown, "segments", lambda _: [(0, 450, tmp_path / "clip.ts")])
        monkeypatch.setattr(putdown, "samples", lambda *args: [putdown.Sample(t, putdown._jpeg_patch(dish if t >= 400 else empty, BOX)) for t in range(450)])
        monkeypatch.setattr(putdown, "decoded_photos", lambda _, **kw: [(t / 4, dish if t / 4 >= 400 else empty)
                                                                     for t in range(1800) if kw["t0"] <= t / 4 <= kw["t1"]])
        result = putdown.find_putdown_journal(journal, tmp_path, BOX, 430, 449)
        assert isinstance(result, dict)
        assert result["putting_action"] == "left it behind"
        assert result["before"]["at"] < 20
        assert 398 <= result["putting"]["at"] < 400
        assert result["left"]["at"] >= 400
