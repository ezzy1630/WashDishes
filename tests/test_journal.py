import json
import sqlite3

import pytest

from wash_dishes.journal import Journal

BOX = [.4, .6, .08, .07]

DISH = {"kind": "pan", "score": .8, "box": BOX}

PERSON = {"kind": "person", "score": .8, "box": [.35, .2, .2, .6], "track_id": 1}

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

@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_time_is_rejected(tmp_path, value):
    with Journal(tmp_path / "journal.db") as journal:
        with pytest.raises(ValueError):
            journal.record(value, [])
