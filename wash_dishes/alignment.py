"""Follow small camera shifts so zones and tracked dishes stay on the objects they describe.

Zones are drawn on one picture of the kitchen. A bump, a draft or a door swinging into the
mount can nudge the camera a few percent; then every counter, the sink and every dish move by
the same amount on screen. The shift is measured by phase correlation of edge images (edges
survive day/night lighting changes better than raw brightness) against a reference view.

Measured on this kitchen over an evening: a person in view drags the reading with them (one
locked a false 3% shift), and after dark the daytime reference stops matching for 20 minutes
at a time. So views with people in them are not measured at all, a recent empty view serves as
an anchor when the reference no longer matches, and an unreadable view never pauses tracking:
a camera bump is rare, while people and changing light are constant. Only readings that agree
on a shift too large to follow mark the camera as moved.

Only a clear reading against the reference itself moves the zones. On 2026-10-14 frames with
someone close to the camera (before the detector saw them) made the reference unreadable; the
anchor then read three spurious shifts in ten minutes, each saved into the next anchor, and the
zones sat 12% too low all night while the reference still read the true position at 0.22 or
better in every one of 836 checks. The anchor can say the zones are still in place, never move them.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

# Beyond this fraction of the frame the camera has been moved, not nudged.
MAX_SHIFT = 0.10
# Phase-correlation peak below which a reading is noise (darkness, a changed scene).
MIN_RESPONSE = 0.05
# A reference reading at least this clear may move the zones; weaker ones only confirm them.
TRUSTED_RESPONSE = 0.15
# Readings (about 2 s apart, empty views only) that must agree before the offset changes.
STABLE_READINGS = 5
AGREEMENT = 0.006
# Offset changes smaller than this are jitter and are ignored.
MIN_CHANGE = 0.004
# How often a recent empty view replaces the anchor while the offset holds.
ANCHOR_SECONDS = 300
# After this long with no readable empty view (usually the lights changed), the current view
# becomes the anchor at the current offset.
REANCHOR_SECONDS = 600
WIDTH = 480


class ViewAlignment:
    """Offset of the current view from the reference, the picture the zones were drawn on.

    The anchor is a recent empty view saved with the offset confirmed at the time; a reading
    against it plus that offset stands in for a reading against the reference. Refreshing the
    anchor keeps the confirmed offset, so small reading errors never add up.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.anchor_path = self.path.with_name(self.path.stem + "-anchor.npz")
        self.reference = None
        self.anchor = None
        self.anchor_offset: tuple[float, float] = (0.0, 0.0)
        self.anchor_at: float | None = None
        self.offset: tuple[float, float] = (0.0, 0.0)
        self.locked = False
        self.state = "unknown"
        self.response: float | None = None
        self.readings: deque[tuple[float, float]] = deque(maxlen=STABLE_READINGS)
        self.last_reading: tuple[float, float] | None = None
        self.unreadable_since: float | None = None
        self.reanchors = 0
        if self.path.exists():
            import numpy as np
            try:
                self.reference = np.load(self.path)
            except (OSError, ValueError):
                self.reference = None
            if self.reference is not None and self.anchor_path.exists():
                try:
                    saved = np.load(self.anchor_path)
                    # The last confirmed view: after a restart the camera has almost always stayed put.
                    self.anchor, self.anchor_offset = saved["features"], (float(saved["offset"][0]), float(saved["offset"][1]))
                    self.offset = self.anchor_offset
                    self.locked, self.state = True, "aligned"
                except (OSError, ValueError, KeyError):
                    self.anchor = None

    @property
    def has_reference(self) -> bool:
        return self.reference is not None

    @staticmethod
    def features(jpeg: bytes):
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None
        image = cv2.resize(image, (WIDTH, round(WIDTH * image.shape[0] / image.shape[1])), interpolation=cv2.INTER_AREA)
        # The burned-in clock in the top-left changes every second; blank that corner.
        image[: image.shape[0] // 14, : image.shape[1] // 2] = 0
        image = cv2.GaussianBlur(image, (5, 5), 0).astype(np.float32)
        magnitude = cv2.magnitude(cv2.Sobel(image, cv2.CV_32F, 1, 0), cv2.Sobel(image, cv2.CV_32F, 0, 1))
        return np.log1p(magnitude)

    def _save(self, path: Path, write) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + ".tmp")
        with open(temporary, "wb") as handle:
            write(handle)
        # An edge map of the kitchen is still household imagery; keep it as private as the rest.
        temporary.chmod(0o600)
        temporary.replace(path)

    def set_reference(self, jpeg: bytes) -> bool:
        import numpy as np
        features = self.features(jpeg)
        if features is None:
            return False
        self._save(self.path, lambda handle: np.save(handle, features))
        self.reference = features
        self.offset, self.locked, self.state = (0.0, 0.0), True, "aligned"
        self.readings.clear()
        self.last_reading = None
        self.unreadable_since = None
        self._set_anchor(features, None)
        return True

    def _set_anchor(self, features, now: float | None) -> None:
        import numpy as np
        offset = self.offset
        try:
            self._save(self.anchor_path, lambda handle: np.savez(handle, features=features, offset=np.array(offset)))
        except OSError:
            pass  # The anchor still works for this run; it just will not survive a restart.
        self.anchor, self.anchor_offset, self.anchor_at = features, offset, now

    @staticmethod
    def _shift(reference, current) -> tuple[float, float, float] | None:
        import cv2
        if reference is None or current is None or current.shape != reference.shape:
            return None
        window = cv2.createHanningWindow(current.shape[::-1], cv2.CV_32F)
        (sx, sy), response = cv2.phaseCorrelate(reference, current, window)
        return sx / current.shape[1], sy / current.shape[0], float(response)

    def _from_anchor(self, current) -> tuple[float, float, float] | None:
        relative = self._shift(self.anchor, current)
        if relative is None or relative[2] < MIN_RESPONSE:
            return None
        return self.anchor_offset[0] + relative[0], self.anchor_offset[1] + relative[1], relative[2]

    def measure(self, jpeg: bytes, current=None) -> tuple[float, float, float] | None:
        """Shift of the current view relative to the reference, as fractions of width and height."""
        current = self.features(jpeg) if current is None else current
        reading = self._shift(self.reference, current)
        if reading is None or reading[2] >= MIN_RESPONSE:
            return reading
        return self._from_anchor(current) or reading

    def observe(self, jpeg: bytes, *, now: float, people: int = 0) -> tuple[float, float] | None:
        """Update from one frame. Returns the change to apply to on-screen positions, if any."""
        if people:
            return None
        current = self.features(jpeg)
        reading = self._shift(self.reference, current)
        if reading is None:
            return None
        trusted = reading[2] >= TRUSTED_RESPONSE
        if reading[2] < MIN_RESPONSE:
            reading = self._from_anchor(current) or reading
        dx, dy, self.response = reading
        if self.response < MIN_RESPONSE:
            # A reading the view no longer supports must not hold tracking paused: the lights
            # switching gives one stray frame and then nothing readable for ten minutes.
            self.last_reading = None
            self.unreadable_since = self.unreadable_since or now
            if now - self.unreadable_since >= REANCHOR_SECONDS:
                self._set_anchor(current, now)
                self.locked, self.state = True, "aligned"
                self.readings.clear()
                self.last_reading, self.unreadable_since = None, None
                self.reanchors += 1
            return None
        self.unreadable_since = None
        self.last_reading = (dx, dy)
        if not trusted:
            return None
        self.readings.append((dx, dy))
        if len(self.readings) < STABLE_READINGS:
            return None
        xs, ys = sorted(r[0] for r in self.readings), sorted(r[1] for r in self.readings)
        if xs[-1] - xs[0] > AGREEMENT or ys[-1] - ys[0] > AGREEMENT:
            return None
        target = (xs[len(xs) // 2], ys[len(ys) // 2])
        if max(abs(target[0]), abs(target[1])) > MAX_SHIFT:
            self.state = "moved"
            return None
        self.state = "aligned"
        if not self.locked:
            # Tracked dishes were last seen in the current picture, whatever the offset is.
            self.offset, self.locked = target, True
            self._set_anchor(current, now)
            return None
        change = (target[0] - self.offset[0], target[1] - self.offset[1])
        if max(abs(change[0]), abs(change[1])) < MIN_CHANGE:
            if self.anchor_at is None or now - self.anchor_at >= ANCHOR_SECONDS:
                self._set_anchor(current, now)
            return None
        self.offset = target
        self._set_anchor(current, now)
        return change

    @property
    def settled(self) -> bool:
        """Whether zones match the picture right now. False mid-bump, before the new shift is confirmed."""
        if self.reference is None:
            return True
        if not self.locked or self.state == "moved":
            return False
        if self.last_reading is None:
            return True
        return max(abs(self.last_reading[0] - self.offset[0]), abs(self.last_reading[1] - self.offset[1])) <= 2 * AGREEMENT

    def status(self) -> dict:
        return {"state": self.state if self.reference is not None else "unknown", "settled": self.settled, "offset": [round(self.offset[0], 4), round(self.offset[1], 4)],
                "response": None if self.response is None else round(self.response, 3), "has_reference": self.reference is not None}


def shift_box(box: list[float], dx: float, dy: float) -> list[float]:
    x, y, w, h = box
    return [min(max(0.0, x + dx), 1 - w), min(max(0.0, y + dy), 1 - h), w, h]


def shift_areas(areas: list[dict], offset: tuple[float, float]) -> list[dict]:
    if offset == (0.0, 0.0):
        return areas
    dx, dy = offset
    return [{**area, "box": shift_box(area["box"], dx, dy),
             **({"outline": [[x + dx, y + dy] for x, y in area["outline"]]} if "outline" in area else {})} for area in areas]
