"""Arrival context for human naming, independent of the strict four-photo proof.

Bodies hiding the spot are useful context here. Neither a person box nor this strip
confirms ownership. Searches stream the journal; decoding uses a bounded candidate pool in
single-threaded, niced FFmpeg, on the existing background proof worker.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import time
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import collage, putdown
from .media import atomic_write
from .store import parse_stamp, stamp, utcnow

KEEP = timedelta(days=7)
MAX_WINDOW = 20 * 60
TIMEOUT = 'Arrival context search reached its bounded time budget.'
DECODE_RETRY = 'Retained arrival footage could not be decoded; retry pending.'
RETRY_DELAY = timedelta(minutes=5)
USE_LOOKBACK = 6 * 3600


def use_frames(frames, box: list[float], anchor: float) -> list[dict]:
    """Up to three visits near the spot, never a claim of dish use or ownership.

    Merge missed detections less than 90 seconds apart, as in the journal audit.
    Sustained visits always outrank pass-throughs. Keep a small geometric shortlist
    per visit for pixel ranking after grouped decoding, rather than decoding hours.
    """
    visits, current = [], []
    for frame in frames:
        if not anchor - USE_LOOKBACK <= frame['at'] <= anchor:
            continue
        people = [d['box'] for d in frame['detections'] if d['kind'] == 'person'
                  and putdown._reach(box, d['box']) < .08]
        if not people:
            continue
        if current and frame['at'] - current[-1]['at'] >= 90:
            visits.append(current)
            current = []
        current.append({'at': frame['at'], 'person_boxes': people})
    if current:
        visits.append(current)

    def observed_seconds(visit):
        # A gap may connect a visit, but is not observed presence. Without this,
        # three pass-through detections can masquerade as a two-minute stay.
        return sum(min(2.5, b['at']-a['at']) for a, b in zip(visit, visit[1:]))

    def rank(visit):
        duration = observed_seconds(visit)
        # Recency matters within each class; a twenty-minute stay can beat a
        # one-minute stay a few minutes later, but not one six hours later.
        return (duration >= 60, visit[-1]['at'] + min(duration, 30 * 60))

    selected = []
    sustained = [v for v in visits if observed_seconds(v) >= 60]
    for priority, visit in enumerate(sorted(sustained or visits, key=rank, reverse=True)[:3]):
        context = {'start': visit[0]['at'], 'end': visit[-1]['at'], 'observations': len(visit), 'priority': priority,
                   'observed_seconds': observed_seconds(visit)}
        times = [f['at'] for f in visit]
        for frame in visit:
            neighbors = []
            for at in (frame['at'] - 3, frame['at'] + 3):
                index = min(len(visit)-1, bisect_right(times, at))
                nearby = min(visit[max(0,index-1):index+1], key=lambda f: abs(f['at']-at))
                if abs(nearby['at']-at) <= 1.5:
                    neighbors.append(nearby)
            # Receipt/segment times are approximate. A stable person over six
            # seconds keeps a moving passer-by's old box off the backsplash.
            frame['person_stability'] = min((max(putdown.box_iou(p, q) for q in n['person_boxes'])
                                            for n in neighbors for p in frame['person_boxes']), default=0) if len(neighbors) == 2 else 0
        # Choose alternate moments across the visit, so a blink/glare in one
        # frame cannot force the sole displayed view. At most nine use decodes.
        pool = sorted(visit, key=lambda f: use_frame_rank(f, [box]), reverse=True)
        picks = []
        for frame in pool:
            if all(abs(frame['at'] - p['at']) >= 3 for p in picks):
                picks.append(frame)
                if len(picks) == 3:
                    break
        selected.extend(f | {'use_visit': context, 'use_order': order} for order, f in enumerate(picks))
    return sorted(selected, key=lambda f: f['at'])


def use_frame_rank(frame: dict, boxes: list) -> tuple:
    people = frame.get('person_boxes', [])
    covered = max((putdown._overlap(b, p) for b in boxes for p in people), default=1)
    stability = frame.get('person_stability', 0)
    return (stability >= .4, covered < .15, len(people) == 1, -covered, stability,
            max((p[2] * p[3] for p in people), default=0))


def with_use_context(journal, found: dict, anchor: float, segments) -> dict:
    """Extend the same plan only when its arrival frames have no nearby person."""
    for frame in found['frames']:
        if not frame.get('person_boxes'):
            continue
        # A moving person's journal box can outlast them in the decoded view.
        # Retain ordinary arrivals when any depicted person has support on both
        # sides; otherwise add nearby-visit context without inventing a put-down.
        surrounding = list(journal.frames(frame['at']-4, frame['at']+4))
        supported = []
        for at in (frame['at']-3, frame['at']+3):
            nearby = min(surrounding, key=lambda f: abs(f['at']-at), default=None)
            supported.append(bool(nearby and abs(nearby['at']-at) <= 1.5 and any(
                d['kind'] == 'person' and putdown.box_iou(p,d['box']) >= .4
                for p in frame['person_boxes'] for d in nearby['detections'])))
        if all(supported):
            return found
    starts = [s[0] for s in segments]
    def retained(frame):
        index = bisect_right(starts, frame['at']) - 1
        return index >= 0 and frame['at'] < segments[index][1]
    frames = use_frames((f for f in journal.frames(anchor - USE_LOOKBACK, anchor) if retained(f)), found['box'], anchor)
    return found | {'frames': sorted([*frames, *found['frames']], key=lambda f: f['at'])}


class _Footage(list):
    """One search owns a bounded decode cache; never retain household pixels globally."""

    def __init__(self, segments):
        super().__init__(segments)
        self.photos = {}
        self.deadline = None
        self.targets = []
        self.durations = {}


def arrival_window(frames, box: list[float], anchor: float, *, family: str | None = None) -> dict | str:
    """Episode at the logged sighting, from sustained absence through covered frames.

    Detection flicker needs twelve seconds of uncovered absence before it can start
    a new episode. A gap can never supply an empty-before frame. Unlike the proof
    finder, several people and long occlusion are retained for a human to inspect.
    """
    before = first = clear = absent = previous = None
    episode_before = None
    count = 0
    candidate = None
    for frame in frames:
        at = frame['at']
        if at > anchor:
            break
        if previous is not None and at - previous > 8:
            before = first = clear = absent = episode_before = candidate = None
            count = 0
        previous = at
        present, people = putdown._spot(frame, box, family)
        covered = bool(people)  # Keep the entire nearby visit, including a body beside the spot.
        if present:
            if first is None:
                first, episode_before = at, before
            count += 1
            absent = None
            if not covered and clear is None:
                clear = at
            if count >= 3 and episode_before is not None:
                candidate = {'before': episode_before, 'appeared': first, 'after': clear or at,
                             'occluded': clear is None, 'source': 'journal'}
        elif not covered:
            if absent is None:
                absent = at
            if first is None or at - absent >= 12:
                # An overlapping different dish is context, never an empty spot.
                if not any(putdown._matches(d, box, None) for d in frame['detections']):
                    before = at
                    first = clear = episode_before = None
                    count = 0
        else:
            absent = None
    if candidate is None:
        return 'No sustained dish arrival with an earlier clear spot in the retained journal.'
    if anchor - candidate['appeared'] > 10 * 60:
        return 'The journal cannot connect a recent spot change to the first recorded sighting.'
    if candidate['after'] - candidate['before'] > MAX_WINDOW:
        return 'The spot was hidden for more than twenty minutes; the arrival cannot be localized.'
    # A little approach context avoids six virtually identical frames after a missed person detection.
    candidate['context_start'] = candidate['before'] - 45
    candidate['after'] = max(candidate['after'], candidate['appeared'] + 6)
    return candidate


def select_frames(frames, box: list[float], window: dict, *, count: int = 6) -> list[dict]:
    """Keep before/after plus spread-out views of people within reach of the spot."""
    start, end = window.get('context_start', window['before']), window['after']
    # Retain at most one candidate per second; a 20-minute window stays tiny.
    candidates = []
    for frame in frames:
        if not start <= frame['at'] <= end:
            continue
        _, people = putdown._spot(frame, box, None)
        item = {'at': frame['at'], 'person_boxes': [p['box'] for p in people]}
        if candidates and frame['at'] - candidates[-1]['at'] < 1:
            if people and not candidates[-1]['person_boxes']:
                candidates[-1] = item
        else:
            candidates.append(item)
    if not candidates:
        return []
    chosen = [candidates[0], candidates[-1]]
    people = [f for f in candidates[1:-1] if f['person_boxes']]
    appeared = window.get('appeared')
    if len(people)>=count-2:
        for fraction in (i/max(1,count-3) for i in range(count-2)):
            selected=people[round(fraction*(len(people)-1))]
            if selected not in chosen:
                chosen.append(selected)
    elif appeared is not None:
        for at in (appeared-4,appeared-2,appeared,appeared+2):
            preferred=min(people,key=lambda f:abs(f['at']-at)) if people else None
            selected=preferred if preferred and abs(preferred['at']-at)<=2 else min(candidates,key=lambda f:abs(f['at']-at))
            if selected not in chosen:
                chosen.append(selected)
    else:
        pool = people if len(people)>=count-2 else candidates[1:-1]
        for i in range(count-2):
            if not pool:
                break
            index=round((i+.5)*len(pool)/(count-2)-.5)
            selected=pool[max(0,min(len(pool)-1,index))]
            if selected not in chosen:
                chosen.append(selected)
    for candidate in candidates:
        if len(chosen) >= count:
            break
        if candidate not in chosen:
            chosen.append(candidate)
    return sorted(chosen, key=lambda f: f['at'])


def _patch_window(buffer: Path, box: list[float], anchor: float, *, deadline: float) -> dict | str:
    """Bounded fallback when the journal starts after the dish; reuse tiny spot patches.

    Sparse patches only locate a changed spot. The strip says approximate context,
    and never claims an observed empty view or a proven person-to-dish association.
    """
    segments = [s for s in footage_segments(buffer) if s[0] <= anchor]
    if not segments or anchor - segments[-1][1] > 60:
        return 'No retained footage covers the first sighting or its arrival.'
    latest = putdown.samples(segments[-1], box, first_only=True, deadline=deadline)
    if not latest:
        return 'The recording at the first sighting could not be decoded.'
    reference = latest[0]
    previous = reference.at
    # At most 48 small decodes, spread over the retained 50-hour buffer.
    stride = max(1, len(segments) // 48)
    for segment in reversed(segments[::stride]):
        if time.monotonic() >= deadline:
            return TIMEOUT
        samples = putdown.samples(segment, box, first_only=True, deadline=deadline)
        if not samples:
            continue
        sample = samples[0]
        if putdown._distance(sample.patch, reference.patch) >= putdown.MIN_CHANGE:
            if previous - sample.at > MAX_WINDOW:
                return 'Retained footage only localizes the spot change to more than twenty minutes.'
            return {'before': sample.at, 'appeared': previous, 'after': reference.at if reference.at - sample.at <= MAX_WINDOW else previous,
                    'source': 'footage_patch', 'approximate': True}
        previous = sample.at
    return 'The dish spot does not change within the retained footage before its first sighting.'


def plan(journal, buffer: Path, item: dict, *, deadline: float | None = None) -> dict | str:
    deadline = deadline or time.monotonic() + 30
    class BoundedJournal:
        def bounds(self):
            return journal.bounds()

        def frames(self, start, end):
            for index, frame in enumerate(journal.frames(start, end)):
                if index % 256 == 0 and time.monotonic() >= deadline:
                    raise putdown._SearchTimeout
                yield frame
    try:
        # Leave a small reserve for useful missing-arrival context when the
        # precision search spends its budget on occluded before/after candidates.
        reserve = min(3, max(0, (deadline - time.monotonic()) / 4))
        retained = {}
        try:
            result = _plan(BoundedJournal(), buffer, item, deadline=deadline-reserve, retained=retained)
        except putdown._SearchTimeout:
            putdown._check_deadline(deadline)
            result = _missing_plan(BoundedJournal(), buffer, item, deadline=deadline, **retained)
        putdown._check_deadline(deadline)
        return result
    except putdown._SearchTimeout:
        return TIMEOUT


def footage_segments(buffer: Path):
    """Reuse recorder indexing; recover the first completed file after a copy/gap.

    The strict finder needs a predecessor mtime. Isolated audit windows may lack
    that file. The fixed recorder segment length supplies its approximate start;
    it never supplies an empty-before observation across the gap.
    """
    found=[(max(start,end-10) if path.name.endswith('-000000.ts') else start,end,path)
           for start,end,path in putdown.segments(buffer)]
    # A recorder restart's first file cannot inherit the previous stream's
    # mtime: startup gaps would stretch an eight-second video to eighteen seconds.
    # Use the same nominal ten-second bound as an isolated first segment below.
    indexed={s[2] for s in found}
    for path in buffer.glob('*.ts'):
        if path in indexed or path.is_symlink():
            continue
        info=path.stat()
        if info.st_mtime>time.time()-30:
            continue
        # Camera records fixed ten-second segments, also the convention used by
        # Media.save_recent_clip. Wall times are estimates; decoder PTS stays exact.
        found.append((info.st_mtime-10,info.st_mtime,path))
    return _Footage(sorted(found))


def _photo_at(segments, at: float) -> tuple[float, bytes] | None:
    segment = next((s for s in segments if s[0] <= at < s[1]), None)
    pictures = []
    if segment:
        deadline = getattr(segments, 'deadline', None)
        if deadline is not None:
            putdown._check_deadline(deadline)
        if segment[2].name.endswith('-000000.ts'):
            # The first segment can be shorter than ten seconds. Probe only a
            # selected first file (once per search), never every retained file.
            # A nominal start otherwise leaves its final seconds undecodable.
            durations = getattr(segments, 'durations', {})
            if segment[2] not in durations:
                duration = 10
                try:
                    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                             '-of', 'default=noprint_wrappers=1:nokey=1', str(segment[2])],
                                            capture_output=True, timeout=max(.01, min(1, deadline-time.monotonic())) if deadline is not None else 1)
                    value = float(result.stdout)
                    if result.returncode == 0 and 0 < value <= 15:
                        duration = value
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
                durations[segment[2]] = duration
            if deadline is not None:
                putdown._check_deadline(deadline)
            segment = (segment[1]-durations[segment[2]], segment[1], segment[2])
            if not segment[0] <= at < segment[1]:
                return None
        # Decode once per short recorder segment and encode only requested
        # instants. Encoding all twenty 2-fps JPEGs costs seconds on the Jetson.
        cache = getattr(segments, 'photos', None)
        targets = ([t for t in getattr(segments, 'targets', []) if segment[0] <= t < segment[1]]
                   if segment[1]-segment[0] <= 15 else []) or [at]
        key = (segment[2], tuple(targets))
        if cache is None or key not in cache:
            pictures = putdown.decoded_photos(segment, fps=2,
                t0=max(segment[0], min(targets)-.5), t1=min(segment[1], max(targets)+.75),
                sample_times=targets, deadline=deadline)
            if cache is not None:
                cache[key] = pictures
        else:
            pictures = cache[key]
        if deadline is not None:
            putdown._check_deadline(deadline)

    return min(pictures,key=lambda p:abs(p[0]-at)) if pictures else None


def _visits(frames, box):
    visits = []
    start = end = previous = None
    for frame in frames:
        if previous is not None and frame['at']-previous>8:
            start = end = None
        _, people = putdown._spot(frame,box,None)
        if people:
            start = frame['at'] if start is None else start
            end = frame['at']
        elif end is not None and frame['at']-end>3:
            visits.append((start,end))
            visits = visits[-24:]
            start = end = None
        previous = frame['at']
    if end is not None:
        visits.append((start,end))
    return visits


def _already_visible(before: bytes, reference: bytes, box: list[float]) -> bool:
    """A small shift/rotation of an existing pan is not a new arrival.

    Search one dish width around the spot; require both shape and colour agreement.
    This is only an absence veto, never evidence of identity or responsibility.
    """
    import cv2
    import numpy as np
    first=cv2.imdecode(np.frombuffer(before,np.uint8),cv2.IMREAD_COLOR)
    later=cv2.imdecode(np.frombuffer(reference,np.uint8),cv2.IMREAD_COLOR)
    if first is None or later is None:
        return True
    if first.shape!=later.shape:
        first=cv2.resize(first,(later.shape[1],later.shape[0]))
    height,width=later.shape[:2]
    x,y,w,h=box
    template=later[int(y*height):int((y+h)*height),int(x*width):int((x+w)*width)]
    search=first[int(max(0,y-h)*height):int(min(1,y+2*h)*height),int(max(0,x-w)*width):int(min(1,x+2*w)*width)]
    if not template.size or search.shape[0]<template.shape[0] or search.shape[1]<template.shape[1]:
        return True
    result=cv2.matchTemplate(cv2.cvtColor(search,cv2.COLOR_BGR2GRAY),cv2.cvtColor(template,cv2.COLOR_BGR2GRAY),cv2.TM_CCOEFF_NORMED)
    _,similarity,_,(left,top)=cv2.minMaxLoc(result)
    if similarity<.5:
        return False
    candidate=search[top:top+template.shape[0],left:left+template.shape[1]]
    def histogram(image):
        hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
        return cv2.normalize(cv2.calcHist([hsv],[0,1],None,[16,8],[0,180,0,256]),None)
    return cv2.compareHist(histogram(candidate),histogram(template),cv2.HISTCMP_BHATTACHARYYA)<.35


def _plan(journal, buffer: Path, item: dict, *, deadline: float, retained: dict) -> dict | str:
    anchor = parse_stamp(item['created_at']).timestamp()
    box = item['box']
    if not box:
        return 'No saved dish box locates the arrival spot.'
    bounds = journal.bounds()
    segments = footage_segments(buffer)
    if isinstance(segments, _Footage):
        segments.deadline = deadline
    reference = _photo_at(segments,anchor)
    if reference is None:
        return DECODE_RETRY if any(s[0] <= anchor < s[1] for s in segments) else 'No retained footage covers the first recorded sighting.'
    # Reuse the already-decoded sighting if the strict arrival search times out.
    # Re-decoding it can consume the entire reserve before nearby visits are read.
    retained.update(segments=segments, reference=reference)
    reference_patch = putdown._jpeg_patch(reference[1],box)
    end = min(anchor+30,bounds[1]) if bounds else anchor
    candidates = []
    if bounds:
        # A recent arrival needs a clear view within MAX_WINDOW. Older nearby
        # visitors cannot establish who placed a dish at this first sighting.
        # Include a minute of pre-roll for absence/approach continuity.
        start = max(bounds[0], anchor-MAX_WINDOW-60)
        # The journal locates visits, not guilt. Compare the actual spot before/after
        # each visit with its first-observed appearance before claiming an arrival.
        visits = _visits(journal.frames(start,end),box)
        candidates = [{'before':a-20,'appeared':a,'after':b+8,'source':'journal_and_footage'}
                      for a,b in reversed(visits) if b-a<=MAX_WINDOW]
        detected = arrival_window(journal.frames(start,end),box,end,family=putdown.FAMILIES.get(item['dishes'][0]['kind']))
        if isinstance(detected,dict):
            candidates.insert(0,detected)
    else:
        window = _patch_window(buffer,box,anchor,deadline=deadline)
        if window == TIMEOUT:
            raise putdown._SearchTimeout
        if isinstance(window,dict):
            candidates.append(window)
    for window in candidates[:12]:
        putdown._check_deadline(deadline)
        if bounds and any(s[0] <= window['before']-.75 and window['before']+.75 < s[1] for s in segments):
            # These observations lie inside the final twelve-second veto for
            # every admissible decoded endpoint (+/- .75s). Reject known
            # occupied/covered spots before spending CPU on two full decodes.
            prior = journal.frames(window['before']-10, window['before']-1)
            if any(putdown._overlap(box, d['box']) > (.15 if d['kind']=='person' else .2)
                   for f in prior for d in f['detections']):
                continue
        before, after = _photo_at(segments,window['before']), _photo_at(segments,window['after'])
        if before is None:
            choices = [s for s in segments if window['before']<=s[0]<window['appeared']]
            before = _photo_at(segments,choices[0][0]+.5) if choices else None
        if before is None or after is None:
            continue
        nearby = list(journal.frames(before[0]-12,before[0]+.75))
        if bounds:
            if len(nearby)<3 or nearby[-1]['at']-nearby[0]['at']<8:
                continue
            # A detector class/size flip cannot establish an empty spot. Any dish
            # detection over the spot, even an oversized pan, vetoes this before.
            occupied = any(putdown._overlap(box,d['box'])>.2 for f in nearby for d in f['detections'] if d['kind']!='person')
            covered = any(putdown._overlap(box,d['box'])>.15 for f in nearby for d in f['detections'] if d['kind']=='person')
            if occupied or covered:
                continue
        prior = putdown._distance(putdown._jpeg_patch(before[1],box),reference_patch)
        later = putdown._distance(putdown._jpeg_patch(after[1],box),reference_patch)
        # A missed detection on an unchanged pan is not an arrival. The post-visit
        # spot must resemble this dish substantially more than the pre-visit spot.
        if prior >= .55 and later < min(.55,prior*.6) and not _already_visible(before[1],reference[1],box):
            window = window | {'arrival_recorded':True,'before':before[0],'after':after[0]}
            selected = select_frames(journal.frames(window['before'],window['after']),box,window)
            if len(selected)<6:
                selected = [{'at':window['before']+i*(window['after']-window['before'])/5,'person_boxes':[]} for i in range(6)]
            return with_use_context(journal, {'window':window,'frames':selected,'box':box}, anchor, segments)
    # Nearby visits are useful context, but cannot supply a put-down. Keep the
    # original missing-arrival fact in both the image caption and metadata.
    return _missing_plan(journal, buffer, item, deadline=deadline, segments=segments, reference=reference)


def _missing_plan(journal, buffer, item, *, deadline, segments=None, reference=None):
    anchor, box = parse_stamp(item['created_at']).timestamp(), item['box']
    if not box:
        return 'No saved dish box locates the arrival spot.'
    if segments is None:
        segments = footage_segments(buffer)
        if isinstance(segments, _Footage):
            segments.deadline = deadline
    reference = reference or _photo_at(segments, anchor)
    if reference is None:
        return DECODE_RETRY if any(s[0] <= anchor < s[1] for s in segments) else 'No retained footage covers the first recorded sighting.'
    available = [s for s in segments if s[1]>=anchor and s[0]<=anchor+40]
    if not available:
        return 'No retained footage covers the first recorded sighting.'
    first = max(anchor,available[0][0])
    last = min(anchor+30,available[-1][1]-.5)
    if last-first<3:
        return 'Insufficient retained footage to show distinct already-present views.'
    window = {'before':first,'after':last,'source':'already_present','arrival_recorded':False,
              'first_visible_at':reference[0],'reason':'Already there at the first retained view; arrival not recorded.'}
    return with_use_context(journal, {'window':window,'box':box,'frames':[{'at':first+i*(last-first)/5,'person_boxes':[]} for i in range(6)]}, anchor, segments)


def same_visit(journal, left: dict, right: dict) -> bool:
    """Nearby spots, nearby times, one continuously present person; empty/gap splits.

    A shared track is stronger than proximity. With no track IDs, one unambiguous
    overlapping person box must continue through every journal observation.
    """
    if any(f.get('use_visit') for p in (left, right) for f in p['frames']):
        return False  # Context from an earlier visit cannot merge naming units.
    a, b = left['box'], right['box']
    distance = max(0, max(a[0], b[0]) - min(a[0]+a[2], b[0]+b[2])) + max(0, max(a[1], b[1]) - min(a[1]+a[3], b[1]+b[3]))
    if distance > .20:
        return False
    person_times = [[f['at'] for f in p['frames'] if f['person_boxes']] for p in (left, right)]
    if not all(person_times):
        return False
    if max(min(t) for t in person_times) - min(max(t) for t in person_times) > 180:
        return False
    start, end = min(min(t) for t in person_times), max(max(t) for t in person_times)
    previous_at = previous_box = track = None
    count = 0
    for frame in journal.frames(start, end):
        if previous_at is not None and frame['at'] - previous_at > 8:
            return False
        people = [d for d in frame['detections'] if d['kind'] == 'person'
                  and min(putdown._reach(a, d['box']), putdown._reach(b, d['box'])) <= .1]
        if not people:
            return False
        if track is not None:
            people = [d for d in people if d.get('track_id') == track]
        elif previous_box is not None:
            people = [d for d in people if putdown.box_iou(d['box'], previous_box) >= .15]
        if len(people) != 1:
            return False
        chosen = people[0]
        if track is None and count == 0:
            track = chosen.get('track_id')
        previous_box, previous_at = chosen['box'], frame['at']
        count += 1
    return count >= 2


def duplicate_clusters(items: list[dict]) -> list[list[dict]]:
    """Coexisting overlapping records at one spot share one physical-dish slot.

    Contained rim/sliver boxes can have tiny IoU. Compare intersection to the
    smaller box too; adjacent non-overlapping dishes remain separate. Preserve
    every source obligation and box for correction/audit instead of deleting rows.
    """
    def overlaps(a,b):
        if not a.get('box') or not b.get('box'):
            return False
        coexist = (min(parse_stamp(a['last_seen_at'] or a['created_at']),parse_stamp(b['last_seen_at'] or b['created_at']))-
                   max(parse_stamp(a['created_at']),parse_stamp(b['created_at']))).total_seconds()
        # Brief overlap can be a transfer or replacement; it cannot deduplicate two
        # physical objects. The worker runs after the two-hour follow-up.
        if coexist < 30*60 or abs((parse_stamp(a['created_at'])-parse_stamp(b['created_at'])).total_seconds())>3600:
            return False
        if putdown.FAMILIES.get(a['dishes'][0]['kind']) != putdown.FAMILIES.get(b['dishes'][0]['kind']):
            return False
        x,y,w,h=a['box']
        xx,yy,ww,hh=b['box']
        small = (w,h) if w*h<ww*hh else (ww,hh)
        ratio=max(w*h,ww*hh)/min(w*h,ww*hh)
        # A small, complete vessel nested beside/in a much larger pan is not a rim.
        if (ratio>4 or ratio>2.5 and a['dishes'][0]['kind']!=b['dishes'][0]['kind']) and .4<=small[0]/small[1]<=2:
            return False
        intersection=max(0,min(x+w,xx+ww)-max(x,xx))*max(0,min(y+h,yy+hh)-max(y,yy))
        return intersection/min(w*h,ww*hh)>=.35 and max(w/ww,ww/w,h/hh,hh/h)<=8
    remaining=list(items)
    groups=[]
    while remaining:
        anchor=remaining.pop(0)
        group=[anchor]+[item for item in remaining if overlaps(anchor,item)]
        remaining=[item for item in remaining if item not in group]
        groups.append(sorted(group,key=lambda item:(item['created_at'],item['id'])))
    return groups


def render(item: dict, frames: list[dict], *, boxes: list[list[float]] | None = None, approximate: bool = False, already_at: float | None = None) -> bytes:
    from . import evidence_image as visual
    if not frames:
        raise ValueError('No retained arrival photos.')
    boxes = boxes or [item['box']]
    arrival_frames = [f for f in frames if not f.get('use_visit')]
    day = datetime.fromtimestamp(arrival_frames[0]['at'],timezone.utc).astimezone(ZoneInfo(os.getenv('WASHDISHES_TIMEZONE','America/Los_Angeles')))
    def clock(at):
        local = datetime.fromtimestamp(at,timezone.utc).astimezone(day.tzinfo)
        return local.strftime(('%b %-d · ' if local.date() != day.date() else '') + '%-I:%M:%S %p')
    last = arrival_frames[-1]
    panels = [{'jpeg':last['jpeg'],'label':'Dish detail' if len(boxes) == 1 else 'Dish details',
               'time':clock(last['at']),'boxes':boxes,'at':last['at']}]
    # Compare the same crop before/after. An already-present window never earns
    # a before label, and duplicate pixels do not earn another panel.
    if already_at is None and not approximate and arrival_frames[0]['jpeg'] != last['jpeg']:
        panels.insert(0,{'jpeg':arrival_frames[0]['jpeg'],'label':'Earlier view · check this spot',
                        'time':clock(arrival_frames[0]['at']),'at':arrival_frames[0]['at'],'boxes':boxes,'expected':True,
                        'region':visual.region(boxes,last['jpeg'])})
    use = [f for f in frames if f.get('use_visit') and f.get('person_boxes')]
    for index, frame in enumerate(use, 1):
        visit = frame['use_visit']
        seconds = visit.get('observed_seconds', visit['end'] - visit['start'])
        duration = f'{int(seconds // 60)} min' if seconds >= 60 else 'under 1 min'
        local_end = datetime.fromtimestamp(visit['end'],timezone.utc).astimezone(day.tzinfo)
        until = local_end.strftime(('%b %-d · ' if local_end.date() != day.date() else '') + '%-I:%M %p')
        heads = [[x,y,w,min(h,max(w,h*.6))] for x,y,w,h in frame['person_boxes']]
        panels.append({'jpeg':frame['jpeg'], 'label':f'Near this spot for {duration}, until {until}',
                       'time':f'Candidate visit {index} · frame at {clock(frame["at"])}',
                       'at':frame['at'], 'boxes':boxes, 'person_boxes':heads, 'people':True})
    if already_at is None:
        for frame in visual.distinct_people([f for f in frames if not f.get('use_visit')]):
            # A journal box can lag behind somebody leaving. Do not give an
            # unchanged patch of counter a "Who was at the counter" panel.
            if not any(any(putdown.box_iou(p, q) >= .15 for q in last.get('person_boxes', []))
                       or visual.person_changed(frame['jpeg'], last['jpeg'], p) for p in frame['person_boxes']):
                continue
            heads = [[x,y,w,min(h,max(w,h*.6))] for x,y,w,h in frame['person_boxes']]
            panels.append({'jpeg':frame['jpeg'],'label':'Who was at the counter',
                           'time':clock(frame['at']),'at':frame['at'],'boxes':heads,'people':True})
    # Tell the visit in the order it happened: before, who was there, then the dish left behind.
    panels.sort(key=lambda panel: panel['at'])
    notes = ['Times are approximate.']
    if already_at is not None:
        notes.append("Arrival wasn't recorded — it was already here at " + datetime.fromtimestamp(already_at,timezone.utc).astimezone(day.tzinfo).strftime('%-I:%M %p') + '.')
    elif approximate:
        notes.append('Arrival timing is uncertain; check these views before naming.')
    if use:
        notes.append('Yellow: dish spot. Blue: people nearby, not confirmed users.')
    notes.append('Only name someone if you know they used these dishes' if use else 'Only name someone if you know they left these dishes')
    subtitle = f"{day:%b %-d} · {collage.place(item['location'])}"
    if use:
        subtitle += ' · Arrival not recorded.' if already_at is not None else ' · Nearby visits; use unconfirmed.'
    return visual.render('Dishes to check',subtitle,panels,notes)


def display_frames(journal, buffer: Path, found: dict, boxes: list, *, deadline: float) -> list[dict]:
    """Decode a bounded pool throughout the arrival, then rank pixels, not times.

    Keep the original endpoints, plus explicitly labeled nearby-visit context.
    Visit membership and identity teaching never follow a proximity guess.
    """
    segments = footage_segments(buffer)
    if isinstance(segments, _Footage):
        segments.deadline = deadline
    candidates = list(found['frames'])
    window = found['window']
    # When planning used most of the budget, keep the six original views.
    # Extra ranking candidates must not displace those endpoint/visitor views.
    if window.get('arrival_recorded',True) and deadline-time.monotonic() > 15:
        x = min(b[0] for b in boxes)
        y = min(b[1] for b in boxes)
        union = [x,y,max(b[0]+b[2] for b in boxes)-x,max(b[1]+b[3] for b in boxes)-y]
        candidates += select_frames(journal.frames(window['before'],window['after']),union,window,count=12)
    # Saved endpoints define the visit. context_start is approach pre-roll for
    # initial planning; applying it again must not expand an existing filmstrip.
    endpoints = [f for f in found['frames'] if not f.get('use_visit')]
    first, last = endpoints[0]['at'], endpoints[-1]['at']
    candidates = sorted({f['at']: f for f in candidates if f.get('use_visit') or first <= f['at'] <= last}.values(), key=lambda f: f['at'])
    # Decode endpoints first, so interior ranking cannot consume their budget.
    use = [f for f in candidates if f.get('use_visit') and f['at'] not in (first,last)]
    if use:
        # One good frame of each ranked visit before spending on alternatives.
        # Missing-arrival interior detail frames add nothing to the two endpoints.
        use.sort(key=lambda f: (f.get('use_order', 0), f['use_visit'].get('priority', 0)))
        candidates = [endpoints[0], endpoints[-1], *use]
    else:
        candidates = [endpoints[0], endpoints[-1], *[f for f in candidates if f['at'] not in (first,last)]]
    if isinstance(segments, _Footage):
        segments.targets = [f['at'] for f in candidates]
    decoded = {}
    for selected in candidates:
        if time.monotonic() >= deadline:
            break
        try:
            photo = _photo_at(segments,selected['at'])
        except putdown._SearchTimeout:
            break
        if not photo or abs(photo[0]-selected['at']) > .75:
            continue
        at,jpeg = photo
        nearby = list(journal.frames(at-.75,at+.75))
        observation = min(nearby,key=lambda f:abs(f['at']-at)) if nearby else None
        people = [p['box'] for p in observation['detections'] if p['kind'] == 'person' and
                  any(putdown._reach(b,p['box']) < (.08 if selected.get('use_visit') else .1) for b in boxes)] if observation and (selected.get('use_visit') or window.get('arrival_recorded',True)) else []
        decoded[round(at,3)] = {**selected, 'at':at,'jpeg':jpeg,'person_boxes':people}
    frames = sorted(decoded.values(),key=lambda f:f['at'])
    if not frames or any(not any(abs(f['at']-endpoint['at']) <= .75 for f in frames)
                         for endpoint in (endpoints[0],endpoints[-1])):
        return []
    from .evidence_image import distinct_people
    arrival_frames = [f for f in frames if not f.get('use_visit')]
    selected = [arrival_frames[0],arrival_frames[-1],*distinct_people(arrival_frames)]
    from .evidence_image import person_changed, person_quality
    reference = arrival_frames[-1]
    nearby = list(journal.frames(reference['at']-.75, reference['at']+.75))
    observation = min(nearby, key=lambda f: abs(f['at']-reference['at'])) if nearby else None
    reference_people = [d['box'] for d in observation['detections'] if d['kind'] == 'person'] if observation else []
    visits = {}
    for frame in frames:
        if frame.get('use_visit') and frame['person_boxes']:
            people = [p for p in frame['person_boxes'] if any(putdown.box_iou(p, q) >= .15 for q in reference_people)
                      or person_changed(frame['jpeg'], reference['jpeg'], p)]
            if people:
                visits.setdefault(frame['use_visit']['start'], []).append(frame | {'person_boxes': people})
    for visit in visits.values():
        selected.append(max(visit, key=lambda f: (*use_frame_rank(f, boxes)[:4], person_quality(f))))
    return sorted({round(f['at'],3):f for f in selected}.values(),key=lambda f:f['at'])


def identity_frame(journal, frames: list[dict], boxes: list[list[float]], *, arrival_recorded: bool) -> dict | None:
    """One depicted, continuously tracked visitor, for later HUMAN identity teaching.

    Inspect every journal observation, not just the six displayed frames. Missing
    tracks, handoffs, gaps or an already-present dish supply no teaching capture.
    This does not attribute responsibility or relax any automatic proof check.
    """
    if not arrival_recorded or not frames or any(f.get('use_visit') for f in frames):
        return None
    observations = list(journal.frames(frames[0]['at'] - .75, frames[-1]['at'] + .75))
    if (not observations or observations[0]['at'] > frames[0]['at'] + .75
            or observations[-1]['at'] < frames[-1]['at'] - .75):
        return None
    active, previous_at, ended = [], None, False
    for observation in observations:
        if previous_at is not None and observation['at'] - previous_at > 2.5:
            return None
        previous_at = observation['at']
        people = [d for d in observation['detections'] if d['kind'] == 'person'
                  and any(putdown._reach(b, d['box']) <= .1 for b in boxes)]
        if len(people) > 1:
            return None
        if not people:
            ended |= bool(active)
            continue
        if ended or people[0].get('track_id') is None:
            return None
        if active and (active[-1][1]['track_id'] != people[0]['track_id']
                       or putdown.box_iou(active[-1][1]['box'], people[0]['box']) < .4):
            return None
        active.append((observation['at'], people[0]))
    if len(active) < 3 or active[-1][0] - active[0][0] < 1:
        return None
    choices = []
    for frame in frames:
        if len(frame['person_boxes']) != 1:
            continue
        at, person = min(active, key=lambda p: abs(p[0] - frame['at']))
        if abs(at - frame['at']) <= .75 and putdown.box_iou(person['box'], frame['person_boxes'][0]) >= .4:
            choices.append(frame | {'person_box': frame['person_boxes'][0]})
    return max(choices, key=lambda f: f['person_box'][2] * f['person_box'][3], default=None)


class Filmstrips:
    def __init__(self, store, media, journal, *, identify=None):
        self.store, self.media, self.journal = store, media, journal
        self.identify = identify
        with store.lock, store.conn:
            store.conn.execute("""CREATE TABLE IF NOT EXISTS filmstrip_jobs(
                obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id), status TEXT NOT NULL,
                attempted_at TEXT NOT NULL, reason TEXT, evidence_id TEXT REFERENCES evidence(id))""")

    def generate(self, obligation_id: int, *, now: datetime | None = None) -> dict:
        # Cross-process claim shared by the service and the recovery CLI. A crash
        # releases it automatically; never wait behind another footage search.
        with (self.store.root / '.filmstrip.lock').open('a') as claim:
            try:
                fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'obligation_id': obligation_id, 'status': 'busy'}
            return self._generate(obligation_id, now=now or utcnow())

    def _generate(self, obligation_id: int, *, now: datetime) -> dict:
        with self.store.lock:
            existing = self.store.conn.execute('SELECT * FROM filmstrip_jobs WHERE obligation_id=?', (obligation_id,)).fetchone()
            if existing and existing['status'] != 'retry' and existing['reason'] != TIMEOUT:
                return dict(existing)
            if existing and parse_stamp(existing['attempted_at']) + RETRY_DELAY > now:
                return dict(existing)
            prior = self.store.conn.execute('SELECT v.* FROM arrival_visits v JOIN arrival_visit_members m ON m.visit_id=v.id WHERE m.obligation_id=?', (obligation_id,)).fetchone()
            old = json.loads(prior['metadata']) if prior else None
            item = self.store.obligation(old['canonical_obligation_ids'][0] if old else obligation_id)
        deadline = time.monotonic() + 30
        found = plan(self.journal, self.media.buffer, item, deadline=deadline-10)
        evidence_id = None
        reason = found if isinstance(found, str) else None
        members = [self.store.obligation(oid) for oid in old['obligation_ids']] if old else [item]
        member_plans = [found] if not reason else []
        if not reason and not old:
            with self.store.lock:
                candidates = self.store.conn.execute("""SELECT id FROM obligations WHERE id!=? AND source IN ('vision','baseline')
                    AND lifecycle NOT IN ('dismissed','disputed') AND archived_at IS NULL AND created_at>=? AND created_at<=?
                    AND NOT EXISTS (SELECT 1 FROM filmstrip_jobs j WHERE j.obligation_id=obligations.id)
                    ORDER BY created_at,id""", (obligation_id,
                    stamp(parse_stamp(item['created_at'])-KEEP), stamp(max(parse_stamp(item['last_seen_at'] or item['created_at']),parse_stamp(item['created_at'])+timedelta(minutes=4))))).fetchall()
            others = [self.store.obligation(r['id']) for r in candidates]
            clusters = duplicate_clusters([item,*others])
            duplicates = next(group for group in clusters if any(m['id']==obligation_id for m in group))
            members = duplicates
            canonical = duplicates[0]
            if canonical['id'] != item['id']:
                found = plan(self.journal,self.media.buffer,canonical,deadline=deadline-10)
                if isinstance(found,str):
                    reason = found
                else:
                    item = canonical
                    member_plans = [found]
            # Other physical dishes can share the same naming unit only through a
            # continuous, unambiguous person visit. Duplicate aliases need no new name.
            for other in others:
                if reason or other in members or time.monotonic()>=deadline-15:
                    continue
                if abs((parse_stamp(other['created_at'])-parse_stamp(item['created_at'])).total_seconds())>240:
                    continue
                if not other['box']:
                    continue
                a,b=item['box'],other['box']
                if abs(a[0]-b[0])+abs(a[1]-b[1])>.4:
                    continue
                other_plan = plan(self.journal,self.media.buffer,other,deadline=deadline-10)
                if isinstance(other_plan,dict) and found['window'].get('arrival_recorded',True) and other_plan['window'].get('arrival_recorded',True) and all(same_visit(self.journal,prior,other_plan) for prior in member_plans):
                    cluster = next(g for g in clusters if any(m['id']==other['id'] for m in g))
                    members.extend(m for m in cluster if m not in members)
                    member_plans.append(other_plan)
            if len(member_plans)>1:
                combined = {'arrival_recorded':True,'appeared':min(p['window'].get('appeared',p['window']['after']) for p in member_plans),
                    'before':min(p['window'].get('context_start',p['window']['before']) for p in member_plans),
                    'after':max(p['window']['after'] for p in member_plans)}
                x=min(m['box'][0] for m in members)
                y=min(m['box'][1] for m in members)
                union=[x,y,max(m['box'][0]+m['box'][2] for m in members)-x,max(m['box'][1]+m['box'][3] for m in members)-y]
                found=found|{'window':combined,'frames':select_frames(self.journal.frames(combined['before'],combined['after']),union,combined)}
        physical = duplicate_clusters(members)
        if old:
            physical = [[next(m for m in members if m['id'] == oid)] for oid in old['canonical_obligation_ids']]
        canonical_ids = [g[0]['id'] for g in physical]
        obligation_ids = sorted(m['id'] for m in members)
        visit_id = prior['id'] if prior else hashlib.sha256((','.join(map(str,obligation_ids))).encode()).hexdigest()[:20]
        created = parse_stamp(prior['created_at']) if prior else now
        if not reason:
            frames = display_frames(self.journal,self.media.buffer,found,[g[0]['box'] for g in physical],deadline=deadline-2)
            if not frames:
                reason = TIMEOUT if time.monotonic() >= deadline else DECODE_RETRY
            if not reason:
                evidence_id = f"arrival-{visit_id}"
                filename = evidence_id + '.jpg'
                picture = render(item, frames, boxes=[g[0]['box'] for g in physical], approximate=found['window'].get('approximate', False), already_at=found['window'].get('first_visible_at') if not found['window'].get('arrival_recorded',True) else None)
                atomic_write(self.media.safe_path(filename), picture)
                try:
                    import io

                    from PIL import Image

                    from .evidence_image import VERSION
                    with Image.open(io.BytesIO(picture)) as image:
                        width, height = image.size
                    metadata = {**(old or {}), 'visit_id': visit_id, 'obligation_ids': obligation_ids, 'render_version': VERSION, 'layout': 'stacked',
                                'image_width': width, 'image_height': height,
                                'timestamp_estimated': True, 'time_basis': 'segment_mtime_and_decoded_pts',
                                'canonical_obligation_ids': canonical_ids, 'duplicate_of': old['duplicate_of'] if old else {str(m['id']):g[0]['id'] for g in physical for m in g[1:]},
                                'window': found['window'], 'dish_boxes': {str(m['id']): m['box'] for m in members},
                                'frames': [{k: v for k, v in f.items() if k != 'jpeg'} for f in frames]}
                    anchor = identity_frame(self.journal, frames, [m['box'] for m in members],
                                            arrival_recorded=found['window'].get('arrival_recorded') is True)
                    if anchor:
                        try:
                            captured = self.identify(item['id'], anchor, metadata) if self.identify else None
                        except Exception:
                            # Identity model failure must not discard usable human context.
                            captured = None
                        if captured:
                            metadata['identity_evidence_id'] = captured
                        else:
                            # CLI recovery does not load a second set of models.
                            # Let the running worker extract cues from this exact
                            # validated depicted frame, never a later visitor.
                            metadata['identity_pending_id'] = self.media.save_snapshot(item['id'], anchor['jpeg'], 'Arrival identity pending',
                                at=datetime.fromtimestamp(anchor['at'], timezone.utc),
                                capture={'identity_source': 'arrival_visit_pending', 'visit_id': visit_id, 'person_box': anchor['person_box']})
                            with self.store.lock, self.store.conn:
                                self.store.conn.execute('UPDATE evidence SET expires_at=? WHERE id=?',
                                    (stamp(created+KEEP), metadata['identity_pending_id']))
                    with self.store.lock, self.store.conn:
                        self.store.conn.execute("""INSERT INTO evidence(id,obligation_id,filename,kind,created_at,expires_at,label,capture)
                            VALUES(?,?,?,'filmstrip',?,?,'arrival',?)""", (evidence_id,obligation_id,filename,stamp(created),stamp(created+KEEP),json.dumps(metadata)))
                        self.store.conn.execute('INSERT INTO arrival_visits VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET evidence_id=excluded.evidence_id,metadata=excluded.metadata', (visit_id,evidence_id,stamp(created),json.dumps(metadata)))
                        self.store.conn.executemany('INSERT OR IGNORE INTO arrival_visit_members VALUES(?,?)', [(oid,visit_id) for oid in obligation_ids])
                        self.store.conn.executemany('INSERT INTO filmstrip_jobs VALUES(?,?,?,?,?) ON CONFLICT(obligation_id) DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,reason=excluded.reason,evidence_id=excluded.evidence_id',
                            [(oid, 'saved', stamp(now), None, evidence_id) for oid in obligation_ids])
                except Exception:
                    self.media.safe_path(filename).unlink(missing_ok=True)
                    raise
        if reason in (TIMEOUT, DECODE_RETRY):
            with self.store.lock, self.store.conn:
                self.store.conn.executemany('INSERT INTO filmstrip_jobs VALUES(?,?,?,?,?) ON CONFLICT(obligation_id) DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,reason=excluded.reason,evidence_id=excluded.evidence_id',
                    [(oid, 'retry', stamp(now), reason, None) for oid in obligation_ids])
            return {'obligation_id': obligation_id, 'status': 'retry', 'reason': reason, 'evidence_id': None}
        with self.store.lock, self.store.conn:
            if reason:
                metadata = {'visit_id':visit_id,'obligation_ids':obligation_ids,'canonical_obligation_ids':canonical_ids,
                            'duplicate_of':{str(m['id']):g[0]['id'] for g in physical for m in g[1:]},
                            'frames':[],'window':{'arrival_recorded':False,'reason':reason}}
                self.store.conn.execute('INSERT INTO arrival_visits VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata',(visit_id,None,stamp(created),json.dumps(metadata)))
                self.store.conn.executemany('INSERT OR IGNORE INTO arrival_visit_members VALUES(?,?)',[(oid,visit_id) for oid in obligation_ids])
                self.store.conn.executemany('INSERT INTO filmstrip_jobs VALUES(?,?,?,?,?) ON CONFLICT(obligation_id) DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,reason=excluded.reason,evidence_id=excluded.evidence_id',
                    [(oid, 'unavailable', stamp(now), reason, None) for oid in obligation_ids])
                self.store._event(obligation_id, 'arrival_filmstrip_unavailable', reason, at=now)
        return {'obligation_id': obligation_id, 'status': 'unavailable' if reason else 'saved', 'reason': reason, 'evidence_id': evidence_id}

    def complete_identity(self, *, now: datetime) -> int | None:
        if not self.identify:
            return None
        with self.store.lock:
            row = self.store.conn.execute("""SELECT v.* FROM arrival_visits v JOIN evidence e ON e.id=v.evidence_id
                WHERE e.expired=0 AND e.expires_at>? AND json_extract(v.metadata,'$.identity_pending_id') IS NOT NULL
                AND COALESCE(json_extract(v.metadata,'$.identity_attempted_at'),'')<=? ORDER BY v.created_at LIMIT 1""",
                (stamp(now), stamp(now-RETRY_DELAY))).fetchone()
            if not row:
                return None
            meta = json.loads(row['metadata'])
            source = self.store.evidence(meta['identity_pending_id'])
        if not source or source['expired']:
            return None
        try:
            jpeg = self.media.safe_path(source['filename']).read_bytes()
        except OSError:
            return None
        frame = {'jpeg': jpeg,
                 'at': parse_stamp(source['created_at']).timestamp(),
                 'person_box': json.loads(source['capture'])['person_box']}
        oid = meta['canonical_obligation_ids'][0]
        try:
            captured = self.identify(oid, frame, meta)
        except Exception:
            captured = None
        meta['identity_attempted_at'] = stamp(now)
        if captured:
            meta['identity_evidence_id'] = captured
            meta.pop('identity_pending_id')
        with self.store.lock, self.store.conn:
            current = self.store.conn.execute('SELECT metadata FROM arrival_visits WHERE id=?', (row['id'],)).fetchone()
            if not current or current['metadata'] != row['metadata']:
                return oid
            self.store.conn.execute('UPDATE arrival_visits SET metadata=? WHERE id=?', (json.dumps(meta),row['id']))
            self.store.conn.execute('UPDATE evidence SET capture=? WHERE id=?', (json.dumps(meta),row['evidence_id']))
        return oid

    def run_once(self, *, now: datetime | None = None) -> int | None:
        now = now or utcnow()
        if (completed := self.complete_identity(now=now)) is not None:
            return completed
        with self.store.lock:
            rows = self.store.conn.execute("""SELECT o.id FROM obligations o WHERE o.source IN ('vision','baseline')
                AND o.lifecycle NOT IN ('dismissed','disputed') AND o.created_at>=? AND o.due_at<=?
                AND NOT EXISTS (SELECT 1 FROM filmstrip_jobs f WHERE f.obligation_id=o.id AND NOT ((f.status='retry' OR f.reason IS ?) AND f.attempted_at<=?))
                AND (o.left_out_at IS NOT NULL OR EXISTS (SELECT 1 FROM evidence e WHERE e.obligation_id=o.id
                    AND e.kind='snapshot' AND e.created_at>=o.due_at AND e.label LIKE 'Matching dish still visible after the deadline%'))
                ORDER BY o.due_at ASC LIMIT 16""", (stamp(now - KEEP), stamp(now), TIMEOUT, stamp(now-RETRY_DELAY))).fetchall()
        for row in rows:
            item = self.store.obligation(row['id'])
            with self.store.lock:
                observed = self.store.observed_left_out_at(item)
            if observed:
                self.generate(row['id'], now=now)
            else:
                with self.store.lock, self.store.conn:
                    self.store.conn.execute('INSERT INTO filmstrip_jobs VALUES(?,?,?,?,?) ON CONFLICT(obligation_id) DO UPDATE SET status=excluded.status,attempted_at=excluded.attempted_at,reason=excluded.reason,evidence_id=excluded.evidence_id',
                        (row['id'], 'unavailable', stamp(now), 'Deadline continuity is unverified; no left-out arrival context generated.', None))
            return row['id']
        return None
