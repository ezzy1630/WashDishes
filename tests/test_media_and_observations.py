import os
import shutil
import subprocess
import threading
import time
from datetime import timedelta

import pytest

from wash_dishes.camera import Camera
from wash_dishes.config import Config
from wash_dishes.demo import evidence
from wash_dishes.runtime import Runtime
from wash_dishes.store import utcnow
from wash_dishes.vision import IdentityConsensus, LocalVision


def make_item(store, minutes=0, owner=None):
    return store.create_obligation(owner, [{"kind": "plate", "quantity": 1}], at=utcnow() - timedelta(minutes=minutes))


def test_retention_expires_media_but_preserves_ledger(store, media):
    item = make_item(store)
    evidence(store, media, item)
    e = store.all_evidence()[0]
    with store.lock, store.conn:
        store.conn.execute("UPDATE evidence SET expires_at=? WHERE id=?", ((utcnow() - timedelta(days=1)).isoformat(), e["id"]))
    media.cleanup()
    assert not media.safe_path(e["filename"]).exists()
    saved = store.obligation(item["id"])
    assert saved["status"] == "review"
    assert saved["evidence"][0]["expired"]
    assert saved["evidence"][0]["url"] is None


def test_confirmed_overdue_evidence_is_promoted(store, media):
    owner = store.add_resident("A")["id"]
    item = make_item(store, minutes=100, owner=owner)
    # Save ordinary evidence before the overdue observation.
    from wash_dishes.store import stamp
    path = media.safe_path("example.jpg")
    path.write_bytes(b"image")
    store.add_evidence(item["id"], "example", path.name, "snapshot", "Deposit", expires=utcnow() + timedelta(days=7))
    store.record_seen(item["id"])
    assert store.evidence("example")["expires_at"] >= stamp(utcnow() + timedelta(days=29))


def test_quota_reclaims_raw_before_evidence_and_never_follows_symlink(store, media, tmp_path):
    store.set_settings({"storage_limit_gb": 0.1})
    outside = tmp_path / "outside.ts"
    outside.write_bytes(b"keep me")
    (media.buffer / "link.ts").symlink_to(outside)
    for index in range(2):
        path = media.buffer / f"record-{index}.ts"
        with path.open("wb") as output:
            output.truncate(70_000_000)
        old = time.time() - 100 - index
        os.utime(path, (old, old))
    item = make_item(store)
    evidence(store, media, item)
    status = media.cleanup()
    assert status["used_bytes"] <= status["limit_bytes"]
    assert status["removed_files"] >= 1
    assert not store.all_evidence()[0]["expired"]
    assert outside.read_bytes() == b"keep me"
    with pytest.raises(ValueError):
        media.safe_path("../outside.ts")


def test_stale_snapshot_and_pause_do_not_claim_connected(store, media):
    camera = Camera(store.root, store, media)
    camera.frame = b"\xff\xd8fake\xff\xd9"
    camera.frame_time = (utcnow() - timedelta(seconds=30)).isoformat()
    assert camera.snapshot() is None
    assert not camera.status()["connected"]


def test_frame_mailbox_remains_available_while_model_lock_is_held(store, media):
    runtime = Runtime(Config(store.root, workers=False), store, media)
    delivered = threading.Event()

    def deliver():
        runtime.offer_frame(b"jpeg", "one")
        delivered.set()

    with runtime.lock:
        producer = threading.Thread(target=deliver)
        producer.start()
        accepted = delivered.wait(1)
    producer.join(timeout=2)
    assert accepted, "Inference must not block the camera's frame producer."
    assert runtime.pending[:2] == (b"jpeg", "one")


@pytest.mark.parametrize("change", ["pause", "reconnect"])
def test_inflight_observation_is_discarded_when_camera_changes(store, media, monkeypatch, change):
    runtime = Runtime(Config(store.root, workers=False), store, media)
    store.set_settings({"camera_enabled": True, "vision_enabled": True})
    started, release = threading.Event(), threading.Event()
    saved = []

    class SlowEngine:
        def reset_observations(self):
            pass

        def status(self):
            return {"ready": True, "message": "Ready"}

        def occupancy(self, areas, people):
            return {}

        def analyze(self, jpeg, **kwargs):
            started.set()
            assert release.wait(3)
            return {"detections": [{"kind": "plate"}]}

    monkeypatch.setattr(runtime, "local_vision", lambda: SlowEngine())
    monkeypatch.setattr(runtime.inventory, "process", lambda *args, **kwargs: saved.append(args))
    runtime.offer_frame(b"jpeg", runtime.camera.stream_generation)
    worker = threading.Thread(target=runtime._run)
    worker.start()
    try:
        assert started.wait(3)
        if change == "pause":
            store.set_settings({"camera_enabled": False})
        runtime.camera.restart()
    finally:
        runtime.stop_event.set()
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert saved == []


def test_reconnect_requires_three_fresh_identity_matches_after_discarded_inference(store, media, monkeypatch):
    runtime = Runtime(Config(store.root, workers=False), store, media)
    store.set_settings({"camera_enabled": True, "vision_enabled": True})
    started, release = threading.Event(), threading.Event()
    accepted = threading.Condition()
    identities = []

    class Engine(LocalVision):
        def __init__(self):
            super().__init__(store.root)
            self.calls = 0
            self._consensus = IdentityConsensus()

        def status(self):
            return {"ready": True, "message": "Ready"}

        def occupancy(self, areas, people):
            return {}

        def analyze(self, jpeg, **kwargs):
            self.calls += 1
            if self.calls == 3:
                started.set()
                assert release.wait(3)
            return {"detections": [], "resident_id": self._consensus.observe(1, eligible=True)}

    engine = Engine()
    runtime.vision = engine
    monkeypatch.setattr(runtime, "local_vision", lambda: engine)

    def record(analysis, *args, **kwargs):
        with accepted:
            identities.append(analysis["resident_id"])
            accepted.notify_all()

    def feed_and_wait(count):
        runtime.offer_frame(b"jpeg", runtime.camera.stream_generation)
        with accepted:
            assert accepted.wait_for(lambda: len(identities) == count, timeout=3)

    monkeypatch.setattr(runtime.inventory, "process", record)
    worker = threading.Thread(target=runtime._run)
    worker.start()
    try:
        feed_and_wait(1)
        feed_and_wait(2)
        runtime.offer_frame(b"jpeg", runtime.camera.stream_generation)
        assert started.wait(3)
        runtime.camera.restart()
        runtime.offer_frame(b"jpeg", runtime.camera.stream_generation)
        release.set()
        with accepted:
            assert accepted.wait_for(lambda: len(identities) == 3, timeout=3)
        feed_and_wait(4)
        feed_and_wait(5)
        assert identities == [None, None, None, None, 1]
    finally:
        runtime.stop_event.set()
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg integration requires the local binary")
def test_real_ffmpeg_segment_export_is_playable(store, media):
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "10", "-f", "segment", "-segment_time", "1", "-reset_timestamps", "1", "-y", str(media.buffer / "fixture-%03d.ts")]
    subprocess.run(command, check=True, timeout=15)
    item = make_item(store)
    evidence_id = media.save_recent_clip(item["id"], seconds=30)
    assert evidence_id
    file = media.safe_path(store.evidence(evidence_id)["filename"])
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1", str(file)], capture_output=True, text=True, check=True, timeout=5)
    assert "codec_name=h264" in result.stdout
