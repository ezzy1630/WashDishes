"""Child process: launch this checkout with workers disabled and isolated storage."""
from __future__ import annotations

import ipaddress
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def local_network_only(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple):
            try:
                local = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                local = address[0] == "localhost"
            if not local:
                raise RuntimeError(f"E2E blocked non-loopback connection: {address[0]}")
    if event == "socket.getaddrinfo" and args[0] not in (None, "localhost", "127.0.0.1", "::1"):
        raise RuntimeError(f"E2E blocked external DNS: {args[0]}")


def install_fixture_clock(clock_file: Path):
    """Advance only this child process's wall clock to exercise real URL expiry.

    No expiry policy, verifier, HTTP route or monotonic timeout is replaced.
    The browser restores the offset before continuing with other actions.
    """
    real_time = time.time

    def wall_time():
        return real_time() + float(clock_file.read_text())

    class FixtureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(wall_time(), tz)

        @classmethod
        def utcnow(cls):
            return datetime.utcfromtimestamp(wall_time())

    # All product modules are already imported by create_app. Keep this adapter
    # outside product code; both common wall-clock idioms see the same offset.
    for name, module in list(sys.modules.items()):
        if name.startswith("wash_dishes.") and module:
            for attribute, value in list(vars(module).items()):
                if value is datetime:
                    setattr(module, attribute, FixtureDateTime)
    time.time = wall_time


def main():
    sys.addaudithook(local_network_only)
    from seed import seed

    from wash_dishes.app import create_app
    from wash_dishes.config import Config

    root = Path(os.environ["HOUSE_E2E_DATA"])
    app = create_app(Config(root=root, workers=False, password=os.environ["WASHDISHES_ACCESS_KEY"]))
    fixture = seed(app)
    install_fixture_clock(Path(os.environ["HOUSE_E2E_CLOCK_FILE"]))
    fixture["house_api"] = hasattr(app.state, "house")
    (root.parent / "fixture.json").write_text(json.dumps(fixture, indent=2))
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["HOUSE_E2E_PORT"]), log_level="warning",
                ssl_certfile=os.environ["HOUSE_E2E_TLS_CERT"], ssl_keyfile=os.environ["HOUSE_E2E_TLS_KEY"])


if __name__ == "__main__":
    main()
