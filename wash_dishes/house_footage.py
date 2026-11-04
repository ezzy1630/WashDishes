"""Recorded household footage. No camera connection and no changes to retention.

The index uses segment write times, so wall clocks are approximate. Only closed,
settled files are offered; each signed capability identifies one immutable source
and one active household member. MP4 conversion copies video and discards audio.
"""
from __future__ import annotations

import bisect
import hashlib
import hmac
import json
import math
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import league
from .store import parse_stamp

LINK_SECONDS = 300
MAX_BYTES = 32 * 1024 * 1024
SEGMENT = re.compile(r'[0-9]{10,20}-[0-9]{6,10}\.ts\Z')


@dataclass(frozen=True)
class Segment:
    path: Path
    end: float
    size: int
    modified: int

    @property
    def id(self):
        return hashlib.sha256(f'{self.path.name}:{self.modified}:{self.size}'.encode()).hexdigest()[:32]


class Playback(FileResponse):
    def __init__(self, path, release):
        super().__init__(path, media_type='video/mp4', headers={'Cache-Control': 'private, no-store'})
        self.release = release

    async def __call__(self, scope, receive, send):
        # Pin the shared file through byte ranges, HEAD, 416 and disconnects.
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(self.release)


@dataclass
class Prepared:
    result: Future
    directory: tempfile.TemporaryDirectory
    used: float
    viewers: int = 0


