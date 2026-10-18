"""Run the dashboard and local vision against recorded footage instead of the RTSP camera.

Usage: .venv/bin/python scripts/replay.py SEGMENT_OR_DIR [...] [--areas jetson-bootstrap/kitchen-areas.json]

Footage loops in real time into the go2rtc relay, then through the same recorder, detector,
inventory rules, and live view as the kitchen camera. Data lives in .data/replay, isolated from live
and demo; models are linked from .data/live/models. Access key: replay.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wash_dishes.app import create_app  # noqa: E402
from wash_dishes.camera import Camera  # noqa: E402
from wash_dishes.config import Config  # noqa: E402


class ReplayCamera(Camera):
    """Publishes looping footage into the local go2rtc relay, then runs the normal camera pipeline."""

    def __init__(self, *args, footage: list[Path], **kwargs):
        super().__init__(*args, **kwargs)
        self.footage = footage
        threading.Thread(target=self._publish, name="replay-publisher", daemon=True).start()

    @staticmethod
    def relay_streams(camera: dict) -> dict[str, str]:
        return {"kitchen": "", "kitchen_sd": "kitchen"}

    def _publish(self) -> None:
        listing = Path(tempfile.mkstemp(suffix=".concat")[1])
        listing.write_text("".join(f"file '{path}'\n" for path in self.footage))
        while not self.stop_event.wait(1):
            if not self.relay.running():
                continue
            command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1", "-f", "concat", "-safe", "0", "-i", str(listing),
                       "-map", "0:v:0", "-c", "copy", "-f", "rtsp", "-rtsp_transport", "tcp", self.relay.rtsp()]
            subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        listing.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("footage", nargs="+", type=Path, help="Recorded .ts/.mp4 segments or directories of them")
    parser.add_argument("--areas", type=Path, default=ROOT / "jetson-bootstrap/kitchen-areas.json")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    footage = sorted(p.resolve() for path in args.footage for p in ([*path.glob("*.ts"), *path.glob("*.mp4")] if path.is_dir() else [path]))
    if not footage:
        parser.error("No footage found.")
    config = Config(ROOT / ".data/replay", password="replay", port=args.port)
    config.prepare()
    for model in (ROOT / ".data/live/models").glob("*"):
        link = config.root / "models" / model.name
        if not link.exists():
            link.symlink_to(model)
    os.environ.setdefault("WASH_DISHES_VISION_DEVICE", "mps" if sys.platform == "darwin" else "cpu")
    app = create_app(config)
    runtime, store = app.state.runtime, app.state.store
    store.set_camera({"host": "replay", "username": "replay", "password": "replay", "stream": "stream1", "enabled": True})
    store.set_settings({"vision_enabled": True, "areas": json.loads(args.areas.read_text())["areas"]})
    runtime.camera = ReplayCamera(config.root, store, app.state.media, runtime.offer_frame, footage=footage)
    print(f"Replaying {len(footage)} files at http://127.0.0.1:{args.port} (access key: replay)", flush=True)
    threading.current_thread().name = "replay"
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
