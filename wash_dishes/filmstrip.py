"""Arrival context for human naming, independent of the strict four-photo proof.

Bodies hiding the spot are useful context here. Neither a person box nor this strip
confirms ownership. Searches stream the journal; decoding is six small windows in
single-threaded, niced FFmpeg, on the existing background proof worker.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from . import collage, putdown
from .media import atomic_write
from .store import parse_stamp, stamp, utcnow

KEEP = timedelta(days=7)
MAX_WINDOW = 20 * 60


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
        for fraction in (0,.2,.55,.85):
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
    latest = putdown.samples(segments[-1], box, first_only=True)
    if not latest:
        return 'The recording at the first sighting could not be decoded.'
    reference = latest[0]
    previous = reference.at
    # At most 48 small decodes, spread over the retained 50-hour buffer.
    stride = max(1, len(segments) // 48)
    for segment in reversed(segments[::stride]):
        if time.monotonic() >= deadline:
            return 'Arrival context search reached its bounded time budget.'
        samples = putdown.samples(segment, box, first_only=True)
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
        return _plan(BoundedJournal(), buffer, item, deadline=deadline)
    except putdown._SearchTimeout:
        return 'Arrival context search reached its bounded time budget.'


def footage_segments(buffer: Path):
    """Reuse recorder indexing; recover the first completed file after a copy/gap.

    The strict finder needs a predecessor mtime. Isolated audit windows may lack
    that file. The fixed recorder segment length supplies its approximate start;
    it never supplies an empty-before observation across the gap.
    """
    found=putdown.segments(buffer)
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
    return sorted(found)


def _photo_at(segments, at: float) -> tuple[float, bytes] | None:
    segment = next((s for s in segments if s[0] <= at < s[1]), None)
    pictures = putdown.decoded_photos(segment, fps=2, t0=max(segment[0],at-.5), t1=min(segment[1],at+.75)) if segment else []
    if not pictures and segment:
        pictures=putdown.photos(segment)
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


def _plan(journal, buffer: Path, item: dict, *, deadline: float) -> dict | str:
    anchor = parse_stamp(item['created_at']).timestamp()
    box = item['box']
    if not box:
        return 'No saved dish box locates the arrival spot.'
    bounds = journal.bounds()
    segments = footage_segments(buffer)
    reference = _photo_at(segments,anchor)
    if reference is None:
        return 'No retained footage covers the first recorded sighting.'
    reference_patch = putdown._jpeg_patch(reference[1],box)
    end = min(anchor+30,bounds[1]) if bounds else anchor
    candidates = []
    if bounds:
        start = max(bounds[0],anchor-50*3600)
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
        if isinstance(window,dict):
            candidates.append(window)
    for window in candidates[:12]:
        if time.monotonic()>=deadline:
            break
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
            return {'window':window,'frames':selected,'box':box}
    # Evidence of an already-present dish is useful, but a nearby visitor is not its
    # placer. Do not select that visitor or label a detector return as a put-down.
    # The prominent image caption and metadata retain this missing-arrival fact.
    available = [s for s in segments if s[1]>=anchor and s[0]<=anchor+40]
    if not available:
        return 'No retained footage covers the first recorded sighting.'
    first = max(anchor,available[0][0])
    last = min(anchor+30,available[-1][1]-.5)
    if last-first<3:
        return 'Insufficient retained footage to show distinct already-present views.'
    window = {'before':first,'after':last,'source':'already_present','arrival_recorded':False,
              'first_visible_at':reference[0],'reason':'Already there at the first retained view; arrival not recorded.'}
    return {'window':window,'box':box,'frames':[{'at':first+i*(last-first)/5,'person_boxes':[]} for i in range(6)]}


def same_visit(journal, left: dict, right: dict) -> bool:
    """Nearby spots, nearby times, one continuously present person; empty/gap splits.

    A shared track is stronger than proximity. With no track IDs, one unambiguous
    overlapping person box must continue through every journal observation.
    """
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


def _region(boxes, frames, picture):
    people = [b for f in frames for b in f['person_boxes']]
    # Include the union of everyone's head/torso, not just a single guessed handler.
    heads = [[x, y, w, min(h, max(w, h * .6))] for x, y, w, h in people]
    if not heads:
        # Missing person detections must not crop a real head out of an arrival.
        # This is framing geometry only, never a synthesized person box in metadata.
        heads = [[max(0,b[0]-.18),max(0,b[1]-.55),min(.55,1-max(0,b[0]-.18)),min(.7,1-max(0,b[1]-.55))] for b in boxes]
    return collage._crop([*boxes, *heads], None, collage._aspect(picture, 500, 375))


def render(item: dict, frames: list[dict], *, boxes: list[list[float]] | None = None, approximate: bool = False, already_at: float | None = None) -> bytes:
    width, height, margin, gap = 1200, 1668, 24, 24
    panel_w, panel_h = 564, 423
    canvas = Image.new('RGB', (width, height), collage.PAPER)
    draw = ImageDraw.Draw(canvas)
    boxes = boxes or [item['box']]
    title = f"{len(boxes)} dishes · arrival context" if len(boxes) > 1 else f"{collage.dish_name(item['dishes']).capitalize()} · arrival context"
    local = ZoneInfo(os.getenv('WASHDISHES_TIMEZONE','America/Los_Angeles'))
    date = datetime.fromtimestamp(frames[0]['at'],timezone.utc).astimezone(local).strftime('%b %d, %Y')
    subtitle = f"{date} · {collage.place(item['location'])}"
    note = 'People shown need human review'
    if approximate:
        note = 'Timing uncertain; put-down not established'
    if already_at is not None:
        clock = datetime.fromtimestamp(already_at,timezone.utc).astimezone(local).strftime('%I:%M %p').lstrip('0')
        note = f"Already there at {clock} — arrival not recorded"
    draw.text((margin, 22), title, font=collage._type(52, 650), fill=collage.INK)
    draw.text((margin, 80), subtitle, font=collage._type(28), fill=collage.MUTED)
    draw.text((margin, 126), note, font=collage._type(28), fill=collage.MUTED)
    region = _region(boxes, frames, frames[0]['jpeg'])
    for i, frame in enumerate(frames):
        x, y = margin + i % 2 * (panel_w + gap), 186 + i // 2 * (panel_h + 68)
        # Paste the scene once, then draw each member spot on that same crop.
        collage._panel(canvas, draw, frame['jpeg'], region, boxes[0], (x, y), (panel_w, panel_h), 'corners')
        for box in boxes[1:]:
            sx, sy = panel_w / (region[2]-region[0]), panel_h / (region[3]-region[1])
            bx, by, bw, bh = box
            cx, cy = x+(bx+bw/2-region[0])*sx, y+(by+bh/2-region[1])*sy
            hw, hh = max(32,bw*sx/2+10), max(32,bh*sy/2+10)
            rect = (max(x+6,cx-hw),max(y+6,cy-hh),min(x+panel_w-6,cx+hw),min(y+panel_h-6,cy+hh))
            collage._mark(draw, rect, 'corners')
        at = datetime.fromtimestamp(frame['at'], timezone.utc)
        label = at.astimezone(ZoneInfo(os.getenv('WASHDISHES_TIMEZONE', 'America/Los_Angeles'))).strftime('%I:%M:%S %p').lstrip('0')
        label,font=collage._fit(draw,label,range(46,33,-2),450,panel_w)
        draw.text((x, y + panel_h + 52), label, font=font, fill=collage.MUTED, anchor='ls')
    out = io.BytesIO()
    for quality in (84, 74, 64, 54, 44, 34):
        out.seek(0)
        out.truncate()
        canvas.save(out, 'JPEG', quality=quality, optimize=True)
        if out.tell() < 600_000:
            return out.getvalue()
    raise ValueError('Arrival filmstrip exceeds the photo budget.')


class Filmstrips:
    def __init__(self, store, media, journal):
        self.store, self.media, self.journal = store, media, journal
        self.plans = {}
        with store.lock, store.conn:
            store.conn.execute("""CREATE TABLE IF NOT EXISTS filmstrip_jobs(
                obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id), status TEXT NOT NULL,
                attempted_at TEXT NOT NULL, reason TEXT, evidence_id TEXT REFERENCES evidence(id))""")

    def generate(self, obligation_id: int, *, now: datetime | None = None) -> dict:
        now = now or utcnow()
        with self.store.lock:
            existing = self.store.conn.execute('SELECT * FROM filmstrip_jobs WHERE obligation_id=?', (obligation_id,)).fetchone()
            if existing:
                return dict(existing)
            item = self.store.obligation(obligation_id)
        deadline = time.monotonic() + 30
        found = self.plans.get(obligation_id)
        if found is None:
            found = plan(self.journal, self.media.buffer, item, deadline=deadline-10)
            self.plans[obligation_id] = found
        if len(self.plans) > 64:
            self.plans.pop(next(iter(self.plans)))
        evidence_id = None
        reason = found if isinstance(found, str) else None
        members = [item]
        member_plans = [found] if not reason else []
        if not reason:
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
                if reason or other in members or time.monotonic()>=deadline:
                    continue
                if abs((parse_stamp(other['created_at'])-parse_stamp(item['created_at'])).total_seconds())>240:
                    continue
                if not other['box']:
                    continue
                a,b=item['box'],other['box']
                if abs(a[0]-b[0])+abs(a[1]-b[1])>.4:
                    continue
                other_plan = self.plans.get(other['id'])
                if other_plan is None:
                    other_plan = plan(self.journal,self.media.buffer,other,deadline=deadline-10)
                    self.plans[other['id']] = other_plan
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
        canonical_ids = [g[0]['id'] for g in physical]
        obligation_ids = sorted(m['id'] for m in members)
        visit_id = hashlib.sha256((','.join(map(str,obligation_ids))).encode()).hexdigest()[:20]
        if not reason:
            segments = footage_segments(self.media.buffer)
            frames = []
            for selected in found['frames']:
                if time.monotonic() >= deadline:
                    reason = 'Arrival context search reached its bounded time budget.'
                    break
                photo = _photo_at(segments,selected['at'])
                if not photo:
                    reason = 'Retained footage does not cover all arrival frames, or a source segment could not be decoded.'
                    break
                at,jpeg = photo
                # Actual decoder PTS is the caption and metadata time. Boxes are the nearest journal observation.
                nearest = list(self.journal.frames(at - .75, at + .75))
                observation = min(nearest, key=lambda f: abs(f['at'] - at)) if nearest else None
                people = [p for p in observation['detections'] if p['kind'] == 'person' and any(putdown._reach(m['box'], p['box']) <= .1 for m in members)] if observation else []
                frames.append({'at': at, 'jpeg': jpeg, 'person_boxes': [p['box'] for p in people]})
            if not reason and (len(frames) != 6 or len({round(f['at'],3) for f in frames}) != 6):
                reason = 'The recording cannot supply six distinct arrival context frames.'
            if not reason:
                evidence_id = f"arrival-{visit_id}"
                filename = evidence_id + '.jpg'
                atomic_write(self.media.safe_path(filename), render(item, frames, boxes=[g[0]['box'] for g in physical], approximate=found['window'].get('approximate', False), already_at=found['window'].get('first_visible_at') if not found['window'].get('arrival_recorded',True) else None))
                try:
                    metadata = {'visit_id': visit_id, 'obligation_ids': obligation_ids,
                                'timestamp_estimated': True, 'time_basis': 'segment_mtime_and_decoded_pts',
                                'canonical_obligation_ids': canonical_ids, 'duplicate_of': {str(m['id']):g[0]['id'] for g in physical for m in g[1:]},
                                'window': found['window'], 'dish_boxes': {str(m['id']): m['box'] for m in members},
                                'frames': [{k: v for k, v in f.items() if k != 'jpeg'} for f in frames]}
                    with self.store.lock, self.store.conn:
                        self.store.conn.execute("""INSERT INTO evidence(id,obligation_id,filename,kind,created_at,expires_at,label,capture)
                            VALUES(?,?,?,'filmstrip',?,?,'arrival',?)""", (evidence_id,obligation_id,filename,stamp(now),stamp(now+KEEP),json.dumps(metadata)))
                        self.store.conn.execute('INSERT INTO arrival_visits VALUES(?,?,?,?)', (visit_id,evidence_id,stamp(now),json.dumps(metadata)))
                        self.store.conn.executemany('INSERT INTO arrival_visit_members VALUES(?,?)', [(oid,visit_id) for oid in obligation_ids])
                        self.store.conn.executemany('INSERT INTO filmstrip_jobs VALUES(?,?,?,?,?)',
                            [(oid, 'saved', stamp(now), None, evidence_id) for oid in obligation_ids])
                except Exception:
                    self.media.safe_path(filename).unlink(missing_ok=True)
                    raise
        with self.store.lock, self.store.conn:
            if reason:
                metadata = {'visit_id':visit_id,'obligation_ids':obligation_ids,'canonical_obligation_ids':canonical_ids,
                            'duplicate_of':{str(m['id']):g[0]['id'] for g in physical for m in g[1:]},
                            'frames':[],'window':{'arrival_recorded':False,'reason':reason}}
                self.store.conn.execute('INSERT INTO arrival_visits VALUES(?,?,?,?)',(visit_id,None,stamp(now),json.dumps(metadata)))
                self.store.conn.executemany('INSERT INTO arrival_visit_members VALUES(?,?)',[(oid,visit_id) for oid in obligation_ids])
                self.store.conn.executemany('INSERT INTO filmstrip_jobs VALUES(?,?,?,?,?)',
                    [(oid, 'unavailable', stamp(now), reason, None) for oid in obligation_ids])
                self.store._event(obligation_id, 'arrival_filmstrip_unavailable', reason, at=now)
        return {'obligation_id': obligation_id, 'status': 'unavailable' if reason else 'saved', 'reason': reason, 'evidence_id': evidence_id}

    def run_once(self, *, now: datetime | None = None) -> int | None:
        now = now or utcnow()
        with self.store.lock:
            rows = self.store.conn.execute("""SELECT o.id FROM obligations o WHERE o.source IN ('vision','baseline')
                AND o.lifecycle NOT IN ('dismissed','disputed') AND o.created_at>=? AND o.due_at<=?
                AND NOT EXISTS (SELECT 1 FROM filmstrip_jobs f WHERE f.obligation_id=o.id)
                AND (o.left_out_at IS NOT NULL OR EXISTS (SELECT 1 FROM evidence e WHERE e.obligation_id=o.id
                    AND e.kind='snapshot' AND e.created_at>=o.due_at AND e.label LIKE 'Matching dish still visible after the deadline%'))
                ORDER BY o.due_at ASC LIMIT 16""", (stamp(now - KEEP), stamp(now))).fetchall()
        for row in rows:
            item = self.store.obligation(row['id'])
            with self.store.lock:
                observed = self.store.observed_left_out_at(item)
            if observed:
                self.generate(row['id'], now=now)
            else:
                with self.store.lock, self.store.conn:
                    self.store.conn.execute('INSERT INTO filmstrip_jobs VALUES(?,?,?,?,?)',
                        (row['id'], 'unavailable', stamp(now), 'Deadline continuity is unverified; no left-out arrival context generated.', None))
            return row['id']
        return None
