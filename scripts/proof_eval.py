"""Private replay inspection and human-grounded proof scoring. Never opens the replay for writing."""
from __future__ import annotations

import argparse
import importlib
import io
import json
import math
import sqlite3
import subprocess
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

REPLAY = Path('/Volumes/Neural/dishwatch-scratch/replay-1022')
SCRATCH = Path('/Volumes/Neural/dishwatch-scratch/v2/laneE')
TRUTH = Path('/Volumes/Neural/dishwatch-scratch/v2/truth/truth.jsonl')
ZONE = ZoneInfo('America/Los_Angeles')


def box_arg(value: str) -> list[float]:
    try:
        box = [float(v) for v in value.split(',')]
    except ValueError as exc:
        raise argparse.ArgumentTypeError('box must be x,y,w,h normalized to 0..1') from exc
    if (len(box) != 4 or not all(math.isfinite(v) for v in box)
            or min(box) < 0 or min(box[2:]) <= 0 or box[0] + box[2] > 1.00001 or box[1] + box[3] > 1.00001):
        raise argparse.ArgumentTypeError('box must fit inside the normalized image')
    return box


def clock(at: float) -> str:
    return datetime.fromtimestamp(at, ZONE).strftime('%m-%d %H:%M:%S')


def font(size: int = 18):
    for path in ('/System/Library/Fonts/Monaco.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, quality=95)


def command(args: list[str]) -> bytes:
    run = subprocess.run(['nice', '-n', '10', *args], capture_output=True, timeout=60)
    if run.returncode:
        raise RuntimeError(run.stderr.decode(errors='replace')[-2000:])
    return run.stdout


@lru_cache(maxsize=64)
def frame_times(path: Path) -> list[float]:
    data = json.loads(command(['ffprobe', '-v', 'error', '-threads', '1', '-select_streams', 'v:0',
                              '-show_frames', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(path)]))
    return [float(f['best_effort_timestamp_time']) for f in data['frames'] if 'best_effort_timestamp_time' in f]


class Frames:
    def __init__(self, buffer: Path):
        from wash_dishes.putdown import segments
        self.segments = segments(buffer)
        if not self.segments:
            raise ValueError(f'No recording segments in {buffer}')
        # segments() deliberately omits the first file, whose preceding mtime is unavailable.
        first = min(buffer.glob('*.ts'), key=lambda p: p.stat().st_mtime)
        times = frame_times(first)
        if len(times) > 1:
            end = first.stat().st_mtime
            self.segments.insert(0, (end - (times[-1] - times[0]) - (times[1] - times[0]), end, first))

    def get(self, at: float) -> tuple[Image.Image, dict]:
        if not math.isfinite(at):
            raise ValueError('Time must be finite')
        candidates = sorted(self.segments, key=lambda s: max(s[0] - at, 0, at - s[1]))[:2]
        choices = []
        for start, end, path in candidates:
            times = frame_times(path)
            if not times:
                continue
            pts = min(times, key=lambda t: abs(start + t - times[0] - at))
            choices.append((abs(start + pts - times[0] - at), start + pts - times[0], pts - times[0], path))
        if not choices:
            raise ValueError('No decodable frame near requested time')
        _, actual, offset, path = min(choices)
        jpeg = command(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-filter_threads', '1', '-filter_complex_threads', '1', '-i', str(path), '-ss', f'{max(0, offset - 0.00001):.6f}', '-map', '0:v:0', '-frames:v', '1', '-threads', '1', '-pix_fmt', 'yuvj420p', '-q:v', '2', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'])
        if not jpeg:
            raise ValueError(f'No frame decoded from {path} at {offset}')
        return Image.open(io.BytesIO(jpeg)).convert('RGB'), {'requested_at': at, 'at': actual,
                'delta_seconds': actual - at, 'segment': str(path), 'offset': offset,
                'timing_basis': 'segment preceding mtime + relative video PTS; first segment duration estimated'}


