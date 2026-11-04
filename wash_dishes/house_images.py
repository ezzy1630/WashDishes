"""Disposable house-site previews; no derivative files extend evidence retention."""
from __future__ import annotations

import hashlib
import io
import threading
from collections import OrderedDict
from pathlib import Path

from PIL import Image

WIDTH = 640
MAX_FILES = 128
MAX_BYTES = 8 * 1024 * 1024


class Thumbnails:
    def __init__(self):
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.lock = threading.Lock()

    def image(self, source: Path) -> bytes:
        # Call only AFTER the signed-media authorization and retention checks.
        info = source.stat()
        identity = f'{source}:{info.st_mtime_ns}:{info.st_size}'
        key = hashlib.sha256(identity.encode()).hexdigest()
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            with Image.open(source) as image:
                if image.width <= WIDTH:
                    return source.read_bytes()
                height = max(1, round(image.height * WIDTH / image.width))
                # JPEG's reduced decode avoids allocating the full camera frame.
                image.draft('RGB', (WIDTH, height))
                image.thumbnail((WIDTH, height), Image.Resampling.LANCZOS, reducing_gap=3)
                output = io.BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=85)
            data = output.getvalue()
            self.cache[key] = data
            # Bound both pathological aspect ratios and a long household history.
            size = sum(map(len, self.cache.values()))
            while len(self.cache) > MAX_FILES or size > MAX_BYTES:
                _, old = self.cache.popitem(last=False)
                size -= len(old)
            return data
