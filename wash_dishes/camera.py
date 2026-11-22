from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .camera_discovery import discover, neighbour_mac
from .config import camera_url
from .media import Media
from .relay import Relay
from .store import Store, stamp, utcnow


class Camera:
    """One camera connection, with compressed recording and a JPEG analysis pipe.

    role "both" does everything in one process. On the Jetson the recorder runs as
    its own service (role "record": go2rtc relay plus segments, nothing else) so app
    restarts and crashes never cut the footage, and the app (role "analyze") only
    reads analysis frames from that recorder's loopback relay.
    """

    def __init__(self, root: Path, store: Store, media: Media, callback=None, role: str = "both"):
        if role not in ("both", "record", "analyze"):
            raise ValueError(f"Unknown camera role: {role}")
        self.root, self.store, self.media = root, store, media
        self.callback = callback
        self.role = role
        self.segment_prefix = ""
        self.segment_index = -1
        self.segment_started = 0.0
        self.lock = threading.RLock()
        self.frame: bytes | None = None
        self.frame_time: str | None = None
        self.message = "Add the Tapo Camera Account in Setup to connect."
        self.process: subprocess.Popen | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.stream_generation = uuid.uuid4().hex
        self.fingerprint: str | None = None
        self.last_analysis = 0.0
        self.started_at = 0.0
        self.reader: threading.Thread | None = None
        self.relay = Relay(root)
        self.relayed = False
        self.discovery_key = ''
        self.discovery_identity = None
        self.discovery_mac = None
        self.discovery_next = 0.0
        self.resolved_host = None

    def _load_discovery(self, camera: dict) -> None:
        # Bind enrollment to the exact configured camera, without saving credentials.
        identity_fields = {key: camera.get(key) for key in ('host', 'username', 'stream')}
        self.discovery_key = hashlib.sha256(json.dumps(identity_fields, sort_keys=True).encode()).hexdigest()
        self.discovery_identity, self.discovery_mac, self.resolved_host = None, None, None
        self.discovery_next = 0.0
        try:
            saved = json.loads((self.root / 'camera-discovery.json').read_text())
            if saved.get('key') == self.discovery_key and isinstance(saved.get('identity'), str) and isinstance(saved.get('mac'), str):
                self.discovery_identity = saved['identity']
                self.discovery_mac = saved['mac']
        except (OSError, ValueError, AttributeError):
            pass

    def _discover_camera(self, camera: dict, connected: bool) -> None:
        if time.monotonic() < self.discovery_next:
            return
        self.discovery_next = time.monotonic() + 60
        found = discover()
        if connected and not self.discovery_identity:
            # A fresh authenticated frame establishes which discovered device to enroll.
            ids = {identity for identity, host in found if host == camera['host']}
            mac = neighbour_mac(camera['host']) if len(ids) == 1 else None
            if mac:
                self.discovery_identity = ids.pop()
                self.discovery_mac = mac
                target = self.root / 'camera-discovery.json'
                try:
                    target.write_text(json.dumps({'key': self.discovery_key, 'identity': self.discovery_identity, 'mac': mac}))
                except OSError:
                    self.discovery_identity = None
                    self.discovery_mac = None
        elif not connected and self.discovery_identity:
            hosts = {host for identity, host in found if identity == self.discovery_identity}
            self.resolved_host = None
            if len(hosts) == 1:
                host = hosts.pop()
                if neighbour_mac(host) == self.discovery_mac:
                    self.resolved_host = host

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._terminate()
        self.relay.stop()
        if self.thread:
            self.thread.join(timeout=5)

    def _terminate(self) -> None:
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def restart(self) -> None:
        self.fingerprint = None
        self._terminate()
        with self.lock:
            self.frame = self.frame_time = None
            self.stream_generation = uuid.uuid4().hex

    def _recording(self) -> bool:
        """True while this recorder writes fresh segments and still splits them on time.

        A camera timestamp collapse once left ffmpeg appending to one segment for 95
        minutes: the file stayed fresh, but no new 10 s segment ever started.
        """
        while (self.media.buffer / f"{self.segment_prefix}-{self.segment_index + 1:06d}.ts").exists():
            self.segment_index += 1
            self.segment_started = time.monotonic()
        if self.segment_index >= 0 and time.monotonic() - self.segment_started > 30:
            return False
        try:
            return time.time() - (self.media.buffer / f"{self.segment_prefix}-{self.segment_index:06d}.ts").stat().st_mtime < 30
        except FileNotFoundError:
            return False

    def video_available(self) -> bool:
        if self.role == "analyze":
            return self.relay.reachable()
        return self.relayed and self.relay.running()

    def snapshot(self) -> bytes | None:
        if not self.store.settings()["camera_enabled"]:
            return None
        with self.lock:
            if not self.frame_time or utcnow().timestamp() - self._frame_timestamp() > 15:
                return None
            return self.frame

    def _frame_timestamp(self) -> float:
        from .store import parse_stamp
        return parse_stamp(self.frame_time).timestamp()

    def status(self) -> dict:
        settings = self.store.settings(private=True)
        return {"configured": bool(settings.get("camera")), "enabled": settings["camera_enabled"], "connected": self.snapshot() is not None and settings["camera_enabled"], "latest_frame_at": self.frame_time, "message": self.message, "snapshot_url": "/api/camera/snapshot", "video": self.video_available()}

    @staticmethod
    def relay_streams(camera: dict) -> dict[str, str]:
        return {"kitchen": camera_url(camera), "kitchen_sd": camera_url(camera | {"stream": "stream2"})}

    def _read_frames(self, process: subprocess.Popen, generation: str) -> None:
        buffer = bytearray()
        if process.stdout is None:
            return
        while not self.stop_event.is_set() and process.poll() is None:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9", max(0, start + 2))
                if start < 0 or end < 0:
                    if len(buffer) > 8 * 1024 * 1024:
                        buffer.clear()
                    break
                frame = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                with self.lock:
                    if process is not self.process:
                        return
                    self.frame, self.frame_time = frame, stamp()
                    observed_at = self.frame_time
                    self.message = "Camera connected. Recording locally." + ("" if self.relayed else " Install go2rtc for smooth live video and to keep the camera password out of the process list.")
                # Vision runs independently; a slow detector cannot block the recording pipe.
                if self.callback:
                    self.callback(frame, generation, observed_at)

    def _run(self) -> None:
        had_connection = False
        retry_at = 0.0
        while not self.stop_event.wait(1):
            settings = self.store.settings(private=True)
            camera = settings.get("camera")
            desired = repr(camera) + str(settings["camera_enabled"]) + str(settings["analysis_fps"])
            if desired != self.fingerprint:
                self._terminate()
                self.fingerprint = desired
                if camera:
                    self._load_discovery(camera)
                retry_at = 0
                with self.lock:
                    self.frame = self.frame_time = None
                if had_connection:
                    self.store.event("observation_gap", "Camera settings changed or monitoring paused; dish state needs fresh observations")
                    had_connection = False
            if not settings["camera_enabled"] or not camera:
                self.message = "Monitoring paused." if camera else "Add the Tapo Camera Account in Setup to connect."
                continue
            if self.process and self.process.poll() is None:
                if (self._recording() if self.role == "record" else self.snapshot() is not None):
                    had_connection = True
                    if self.role != "analyze" and not self.discovery_identity:
                        self._discover_camera(camera, connected=True)
                    continue
                if time.monotonic() - self.started_at < 20:
                    continue
                self._terminate()
                self.message = "No recent camera frames. Reconnecting…"
            if had_connection:
                if self.role != "record":
                    self.store.event("observation_gap", "Camera disconnected; unseen cleanup cannot be inferred")
                had_connection = False
            if time.monotonic() < retry_at:
                continue
            if self.role != "analyze":
                self._discover_camera(camera, connected=False)
            retry_at = time.monotonic() + 10
            if self.role != "analyze" and self.discovery_identity and (not self.resolved_host or neighbour_mac(self.resolved_host) != self.discovery_mac):
                self.resolved_host = None
                self.message = 'Waiting to find the enrolled camera on the local network.'
                continue
            if not shutil.which("ffmpeg"):
                self.message = "Install FFmpeg on this machine to record the camera."
                continue
            self.stream_generation = uuid.uuid4().hex
            if self.resolved_host:
                camera = camera | {'host': self.resolved_host}
            if self.role == "analyze":
                # The recorder service owns the camera connection; read its loopback relay.
                self.relayed, url = True, self.relay.rtsp()
            else:
                try:
                    self.relayed = self.relay.ensure(self.relay_streams(camera))
                    url = self.relay.rtsp() if self.relayed else camera_url(camera)
                except (OSError, ValueError):
                    self.relayed, url = False, camera_url(camera)
            self.segment_prefix, self.segment_index = str(time.time_ns()), -1
            filename = str(self.media.buffer / (self.segment_prefix + "-%06d.ts"))
            # RTSP uses its demuxer timeout; Ubuntu FFmpeg rejects the generic rw_timeout here.
            command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", url]
            if self.role != "analyze":
                command += ["-map", "0:v:0", "-an", "-c:v", "copy", "-f", "segment", "-segment_time", "10", "-reset_timestamps", "1", "-segment_format", "mpegts", filename]
            if self.role != "record":
                command += ["-map", "0:v:0", "-an", "-vf", f"fps={settings['analysis_fps']},scale='min(2304,iw)':-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
            try:
                self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL if self.role == "record" else subprocess.PIPE, stderr=subprocess.DEVNULL)
                self.started_at = time.monotonic()
                if self.role != "record":
                    self.reader = threading.Thread(target=self._read_frames, args=(self.process, self.stream_generation), name="camera-frames", daemon=True)
                    self.reader.start()
                self.message = ("Waiting for the recorder service's video…" if self.role == "analyze" else
                                "Connecting to the camera…" if self.relayed else "Connecting directly. Install go2rtc for smooth video and to keep the camera password private.")
            except OSError:
                self.message = "FFmpeg could not start. Check the local installation."
            if self.process and self.process.poll() is not None:
                self.message = ("The recorder service isn't serving video. Check dish-watch-recorder." if self.role == "analyze" else
                                "Camera unreachable. Check its address, Camera Account, and home Wi-Fi.")
