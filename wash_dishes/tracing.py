"""Bounded CPU-only searches of retained video for activity near a current dish.

A regional scene change is a review candidate, never an object match or an owner.
Search snapshots are temporary; keeping a candidate copies it into incident evidence.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .media import atomic_write
from .store import parse_stamp, stamp, utcnow

MAX_SAMPLES = 360
MAX_CANDIDATES = 12
CACHE_BYTES = 256_000_000


def region_signature(jpeg: bytes, box: list[float]):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("A retained video frame could not be decoded.")
    height, width = image.shape
    x, y, w, h = box
    crop = image[int(y * height):math.ceil((y + h) * height), int(x * width):math.ceil((x + w) * width)]
    small = cv2.resize(crop, (120, 80), interpolation=cv2.INTER_AREA).astype(np.float32)
    return small / (float(np.median(small)) + 1)


def change_score(before, after) -> float:
    import numpy as np
    return float(np.mean(np.abs(after - before) > .18))


class TraceSearch:
    def __init__(self, root, store, media, dataset):
        self.root = Path(root) / "traces"
        self.store, self.media, self.dataset = store, media, dataset
        self.lock = threading.RLock()
        self.clip_lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = None

    def _path(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError("Unknown footage search.")
        return self.root / job_id

    def _write(self, job: dict) -> None:
        atomic_write(self._path(job['id']) / 'job.json', json.dumps(job).encode())

    def get(self, job_id: str) -> dict:
        path = self._path(job_id) / 'job.json'
        with self.lock:
            if not path.is_file():
                raise LookupError("Footage search expired or was not found. Start a new search from Live.")
            return json.loads(path.read_text())

    def recent(self, limit: int | None = 12) -> list[dict]:
        with self.lock:
            paths = sorted(self.root.glob('*/job.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
            return [json.loads(p.read_text()) for p in paths]

    def create(self, frame_id: str, area_id: str, hours: float, obligation_id: int | None = None) -> dict:
        frame = self.dataset.item(frame_id)
        area = next((a for a in frame.get('capture', {}).get('areas', []) if a['id'] == area_id), None)
        if area is None:
            raise ValueError("Choose an area on a frozen live frame before searching.")
        if obligation_id is not None:
            allowed = {d.get('obligation_id') for d in frame['capture'].get('detections', []) if d.get('area_id') == area_id}
            if obligation_id not in allowed:
                raise ValueError("This record was not linked to a dish in the selected area at capture time.")
            self.store.obligation(obligation_id)
        with self.lock:
            active = next((j for j in self.recent() if j['status'] in ('queued', 'running')), None)
            if active:
                if (active['frame_id'], active['area']['id'], active['obligation_id']) == (frame_id, area_id, obligation_id):
                    return active
                raise ValueError("Another footage search is running. Open it below or wait for it to finish.")
            job_id = uuid.uuid4().hex
            folder = self._path(job_id)
            folder.mkdir(parents=True, mode=0o700)
            atomic_write(folder / 'anchor.jpg', self.dataset.image(frame_id).read_bytes())
            end = parse_stamp(frame['created_at'])
            job = {'id': job_id, 'frame_id': frame_id, 'area': area, 'obligation_id': obligation_id,
                   'created_at': stamp(), 'from': stamp(end - timedelta(hours=hours)), 'to': stamp(end),
                   'status': 'queued', 'message': 'Waiting to search retained footage.', 'candidates': [], 'coverage': {}}
            self._write(job)
            return job

    def start(self):
        self.stopped.clear()
        with self.lock:
            for job in self.recent():
                if job['status'] == 'running':
                    job.update(status='queued', message='Resuming after restart.')
                    self._write(job)
        self.thread = threading.Thread(target=self._worker, name='footage-search', daemon=True)
        self.thread.start()

    def enqueue_missing(self, sequences) -> dict | None:
        """At most one bounded look-back; never turn scene changes into ownership."""
        from .config import monitoring_areas
        with self.lock:
            jobs = self.recent(limit=None)
            if any(j['status'] in ('queued', 'running') for j in jobs):
                return None
            seen = {j['obligation_id'] for j in jobs}
            settings = self.store.settings()
            cutoff = utcnow() - timedelta(hours=min(settings['buffer_hours'], 2))
            for item in self.store.obligations():
                if item['id'] in seen or item['lifecycle'] != 'open' or parse_stamp(item['created_at']) < cutoff:
                    continue
                if any(e['event'] == 'automatic_lookback_queued' for e in self.store.obligation(item['id'])['events']):
                    continue
                try:
                    if sequences.get(item['id'])['complete']:
                        continue
                except LookupError:
                    pass
                area = next((a for a in monitoring_areas(settings) if a['name'] == item['location']), None)
                photo = next((e for e in item['evidence'] if e['kind'] == 'snapshot' and not e['expired']
                              and e['capture'].get('stage') == 'leaving'), None)
                if not area or not photo:
                    continue
                source = self.media.safe_path(self.store.evidence(photo['id'])['filename'])
                if not source.is_file():
                    continue
                job_id = uuid.uuid4().hex
                folder = self._path(job_id)
                folder.mkdir(parents=True, mode=0o700)
                atomic_write(folder / 'anchor.jpg', source.read_bytes())
                end = parse_stamp(photo['created_at'])
                job = {'id': job_id, 'frame_id': None, 'anchor_evidence_id': photo['id'], 'automatic': True,
                       'area': area, 'obligation_id': item['id'], 'created_at': stamp(),
                       'from': stamp(end - timedelta(minutes=30)), 'to': stamp(end),
                       'status': 'queued', 'message': 'Automatically searching for the missing earlier activity.',
                       'candidates': [], 'coverage': {}}
                self._write(job)
                self.store.event('automatic_lookback_queued', 'Bounded earlier-footage search queued; candidates require review', item['id'])
                return job
        return None

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=15)

    def _worker(self):
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 15)
        except (AttributeError, OSError):
            pass
        while not self.stopped.wait(1):
            queued = next((j for j in self.recent() if j['status'] == 'queued'), None)
            if queued:
                self.run(queued['id'])
            self.cleanup()

    def _frame(self, path: Path) -> bytes:
        # Seeking to zero in a reset-timestamp MPEG-TS can silently produce no
        # frame despite exit 0. Decode its first available keyframe sequentially.
        result = subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-threads', '1', '-filter_threads', '1', '-i', str(path),
                                 '-frames:v', '1', '-vf', 'scale=1280:-2', '-threads', '1', '-q:v', '3', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'],
                                capture_output=True, timeout=8)
        return result.stdout if result.returncode == 0 and result.stdout.startswith(b'\xff\xd8') else b''

    def run(self, job_id: str):
        job = self.get(job_id)
        job.update(status='running', message='Scanning changes in the selected area. Live detection keeps running.')
        with self.lock:
            self._write(job)
        try:
            start, end = parse_stamp(job['from']).timestamp(), parse_stamp(job['to']).timestamp()
            complete = sorted((p for p in self.media.buffer.glob('*.ts') if not p.is_symlink()), key=lambda p: p.stat().st_mtime)[:-1]
            segments = [(p, p.stat().st_mtime) for p in complete if start <= p.stat().st_mtime <= end]
            job['coverage'] = {'segments': len(segments), 'sampled': 0, 'unreadable': 0,
                               'from': stamp(datetime.fromtimestamp(segments[0][1] - 10, timezone.utc)) if segments else None,
                               'to': stamp(datetime.fromtimestamp(segments[-1][1], timezone.utc)) if segments else None,
                               'timestamp_estimated': True, 'continuous': False}
            # A 24h request may contain only 6h of retained video. Base sampling
            # on the available span so it does not unnecessarily widen clips.
            sample_limit = 48 if job.get('automatic') else MAX_SAMPLES
            interval = max(30, (segments[-1][1] - segments[0][1]) / sample_limit) if segments else 30
            selected, previous_at = [], -math.inf
            for path, at in segments:
                if at - previous_at >= interval:
                    selected.append((path, at))
                    previous_at = at
            # Keep only a small set of distinct visits and their source frames in memory.
            best, previous = [], None
            for index, (path, ended) in enumerate(selected):
                if self.stopped.is_set():
                    return  # Persisted running job resumes on next service start.
                jpeg = self._frame(path)
                if not jpeg:
                    job['coverage']['unreadable'] += 1
                    continue
                at = ended - 10  # Segment completion time; camera overlay is the original time reference.
                sig = region_signature(jpeg, job['area']['box'])
                job['coverage']['sampled'] += 1
                if previous is not None:
                    score = change_score(previous[2], sig)
                    if score >= .025:
                        row = {'score': score, 'before_at': previous[0], 'after_at': at, 'before': previous[1], 'after': jpeg}
                        near = next((b for b in best if abs(b['after_at'] - at) < 120), None)
                        if near is None:
                            best.append(row)
                        elif score > near['score']:
                            best[best.index(near)] = row
                        best = sorted(best, key=lambda b: -b['score'])[:MAX_CANDIDATES]
                previous = at, jpeg, sig
                if index % 20 == 0:
                    job['message'] = f"Scanned {index + 1} of {len(selected)} sampled recordings."
                    with self.lock:
                        self._write(job)
            candidates = []
            for i, row in enumerate(sorted(best, key=lambda b: -b['after_at'])):
                for side in ('before', 'after'):
                    atomic_write(self._path(job_id) / f'{i}-{side}.jpg', row[side])
                candidates.append({'index': i, 'from': stamp(datetime.fromtimestamp(row['before_at'], timezone.utc)),
                                   'to': stamp(datetime.fromtimestamp(row['after_at'], timezone.utc)),
                                   'change': round(row['score'], 3), 'kept': False,
                                   'reason': 'Area changed between sampled views; dish matching and responsibility are unconfirmed.'})
            if segments and not job['coverage']['sampled']:
                raise ValueError("No readable samples.")
            job.update(status='complete', candidates=candidates,
                       message=('No retained recordings cover this window. Earlier use cannot be recovered from this buffer.' if not segments
                                else f"Found {len(candidates)} possible activity windows from {job['coverage']['sampled']} samples. These are review candidates, not matches."))
        except (ImportError, OSError, ValueError, subprocess.SubprocessError):
            job.update(status='failed', message='The retained footage could not be read. Start another search or check local recording storage.')
        with self.lock:
            self._write(job)

    def picture(self, job_id: str, index: int, side: str) -> Path:
        job = self.get(job_id)
        if side not in ('before', 'after', 'anchor') or (side != 'anchor' and not 0 <= index < len(job['candidates'])):
            raise ValueError("Unknown candidate picture.")
        path = self._path(job_id) / ('anchor.jpg' if side == 'anchor' else f'{index}-{side}.jpg')
        if not path.is_file():
            raise LookupError("This search picture expired. Start a new search.")
        return path

    def clip(self, job_id: str, index: int) -> Path:
        job = self.get(job_id)
        if not 0 <= index < len(job['candidates']):
            raise ValueError("Unknown activity candidate.")
        row = job['candidates'][index]
        target = self._path(job_id) / f'{index}.mp4'
        # Clip work must not hold the media lock while FFmpeg runs: inference
        # saves snapshots through that same lock. Short-lived hardlinks survive
        # buffer rotation without copying or pinning the whole search window.
        with self.lock, self.clip_lock:
            job['accessed_at'] = stamp()
            self._write(job)
            if target.is_file():
                return target
            with tempfile.TemporaryDirectory(prefix='.clip-', dir=self._path(job_id)) as temporary:
                staging = Path(temporary)
                with self.media.lock:
                    complete = sorted((p for p in self.media.buffer.glob('*.ts') if not p.is_symlink()), key=lambda p: p.stat().st_mtime)[:-1]
                    start, end = parse_stamp(row['from']).timestamp(), parse_stamp(row['to']).timestamp() + 15
                    if end - start > 180:
                        raise ValueError("This candidate spans more than three minutes. Search a shorter period to review video; its snapshots remain available.")
                    segments = [p for p in complete if start <= p.stat().st_mtime <= end]
                    if not segments:
                        raise LookupError("The original rolling footage expired. Candidate snapshots remain; a clip can no longer be recovered.")
                    times = [p.stat().st_mtime for p in segments]
                    if times[0] > start + 15 or times[-1] < parse_stamp(row['to']).timestamp() or any(b - a > 15 for a, b in zip(times, times[1:])):
                        raise ValueError("This window has missing recordings. A continuous clip is unavailable; review the snapshots instead.")
                    for number, path in enumerate(segments):
                        os.link(path, staging / f'{number:03d}.ts')
                listing = staging / 'segments.concat'
                listing.write_text('\n'.join("file '" + str(p.resolve()).replace("'", "'\\''") + "'" for p in sorted(staging.glob('*.ts'))) + '\n')
                try:
                    exported = staging / 'clip.mp4'
                    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                                    '-an', '-c:v', 'copy', '-movflags', '+faststart', '-y', str(exported)], capture_output=True, timeout=20, check=True)
                    os.replace(exported, target)
                    target.chmod(0o600)
                except (OSError, subprocess.SubprocessError):
                    target.unlink(missing_ok=True)
                    raise ValueError("This candidate's video could not be assembled. Review its snapshots instead.") from None
        return target

    def keep(self, job_id: str, index: int) -> dict:
        with self.lock:
            job = self.get(job_id)
            if not 0 <= index < len(job['candidates']):
                raise ValueError("Unknown activity candidate.")
            row = job['candidates'][index]
            if row['kept']:
                return job
            if job['obligation_id'] is None:
                raise ValueError("Select a tracked dish with a record before attaching historical evidence.")
            clip = self.clip(job_id, index)  # Do not claim success if source footage has expired.
            obligation = job['obligation_id']
            for side, at in (('before', row['from']), ('after', row['to'])):
                self.media.save_snapshot(obligation, self.picture(job_id, index, side).read_bytes(),
                                         f"Earlier activity candidate ({side}; recording time estimated); dish match unconfirmed", at=parse_stamp(at))
            evidence_id = uuid.uuid4().hex
            destination = self.media.safe_path(evidence_id + '.mp4')
            shutil.copyfile(clip, destination)
            destination.chmod(0o600)
            self.store.add_evidence(obligation, evidence_id, destination.name, 'clip', 'Earlier activity candidate; dish match and responsibility unconfirmed', at=parse_stamp(row['to']))
            row.update(kept=True, evidence_id=evidence_id)
            self.store.event('earlier_activity_kept', 'Historical candidate saved for review; no owner or deposit time assigned', obligation)
            self._write(job)
            return job

    def cleanup(self):
        with self.lock:
            jobs = self.recent(limit=None)
            used = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
            for job in reversed(jobs):
                if job['status'] in ('queued', 'running'):
                    continue
                if job.get('accessed_at') and parse_stamp(job['accessed_at']) > utcnow() - timedelta(minutes=1):
                    continue  # Let FileResponse open a clip before cache reclamation.
                if len(jobs) <= 8 and used <= CACHE_BYTES and parse_stamp(job['created_at']) > utcnow() - timedelta(days=7):
                    continue
                folder = self._path(job['id'])
                size = sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
                shutil.rmtree(folder)
                used -= size
                jobs.remove(job)
