"""Who last used a dish, from what each visit to the kitchen changed.

The kitchen only changes while someone is within reach. The journal's person boxes split a spot's
history into clear views of it and visits to it; comparing the clear view just before a visit with the
one just after shows whether that visit changed the spot. Walking back from when the dish was logged,
the newest visit that put it there is its arrival. No dish detector is involved: it misses pans on the
stove and calls them bowls, and every check built on its boxes inherited that.

A dish belongs to whoever last used it, never to someone who only carried it (household rule,
2026-10-22). At an arrival, another spot that the same visit emptied of something that looks like this
dish is where it came from, and that spot is followed back the same way. Someone who stayed with the
dish alone for minutes used it. When look-alike dishes moved together and lead back to different
visits, or several people shared the spot, nobody is named.
"""
from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .footage import photos, segments
from .inventory import rests_in

REACH = 0.06        # a person box this close to a spot can touch it: a forearm at this camera
PERSON = 0.35       # journal person score that counts
STEP = 3.0          # journal frames further apart than this break a clear view
HOLD = 6.0          # clear seconds needed on each side of a visit; journal and recording clocks differ by 0-2 s
EDGE = 3.0          # a view is sampled this far inside its clear stretch
BLIND = 60.0        # a longer journal gap could hide a whole visit
NEAR = 300.0        # how far around a visit to look for where a dish came from
# 2026-10-22 footage, rack-side spot over four hours: visits that changed nothing .05-.14, arrivals
# .28-1.0. On lane T's labelled moves this found 10 of 12 on counters, stove and racks.
CHANGED = 0.25
SAME = 0.90         # CLIP cosine at which the view before a visit still holds the same dish
USE = 120.0         # alone within reach this long: used it, not carried it
# Only a dish that was left somewhere can be moved by someone else. One that rested untouched for less
# than this was still being handled, as when someone serves onto a plate and carries it off.
REST = 180.0
# A source looked like the dish before the visit and less so after. Every metal pan scores .78-.83
# against every other, so look-alikes are expected and are followed rather than guessed between.
MATCH, DROP = 0.75, 0.05
HOPS = 3
LOOKALIKES = 3
CANDIDATES = 40     # changed spots compared with the dish, most changed first
SMALLEST = (0.04, 0.05)
STOVE_WORDS = ("stove", "hob", "cooktop", "burner")
HISTORY = 20 * 3600


class _OutOfTime(Exception):
    pass


@dataclass(frozen=True)
class Visit:
    before: float   # a clear view just before
    start: float    # someone came within reach
    end: float
    after: float    # a clear view just after
    blind: bool     # nobody was seen or the journal stopped: a change has no witness


@dataclass(frozen=True)
class Source:
    box: list
    seen: float     # a clear view of the dish there, before the visit that moved it
    likeness: float
    rested: bool | None  # left there untouched for REST; None when no clear view shows either way


def _within(box: list, person: list) -> bool:
    x, y, w, h = box
    px, py, pw, ph = person
    return px - REACH < x + w and x - REACH < px + pw and py - REACH < y + h and y - REACH < py + ph


def _coverage(box: list, other: list) -> float:
    x, y, w, h = box
    px, py, pw, ph = other
    return max(0, min(x + w, px + pw) - max(x, px)) * max(0, min(y + h, py + ph) - max(y, py)) / (w * h)


def _body(person: list) -> list:
    # Head and torso, as in the proof checker: hands at the edge of a person box may cover the dish.
    x, y, w, h = person
    return [x + .2 * w, y, .6 * w, .8 * h]


def stove(area: dict) -> bool:
    return any(word in f"{area.get('id', '')} {area.get('name', '')}".lower() for word in STOVE_WORDS)


