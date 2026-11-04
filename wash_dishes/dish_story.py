"""Fixed-camera spot stories for human review; never identity or scoring evidence.

No cross-spot identity search. Pixel comparisons nominate a local appearance change,
not cooking, ownership, emptiness of the whole surface, or continuous presence.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from . import collage, evidence_image, filmstrip, putdown
from .store import parse_stamp

VERSION = 1
BUDGET = 45
LOOKBACK = 6 * 3600
TIMEOUT = 'Story search reached its bounded time budget; retry pending.'


def overlap(a, b, pad=.012):
    x, y, w, h = a
    u, v, s, t = b
    return x-pad < u+s and u < x+w+pad and y-pad < v+t and v < y+h+pad


def people(frame, box, *, covering=False):
    return [d['box'] for d in frame['detections'] if d['kind'] == 'person'
            and d.get('score', 1) >= .2
            and (overlap(box, d['box']) if covering else putdown._reach(box, d['box']) <= .08)]


def visits(frames, box):
    found, current = [], []
    previous = None
    for frame in frames:
        if previous is not None and frame['at']-previous > 30:
            if current:
                found.append(current)
            current = []
        if people(frame, box):
            if current and frame['at']-current[-1]['at'] > 15:
                found.append(current)
                current = []
            current.append(frame)
        previous = frame['at']
    if current:
        found.append(current)
    return [v for v in found if v[-1]['at']-v[0]['at'] >= 4]


def patch(jpeg, box):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('Unreadable story photo.')
    h, w = image.shape[:2]
    x, y, bw, bh = box
    crop = image[int(y*h):int((y+bh)*h), int(x*w):int((x+bw)*w)]
    if not crop.size:
        raise ValueError('Dish box is outside the photo.')
    return cv2.GaussianBlur(cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA), (5, 5), 0)


def comparison(a, b):
    """Brightness-normalized Lab plus spatial correlation, tolerant of tiny shifts.

    Colour alone matches empty burners; correlation alone matches different white
    plates. Require both for the later view. An ambiguous result is neither a match
    nor an arrival. This is appearance at one spot, not physical-instance identity.
    """
    import cv2
    import numpy as np
    la, lb = [cv2.cvtColor(p.astype(np.float32)/255, cv2.COLOR_BGR2LAB) for p in (a, b)]
    la[..., 0] -= la[..., 0].mean()-lb[..., 0].mean()
    difference = float(np.abs(la-lb).mean()/20)
    ga, gb = [cv2.cvtColor(p, cv2.COLOR_BGR2GRAY).astype(np.float32) for p in (a, b)]
    corr = float(cv2.matchTemplate(ga, gb[2:-2, 2:-2], cv2.TM_CCOEFF_NORMED).max())
    # Flat crops cannot establish a dish shape, even if both are equally dark.
    texture = min(float(ga.std()), float(gb.std()))
    return {'difference': difference, 'correlation': corr, 'texture': texture,
            'same': difference <= .16 and corr >= .72 and texture >= 5,
            'different': difference >= .28 and corr < .65}


def partly_visible(before, reference):
    """An occluded dish is still present if distinctive parts remain in place.

    Whole-crop comparison can change when a pot blocks half a glass. Three
    textured matching tiles veto absence; this is not an identity score.
    """
    import cv2
    import numpy as np
    a,b = [cv2.cvtColor(p,cv2.COLOR_BGR2GRAY).astype(np.float32) for p in (before,reference)]
    matches = 0
    for y in (4,24,44):
        for x in (4,24,44):
            tile = b[y:y+16,x:x+16]
            if tile.std()<8:
                continue
            search = a[max(0,y-4):min(64,y+20),max(0,x-4):min(64,x+20)]
            score = float(cv2.matchTemplate(search,tile,cv2.TM_CCOEFF_NORMED).max())
            # NCC is insensitive to brightness; reject low-contrast background
            # texture matching a strongly outlined rim merely by spatial shape.
            if score>=.92 and search.std() >= tile.std()*.6:
                matches += 1
    return matches >= 3


def select(journal, buffer: Path, item: dict, *, now=None, deadline=None, decode=None, pose=None):
    """Return four decoded observations + comparisons, or a plain refusal reason."""
    deadline = deadline if deadline is not None else time.monotonic()+BUDGET
    try:
        return _select(journal, buffer, item, now=now, deadline=deadline, decode=decode, pose=pose)
    except putdown._SearchTimeout:
        return TIMEOUT


def _select(journal, buffer, item, *, now, deadline, decode, pose):
    item = item | {'due_at': item.get('followup_at') or item.get('due_at')}
    box = item.get('box')
    if not box or not item.get('due_at'):
        return 'No fixed dish box or deadline.'
    anchor, due = (parse_stamp(item[k]).timestamp() for k in ('created_at', 'due_at'))
    now = now.timestamp() if isinstance(now, datetime) else now or time.time()
    if now < due:
        return 'The dish is not past its deadline.'
    bounds = journal.bounds()
    if not bounds:
        return 'No retained journal.'
    end = min(now, bounds[1])
    if end < due:
        return 'No post-deadline journal observation.'
    frames = []
    for index, frame in enumerate(journal.frames(max(bounds[0], anchor-LOOKBACK), min(end, anchor+600))):
        if index % 256 == 0:
            putdown._check_deadline(deadline)
        frames.append(frame)
    # Clear JOURNAL instants locate candidates, including sparse journal periods.
    # Exact decoded photos are checked again; gaps never imply absence.
    clear = [f for f in frames if not people(f, box, covering=True)]
    reference_views = [f for f in clear if anchor <= f['at'] <= anchor+600]
    if not reference_views:
        return 'No clear dish reference near its first sighting.'
    segments = filmstrip.footage_segments(buffer) if decode is None else None
    if segments is not None:
        segments.deadline = deadline
    cache = {}
    def photo(frame):
        putdown._check_deadline(deadline)
        if frame['at'] not in cache:
            got = (frame['at'],frame['jpeg']) if 'jpeg' in frame else decode(frame['at']) if decode else filmstrip._photo_at(segments, frame['at'])
            if not got or abs(got[0]-frame['at']) > .75:
                cache[frame['at']] = None
            else:
                at, jpeg = got
                neighbors = list(journal.frames(at-3, at+3))
                nearest = min(neighbors, key=lambda f: abs(f['at']-at), default=None)
                if nearest is None and 'jpeg' in frame:
                    nearest = frame
                # Any observed covering person around this approximate decode time
                # vetoes a supposedly clear comparison, including people below reach.
                cache[frame['at']] = {'at': at, 'jpeg': jpeg,
                    'person_boxes': people(nearest, box) if nearest else [],
                    'covered': not nearest or abs(nearest['at']-at) > .75 or
                               any(people(f, box, covering=True) for f in neighbors),
                    'patch': patch(jpeg, box)}
        result = cache[frame['at']]
        if result and pose and 'pose' not in result:
            putdown._check_deadline(deadline)
            result['pose'] = pose(result['jpeg'], box, result['person_boxes'])
            result['covered'] |= any(overlap(box,p) for p in result['pose']['person_boxes'])
        if result and not result.get('pose',{}).get('available'):
            support = list(journal.frames(result['at']-3,result['at']+3))
            dense = (len(support)>=4 and support[0]['at']<=result['at']-2.5
                     and support[-1]['at']>=result['at']+2.5
                     and all(b['at']-a['at']<=3 for a,b in zip(support,support[1:])))
            # Without exact-frame person boxes, a sparse non-person reading is
            # insufficient to call a comparison clear. DURING remains context.
            result['covered'] |= not dense
        return result
    reference_frame = reference_views[0]
    if any(d['kind'] != 'person' and putdown._overlap(box,d['box']) >= .85
           and d['box'][2]*d['box'][3] >= 2.5*box[2]*box[3] for d in reference_frame['detections']):
        return 'The reference box is a fragment inside another dish; arrival is not isolated.'
    reference = photo(reference_frame)
    if not reference or reference['covered']:
        return 'The reference dish view is unavailable or covered.'
    # Verify AFTER first. Searching an arrival is wasted work if the spot is gone.
    later = [f for f in journal.frames(due, min(end, due+1800)) if not people(f, box, covering=True)]
    after = None
    for fraction in (0, .33, .66, 1):
        if not later:
            break
        candidate = photo(later[round(fraction*(len(later)-1))])
        if candidate and not candidate['covered']:
            score = comparison(reference['patch'], candidate['patch'])
            if score['same']:
                after = candidate | {'comparison': score}
                break
    if not after:
        return 'No clear post-deadline view verifies the reference dish at this spot.'
    for visit in reversed(visits(frames, box)[-12:]):
        putdown._check_deadline(deadline)
        start, finish = visit[0]['at'], visit[-1]['at']
        if start > reference['at'] or reference['at']-finish > LOOKBACK:
            continue
        earlier = [f for f in clear if start-300 <= f['at'] < start-3]
        following = [f for f in clear if finish+3 < f['at'] <= min(finish+300, reference['at']+3)]
        if not earlier or not following:
            continue
        settled = photo(following[0])
        if not settled or settled['covered'] or not comparison(reference['patch'], settled['patch'])['same']:
            continue
        before = photo(earlier[-1])
        if not before or before['covered']:
            continue
        change = comparison(reference['patch'], before['patch'])
        if not change['different'] or partly_visible(before['patch'],reference['patch']):
            continue
        # A rotated, shifted or partly covered existing dish is not an empty
        # BEFORE. This veto searches one box-width around the final spot; it
        # never supplies cross-spot identity or a positive arrival claim.
        if filmstrip._already_visible(before['jpeg'],reference['jpeg'],box):
            continue
        # Decode short 2-fps bursts across the visit, not just journal instants.
        # Four bursts bound work even during a long cooking visit.
        pool = during_candidates(segments, visit, journal, box, deadline=deadline) if decode is None else visit
        decoded = [p for f in pool if (p := photo(f)) and p['person_boxes']]
        for p in decoded:
            p['dish_visible'] = not p['covered'] and comparison(reference['patch'], p['patch'])['same']
        # Wrists and local changes rank views; neither is an assertion of use.
        for a,b in zip(decoded,decoded[1:]):
            b['spot_motion'] = comparison(a['patch'],b['patch'])['difference']
        decoded.sort(key=lambda p: (bool(p.get('pose',{}).get('wrists')), p['dish_visible'],
                                    p.get('spot_motion',0), len(p['person_boxes'])), reverse=True)
        if len(decoded) < 2:
            continue
        first = decoded[0]
        second = next((p for p in decoded[1:] if abs(p['at']-first['at']) >= 4), None)
        if not second:
            continue
        during = sorted([first, second], key=lambda p: p['at'])
        return {'version': VERSION, 'box': box, 'obligation_id': item['id'],
                'reference_at': reference['at'], 'visit': {'start': start, 'end': finish},
                'before_comparison': change, 'after_comparison': after['comparison'],
                'frames': [before, *during, after], 'time_basis': 'segment_mtime_and_decoded_pts',
                'limitations': 'Same spot only. Nearby people are context, not proof of use or ownership.'}
    return 'Arrival unknown: no clear verified spot change spanning a retained visit.'


def during_candidates(segments, visit, journal, box, *, deadline):
    """At most four short bursts, 2 fps. Cache bytes for selection's exact check."""
    start, end = visit[0]['at'], visit[-1]['at']
    times = sorted({min(end,max(start,start+q*(end-start)+offset))
                    for q in (.1,.35,.65,.9) for offset in (-1,-.5,0,.5,1)})
    segments.targets = times
    out = []
    for at in times:
        putdown._check_deadline(deadline)
        got = filmstrip._photo_at(segments,at)
        if not got:
            continue
        actual,jpeg = got
        nearby = list(journal.frames(actual-8,actual+8))
        nearest = min(nearby,key=lambda f:abs(f['at']-actual),default=None)
        if nearest and abs(nearest['at']-actual) <= 8 and people(nearest,box):
            out.append({'at':actual,'jpeg':jpeg,'detections':nearest['detections']})
    return sorted({round(f['at'],3):f for f in out}.values(),key=lambda f:f['at'])