def marked(image: Image.Image, box: list[float]) -> Image.Image:
    image = image.copy()
    x, y, w, h = box
    rect = (round(x * image.width), round(y * image.height), round((x + w) * image.width), round((y + h) * image.height))
    ImageDraw.Draw(image).rectangle(rect, outline='#ffdd00', width=3)
    return image


def context(image: Image.Image, box: list[float]) -> Image.Image:
    x, y, w, h = box
    # Twice the box width and height, with a small minimum for tiny detections.
    cw, ch = max(w * 2, .08), max(h * 2, .08)
    left, top = max(0, x + w / 2 - cw / 2), max(0, y + h / 2 - ch / 2)
    right, bottom = min(1, x + w / 2 + cw / 2), min(1, y + h / 2 + ch / 2)
    return marked(image, box).crop((round(left * image.width), round(top * image.height), round(right * image.width), round(bottom * image.height)))


def frame(args) -> None:
    image, meta = Frames(args.replay / 'buffer').get(args.at)
    out = args.out or SCRATCH / f'frame-{args.at:.3f}.jpg'
    save(marked(image, args.box) if args.box else image, out)
    if args.box:
        crop = out.with_name(out.stem + '-context.jpg')
        save(context(image, args.box), crop)
        meta['context'] = str(crop)
    meta['image'] = str(out)
    out.with_suffix('.json').write_text(json.dumps(meta, indent=2) + '\n')
    print(json.dumps(meta))