class People:
    """Person boxes the journal recorded. Tracker ids are not used: the live tracker split one cook
    alone at the stove for 17 minutes into more than eight ids."""

    def __init__(self, frames: Iterable[dict]):
        self.at: list[float] = []
        self.people: list[list[list]] = []
        for frame in frames:
            self.at.append(frame["at"])
            self.people.append([d["box"] for d in frame["detections"] if d["kind"] == "person" and d["score"] >= PERSON])

    def near(self, i: int, box: list) -> list[list]:
        return [p for p in self.people[i] if _within(box, p)]

    def clear_views(self, box: list, t0: float, t1: float) -> list[tuple[float, float]]:
        """Stretches of at least HOLD seconds with nobody within reach of `box`."""
        views, first, last = [], None, None
        for i in range(bisect_left(self.at, t0), bisect_right(self.at, t1)):
            at, near = self.at[i], self.near(i, box)
            if first is not None and (at - last > STEP or near):
                if last - first >= HOLD:
                    views.append((first, last))
                first = None
            if first is None:
                if not near:
                    first = last = at
            else:
                last = at
        if first is not None and last - first >= HOLD:
            views.append((first, last))
        return views

    def visits(self, box: list, until: float, floor: float, horizon: float) -> list[Visit]:
        """Visits to `box` that began before `until`, newest first."""
        views = self.clear_views(box, floor, horizon)
        out = []
        for (b0, b1), (a0, a1) in zip(views, views[1:]):
            if b1 >= until:
                break
            i, j = bisect_left(self.at, b1), bisect_right(self.at, a0)
            seen = any(self.near(k, box) for k in range(i, j))
            gap = max((self.at[k + 1] - self.at[k] for k in range(i, j - 1)), default=0.0)
            out.append(Visit(max(b0, b1 - EDGE), b1, a0, min(a1, a0 + EDGE), not seen or gap > BLIND))
        return out[::-1]

    def stays(self, visit: Visit, box: list) -> tuple[float, float, list[tuple[float, list]]]:
        """Seconds one person was alone within reach, seconds people shared it, and the alone frames."""
        alone = shared = 0.0
        frames = []
        for k in range(bisect_left(self.at, visit.start), bisect_right(self.at, visit.end)):
            dt = min(self.at[k + 1] - self.at[k], STEP) if k + 1 < len(self.at) else 1.0
            near = self.near(k, box)
            if len(near) == 1:
                alone += dt
                frames.append((self.at[k], near[0]))
            elif near:
                shared += dt
        return alone, shared, frames


class Footage:
    """Keyframes of the rolling recording, one about every two seconds. Decoding only keyframes costs
    about a quarter of one exact frame, and every view taken here is at least EDGE inside a clear stretch,
    so the nearest keyframe still shows it. Looks use a half-size copy; panels keep the full JPEG."""

    def __init__(self, buffer: Path):
        self.segments = segments(buffer)
        self.starts = [s for s, _, _ in self.segments]
        self.keyframes = lru_cache(maxsize=24)(lambda k: photos(self.segments[k]))
        self.image = lru_cache(maxsize=24)(self._image)

    def frame(self, at: float) -> tuple[float, bytes] | None:
        """The keyframe nearest `at`, with its own time."""
        k = bisect_right(self.starts, at) - 1
        near = [j for j in (k, k - 1, k + 1) if 0 <= j < len(self.segments) and self.segments[j][0] - 2 <= at <= self.segments[j][1] + 2]
        best = None
        for j in near:
            for found in self.keyframes(j):
                if best is None or abs(found[0] - at) < abs(best[0] - at):
                    best = found
            if best is not None and abs(best[0] - at) <= 1:
                break
        return best if best is not None and abs(best[0] - at) <= 1.5 else None

    def _image(self, at: float):
        import cv2
        found = self.frame(at)
        return None if found is None else cv2.imdecode(np.frombuffer(found[1], np.uint8), cv2.IMREAD_REDUCED_COLOR_2)


def _crop(image, box: list, pad: float = 0.0):
    h, w = image.shape[:2]
    x, y, bw, bh = box
    return image[max(0, int((y - pad) * h)):max(1, int((y + bh + pad) * h)), max(0, int((x - pad) * w)):max(1, int((x + bw + pad) * w))]


def look(image, box: list):
    import cv2
    crop = _crop(image, box)
    if crop.size == 0:
        return None
    small = cv2.GaussianBlur(cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA), (5, 5), 0)
    return cv2.cvtColor(small.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)


def change(a, b) -> float:
    """0 for the same view, about 1 for a different dish: mean Lab difference once brightness is matched."""
    a = a.copy()
    a[..., 0] -= a[..., 0].mean() - b[..., 0].mean()
    return float(np.abs(a - b).mean() / 20)