def metadata(found):
    return found | {'frames': [{k: v for k, v in f.items() if k not in ('jpeg', 'patch')}
                              for f in found['frames']]}


def render(item, found):
    box = found['box']
    frames = found['frames']
    # One shared crop across BEFORE/AFTER makes the comparison easy to inspect.
    region = evidence_image.region([box], frames[0]['jpeg'])
    panels = []
    for index, frame in enumerate(frames):
        if index == 0:
            label = 'BEFORE · spot before this change'
        elif index == 3:
            label = 'AFTER · matching dish at this spot'
        else:
            count = len(frame['person_boxes'])
            label = f"DURING · {'Person' if count == 1 else str(count)+' people'} at the {collage.place(item.get('location')).lower()}"
            label += ' · dish visible' if frame['dish_visible'] else ' · dish not verified'
        heads = [[x,y,w,min(h,max(w,h*.6))] for x,y,w,h in frame['person_boxes']]
        bounds = evidence_image.region([box,*heads], frame['jpeg']) if index in (1,2) else region
        at = datetime.fromtimestamp(frame['at'], timezone.utc).astimezone(collage.timezone())
        panels.append({'jpeg': frame['jpeg'], 'label': label, 'time': at.strftime('%b %-d · %-I:%M:%S %p %Z'),
                       'boxes': [box], 'region': bounds, 'expected': index == 0})
    due = parse_stamp(item['due_at']).astimezone(collage.timezone())
    return evidence_image.render('Dish at this spot', collage.place(item.get('location')), panels,
        [f"Deadline · {due:%b %-d · %-I:%M %p %Z}",
         'People nearby are context. Use and ownership are not established.',
         'Photos show separate moments; they do not prove continuous presence.'])
