from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

DEFAULT_SETTINGS = {
    "deadline_minutes": 120,
    "buffer_hours": 50,
    "evidence_days": 7,
    "review_days": 30,
    "storage_limit_gb": 20,
    "photo_days": 90,
    "analysis_fps": 4,
    "dataset_enabled": True,
    "dataset_limit_gb": 3,
    "camera_enabled": False,
    "vision_enabled": False,
    "detector_path": "",
    "zones": {"dirty": [0.05, 0.35, 0.35, 0.5], "sink": [0.4, 0.35, 0.3, 0.5], "clean": [0.72, 0.35, 0.25, 0.5]},
    "areas": [],
    # Name owners automatically once measured accurate enough (store.attribution_gate).
    "auto_attribute": True,
    # ntfy topic URL for late-dish alerts and the weekly summary; empty means off.
    "notify_url": "",
    "dashboard_url": "",
}


def monitoring_areas(settings: dict) -> list[dict]:
    """Keep older three-rectangle installations readable without rewriting their data."""
    if settings.get("areas"):
        return settings["areas"]
    return [{"id": role, "name": role.title(), "role": role, "box": box}
            for role, box in settings.get("zones", DEFAULT_SETTINGS["zones"]).items()]


def validate_rectangle(box: list) -> None:
    if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (float, int) for v in box):
        raise ValueError("Each area needs x, y, width, and height between 0 and 1.")
    x, y, width, height = box
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0.02 <= width <= 1 and 0.02 <= height <= 1
            and x + width <= 1.000001 and y + height <= 1.000001):
        raise ValueError("Areas must fit inside the camera image.")


@dataclass(frozen=True)
class Config:
    root: Path
    demo: bool = False
    workers: bool = True
    password: str | None = None
    port: int = 8787
    snapshot: bool = False

    @classmethod
    def environment(cls, *, demo: bool | None = None, workers: bool = True) -> Config:
        demo = os.getenv("WASHDISHES_DEMO", "0") == "1" if demo is None else demo
        base = Path(os.getenv("WASHDISHES_DATA_DIR", str(Path.home() / ".local/share/washdishes"))).expanduser()
        return cls(base / ("demo" if demo else "live"), demo, workers, os.getenv("WASHDISHES_ACCESS_KEY"), int(os.getenv("WASHDISHES_PORT", "8787")))

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        for name in ("media", "buffer", "models", "faces"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)


def camera_url(camera: dict) -> str:
    host = camera.get("host", "").strip()
    if not host or len(host) > 253 or not re.fullmatch(r"[a-zA-Z0-9._:-]+", host):
        raise ValueError("Enter a camera IP address or local hostname, without a URL or port.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host or host.startswith("-") or host.endswith("-"):
            raise ValueError("Enter a valid camera IP address or hostname.") from None
    else:
        if not (address.is_private or address.is_loopback) or address.is_unspecified or address.is_multicast:
            raise ValueError("Use the camera's private address on your home network.")
        if address.version == 6:
            host = f"[{host}]"
    stream = camera.get("stream", "stream1")
    if stream not in ("stream1", "stream2"):
        raise ValueError("Select stream1 or stream2.")
    username = str(camera.get("username", ""))
    password = str(camera.get("password", ""))
    if not username or not password or len(username) > 128 or len(password) > 256:
        raise ValueError("The separate Tapo Camera Account username and password are required.")
    return f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}@{host}:554/{stream}"


def validate_settings(patch: dict) -> dict:
    unknown = set(patch) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}")
    bounds = {"deadline_minutes": (1, 1440), "buffer_hours": (0.1, 72), "evidence_days": (1, 90), "review_days": (1, 365), "storage_limit_gb": (0.1, 2000),
              "photo_days": (1, 730), "analysis_fps": (1, 10), "dataset_limit_gb": (0, 100)}
    for key, (low, high) in bounds.items():
        if key in patch:
            value = patch[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"{key.replace('_', ' ')} must be between {low} and {high}.")
            if key in ("deadline_minutes", "evidence_days", "review_days", "photo_days") and value != int(value):
                raise ValueError(f"{key.replace('_', ' ')} must be a whole number.")
    for key in ("camera_enabled", "vision_enabled", "dataset_enabled", "auto_attribute"):
        if key in patch and not isinstance(patch[key], bool):
            raise ValueError(f"{key} must be true or false.")
    if "detector_path" in patch and (not isinstance(patch["detector_path"], str) or len(patch["detector_path"]) > 512):
        raise ValueError("Enter a local detector filename or path.")
    if "notify_url" in patch:
        url = patch["notify_url"]
        if not isinstance(url, str) or len(url) > 512 or (url and not re.fullmatch(r"https?://[^\s/@]+(:\d+)?/[A-Za-z0-9_-]{1,64}", url)):
            raise ValueError("Enter an ntfy topic address such as https://ntfy.sh/your-private-topic, or leave it empty.")
    if "dashboard_url" in patch:
        from urllib.parse import urlsplit
        url = patch['dashboard_url']
        if not isinstance(url, str) or len(url) > 512:
            raise ValueError('Enter the household dashboard address.')
        parts = urlsplit(url)
        if url and (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment or parts.path not in ('', '/') or any(c.isspace() for c in url)):
            raise ValueError('Use the household dashboard origin, without credentials, path or query.')
    if "zones" in patch:
        zones = patch["zones"]
        if not isinstance(zones, dict) or set(zones) != {"dirty", "sink", "clean"}:
            raise ValueError("Define dirty, sink, and clean zones.")
        for zone in zones.values():
            validate_rectangle(zone)
    if "areas" in patch:
        areas = patch["areas"]
        if not isinstance(areas, list) or len(areas) > 24:
            raise ValueError("Define at most 24 monitoring areas.")
        ids = set()
        for area in areas:
            if not isinstance(area, dict) or set(area) - {"outline"} != {"id", "name", "role", "box"}:
                raise ValueError("Each area needs an id, name, role, and rectangle.")
            if "outline" in area:
                outline = area["outline"]
                if (not isinstance(outline, list) or not 3 <= len(outline) <= 24 or any(
                        not isinstance(p, list) or len(p) != 2 or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in p) for p in outline)):
                    raise ValueError("An area outline needs 3 to 24 points inside the picture.")
            if not isinstance(area["id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", area["id"]) or area["id"] in ids:
                raise ValueError("Area identifiers must be short and unique.")
            ids.add(area["id"])
            if not isinstance(area["name"], str) or not 1 <= len(area["name"].strip()) <= 60 or any(ord(c) < 32 for c in area["name"]):
                raise ValueError("Give each area a name of at most 60 characters.")
            if area["role"] not in ("dirty", "sink", "clean", "storage"):
                raise ValueError("Choose dirty, sink, clean, or storage for each area.")
            validate_rectangle(area["box"])
    return patch
