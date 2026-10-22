"""Replay the live detector into a resumable, private journal; never modify the recording root."""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

from wash_dishes.journal import Journal
from wash_dishes.putdown import decoded_photos, segments
from wash_dishes.vision import LocalVision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=1)
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--imgsz", type=int, default=1280, help="Live CUDA default is 1280; record overrides in evaluation.")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--end", type=float, default=float("inf"))
    parser.add_argument("--newest-first", action="store_true")
    parser.add_argument("--priority", type=Path, help="Optional JSON list of [start,end] windows to process first.")
    parser.add_argument("--only-priority", action="store_true", help="Build only the listed windows, retaining complete boundary segments.")
    args = parser.parse_args()
    if not 1 <= args.fps <= 4 or args.imgsz < 32 or not math.isfinite(args.start) or math.isnan(args.end) or args.start >= args.end:
        parser.error("Use 1–4 fps, a valid image size, and start < end.")
    if args.only_priority and not args.priority:
        parser.error("--only-priority requires --priority.")
    root, output = args.root.resolve(), args.output.resolve()
    if output.is_relative_to(root):
        parser.error("Output must be outside the read-only replay root.")
    scratch = output.parent
    scratch.mkdir(parents=True, exist_ok=True)
    runtime = scratch / "detector-runtime"
    runtime.mkdir(exist_ok=True)
    models = runtime / "models"
    if not models.exists():
        models.symlink_to(root / "models", target_is_directory=True)
    elif models.resolve() != (root / "models").resolve():
        parser.error("Scratch models point at a different replay root.")
    os.environ.update(WASH_DISHES_VISION_DEVICE=args.device, WASH_DISHES_IMGSZ=str(args.imgsz),
                      YOLO_CONFIG_DIR=str(runtime / "vision-settings"), YOLO_OFFLINE="true", YOLO_AUTOINSTALL="false")
    import torch
    torch.set_num_threads(2)
    # Keep the same weights/analyze path while directing its settings/cache writes to scratch.
    vision = LocalVision(root=root)
    vision.root = runtime
    selected = [s for s in segments(root / "buffer") if s[1] > s[0] and s[1] > args.start and s[0] < args.end]
    if args.newest_first:
        selected.reverse()
    if args.priority:
        windows = json.loads(args.priority.read_text())
        if not isinstance(windows, list) or any(not isinstance(w, list) or len(w) != 2
                or not all(isinstance(t, (int, float)) and math.isfinite(t) for t in w) or not w[0] < w[1] for w in windows):
            parser.error("Priority windows must be [start,end] pairs.")
        if args.only_priority:
            selected = [s for s in selected if any(s[1] >= start and s[0] <= end for start, end in windows)]
        selected.sort(key=lambda s: not any(s[1] >= start and s[0] <= end for start, end in windows))
    progress_path = output.with_suffix(".progress.json")
    manifest = {"root": str(root), "fps": args.fps, "imgsz": args.imgsz, "device": args.device}
    previous = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    if previous and previous["config"] != manifest:
        parser.error("Existing journal used different detector settings; choose another output.")
    completed = set(previous.get("completed_segments", []))
    failed = set(previous.get("failed_segments", []))
    started, count, previous_end = time.monotonic(), 0, None
    with Journal(output, max_fps=args.fps) as journal:
        for index, segment in enumerate(selected):
            if segment[2].name in completed:
                continue
            if previous_end is None or abs(segment[0] - previous_end) > 2:
                vision.reset_observations()
            decoded = 0
            for at, jpeg in decoded_photos(segment, fps=args.fps):
                if args.start <= at <= args.end:
                    analysis = vision.analyze(jpeg)
                    # Replay windows may run out of order. ByteTrack's process-local
                    # IDs then have no chronological meaning; retain geometry instead.
                    detections = [{k: v for k, v in d.items() if k != "track_id"} for d in analysis["detections"]]
                    journal.record(at, detections)
                    count += 1
                    decoded += 1
            journal.flush()
            previous_end = segment[1]
            if decoded and args.start <= segment[0] and segment[1] <= args.end:
                completed.add(segment[2].name)
                failed.discard(segment[2].name)
            elif not decoded:
                failed.add(segment[2].name)
            if index % 30 == 0 or index == len(selected) - 1:
                state = {"config": manifest, "completed_segments": sorted(completed), "frames_this_run": count,
                         "elapsed_seconds": time.monotonic() - started, "last_segment": segment[2].name,
                         "failed_segments": sorted(failed)}
                temporary = progress_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(state))
                temporary.replace(progress_path)
                print(json.dumps({k: v for k, v in state.items() if k not in ("completed_segments", "failed_segments")} | {"segments_done": len(completed), "segments_total": len(selected), "segments_failed": len(failed)}), flush=True)
    selected_names = {s[2].name for s in selected}
    print(json.dumps({"selected_segments": len(selected), "completed_selected": len(completed & selected_names),
                      "failed_selected": len(failed & selected_names), "frames_this_run": count}), flush=True)
    if failed & selected_names:
        raise SystemExit(f"{len(failed & selected_names)} segments could not be decoded; see {progress_path}")


if __name__ == "__main__":
    main()
