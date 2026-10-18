"""Before/after crops of everything that changed in the monitored areas, for hand-labeling evaluation truth.

Activity and change are plain frame differencing, not the dish detector, so the truth does not
inherit the detector's blind spots. With --people, someone the person detector sees in an area also
counts as activity, so a person standing still doesn't end a visit and before/after frames are
empty views. Activity anywhere in the areas groups seconds into visits; each
visit's before and after frames are compared, and every solid patch of change becomes one row,
assigned to an area with the same priority the inventory uses. Run on the device holding the footage:

    python scripts/truth_sheets.py FOOTAGE_DIR AREAS_JSON OUT_DIR --from ISO --to ISO [--offset DX DY] [--people DATA_ROOT]

Only keyframes are decoded (about one every two seconds), which is plenty to find visits.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from wash_dishes.inventory import area_for, in_area

WIDTH, HEIGHT = 768, 432
ACTIVE_LEVEL = 25       # grey levels a pixel must move to count
ACTIVE_FRACTION = 0.01  # of the monitored pixels, to call a moment active
MERGE_GAP = 10          # seconds of quiet that still belong to the same visit
MIN_PATCH = 80          # pixels (at 768 wide) for a patch of change worth a look


def keyframes(folder: Path, start: datetime, end: datetime):
    for path in sorted(Path(folder).glob("*.ts"), key=lambda p: p.stat().st_mtime):
        ended = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        if not start - timedelta(seconds=10) <= ended <= end + timedelta(seconds=10):
            continue
        raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-skip_frame", "nokey", "-i", str(path), "-vsync", "passthrough",
                              "-vf", f"scale={WIDTH}:{HEIGHT}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"], capture_output=True).stdout
        count = len(raw) // (WIDTH * HEIGHT * 3)
        for index in range(count):
            at = ended - timedelta(seconds=10 * (count - index) / count)
            if start <= at <= end:
                yield at, np.frombuffer(raw, np.uint8, WIDTH * HEIGHT * 3, index * WIDTH * HEIGHT * 3).reshape(HEIGHT, WIDTH, 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("footage")
    parser.add_argument("areas")
    parser.add_argument("out")
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--offset", nargs=2, type=float, default=(0.0, 0.0))
    parser.add_argument("--people", metavar="DATA_ROOT", help="also treat a detected person in an area as activity")
    args = parser.parse_args()
    people = None
    if args.people:
        from wash_dishes.vision import LocalVision
        vision = LocalVision(Path(args.people))

        def people(jpeg: bytes) -> list[list[float]]:
            return [d["box"] for d in vision.analyze(jpeg)["detections"] if d["kind"] == "person" and d.get("score", 0) >= 0.35]
    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    dx, dy = args.offset
    areas = [{**a, "box": [a["box"][0] + dx, a["box"][1] + dy, a["box"][2], a["box"][3]]} for a in json.loads(Path(args.areas).read_text())]
    monitored = np.zeros((HEIGHT, WIDTH), bool)
    for area in areas:
        x, y, w, h = area["box"]
        monitored[max(0, int(y * HEIGHT)):int((y + h) * HEIGHT), max(0, int(x * WIDTH)):int((x + w) * WIDTH)] = True
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Kept as JPEG: hours of raw frames would not fit in memory.
    frames: list[tuple[datetime, bytes]] = []
    active: list[datetime] = []
    previous = None
    for at, image in keyframes(Path(args.footage), start, end):
        grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.int16)
        jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        moved = previous is not None and (np.abs(grey - previous)[monitored] > ACTIVE_LEVEL).mean() > ACTIVE_FRACTION
        if moved or (people and any(in_area(box, a["box"]) for a in areas for box in people(jpeg))):
            active.append(at)
        previous = grey
        frames.append((at, jpeg))
    times = [at for at, _ in frames]

    def frame_near(at: datetime):
        jpeg = frames[min(range(len(times)), key=lambda i: abs((times[i] - at).total_seconds()))][1]
        return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)

    visits: list[list[datetime]] = []
    for at in active:
        if visits and (at - visits[-1][1]).total_seconds() <= MERGE_GAP:
            visits[-1][1] = at
        else:
            visits.append([at - timedelta(seconds=2), at])
    rows, candidates = [], []
    for first, last in visits:
        before, after = frame_near(first - timedelta(seconds=3)), frame_near(last + timedelta(seconds=4))
        diff = np.abs(cv2.cvtColor(before, cv2.COLOR_BGR2GRAY).astype(np.int16) - cv2.cvtColor(after, cv2.COLOR_BGR2GRAY).astype(np.int16)) > ACTIVE_LEVEL
        diff = cv2.morphologyEx((diff & monitored).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        diff = cv2.dilate(diff, np.ones((7, 7), np.uint8))
        count, _, stats, _ = cv2.connectedComponentsWithStats(diff)
        for label in range(1, count):
            x, y, w, h, size = stats[label]
            if size < MIN_PATCH:
                continue
            box = [x / WIDTH, y / HEIGHT, w / WIDTH, h / HEIGHT]
            area = area_for(box, areas)
            if area is None:
                continue
            number = len(candidates)
            candidates.append({"number": number, "area": area["id"], "name": area["name"], "start": first.isoformat(), "end": last.isoformat(),
                               "box": [round(v, 4) for v in box], "pixels": int(size)})
            pad = 40
            left, top = max(0, x - pad), max(0, y - pad)
            right, bottom = min(WIDTH, x + w + pad), min(HEIGHT, y + h + pad)
            pair = []
            for image in (before, after):
                crop = image[top:bottom, left:right].copy()
                cv2.rectangle(crop, (x - left, y - top), (x - left + w, y - top + h), (0, 255, 255), 1)
                scale = 180 / crop.shape[0]
                pair.append(cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), 180)))
            row = np.hstack([pair[0], np.full((180, 6, 3), 255, np.uint8), pair[1]])
            canvas = np.zeros((206, 1200, 3), np.uint8)
            canvas[26:, :min(1200, row.shape[1])] = row[:, :1200]
            cv2.putText(canvas, f"#{number} {area['name']}  {first.strftime('%H:%M:%S')}-{last.strftime('%H:%M:%S')}  {size}px",
                        (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
            rows.append(canvas)
    for page in range(0, len(rows), 8):
        block = rows[page:page + 8]
        grid = [np.hstack(block[i:i + 2]) if i + 1 < len(block) else np.hstack([block[i], np.zeros_like(block[i])]) for i in range(0, len(block), 2)]
        cv2.imwrite(str(out / f"sheet-{page // 8:03d}.jpg"), np.vstack(grid), [cv2.IMWRITE_JPEG_QUALITY, 80])
    (out / "candidates.json").write_text(json.dumps(candidates, indent=1))
    print(len(visits), "visits,", len(candidates), "changes on", (len(rows) + 7) // 8, "sheets")


if __name__ == "__main__":
    main()
