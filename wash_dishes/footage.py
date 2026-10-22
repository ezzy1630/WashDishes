"""The rolling camera recording: its segments and the frames in them.

A segment ends at its file's mtime and starts where the previous one ended. Decoding is niced and
single-threaded so reading the recording never competes with the live camera loop.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


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


def photos(segment: tuple[float, float, Path]) -> list[tuple[float, bytes]]:
    """Full-resolution keyframes of one segment as JPEG."""
    start, end, path = segment
    times, data = _ffmpeg(path, ["-vf", "showinfo", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg"], False)
    jpegs, i = [], 0
    while (s := data.find(b"\xff\xd8", i)) >= 0 and (e := data.find(b"\xff\xd9", s)) >= 0:
        jpegs.append(data[s:e + 2])
        i = e + 2
    return [(min(end, start + t), j) for t, j in zip(times, jpegs)]