def strip(args) -> None:
    if args.every <= 0 or args.end < args.start or not all(math.isfinite(v) for v in (args.every, args.start, args.end)):
        raise ValueError('Require finite --from <= --to and --every > 0')
    count = math.floor((args.end - args.start) / args.every) + 1
    if count > 1000:
        raise ValueError('Split strips into at most 1000 samples each')
    frames = Frames(args.replay / 'buffer')
    width, height, cols = 300, 240, min(5, count)
    sheet = Image.new('RGB', (width * cols, height * math.ceil(count / cols)), '#202020')
    metadata = []
    draw = ImageDraw.Draw(sheet)
    for i in range(count):
        at = args.start + i * args.every
        image, meta = frames.get(at)
        crop = ImageOps.contain(context(image, args.box), (width - 8, height - 48))
        x, y = (i % cols) * width, (i // cols) * height
        sheet.paste(crop, (x + (width - crop.width) // 2, y + 45))
        draw.text((x + 4, y + 3), clock(meta['at']), font=font(), fill='white')
        draw.text((x + 4, y + 24), f"{meta['at']:.2f} delta {meta['delta_seconds']:+.2f}s", font=font(12), fill='white')
        metadata.append(meta)
    save(sheet, args.out)
    args.out.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps({'image': str(args.out), 'samples': count}))


def database(replay: Path) -> sqlite3.Connection:
    conn = sqlite3.connect((replay / 'history.db').resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def obligations(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("""SELECT o.*, p.status proof_status FROM obligations o
        LEFT JOIN proofs p ON p.obligation_id=o.id
        WHERE (o.lifecycle='open' AND o.archived_at IS NULL) OR p.obligation_id IS NOT NULL ORDER BY o.created_at""")]


def late_evidence(conn: sqlite3.Connection, oid: int):
    return conn.execute("""SELECT e.* FROM evidence e JOIN obligations o ON o.id=e.obligation_id
        WHERE e.obligation_id=? AND e.kind='snapshot' AND e.expired=0
        AND e.label LIKE 'Matching dish still visible after the deadline%'
        AND e.created_at>=o.due_at ORDER BY e.created_at LIMIT 1""", (oid,)).fetchone()


def jsonl(path: Path) -> dict[int, dict]:
    rows = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
                rows[int(row['obligation_id'])] = row
            except (ValueError, KeyError) as exc:
                raise ValueError(f'{path}:{number}: invalid result/truth line') from exc
    return rows


def disagreement(truth: dict, result: dict) -> str | None:
    for key, failure in (('is_dish', 'not a dish'), ('left_out_dirty', 'clean, drying, stored or still cooking'),
                         ('still_there_at_late', 'same dish not established in late photo')):
        if truth.get(key) is not True:
            return failure if truth.get(key) is False else f'{key} unknown'
    if truth.get('arrival_known') != 'seen' or truth.get('arrival_at') is None:
        return f"arrival {truth.get('arrival_known', 'unknown')}"
    before, left = result.get('before_at'), result.get('left_at')
    putting = result.get('putting_at')
    if before is None and left is None:
        before = left = putting
    if (before is None or left is None or not isinstance(before, (float, int)) or not isinstance(left, (float, int))
            or not math.isfinite(before) or not math.isfinite(left) or before > left):
        return 'missing or invalid put-down window'
    if putting is not None and (not math.isfinite(putting) or not before <= putting <= left):
        return 'putting_at outside put-down window'
    if not before - 45 <= truth['arrival_at'] <= left + 45:
        return f"wrong arrival: truth {truth['arrival_at']:.1f}, result window {before:.1f}..{left:.1f}"
    return None


def score_rows(truth: dict[int, dict], results: dict[int, dict]) -> dict:
    ready = {oid: row for oid, row in results.items() if row.get('status') == 'ready'}
    eligible = {oid for oid, row in truth.items() if row.get('is_dish') is True and row.get('left_out_dirty') is True
                and row.get('arrival_known') == 'seen'}
    correct, disagreements = [], []
    for oid, row in ready.items():
        reason = disagreement(truth[oid], row) if oid in truth else 'not labeled'
        if reason:
            disagreements.append({'obligation_id': oid, 'reason': reason})
        else:
            correct.append(oid)
    for oid in sorted(eligible - set(ready)):
        disagreements.append({'obligation_id': oid, 'reason': f"missed eligible dish: {results.get(oid, {}).get('status', 'missing result')}"})
    return {'ready': len(ready), 'correct_ready': len(correct), 'truth_eligible': len(eligible),
            'precision': len(correct) / len(ready) if ready else None,
            'recall': len(correct) / len(eligible) if eligible else None,
            'ready_unlabeled': len(set(ready) - set(truth)), 'truth_labeled': len(truth),
            'truth_eligible_with_late': sum(truth[oid].get('still_there_at_late') is True for oid in eligible),
            'truth_unresolved': sum(row.get('is_dish') is None or row.get('left_out_dirty') is None
                                    or row.get('arrival_known') == 'unclear' for row in truth.values()),
            'correct_ids': correct, 'disagreements': disagreements,
            'scope': 'Conservative temporal/eligibility score; unknowns do not pass. Recall only over labeled seen arrivals. New collages still require human handling/identity review.'}


def score(args) -> None:
    report = score_rows(jsonl(args.truth), jsonl(args.results))
    print(json.dumps(report, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')


def baseline(args) -> None:
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with database(args.replay) as conn, args.out.open('w') as out:
        for row in conn.execute('SELECT * FROM proofs ORDER BY obligation_id'):
            data = json.loads(row['data'])
            out.write(json.dumps({'obligation_id': row['obligation_id'], 'status': row['status'],
                                  **{k: data[k] for k in ('before_at', 'putting_at', 'left_at', 'reason') if k in data}}) + '\n')
    args.results = args.out
    score(args)


def run(args) -> None:
    from wash_dishes.collage import render
    module, name = args.finder.split(':', 1)
    finder = getattr(importlib.import_module(module), name)
    args.out.mkdir(parents=True, exist_ok=True)
    with database(args.replay) as conn, (args.out / 'results.jsonl').open('w', buffering=1) as output:
        items = obligations(conn)
        if args.ids:
            wanted = {int(oid) for group in args.ids for oid in group.split(',')}
            missing = wanted - {item['id'] for item in items}
            if missing:
                raise ValueError(f'IDs outside replay evaluation set: {sorted(missing)}')
            items = [item for item in items if item['id'] in wanted]
        for item in items:
            oid = item['id']
            result = {'obligation_id': oid, 'status': 'untraced'}
            late = late_evidence(conn, oid)
            try:
                if late is None:
                    raise ValueError('no late evidence after deadline')
                if not item['box']:
                    raise ValueError('no obligation box')
                late_path = (args.replay / 'media' / late['filename']).resolve()
                if not late_path.is_relative_to((args.replay / 'media').resolve()):
                    raise ValueError('late evidence path escapes replay media')
                late_jpeg = late_path.read_bytes()
                box = json.loads(item['box'])
                found = finder(args.replay / 'buffer', box, timestamp(item['created_at']), timestamp(late['created_at']))
                if isinstance(found, str):
                    result['reason'] = found
                elif isinstance(found, dict):
                    times = {f'{key}_at': found[key]['at'] for key in ('before', 'putting', 'left')}
                    meta = {**times, 'late_at': timestamp(late['created_at']), 'box': box,
                            'person_box': found['putting'].get('person_box'),
                            'late_box': json.loads(late['capture'] or '{}').get('box'),
                            'dish': ', '.join(d['kind'] for d in json.loads(item['dishes'])), 'location': item['location'] or 'Kitchen'}
                    pictures = {key: found[key]['jpeg'] for key in ('before', 'putting', 'left')}
                    pictures['late'] = late_jpeg
                    folder = args.out / str(oid)
                    folder.mkdir(exist_ok=True)
                    for key, jpeg in pictures.items():
                        (folder / f'{key}.jpg').write_bytes(jpeg)
                    (folder / 'collage.jpg').write_bytes(render(meta, pictures, None))
                    (folder / 'meta.json').write_text(json.dumps(meta, indent=2) + '\n')
                    result.update(status='ready', **times)
                else:
                    raise ValueError('finder must return dict or reason string')
            except Exception as exc:
                result.update(status='error', reason=f'{type(exc).__name__}: {exc}')
            output.write(json.dumps(result) + '\n')
            print(json.dumps(result), flush=True)


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--repo', type=Path, default=argparse.SUPPRESS)
    common.add_argument('--replay', type=Path, default=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('frame', parents=[common])
    p.add_argument('--at', type=float, required=True)
    p.add_argument('--box', type=box_arg)
    p.add_argument('--out', type=Path)
    p = sub.add_parser('strip', parents=[common])
    p.add_argument('--box', type=box_arg, required=True)
    p.add_argument('--from', dest='start', type=float, required=True)
    p.add_argument('--to', dest='end', type=float, required=True)
    p.add_argument('--every', type=float, required=True)
    p.add_argument('--out', type=Path, required=True)
    for action in ('score', 'baseline'):
        p = sub.add_parser(action, parents=[common])
        p.add_argument('--truth', type=Path, default=TRUTH)
        p.add_argument('--report', type=Path)
        if action == 'score':
            p.add_argument('--results', type=Path, required=True)
        else:
            p.add_argument('--out', type=Path, default=SCRATCH / 'baseline.jsonl')
    p = sub.add_parser('run', parents=[common])
    p.add_argument('--finder', required=True)
    p.add_argument('--ids', nargs='+')
    p.add_argument('--out', type=Path, default=SCRATCH / 'run')
    args = parser.parse_args()
    args.repo = getattr(args, 'repo', Path(__file__).resolve().parents[1]).resolve()
    args.replay = getattr(args, 'replay', REPLAY)
    if not (args.repo / 'wash_dishes').is_dir():
        parser.error('Use --repo PATH pointing to a Dish Watch checkout (required for shared tools copy)')
    sys.path.insert(0, str(args.repo))
    import wash_dishes
    if not Path(wash_dishes.__file__).resolve().is_relative_to(args.repo):
        parser.error('wash_dishes imported from a different checkout')
    try:
        globals()[args.action](args)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
