import os
import threading
import time

import pytest

from wash_dishes.camera import Camera

CAMERA = {'host': '192.168.68.55', 'username': 'camera', 'password': 'secret', 'stream': 'stream1', 'enabled': True}


class FakeProcess:
    stdout = None

    def __init__(self, command, **kwargs):
        self.command, self.kwargs = command, kwargs

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def started_command(store, media, monkeypatch, role):
    store.set_camera(CAMERA)
    started, ensured = [], []
    ready = threading.Event()

    def popen(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        started.append(process)
        ready.set()
        return process

    monkeypatch.setattr('wash_dishes.camera.subprocess.Popen', popen)
    monkeypatch.setattr('wash_dishes.camera.shutil.which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr('wash_dishes.camera.discover', lambda: [])
    monkeypatch.setattr('wash_dishes.relay.Relay.ensure', lambda self, streams: ensured.append(streams) or True)
    camera = Camera(store.root, store, media, role=role)
    camera.start()
    try:
        assert ready.wait(5)
    finally:
        camera.stop_event.set()
        camera.thread.join(timeout=5)
    return camera, started[0], ensured


def test_recorder_role_owns_the_camera_and_writes_only_segments(store, media, monkeypatch):
    camera, process, ensured = started_command(store, media, monkeypatch, 'record')
    assert ensured, 'The recorder must hold the one camera connection.'
    assert '-f' in process.command and 'segment' in process.command
    assert 'pipe:1' not in process.command
    assert process.kwargs['stdout'] is not None and camera.reader is None


def test_analyze_role_reads_the_recorders_relay_and_never_records(store, media, monkeypatch):
    camera, process, ensured = started_command(store, media, monkeypatch, 'analyze')
    assert not ensured, 'The app must not open its own camera connection beside the recorder.'
    assert process.command[process.command.index('-i') + 1] == camera.relay.rtsp()
    assert 'segment' not in process.command and 'pipe:1' in process.command


def test_combined_role_is_unchanged(store, media, monkeypatch):
    _, process, ensured = started_command(store, media, monkeypatch, 'both')
    assert ensured and 'segment' in process.command and 'pipe:1' in process.command


def test_recorder_health_follows_its_own_newest_segment(store, media):
    camera = Camera(store.root, store, media, role='record')
    camera.segment_prefix, camera.segment_index = '123', -1
    assert not camera._recording()
    for index in range(3):
        (media.buffer / f'123-{index:06d}.ts').write_bytes(b'ts')
    assert camera._recording() and camera.segment_index == 2
    stale = time.time() - 60
    os.utime(media.buffer / '123-000002.ts', (stale, stale))
    assert not camera._recording(), 'A stalled writer must be restarted, not reported healthy.'


def test_unknown_role_is_rejected(store, media):
    with pytest.raises(ValueError):
        Camera(store.root, store, media, role='other')
