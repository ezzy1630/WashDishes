"""Find a dish's arrival from detector history and refine it in the full-rate recording.

The legacy patch-only finder remains available for footage older than the journal.
It decodes only keyframes (about one every two seconds) by a low-priority single-threaded FFmpeg that
returns just a 32x32 grey crop of the spot, so a search costs one niced core for one to three minutes.
The spot is compared with two looks: the dish (sampled after the camera logged it) and the spot before
it (the last time, walking back, that the spot held steady at something else: the bare counter or
another item). A frame like neither is something over the spot, usually a person; the put-down is the
change from "before" through "covered" to "dish". Comparing with both looks, rather than a fixed
threshold, keeps day/night lighting and contrasty counters from reading as a person.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .journal import Journal
from .vision import box_iou

PRESENT_MINUTES = 15      # how long after logging the dish's look is sampled
DENSE_MINUTES = 45        # searched every 30 s back from logging, then every 2 min
SEARCH_HOURS = 23         # the recording keeps 24 h
MIN_CHANGE = 0.12         # smallest difference that counts as a different look
STEADY_RUN = 3            # consecutive alike samples that are not the dish: the spot before it
MAX_GAP = 120             # seconds from the last "before" view to the dish being there: longer means the
                          # spot was hidden while it arrived, and the picture would only show who stood in front
SIDE = 32
SEARCH_SECONDS = 480
SEARCH_TIMEOUT = "the put-down search reached its time budget"


class _SearchTimeout(Exception):
    pass


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _SearchTimeout


@dataclass
class Sample:
    at: float             # Unix time
    patch: object         # normalised SIDE x SIDE float array
    diff: float = 0.0
    state: str = ""       # dish / before / covered, while examining the put-down


def segments(buffer: Path) -> list[tuple[float, float, Path]]:
    """(start, end, path) per finished segment; a segment ends at its mtime and starts where the previous ended."""
    ends = sorted((p.stat().st_mtime, p) for p in buffer.glob("*.ts") if not p.is_symlink())
    return [(ends[i - 1][0], t, p) for i, (t, p) in enumerate(ends) if i and t - ends[i - 1][0] < 60]


def _ffmpeg(path: Path, output: list[str], first_only: bool) -> tuple[list[float], bytes]:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-threads", "1", "-filter_threads", "1", "-skip_frame", "nokey", "-i", str(path),
               *(["-frames:v", "1"] if first_only else []), "-fps_mode", "passthrough", *output, "-"]
    try:
        run = subprocess.run(command, capture_output=True, timeout=60, preexec_fn=lambda: os.nice(15))
    except (OSError, subprocess.TimeoutExpired):
        return [], b""
    return [float(t) for t in re.findall(rb"pts_time:\s*([0-9.]+)", run.stderr)], run.stdout


def samples(segment: tuple[float, float, Path], box: list[float], first_only: bool = False) -> list[Sample]:
    import numpy as np
    start, end, path = segment
    x, y, w, h = box
    crop = f"crop=iw*{w:.5f}:ih*{h:.5f}:iw*{x:.5f}:ih*{y:.5f},scale={SIDE}:{SIDE}:flags=area,format=gray,showinfo"
    times, data = _ffmpeg(path, ["-vf", crop, "-f", "rawvideo"], first_only)
    out = []
    for k in range(min(len(times), len(data) // SIDE ** 2)):
        q = np.frombuffer(data, np.uint8, SIDE ** 2, k * SIDE ** 2).reshape(SIDE, SIDE).astype(np.float32)
        out.append(Sample(min(end, start + times[k]), (q - q.mean()) / (q.std() + 1e-3)))
    return out


def photos(segment: tuple[float, float, Path]) -> list[tuple[float, bytes]]:
    """Full-resolution keyframes of one segment as JPEG."""
    start, end, path = segment
    times, data = _ffmpeg(path, ["-vf", "showinfo", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg"], False)
    jpegs, i = [], 0
    while (s := data.find(b"\xff\xd8", i)) >= 0 and (e := data.find(b"\xff\xd9", s)) >= 0:
        jpegs.append(data[s:e + 2])
        i = e + 2
    return [(min(end, start + t), j) for t, j in zip(times, jpegs)]


def decoded_photos(segment: tuple[float, float, Path], *, fps: float | None = None,
                   t0: float | None = None, t1: float | None = None) -> list[tuple[float, bytes]]:
    """Decode actual frames, including inter-keyframes, in one bounded segment/window."""
    start, end, path = segment
    offset = max(0.0, (t0 if t0 is not None else start) - start)
    duration = min(end, t1 if t1 is not None else end) - start - offset
    if duration <= 0:
        return []
    # MPEG-TS seeking can jump to the next keyframe. Decode the small segment and
    # trim after decoding so a sub-second reach is retained, with its real PTS.
    filters = [f"trim=start={offset}:end={offset + duration}"] + ([f"fps={fps}"] if fps else []) + ["showinfo", "setpts=PTS-STARTPTS"]
    command = ["nice", "-n", "10", "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-threads", "1",
               "-i", str(path), "-an", "-filter_threads", "1",
               "-vf", ",".join(filters), "-fps_mode", "passthrough", "-q:v", "4", "-threads", "1",
               "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    try:
        run = subprocess.run(command, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if run.returncode:
        return []
    times = [float(t) for t in re.findall(rb"pts_time:\s*([0-9.]+)", run.stderr)]
    pictures, i = [], 0
    for at in times:
        s = run.stdout.find(b"\xff\xd8", i)
        e = run.stdout.find(b"\xff\xd9", s)
        if s < 0 or e < 0:
            break
        pictures.append((start + at, run.stdout[s:e + 2]))
        i = e + 2
    return pictures


def _distance(a, b) -> float:
    import numpy as np
    return float(np.mean(np.abs(a - b)))


def find_putdown(buffer: Path, box: list[float], logged: float, present_until: float, *,
                 earlier_than: float | None = None, deadline: float | None = None) -> dict | str:
    deadline = time.monotonic() + SEARCH_SECONDS if deadline is None else deadline
    try:
        _check_deadline(deadline)
        result = _find_putdown(buffer, box, logged, present_until, deadline=deadline)
        _check_deadline(deadline)
        # The legacy finder has no episode cursor. Preserve its original anchor and
        # refuse a repeated/later result rather than silently treating another dish
        # at a fabricated logging time as this one.
        if isinstance(result, dict) and earlier_than is not None and not result["putting"]["at"] < earlier_than:
            return "the patch search did not find an earlier arrival"
        return result
    except _SearchTimeout:
        return SEARCH_TIMEOUT


def _find_putdown(buffer: Path, box: list[float], logged: float, present_until: float, *, deadline: float) -> dict | str:
    """Unix times of the spot before the dish ("before"), the person putting it down ("putting") and the
    dish left there ("left"), each with its JPEG; or, when the footage does not show it, why not.

    `logged` is when the camera recorded the dish and `present_until` a later time it was still there;
    together they bound where the dish is known to be present.
    """
    import numpy as np
    segs = segments(buffer)
    def patches(seg, *, first_only=False):
        _check_deadline(deadline)
        result = samples(seg, box, first_only=first_only)
        _check_deadline(deadline)
        return result
    # The dish's own look: the per-pixel median over samples after logging is robust to someone
    # briefly standing over it; the typical distance from that median is the noise floor.
    after = [seg for seg in segs if logged <= seg[0] <= min(present_until, logged + PRESENT_MINUTES * 60)]
    present = [s for seg in after[::5] for s in patches(seg, first_only=True)]
    if len(present) < 3:
        return "not enough footage of the dish after the camera logged it"
    dish = np.median(np.stack([s.patch for s in present]), axis=0)
    threshold = max(MIN_CHANGE, 2.5 * float(np.median([_distance(s.patch, dish) for s in present])))

    # Walk back until the spot holds steady at something that is not the dish. A person passing or
    # cooking moves between samples 30 s apart; a bare counter or another item stays put.
    run, last, steady = [], None, None
    for seg in reversed([s for s in segs if logged - SEARCH_HOURS * 3600 <= s[0] <= logged]):
        step = 30 if logged - seg[0] < DENSE_MINUTES * 60 else 120
        if last is not None and last - seg[0] < step:
            continue
        for s in patches(seg, first_only=True):
            last = s.at
            if _distance(s.patch, dish) <= threshold:
                run = []
                continue
            run.append(s)
            tail = run[-STEADY_RUN:]
            if len(tail) == STEADY_RUN and all(_distance(a.patch, b.patch) <= threshold for i, a in enumerate(tail) for b in tail[i + 1:]):
                steady = tail
        if steady:
            break
    if not steady:
        return f"the dish was already there {SEARCH_HOURS} hours before the camera logged it, or its spot never held still without it"
    empty = np.median(np.stack([s.patch for s in steady]), axis=0)
    empty_threshold = max(threshold, 2.5 * float(np.median([_distance(s.patch, empty) for s in steady])))
    if _distance(empty, dish) <= threshold:
        return "the spot looked the same before and after the dish"

    # Every keyframe from that steady view until the dish is back and settled.
    window, used = [], []
    for seg in segs:
        if seg[1] < steady[0].at - 2 or seg[0] > logged + 60:
            continue
        used.append(seg)
        for s in patches(seg):
            if s.at >= steady[0].at - 2:
                s.diff = min(_distance(s.patch, dish), _distance(s.patch, empty))
                s.state = "dish" if _distance(s.patch, dish) <= threshold else "before" if _distance(s.patch, empty) <= empty_threshold else "covered"
                window.append(s)
        settled = [s for s in window if s.state == "dish"]
        if len(settled) >= 3 and settled[-1].at - settled[0].at >= 20:
            break
    back = next((s for s in window if s.state == "dish"), None)
    if back is None:
        return "the dish never looked settled after the spot was empty"
    before = max((s for s in window if s.at < back.at and s.state == "before"), key=lambda s: s.at, default=None)
    if before is None:
        return "no clear view of the spot just before the dish appeared"
    covered = [s for s in window if before.at < s.at < back.at and s.state == "covered"]
    if not covered:
        # The dish appeared without anything over the spot: lighting or a slide in from out of view.
        # That is not a picture of anyone.
        return "nobody covered the spot as the dish appeared"
    if back.at - before.at > MAX_GAP:
        return f"the spot was hidden for {int(back.at - before.at)} s while the dish arrived"
    putting = max(covered, key=lambda s: s.diff)
    left = min((s for s in window if back.at <= s.at <= back.at + 60 and s.state == "dish"), key=lambda s: _distance(s.patch, dish))
    chosen = {"before": before, "putting": putting, "left": left}
    # Decode full pictures only for the few segments that hold the chosen moments.
    pictures = {}
    for seg in used:
        if any(seg[0] - 1 <= s.at <= seg[1] + 1 for s in chosen.values()):
            _check_deadline(deadline)
            pictures.update(photos(seg))
    result = {"threshold": threshold, "method": "patch"}
    for key, s in chosen.items():
        at, jpeg = min(pictures.items(), key=lambda p: abs(p[0] - s.at), default=(None, None))
        if jpeg is None or abs(at - s.at) > 1:
            return "the full pictures could not be decoded"
        result[key] = {"at": s.at, "diff": s.diff, "jpeg": jpeg}
    return result


JOURNAL_TOO_NEW = "the dish was already present when the detection journal began"
# The full-frame model repeatedly calls the same pan bowl/plate/pot as it tilts.
FAMILIES = {"pan": "vessel", "pot": "vessel", "plate": "vessel", "bowl": "vessel",
            "cup": "cup", "fork": "utensil", "knife": "utensil", "spoon": "utensil"}


def _overlap(box: list[float], other: list[float]) -> float:
    x, y, w, h = box
    px, py, pw, ph = other
    return max(0, min(x + w, px + pw) - max(x, px)) * max(0, min(y + h, py + ph) - max(y, py)) / (w * h)


def _reach(box: list[float], person: list[float]) -> float:
    x, y, w, h = box
    px, py, pw, ph = person
    return max(px - x - w / 2, 0, x + w / 2 - px - pw) + max(py - y - h / 2, 0, y + h / 2 - py - ph)


def _matches(detection: dict, box: list[float], family: str | None) -> bool:
    if detection["kind"] == "person" or (family and FAMILIES.get(detection["kind"]) != family):
        return False
    other = detection["box"]
    # A large nearby pan must not stand in for a small cup nested inside its box.
    ratio = other[2] * other[3] / (box[2] * box[3])
    if not .3 <= ratio <= 3.3:
        return False
    centers = (abs(other[0] + other[2] / 2 - box[0] - box[2] / 2) / max(other[2], box[2]),
               abs(other[1] + other[3] / 2 - box[1] - box[3] / 2) / max(other[3], box[3]))
    return box_iou(box, other) >= .3 or max(centers) <= .28


def _spot(frame: dict, box: list[float], family: str | None) -> tuple[bool, list[dict]]:
    present = any(_matches(d, box, family) for d in frame["detections"])
    people = [d for d in frame["detections"] if d["kind"] == "person" and d["score"] >= .2 and _reach(box, d["box"]) <= .1]
    return present, people


@dataclass
class Arrival:
    before: float
    appeared: float
    clear: float
    left_behind: bool
    family: str | None
    hidden_seconds: float = 0


@dataclass
class _EarlierPatch:
    before: float
    reference_times: list[float]


def journal_arrival(frames: Iterable[dict], box: list[float], anchor: float, *, family: str | None = None,
                    flicker_seconds: float = 12, max_sample_gap: float = 8,
                    absence_is_clear: Callable[[float], bool | None] | None = None,
                    minimum_detections: int = 3) -> Arrival | str:
    """Find the start of the presence episode containing anchor, in constant memory.

    A missing detection is absence only after sustained clear views. A covered
    spot preserves presence; missing footage does not. Person continuity through
    a long initial occlusion supports a departure caption, never a named identity.
    """
    before = first = clear = absent_since = previous_at = None
    episode_before = None
    count = 0
    prior_person = None
    single_person = True
    covered_since = None
    episode_hidden = 0.0
    episode_single = False
    gap = False
    last_people, people_at = [], float("-inf")
    next_absence_check = float("-inf")
    for frame in frames:
        at = frame["at"]
        if at > anchor:
            break
        if previous_at is not None and at - previous_at > max_sample_gap:
            before = first = clear = absent_since = episode_before = covered_since = None
            count, prior_person, single_person, gap = 0, None, True, True
            last_people, people_at = [], float("-inf")
            next_absence_check = float("-inf")
        previous_at = at
        present, people = _spot(frame, box, family)
        if people:
            last_people, people_at = people, at
        elif at - people_at <= 3:
            # One missed seated-person detection is not an empty table.
            people = last_people
        covered = any(_overlap(box, p["box"]) > .15 for p in people)
        if covered and clear is None:
            if covered_since is None:
                covered_since = at
            if len(people) != 1:
                single_person = False
            elif prior_person is not None:
                old_id, new_id = prior_person.get("track_id"), people[0].get("track_id")
                if (old_id is not None and new_id is not None and old_id != new_id) or box_iou(prior_person["box"], people[0]["box"]) < .15:
                    single_person = False
            prior_person = people[0] if len(people) == 1 else None
        if present:
            if first is None:
                first, episode_before, count = at, before, 0
                episode_hidden = at - covered_since if covered_since is not None else 0
                episode_single = single_person and prior_person is not None
            count += 1
            absent_since = None
            next_absence_check = float("-inf")
            if clear is None and not covered:
                clear = at
                # A dish can first be detected under a departing person's arm.
                if covered_since is not None:
                    episode_hidden = at - covered_since
                    episode_single = single_person and prior_person is not None
        elif any(_matches(d, box, None) for d in frame["detections"]):
            # A class flip while cooking, or another dish at this spot, is not
            # evidence of an empty surface. Do not reuse an earlier empty view.
            absent_since = None
            if first is None:
                before = None
        elif not covered:
            if absent_since is None:
                absent_since = at
            if first is None or at - absent_since >= flicker_seconds and at >= next_absence_check:
                if first is not None and absence_is_clear is not None:
                    changed = absence_is_clear(at)
                    if changed is None:
                        return "the recording cannot resolve the detector's gaps in dish visibility"
                    if not changed:
                        next_absence_check = at + 60
                        continue
                before = at
                first = clear = episode_before = None
                count, covered_since, prior_person, single_person = 0, None, None, True
        else:
            absent_since = None
    if first is None or clear is None or count < minimum_detections:
        return "no sustained clear dish detection near the camera's logged time"
    if episode_before is None:
        return "the recording or detector journal has a gap before the dish appears" if gap else JOURNAL_TOO_NEW
    hidden = max(episode_hidden, clear - first if episode_hidden else 0)
    if hidden > MAX_GAP and not episode_single:
        return "the spot was hidden by different or untracked people while the dish appeared"
    return Arrival(episode_before, first, clear, hidden > MAX_GAP, family, hidden)


def _body_cover(box: list[float], person: list[float]) -> float:
    px, py, pw, ph = person
    head = [px + .2 * pw, py, .6 * pw, .3 * ph]
    torso = [px + .25 * pw, py + .2 * ph, .5 * pw, .55 * ph]
    return max(_overlap(box, head), _overlap(box, torso))


def _contact_candidates(candidates: list[dict], box: list[float]) -> list[dict]:
    # Keep a release instant even if the person's detector box shrinks as the
    # arm retracts. Proximity to a neighboring sink alone is not a reach.
    touching = [c for c in candidates if _overlap(box, c["person_box"]) >= .5]
    return [c for c in candidates if any(abs(c["at"] - t["at"]) <= .75
            and box_iou(c["person_box"], t["person_box"]) >= .15 for t in touching)]


def _jpeg_patch(jpeg: bytes, box: list[float]):
    import io

    import numpy as np
    from PIL import Image
    with Image.open(io.BytesIO(jpeg)) as image:
        x, y, w, h = box
        crop = image.crop((int(x * image.width), int(y * image.height), int((x + w) * image.width), int((y + h) * image.height)))
        q = np.asarray(crop.convert("L").resize((SIDE, SIDE)), dtype=np.float32)
    return (q - q.mean()) / (q.std() + 1e-3)


def find_putdown_journal(journal: Journal, buffer: Path, box: list[float], logged: float, present_until: float,
                         *, kind: str | None = None, people: Callable[[bytes], list[dict]] | None = None,
                         earlier_than: float | None = None, deadline: float | None = None) -> dict | str:
    """Find an arrival strictly before `earlier_than`, with one shared wall-time budget.

    The cutoff re-anchors appearance to an earlier clear view; it does not move the
    dish's logging time or its history limit. Every alternate Putting frame obeys it.
    """
    if earlier_than is not None and not math.isfinite(earlier_than):
        raise ValueError("The arrival cutoff must be finite.")
    deadline = time.monotonic() + SEARCH_SECONDS if deadline is None else deadline
    try:
        _check_deadline(deadline)
        result = _find_putdown_journal(journal, buffer, box, logged, present_until,
                                      kind=kind, people=people, earlier_than=earlier_than, deadline=deadline)
        _check_deadline(deadline)
        return result
    except _SearchTimeout:
        return SEARCH_TIMEOUT


def _find_putdown_journal(journal: Journal, buffer: Path, box: list[float], logged: float, present_until: float,
                          *, kind: str | None, people: Callable[[bytes], list[dict]] | None,
                          earlier_than: float | None, deadline: float) -> dict | str:
    """Journal arrival plus a full-frame-rate view of the final reach/departure.

    `people` rechecks two clear views and up to twelve short-listed pictures per
    refinement window (at most eight windows), using the already loaded detector.
    The caller may fall back to find_putdown only for JOURNAL_TOO_NEW; other
    missing-history or visibility failures remain rejections.
    """
    bounds = journal.bounds()
    if bounds is None or bounds[0] >= logged:
        return JOURNAL_TOO_NEW
    # Infer a coarse family from the best nearby match when the caller only knows a box.
    if earlier_than is not None and kind is None:
        logged_matches = [(box_iou(box, d["box"]) * d["score"] / (1 + abs(f["at"] - logged) / 60), d["kind"])
                          for f in journal.frames(max(bounds[0], logged - 30), min(present_until, logged + 180))
                          for d in f["detections"] if _matches(d, box, None)]
        # Rewind the appearance, not the kind of dish we are looking for.
        kind = max(logged_matches)[1] if logged_matches else None
    search_at = logged if earlier_than is None else min(logged, math.nextafter(earlier_than, -math.inf))
    search_end = min(present_until, logged + 180) if earlier_than is None else min(present_until, search_at)
    def usable(frame):
        # The rejected Before can sit beside a long handling sequence. A retry
        # needs the last clear match, not a crop of the person covering the dish.
        return earlier_than is None or not any(_overlap(box, p["box"]) > .15
                                               for p in _spot(frame, box, FAMILIES.get(kind))[1])
    near = list(journal.frames(max(bounds[0], search_at - 30), search_end))
    choices = [(box_iou(box, d["box"]) * d["score"] / (1 + abs(f["at"] - search_at) / 60), f["at"], d["kind"])
               for f in near if usable(f) for d in f["detections"] if _matches(d, box, FAMILIES.get(kind))]
    if not choices:
        # A dish can be logged long after placement, then hidden by a stack.
        # Its last actual detection is a better anchor than inventing absence.
        last_match = None
        for frame in journal.frames(max(bounds[0], logged - SEARCH_HOURS * 3600), search_at):
            _check_deadline(deadline)
            if usable(frame) and any(_matches(d, box, FAMILIES.get(kind)) for d in frame["detections"]):
                last_match = frame["at"]
        if last_match is None:
            return "the journal detector did not see this dish in the available history"
        near = list(journal.frames(max(bounds[0], last_match - 30), min(search_end, last_match + 5)))
        choices = [(box_iou(box, d["box"]) * d["score"] / (1 + abs(f["at"] - last_match) / 60), f["at"], d["kind"])
                   for f in near if usable(f) for d in f["detections"] if _matches(d, box, FAMILIES.get(kind))]
    _, anchor, detected_kind = max(choices)
    family = FAMILIES.get(kind or detected_kind)
    segs = segments(buffer)
    import numpy as np
    ends = [s[1] for s in segs]

    @lru_cache(maxsize=12)
    def segment_patches(index):
        _check_deadline(deadline)
        result = samples(segs[index], box)
        _check_deadline(deadline)
        return result

    def patch_at(at):
        index = bisect_right(ends, at)
        if index >= len(segs) or segs[index][0] > at:
            return None
        nearest = min(segment_patches(index), key=lambda s: abs(s.at - at), default=None)
        return nearest.patch if nearest and abs(nearest.at - at) <= 2.5 else None

    reference_patches, reference_times = [], []
    for _, at, _ in sorted(choices, reverse=True):
        if any(abs(at - prior) < 3 for prior in reference_times):
            continue
        frame = min(near, key=lambda f: abs(f["at"] - at))
        if any(_overlap(box, p["box"]) > .15 for p in _spot(frame, box, family)[1]):
            continue
        patch = patch_at(at)
        if patch is not None:
            reference_patches.append(patch)
            reference_times.append(at)
        if len(reference_patches) >= 3:
            break
    if not reference_patches:
        return "no clear recorded view of the detected dish near its logged time"
    continuity_patch = np.median(np.stack(reference_patches), axis=0)
    continuity_threshold = min(.35, max(.2, 3 * float(np.median([_distance(p, continuity_patch) for p in reference_patches]))))
    patch_checks = 0

    def absence_is_clear(at):
        nonlocal patch_checks
        patch_checks += 1
        patch = patch_at(at)
        return None if patch is None else _distance(patch, continuity_patch) > continuity_threshold

    # Include enough following samples to establish a run, but never jump to a later replacement.
    anchor = min(present_until if earlier_than is None else search_end, anchor + 5)
    arrival = journal_arrival(journal.frames(max(bounds[0], logged - SEARCH_HOURS * 3600), anchor), box, anchor,
                              family=family, absence_is_clear=absence_is_clear, minimum_detections=1)
    rewind_checks = 0

    def rewind(start):
        """A late recognition is an anchor, not a placement timestamp."""
        nonlocal rewind_checks
        same = start
        at = start - 4
        lower = max(segs[0][0], logged - SEARCH_HOURS * 3600) if segs else start
        while at >= lower:
            _check_deadline(deadline)
            if rewind_checks >= 800:
                return "the dish's appearance needs more backwards tracing than this proof attempt allows"
            rewind_checks += 1
            patch = patch_at(at)
            if patch is None:
                return "the recording has a gap while tracing the dish backwards"
            difference = _distance(patch, continuity_patch)
            if difference <= continuity_threshold:
                same = at
            elif difference > max(.25, 1.5 * continuity_threshold):
                neighbors = list(journal.frames(at - 1.5, at + 1.5))
                hidden = any(_body_cover(box, d["box"]) > .3 for f in neighbors for d in f["detections"] if d["kind"] == "person")
                earlier = patch_at(at - 4)
                if not hidden and earlier is not None and _distance(patch, earlier) <= continuity_threshold:
                    dense = [p for i, seg in enumerate(segs) if seg[1] > at and seg[0] <= same
                             for p in segment_patches(i) if at < p.at <= same
                             and _distance(p.patch, continuity_patch) <= continuity_threshold]
                    appeared = min((p.at for p in dense), default=same)
                    return Arrival(at, appeared, same, False, family)
            at -= 30 if start - at < DENSE_MINUTES * 60 else 120
        return "the dish was already present at the start of the available footage"

    if isinstance(arrival, str):
        arrival = rewind(anchor)
    elif (before_patch := patch_at(arrival.before)) is not None and _distance(before_patch, continuity_patch) <= max(.25, 1.5 * continuity_threshold):
        arrival = rewind(arrival.clear)
    if isinstance(arrival, str):
        return arrival
    def refine(arrival):
        _check_deadline(deadline)
        if arrival.clear < bounds[0]:
            return JOURNAL_TOO_NEW
        covered_until = arrival.before
        for start, end, _ in segs:
            if end <= covered_until:
                continue
            if start > covered_until + 2:
                break
            covered_until = end
            if covered_until >= arrival.clear:
                break
        if covered_until < arrival.clear:
            return "the recording has a gap across the possible arrival"
        windows = [(arrival.before - 45, arrival.before - 42), (arrival.before - 8, arrival.before + 1.5),
                   (arrival.appeared - 12, arrival.appeared + 10), (arrival.clear - 8, arrival.clear + 10)]
        merged = []
        for t0, t1 in sorted(windows):
            if merged and t0 <= merged[-1][1] + 3:
                merged[-1][1] = max(t1, merged[-1][1])
            else:
                merged.append([t0, t1])
        around = [frame for t0, t1 in merged for frame in journal.frames(t0 - 1.5, t1 + 1.5)]
        around = sorted({frame["at"]: frame for frame in around}.values(), key=lambda frame: frame["at"])
        if not around:
            return "no journal frames around the arrival"

        def observation(at: float) -> dict | None:
            # Only a short window is decoded; a binary search avoids work proportional to a long meal.
            index = bisect_left(times, at)
            nearest = min(around[max(0, index - 1):index + 1], key=lambda f: abs(f["at"] - at), default=None)
            return nearest if nearest and abs(nearest["at"] - at) <= 1.25 else None

        times = [f["at"] for f in around]
        pictures = {}
        for t0, t1 in merged:
            for seg in segs:
                if seg[1] > t0 and seg[0] < t1:
                    _check_deadline(deadline)
                    pictures.update(decoded_photos(seg, t0=t0, t1=t1))
                    _check_deadline(deadline)
        states = []
        for at, jpeg in sorted(pictures.items()):
            frame = observation(at)
            if frame is not None:
                present, nearby = _spot(frame, box, None)
                # Adjacent detections veto a false empty view caused by person flicker.
                neighbors = around[bisect_left(times, at - 1.25):bisect_right(times, at + 1.25)]
                covered = any(_overlap(box, d["box"]) > .15 for f in neighbors for d in f["detections"] if d["kind"] == "person")
                states.append((at, jpeg, present, nearby, covered))
        patches = {s[0]: _jpeg_patch(s[1], box) for s in states}
        # The coarse trace already anchors the after look. Full-rate JPEG crops
        # use a different resize path from FFmpeg's patches: compare like to like.
        reference_states = [s for s in states if s[0] >= arrival.clear and not s[4]]
        reference = [patches[s[0]] for s in reference_states]
        if len(reference) < 3:
            return "the footage has no clear empty-before and visible-after frames at the journal arrival"
        dish_patch = np.median(np.stack(reference), axis=0)
        threshold = max(MIN_CHANGE, 2.5 * float(np.median([_distance(p, dish_patch) for p in reference])))
        # Recognition often lags a quick deposit by several seconds. A negative
        # detector result alone must not label the already-present dish as Before.
        distances = {at: _distance(patch, dish_patch) for at, patch in patches.items()}
        run, left = [], None
        for state in states:
            if state[0] < arrival.appeared - 12 or state[4] or distances[state[0]] > threshold:
                run = []
                continue
            if run and state[0] - run[-1][0] > .5:
                run = []
            run.append(state)
            if state[0] - run[0][0] >= .35:
                left = run[0]
                break
        if left is None:
            return "the dish did not settle into a clear view near the journal arrival"
        settled_at = left[0]
        # Give the short handling sequence room to finish. A single clear instant
        # under a moving hand is not the best view of the dish left behind.
        left = next((s for s in states if s[0] >= settled_at + 3 and not s[4]
                     and distances[s[0]] <= threshold), None)
        if left is None:
            return "the footage has no clear view after the handling finished"
        # Stay outside the handling seconds: a different vessel held over the
        # spot can fool both a negative journal frame and the later crop check.
        clear_before = [s for s in states if s[0] <= settled_at - 5 and not s[2] and not s[4]]
        if not clear_before:
            return "the footage has no clear view of the spot before the arrival"
        # Anchor empty to the early, steady view, not the last patch unlike the dish:
        # that last patch can be the dish itself still moving into position.
        early = [patches[s[0]] for s in clear_before if s[0] <= clear_before[0][0] + .5]
        empty_patch = np.median(np.stack(early), axis=0)
        empty_threshold = max(MIN_CHANGE, 2.5 * float(np.median([_distance(p, empty_patch) for p in early])))
        before = max((s for s in clear_before if _distance(patches[s[0]], empty_patch) <= empty_threshold
                      and distances[s[0]] > max(.25, threshold * 1.5)), default=None, key=lambda s: s[0])
        if before is None:
            return _EarlierPatch(clear_before[0][0], [s[0] for s in reference_states[::max(1, len(reference_states) // 3)]][:3])
        # A negative frame can still contain the old dish. Confirm the proposed
        # empty look against recent strong detections before accepting a later
        # uncovering or another item stacked inside the dish as its arrival.
        before_patch = patch_at(before[0])
        earlier_matches = [(box_iou(box, d["box"]) * d["score"], f["at"])
                           for f in journal.frames(before[0] - 120, before[0] - 3)
                           for d in f["detections"] if _matches(d, box, family) and box_iou(box, d["box"]) >= .6]
        checked_times = []
        for _, at in sorted(earlier_matches, reverse=True):
            if any(abs(at - prior) < 5 for prior in checked_times):
                continue
            checked_times.append(at)
            old_patch = patch_at(at)
            if before_patch is not None and old_patch is not None and _distance(before_patch, old_patch) <= continuity_threshold:
                return _EarlierPatch(at, [before[0]])
            if len(checked_times) >= 3:
                break
        if people is not None and any(_body_cover(box, p["box"]) > .6 for state in (before, left)
                                      for p in people(state[1]) if p["kind"] == "person"):
            return "a person covers the dish spot in the proposed before or left frame"
        change = _distance(empty_patch, dish_patch)
        if change < MIN_CHANGE:
            return "the dish spot looked the same before and after the journal detection"
        ranked = []
        for at, jpeg, _, nearby, _ in states:
            if earlier_than is not None and at >= earlier_than:
                continue
            earliest = arrival.clear - 8 if arrival.left_behind else settled_at - 3
            latest = left[0] if arrival.left_behind else min(left[0], settled_at + .5)
            # Candidates may be selected by a later checker. Keep every alternative
            # near the refined arrival, not an earlier approach with a visible arm.
            if not arrival.left_behind:
                earliest = max(earliest, left[0] - 5)
            if not max(before[0], earliest) < at < latest:
                continue
            exact_retry = earlier_than is not None and people is not None
            if len(nearby) != 1 and exact_retry:
                # An earlier episode's 1-fps journal can miss a sub-second release.
                # A neighboring observation only schedules an exact-frame check;
                # it cannot supply the person for the returned picture. Preserve
                # the first search's narrower candidate set.
                neighbors = around[bisect_left(times, at - 1.25):bisect_right(times, at + 1.25)]
                hint = min((f for f in neighbors if len(_spot(f, box, None)[1]) == 1),
                           key=lambda f: abs(f["at"] - at), default=None)
                nearby = _spot(hint, box, None)[1] if hint else []
            if len(nearby) != 1:
                continue
            person = nearby[0]["box"]
            if not exact_retry and _body_cover(box, person) > .3:
                continue
            patch = patches[at]
            # Prefer a visible dish/spot at the edge of a person's reach over an opaque body.
            rank = .25 * _distance(patch, dish_patch) + 2 * _body_cover(box, person) + _reach(box, person) + .35 * abs(settled_at - at)
            ranked.append((rank, at, jpeg, person))
        candidates, attempted = [], []
        for _, at, jpeg, person in sorted(ranked):
            _check_deadline(deadline)
            # Resolve a quick hand release more finely than the 1–4 fps journal.
            # Earlier approaches need fewer checks; inference remains bounded.
            spacing = .12 if abs(settled_at - at) <= 1 else .4
            if any(abs(at - checked_at) < spacing for checked_at in attempted):
                continue
            if len(attempted) >= 12:
                break
            attempted.append(at)
            if people is not None:
                checked = [p for p in people(jpeg) if p["kind"] == "person" and _reach(box, p["box"]) <= .1]
                if len(checked) != 1 or _body_cover(box, checked[0]["box"]) > .3:
                    continue
                person = checked[0]["box"]
            candidate = {"at": at, "jpeg": jpeg, "person_box": person}
            candidates.append(candidate)
            if len(candidates) >= 8:
                break
        candidates = _contact_candidates(candidates, box)
        if not candidates:
            # An appearance change without any person may be a lighting step.
            # Continue from the previous clear look instead of treating late
            # recognition as an unobservable deposit.
            return _EarlierPatch(before[0], [before[0]])
        # Patch rewinding can join separate clear views across a long handling
        # sequence. Apply the same arrival-gap limit as the legacy finder to the
        # final proposal, after any further appearance rewind. Only a journal-
        # supported long occlusion may use the departure path.
        if not arrival.left_behind and settled_at - before[0] > MAX_GAP:
            return "the put-down is hidden across too long a gap"
        found = {"method": "journal", "before": {"at": before[0], "jpeg": before[1]}, "putting": candidates[0],
                "left": {"at": left[0], "jpeg": left[1]}, "candidates": candidates,
                "arrival_anchor_at": arrival.appeared, "arrival_window": [before[0], left[0]], "settled_at": settled_at,
                "putting_action": "left it behind" if arrival.left_behind else "putting it down",
                "hidden_seconds": arrival.hidden_seconds, "patch_change": change, "continuity_patch_checks": patch_checks,
                "rewind_patch_checks": rewind_checks}
        return found

    for _ in range(8):
        result = refine(arrival)
        if not isinstance(result, _EarlierPatch):
            return result
        prior = [patch_at(at) for at in result.reference_times]
        prior = [p for p in prior if p is not None]
        if not prior:
            return "the recording has a gap before the detected dish"
        continuity_patch = np.median(np.stack(prior), axis=0)
        continuity_threshold = min(.35, max(.2, 3 * float(np.median([_distance(p, continuity_patch) for p in prior]))))
        arrival = rewind(result.before)
        if isinstance(arrival, str):
            return arrival
    return "the dish's appearance changes too often to establish its arrival"
