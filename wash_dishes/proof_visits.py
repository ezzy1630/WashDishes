"""Pure person-presence evidence for a proof's before/after change window.

This is deliberately independent of inventory/filmstrip grouping. A visit is a
continuous run of exactly one person within reach, bracketed by clear views.
Bounding-box continuity is an association, never an identity or evidence of use.
"""
from __future__ import annotations

from collections.abc import Iterable

from .vision import box_iou

MAX_SAMPLE_GAP = 2.5
REACH = .10


def reach(box: list[float], person: list[float]) -> float:
    x, y, w, h = box
    px, py, pw, ph = person
    return max(px - x - w / 2, 0, x + w / 2 - px - pw) + max(py - y - h / 2, 0, y + h / 2 - py - ph)


def nearby(frame: dict, box: list[float]) -> list[dict]:
    return [p for p in frame['detections'] if p['kind'] == 'person'
            and p.get('score', 1) >= .2 and reach(box, p['box']) <= REACH]


def single_visitor(frames: Iterable[dict], box: list[float]) -> dict | str:
    """Validate EVERY observation between the supplied clear endpoints.

    Refuse missing samples, handoffs, overlapping people and disjoint visits.
    Old journals lack track IDs: those require adjacent box IoU >= .4. If
    either observation has a track ID, both must have the same one. No missed
    person is interpolated. Return the observed person boxes for exact-frame
    selection/checking; a caller still has to prove the spot changed.
    """
    frames = list(frames)
    if len(frames) < 5 or nearby(frames[0], box) or nearby(frames[-1], box):
        return 'the visit needs clear views before and after the person'
    observations, prior, ended = [], None, False
    for index, frame in enumerate(frames):
        if index and not 0 < frame['at'] - frames[index - 1]['at'] <= MAX_SAMPLE_GAP:
            return 'the person cannot be tracked across a journal gap'
        people = nearby(frame, box)
        if len(people) > 1:
            return 'more than one person was within reach during the change'
        if not people:
            ended |= bool(observations)
            continue
        if ended:
            return 'separate visits occurred between the clear views'
        person = people[0]
        if prior:
            old, new = prior.get('track_id'), person.get('track_id')
            if (old is not None or new is not None) and (old is None or old != new):
                return 'the person track changed during the visit'
            if box_iou(prior['box'], person['box']) < .4:
                return 'the person could not be followed continuously during the visit'
        observations.append({'at': frame['at'], 'person_box': person['box']})
        prior = person
    if len(observations) < 3 or observations[-1]['at'] - observations[0]['at'] < 1:
        return 'too few observations to establish a visit'
    return {'start': observations[0]['at'], 'end': observations[-1]['at'], 'observations': observations}


def arrival_visits(frames: Iterable[dict], box: list[float]) -> Iterable[tuple[float, float, float]]:
    """Yield clear-before/start/clear-after times for visits, without using dish appearance.

    This is a discovery primitive, not an attribution verdict. The caller must
    validate every person observation and the actual pictures. A journal gap
    invalidates the open visit; no missing observations are interpolated.
    """
    before = start = previous = None
    for frame in frames:
        at = frame['at']
        if previous is not None and not 0 < at - previous <= MAX_SAMPLE_GAP:
            before = start = None
        previous = at
        if nearby(frame, box):
            if start is None and before is not None:
                start = at
        else:
            if start is not None:
                yield before, start, at
            before, start = at, None