class Tracer:
    """`tracer(box, logged, until, deadline=...)` for Proofs.run_once: the pictures of whoever last used the
    dish now at `box` (logged at `logged`, photographed late at `until`), or why nobody can be named."""

    def __init__(self, journal, footage: Callable[[], Footage], embed: Callable[[list], np.ndarray],
                 areas: Callable[[], list[dict]]):
        self.journal, self.footage, self.embed, self.areas = journal, footage, embed, areas

    def __call__(self, box: list, logged: float, until: float, *, deadline: float | None = None) -> dict | str:
        floor = logged - HISTORY
        trace = _Trace(People(self.journal.frames(floor, until)), self.footage(), self.embed, self.areas(),
                       floor, deadline if deadline is not None else time.monotonic() + 1200)
        try:
            return trace.follow(box, logged, until, ())
        except _OutOfTime:
            return "the trace reached its time budget"


class _Trace:
    def __init__(self, people: People, footage: Footage, embed, areas: list[dict], floor: float, deadline: float):
        self.people, self.footage, self.embed, self.areas = people, footage, embed, areas
        self.floor, self.deadline = floor, deadline

    def tick(self) -> None:
        if time.monotonic() >= self.deadline:
            raise _OutOfTime

    def look(self, at: float, box: list):
        self.tick()
        image = self.footage.image(round(at * 2) / 2)
        return None if image is None else look(image, box)

    def crop(self, at: float, box: list):
        return _crop(self.footage.image(round(at * 2) / 2), box, 0.005)

    def follow(self, box: list, seen: float, horizon: float, moves: tuple) -> dict | str:
        visit = self.arrival(box, seen, horizon)
        if isinstance(visit, str):
            return visit
        matches = self.sources(visit, box)
        sources = [s for s in matches if s.rested]
        if not sources:
            if any(s.rested is None for s in matches):
                return "it came from a spot the camera could not see long enough to tell who used it"
            return self.user(visit, box, moves)
        if len(moves) >= HOPS:
            return "the dish was moved more times than can be followed back"
        first = None
        for s in sources[:LOOKALIKES]:
            trail = self.follow(s.box, s.seen, s.seen + NEAR, (*moves, {"at": visit.start, "from": s.box, "to": box}))
            if len(sources) == 1:
                return trail
            # Look-alikes name someone only if every trail ends at the same use: stop at the first that doesn't.
            if isinstance(trail, str) or (first is not None and (tuple(trail["box"]), trail["left"]["at"]) != first):
                return "look-alike dishes were moved together and lead back to different people"
            first = (tuple(trail["box"]), trail["left"]["at"])
            found = trail
        return found

    def arrival(self, box: list, seen: float, horizon: float) -> Visit | str:
        """The newest visit before `seen` that put the dish at `box`."""
        dish = None
        for visit in self.people.visits(box, seen, self.floor, horizon):
            a, b = self.look(visit.before, box), self.look(visit.after, box)
            if a is None or b is None:
                return "the recording is missing around the dish"
            dish = dish if dish is not None else visit.after
            if change(a, b) < CHANGED:
                continue
            if visit.blind:
                return "the dish arrived while the camera history has a gap"
            alone, shared, _ = self.people.stays(visit, box)
            if alone + shared < USE:
                vectors = self.embed([self.crop(visit.before, box), self.crop(dish, box)])
                if float(vectors[0] @ vectors[1]) >= SAME:
                    continue  # a brief visit beside the same dish; staying with it for minutes is using it
            return visit
        return "the dish was already there when the recording starts"

    def cells(self, size: tuple[float, float]) -> list[list[float]]:
        """Dish-sized spots on every surface a used dish could have come from."""
        w, h = size
        out = set()
        for area in self.areas:
            if area["role"] not in ("dirty", "sink"):
                continue
            ax, ay, aw, ah = area["box"]
            for i in range(max(1, int((aw - w) / (w / 2)) + 1)):
                for j in range(max(1, int((ah - h) / (h / 2)) + 1)):
                    x, y = min(ax + i * w / 2, 1 - w), min(ay + j * h / 2, 1 - h)
                    out.add((round(x, 4), round(y, 4), w, h))
        return [list(c) for c in sorted(out)]

    def sources(self, visit: Visit, box: list) -> list[Source]:
        """Spots this visit emptied of something that looks like the dish, best match first."""
        changed = []
        for cell in self.cells((max(box[2], SMALLEST[0]), max(box[3], SMALLEST[1]))):
            if _coverage(box, cell) > .5:
                continue  # the dish's own spot
            # Clear just before this visit and again once the dish was set down, with someone at it in
            # between: carrying a dish empties its spot within the same few seconds. A spot emptied
            # during the visit can stay clear right through to after it.
            views = self.people.clear_views(cell, visit.start - NEAR - REST, visit.end + NEAR)
            last = next(((v, t) for v in reversed(views) if v[0] <= (t := min(v[1], visit.start) - EDGE)), None)
            first = next(((v, t) for v in views if (t := max(v[0], visit.end) + EDGE) <= v[1]), None)
            if last is None or first is None or last[0] == first[0]:
                continue
            seen, then = last[1], first[1]
            a, b = self.look(seen, cell), self.look(then, cell)
            if a is not None and b is not None and (score := change(a, b)) >= CHANGED:
                changed.append((score, cell, seen, then, views))
        if not changed:
            return []
        changed = sorted(changed, key=lambda c: -c[0])[:CANDIDATES]
        vectors = self.embed([self.crop(visit.after, box)] + [self.crop(t, cell) for _, cell, seen, then, _ in changed for t in (seen, then)])
        changed = [(score, cell, seen, views) for score, cell, seen, then, views in changed]
        matches = []
        for k, (_, cell, seen, views) in enumerate(changed):
            was, now = float(vectors[1 + 2 * k] @ vectors[0]), float(vectors[2 + 2 * k] @ vectors[0])
            if was >= MATCH and was - now >= DROP:
                matches.append(Source(cell, seen, was, self.rested(cell, seen, views)))
        kept: list[Source] = []
        for source in sorted(matches, key=lambda s: -s.likeness):
            # Overlapping spots are one object seen through neighbouring windows.
            if all(_coverage(source.box, k.box) == 0 for k in kept):
                kept.append(source)
        return kept

    def rested(self, cell: list, seen: float, views: list[tuple[float, float]]) -> bool | None:
        """Whether the clear view REST before `seen` shows the spot as it was at `seen`."""
        earlier = next((t for v in reversed(views) if v[0] <= (t := min(v[1], seen - REST))), None)
        if earlier is None:
            return None
        a, b = self.look(earlier, cell), self.look(seen, cell)
        return None if a is None or b is None else change(a, b) < CHANGED

    def user(self, visit: Visit, box: list, moves: tuple) -> dict | str:
        """Whoever this visit shows with the dish. Alone with it for minutes: used it. Only setting it
        down, with no visible spot it came from: brought it from out of view."""
        alone, shared, frames = self.people.stays(visit, box)
        if shared and shared >= alone:
            return "several people were at the dish together"
        if not frames:
            return "nobody was seen at the dish when it arrived"
        if alone >= USE:
            use = "cooking" if any(stove(a) and rests_in(box, a) for a in self.areas) else "using"
            # Whoever was with it last: a later cook takes a pan over (household rule).
            frames = [f for f in frames if f[0] >= frames[-1][0] - USE]
        else:
            use = "brought"
        picks = sorted(frames, key=lambda f: (_coverage(box, _body(f[1])), -f[0]))[:4]
        pictures, taken = [], set()
        for at, _ in picks:  # least of the body over the dish first; the checker confirms the person
            if (found := self.footage.frame(at)) and found[0] not in taken:
                taken.add(found[0])
                pictures.append({"at": found[0], "jpeg": found[1]})
        before, left = self.footage.frame(visit.before), self.footage.frame(visit.after)
        if before is None or left is None or not pictures:
            return "the recording is missing around the dish"
        return {"before": {"at": before[0], "jpeg": before[1]}, "putting": pictures[0], "candidates": pictures[1:],
                "left": {"at": left[0], "jpeg": left[1]}, "box": box, "use": use, "stay": round(alone),
                "moves": list(moves), "method": "visits"}
