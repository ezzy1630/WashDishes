"""Find the moment a dish was put down in the rolling recording, by watching the dish's own box.

Only keyframes are decoded (about one every two seconds) by a low-priority single-threaded FFmpeg that
returns just a 32x32 grey crop of the spot, so a search costs one niced core for one to three minutes.
The spot is compared with two looks: the dish (sampled after the camera logged it) and the spot before
it (the last time, walking back, that the spot held steady at something else: the bare counter or
another item). A frame like neither is something over the spot, usually a person; the put-down is the
change from "before" through "covered" to "dish". Comparing with both looks, rather than a fixed
threshold, keeps day/night lighting and contrasty counters from reading as a person.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PRESENT_MINUTES = 15      # how long after logging the dish's look is sampled
DENSE_MINUTES = 45        # searched every 30 s back from logging, then every 2 min
SEARCH_HOURS = 23         # the recording keeps 24 h
MIN_CHANGE = 0.12         # smallest difference that counts as a different look
STEADY_RUN = 3            # consecutive alike samples that are not the dish: the spot before it
MAX_GAP = 120             # seconds from the last "before" view to the dish being there: longer means the
                          # spot was hidden while it arrived, and the picture would only show who stood in front
SIDE = 32


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
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-threads", "1", "-skip_frame", "nokey", "-i", str(path),
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


def _distance(a, b) -> float:
    import numpy as np
    return float(np.mean(np.abs(a - b)))


def find_putdown(buffer: Path, box: list[float], logged: float, present_until: float) -> dict | str:
    """Unix times of the spot before the dish ("before"), the person putting it down ("putting") and the
    dish left there ("left"), each with its JPEG; or, when the footage does not show it, why not.

    `logged` is when the camera recorded the dish and `present_until` a later time it was still there;
    together they bound where the dish is known to be present.
    """
    import numpy as np
    segs = segments(buffer)
    # The dish's own look: the per-pixel median over samples after logging is robust to someone
    # briefly standing over it; the typical distance from that median is the noise floor.
    after = [seg for seg in segs if logged <= seg[0] <= min(present_until, logged + PRESENT_MINUTES * 60)]
    present = [s for seg in after[::5] for s in samples(seg, box, first_only=True)]
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
        for s in samples(seg, box, first_only=True):
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
        for s in samples(seg, box):
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
            pictures.update(photos(seg))
    result = {"threshold": threshold}
    for key, s in chosen.items():
        at, jpeg = min(pictures.items(), key=lambda p: abs(p[0] - s.at), default=(None, None))
        if jpeg is None or abs(at - s.at) > 1:
            return "the full pictures could not be decoded"
        result[key] = {"at": s.at, "diff": s.diff, "jpeg": jpeg}
    return result
