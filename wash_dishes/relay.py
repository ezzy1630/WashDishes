"""Local go2rtc relay: one camera connection, credentials kept out of process arguments.

go2rtc holds the Tapo login in a private config file and re-serves the stream on loopback,
both as RTSP for the recorder and as fragmented MP4 for smooth video in the dashboard.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

API_PORT = int(os.getenv("WASHDISHES_RELAY_API_PORT", "18984"))
RTSP_PORT = int(os.getenv("WASHDISHES_RELAY_RTSP_PORT", "18554"))


class Relay:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.config = self.root / "go2rtc.yaml"
        self.process: subprocess.Popen | None = None
        self.fingerprint: str | None = None

    def binary(self) -> str | None:
        for candidate in (os.getenv("WASHDISHES_GO2RTC"), str(self.root.parent / "tools" / "go2rtc"), str(self.root / "tools" / "go2rtc")):
            if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return candidate
        return shutil.which("go2rtc")

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def ensure(self, streams: dict[str, str]) -> bool:
        """Start or restart go2rtc for these sources; an empty source accepts a local RTSP publisher."""
        fingerprint = json.dumps(streams, sort_keys=True)
        if self.running() and fingerprint == self.fingerprint:
            return True
        self.stop()
        binary = self.binary()
        if not binary:
            return False
        # JSON is valid YAML, and it quotes percent-encoded credentials safely.
        settings = {"api": {"listen": f"127.0.0.1:{API_PORT}"}, "rtsp": {"listen": f"127.0.0.1:{RTSP_PORT}"},
                    "webrtc": {"listen": ""}, "srtp": {"listen": ""}, "rtmp": {"listen": ""}, "log": {"level": "error"},
                    "streams": {name: source or None for name, source in streams.items()}}
        descriptor = os.open(self.config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(settings, output)
        os.chmod(self.config, 0o600)
        self.process = subprocess.Popen([binary, "-config", str(self.config)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.fingerprint = fingerprint
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.running():
            try:
                with urlopen(f"http://127.0.0.1:{API_PORT}/api", timeout=1):
                    return True
            except OSError:
                time.sleep(0.2)
        return self.running()

    def stop(self) -> None:
        process, self.process = self.process, None
        self.fingerprint = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    @staticmethod
    def rtsp(name: str = "kitchen") -> str:
        return f"rtsp://127.0.0.1:{RTSP_PORT}/{name}"

    @staticmethod
    def mp4(name: str = "kitchen") -> str:
        return f"http://127.0.0.1:{API_PORT}/api/stream.mp4?src={name}&video=h264"
