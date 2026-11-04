"""Disposable house-site previews. Originals and evidence retention are unchanged."""
from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

from PIL import Image

from .media import atomic_write

WIDTH = 640
MAX_FILES = 128


class Thumbnails:
    def __init__(self, root: Path):
        self.root = root / 'house-thumbnails'
        self.lock = threading.Lock()

    def image(self, source: Path) -> bytes:
        # Call only AFTER the signed-media authorization and retention checks.
        info = source.stat()
        identity = f'{source}:{info.st_mtime_ns}:{info.st_size}'
        key = hashlib.sha256(identity.encode()).hexdigest()
        with self.lock:
            target = self.root / f'{key}.jpg'
            if target.is_file():
                return target.read_bytes()
            with Image.open(source) as image:
                if image.width <= WIDTH:
                    return source.read_bytes()
                height = max(1, round(image.height * WIDTH / image.width))
                # JPEG's reduced decode avoids allocating the full camera frame.
                image.draft('RGB', (WIDTH, height))
                image.thumbnail((WIDTH, height), Image.Resampling.LANCZOS, reducing_gap=3)
                output = io.BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=85)
            self.root.mkdir(mode=0o700, exist_ok=True)
            atomic_write(target, output.getvalue())
            # Regenerable previews cannot grow with the household's history.
            files = sorted(self.root.glob('*.jpg'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
            for old in files[MAX_FILES:]:
                old.unlink(missing_ok=True)
            return output.getvalue()