class Footage:
    def __init__(self, house):
        self.house = house
        self.cache_lock = threading.Lock()
        self.cache: dict[str, Prepared] = {}
        self.index_lock = threading.Lock()
        self.scanned = 0.0
        self.segments: list[Segment] = []
        self.moments_at = 0.0
        self.moments: list[dict] = []

    def inventory(self):
        with self.index_lock:
            if time.monotonic() - self.scanned < 5:
                return self.segments
            root = self.house.media.buffer.resolve()
            found = []
            for path in root.glob('*.ts'):
                if not SEGMENT.fullmatch(path.name) or path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                    if path.is_file() and 0 < stat.st_size <= MAX_BYTES:
                        found.append(Segment(path, stat.st_mtime, stat.st_size, stat.st_mtime_ns))
                except FileNotFoundError:
                    continue
            found.sort(key=lambda s: (s.end, s.path.name))
            # The newest can still be open even if no packets arrived recently.
            # A grace period also avoids the writer's last flush/rotation race.
            self.segments = [s for s in found[:-1] if s.end < time.time() - 15]
            self.scanned = time.monotonic()
            return self.segments

    def member(self, uid):
        with self.house.store.lock:
            row = self.house.store.conn.execute(
                'SELECT id FROM residents WHERE id=? AND active=1 AND auth_user_id IS NOT NULL', (uid,)).fetchone()
        if not row:
            raise HTTPException(403, 'Sign in with an active household account.')

    def source(self, chunk):
        if not re.fullmatch('[a-f0-9]{32}', chunk):
            raise HTTPException(404, 'Recording not found.')
        segment = next((s for s in self.inventory() if s.id == chunk), None)
        if segment is None:
            raise HTTPException(410, 'This recording is no longer kept. Refresh the footage.')
        path = segment.path
        try:
            stat = path.stat()
            valid = (not path.is_symlink() and path.resolve().parent == self.house.media.buffer.resolve()
                     and stat.st_mtime_ns == segment.modified and stat.st_size == segment.size)
        except FileNotFoundError:
            valid = False
        if not valid:
            raise HTTPException(410, 'This recording is no longer kept. Refresh the footage.')
        return segment

    def signature(self, chunk, uid, exp):
        return hmac.new(self.house.media_key, f'footage:v1:{chunk}:{uid}:{exp}'.encode(), hashlib.sha256).hexdigest()

    def markers(self, start, end):
        if time.monotonic() - self.moments_at < 60:
            return self.moments
        store = self.house.store
        with store.lock:
            rows = store.conn.execute('''SELECT id,created_at FROM obligations
                WHERE julianday(created_at)>=julianday(?) AND julianday(created_at)<=julianday(?)
                ORDER BY created_at LIMIT 1000''', (
                    time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start)),
                    time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(end)))).fetchall()
        moments = [{'at': parse_stamp(r['created_at']).timestamp(), 'kind': 'dish', 'label': 'Dish first seen'} for r in rows]
        journal = store.root / 'journal.db'
        if journal.is_file():
            # An indexed one-frame sample per 30 seconds bounds work on the
            # camera's journal. These are presence hints, never a complete visit
            # ledger or attribution. Open a separate read-only connection.
            try:
                conn = sqlite3.connect(journal.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.1)
                try:
                    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                    deadline = time.monotonic() + 1
                    last = -math.inf
                    for at in range(int(start), int(end) + 1, 30):
                        row = conn.execute('SELECT at,detections FROM frames WHERE at>=? AND at<? ORDER BY at LIMIT 1', (at, at + 1)).fetchone()
                        if row and any(d.get('kind') == 'person' for d in json.loads(row[1])):
                            if row[0] - last > 90:
                                moments.append({'at': row[0], 'kind': 'person', 'label': 'Person detected · sampled'})
                            last = row[0]
                finally:
                    conn.close()
            except (sqlite3.Error, ValueError):
                pass
        self.moments = sorted(moments, key=lambda m: m['at'])
        self.moments_at = time.monotonic()
        return self.moments

    def index(self):
        segments = self.inventory()
        if not segments:
            return {'ranges': [], 'moments': [], 'timezone': str(league.timezone()), 'clock_estimated': True}
        ranges = []
        for s in segments:
            start = s.end - 10
            if ranges and start - ranges[-1]['end'] <= 5:
                ranges[-1]['end'] = s.end
            else:
                ranges.append({'start': start, 'end': s.end})
        return {'ranges': ranges, 'moments': self.markers(ranges[0]['start'], ranges[-1]['end']),
                'timezone': str(league.timezone()), 'clock_estimated': True}

    @lru_cache(maxsize=512)
    def probe(self, segment):
        try:
            result = subprocess.run(['nice', '-n', '15', 'ffprobe', '-v', 'error', '-show_entries',
                'format=duration:stream=codec_name,codec_type', '-of', 'json', str(segment.path)],
                capture_output=True, timeout=5, check=True)
            data = json.loads(result.stdout)
            duration = float(data['format']['duration'])
            video = next(s for s in data['streams'] if s['codec_type'] == 'video')
            if video['codec_name'] != 'h264' or not 0 < duration <= 60:
                raise HTTPException(415, 'This recording format cannot be played here.')
            return duration
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
            raise HTTPException(503, 'Could not read this recording. Try another moment.') from None

    def chunk(self, at, uid):
        if not math.isfinite(at):
            raise HTTPException(400, 'Choose a recording time.')
        segments = self.inventory()
        if not segments or at < segments[0].end - 60 or at > segments[-1].end + 1:
            raise HTTPException(410, 'Footage from this time is no longer kept or has not finished recording. The photo is still available.')
        i = min(bisect.bisect_left([s.end for s in segments], at), len(segments) - 1)
        s = self.source(segments[i].id)
        duration = self.probe(s)
        start = s.end - duration
        if at < start - 2:
            raise HTTPException(404, 'There is a recording gap at this time. Pick a marked recording on the timeline.')
        exp = int(time.time()) + LINK_SECONDS
        url = (f'{self.house.config.public_origin}/house/footage/{s.id}.mp4?uid={uid}&exp={exp}'
               f'&sig={self.signature(s.id, uid, exp)}')
        return {'url': url, 'start': start, 'end': s.end, 'offset': max(0, min(at - start, duration - 0.1)),
                'previous': segments[i - 1].end - 0.1 if i else None,
                'next': segments[i + 1].end - 0.1 if i + 1 < len(segments) else None,
                'expires': exp, 'clock_estimated': True}

    def file(self, chunk, uid, exp, sig):
        now = int(time.time())
        if exp <= now or exp > now + LINK_SECONDS or not hmac.compare_digest(self.signature(chunk, uid, exp), sig):
            raise HTTPException(403, 'This footage link expired or is invalid. Open the recording again.')
        self.member(uid)
        s = self.source(chunk)
        # No viewer-count admission. Same-source requests share one preparation;
        # distinct chunks remux independently at nice 15. Retention is checked
        # before consulting the temporary cache, including on every range read.
        with self.cache_lock:
            self.prune()
            entry = self.cache.get(chunk)
            build = entry is None
            if build:
                entry = Prepared(Future(), tempfile.TemporaryDirectory(prefix='dishwatch-footage-'), time.monotonic())
                self.cache[chunk] = entry
            entry.viewers += 1

        def release():
            with self.cache_lock:
                entry.viewers -= 1
                entry.used = time.monotonic()
                self.prune()

        try:
            if build:
                path = Path(entry.directory.name) / 'chunk.mp4'
                self.probe(s)
                subprocess.run(['nice', '-n', '15', 'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                    '-i', str(s.path), '-map', '0:v:0', '-an', '-c:v', 'copy', '-movflags', '+faststart',
                    '-threads', '1', '-y', str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=True)
                self.source(chunk)
                entry.result.set_result(path)
            path = entry.result.result(timeout=15)
            return Playback(path, release)
        except BaseException as error:
            if build and not entry.result.done():
                entry.result.set_exception(error)
            release()
            if isinstance(error, (OSError, subprocess.SubprocessError, TimeoutError)):
                raise HTTPException(503, 'Could not open this recording. Try again shortly.') from None
            raise

    def prune(self):
        # Called under cache_lock. Active responses pin their temporary files.
        idle = sorted(((key, e) for key, e in self.cache.items() if not e.viewers), key=lambda p: p[1].used)
        size = sum(sum(p.stat().st_size for p in Path(e.directory.name).glob('*.mp4')) for e in self.cache.values())
        for key, entry in idle:
            if time.monotonic() - entry.used > 60 or size > 128 * 1024 * 1024 or (entry.result.done() and entry.result.exception()):
                size -= sum(p.stat().st_size for p in Path(entry.directory.name).glob('*.mp4'))
                entry.directory.cleanup()
                del self.cache[key]

    def close(self):
        with self.cache_lock:
            for entry in self.cache.values():
                entry.directory.cleanup()
            self.cache.clear()


def install(router: APIRouter, house):
    footage = Footage(house)
    house.footage = footage

    def fields(request, allowed):
        if set(request.query_params) - allowed:
            raise HTTPException(400, 'Unexpected footage request fields.')

    @router.get('/api/footage')
    def index(request: Request, resident=Depends(house.resident)):
        fields(request, set())
        return footage.index()

    @router.get('/api/footage/chunk')
    def chunk(at: float, request: Request, resident=Depends(house.resident)):
        fields(request, {'at'})
        return footage.chunk(at, resident['id'])

    @router.api_route('/footage/{chunk}.mp4', methods=['GET', 'HEAD'])
    def file(chunk: str, uid: int, exp: int, sig: str, request: Request):
        fields(request, {'uid', 'exp', 'sig'})
        return footage.file(chunk, uid, exp, sig)
