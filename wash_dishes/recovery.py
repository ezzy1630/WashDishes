"""Recover localized earlier candidates from original saved detector captures.

A matching box and overlapping person are candidates, not identity or responsibility.
Never backdate the clock from a detector miss or infer washing from disappearance.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta

from .inventory import overlap, pair
from .store import parse_stamp
from .vision import box_iou


def compatible(a: str, b: str) -> bool:
    return a == b or {a, b} <= {'pan', 'pot', 'bowl'}


def earlier_candidates(item: dict, labels: list[dict]) -> tuple[dict | None, dict | None]:
    end = parse_stamp(item['created_at'])
    start = end - timedelta(hours=6 if not item['timing_known'] else .5)
    frames = sorted((f for f in labels if start <= parse_stamp(f['created_at']) < end), key=lambda f: f['created_at'])
    candidates = []
    for frame in frames:
        boxes = frame.get('predicted', [])
        dishes = [d for d in boxes if d['kind'] != 'person' and compatible(d['kind'], item['dishes'][0]['kind'])
                  and d.get('score', 0) >= .35 and box_iou(d['box'], item['box']) >= .35]
        if len(dishes) != 1:
            continue
        dish = dishes[0]
        people = [p for p in boxes if p['kind'] == 'person' and p.get('score', 0) >= .5 and overlap(p['box'], dish['box']) >= .75]
        if len(people) == 1:
            candidates.append(dict(frame, recovered_dish=dish))
    if not candidates:
        return None, None
    using = candidates[-1]
    # Earlier context only: a missing detector box does not prove the dish absent.
    before = next((f for f in reversed(frames) if f['created_at'] < using['created_at']
                   and not any(p['kind'] == 'person' and overlap(p['box'], item['box']) > 0 for p in f.get('predicted', []))), None)
    return before, using


class EvidenceRecovery:
    def __init__(self, store, media, dataset):
        self.store, self.media, self.dataset = store, media, dataset
        self.attempts = {}
        self._labels = {}

    def refresh(self, limit: int = 2) -> int:
        paths = list(self.dataset.labels.glob('*.json'))
        infos = {}
        for path in paths:
            try:
                infos[path] = path.stat()
            except FileNotFoundError:
                pass
        self._labels = {p: cached for p, cached in self._labels.items() if p in infos}
        version = (len(paths), max((info.st_mtime_ns for info in infos.values()), default=0),
                   self.media.capture_status().get('last_activity_photo_at'))
        now = time.monotonic()
        items = [i for i in self.store.obligations() if i['lifecycle'] == 'open' and i['box']]
        ids = {i['id'] for i in items}
        self.attempts = {key:value for key,value in self.attempts.items() if key in ids}
        pending = []
        for item in items:
            prior, attempted = self.attempts.get(item['id'], (None, float('-inf')))
            if prior == version or now-attempted < 60:
                continue
            detail = self.store.obligation(item['id'])
            stages = {e['capture'].get('stage') for e in detail['evidence']}
            if 'leaving' in stages and not {'before', 'using'} <= stages:
                pending.append((detail, stages))
        pending.sort(key=lambda p: self.attempts.get(p[0]['id'], (None, float('-inf')))[1])
        if not pending:
            return 0
        labels = None
        recovered = 0
        for item, stages in pending[:limit]:
            self.attempts[item['id']] = (version, now)
            if self._journal(item, stages, items):
                recovered += 1
                continue
            if labels is None:
                labels = self._saved_labels(infos)
            end = parse_stamp(item['created_at'])
            start = end - timedelta(hours=6 if not item['timing_known'] else .5)
            retained = []
            for label in labels:
                if not start <= parse_stamp(label['created_at']) < end:
                    continue
                try:
                    if self.dataset.image(label['id']).is_file():
                        retained.append(label)
                except (OSError, ValueError, KeyError, LookupError):
                    continue
            before, using = earlier_candidates(item, retained)
            if using and not self._unique_target(item, using['recovered_dish'], items):
                continue
            saved = []
            for stage, frame in (('before', before), ('using', using)):
                if stage in stages or frame is None:
                    continue
                box = frame['recovered_dish']['box'] if stage == 'using' else item['box']
                try:
                    data = self.dataset.image(frame['id']).read_bytes()
                except OSError:
                    continue
                self.media.save_snapshot(item['id'], data,
                    'Earlier saved view; verify whether the dish was absent' if stage == 'before' else 'Person near this dish; handling is unverified',
                    at=parse_stamp(frame['created_at']), capture={'stage':stage, 'box':box,
                    'box_kind':'candidate_item' if stage == 'using' else 'expected_position',
                    'recovered':True, 'dataset_frame_id':frame['id'], 'association':'same_location_candidate'})
                saved.append(stage)
                recovered += 1
            if saved or not any(e['event'] == 'saved_capture_search' for e in item['events']):
                self.store.event('saved_capture_search', 'Earlier saved captures checked; no name, responsibility or clock inferred.', item['id'], {'recovered':saved})
        return recovered

    def _saved_labels(self, infos):
        """Cache parsing, never file availability or the outcome of a search.

        ctime and inode also invalidate rewrites/replacements with restored mtime.
        Photos are rechecked for each eligible window, so deleted originals cannot
        become recovery candidates from the cache.
        """
        labels = []
        for path, info in infos.items():
            version = (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)
            cached = self._labels.get(path)
            try:
                if cached is None or cached[0] != version:
                    cached = (version, json.loads(path.read_text()))
                    self._labels[path] = cached
                label = cached[1]
                # Match the dataset's validation before considering its time range.
                self.dataset._check_id(label['id'])
                labels.append(label)
            except (OSError, ValueError, KeyError, LookupError):
                continue
        return labels

    @staticmethod
    def _unique_target(item, dish, items):
        matches, _, _ = pair([i for i in items if i['location'] == item['location']], [dish])
        return len(matches) == 1 and matches[0][0]['id'] == item['id']

    def _journal(self, item, stages, items):
        """Recover event-time originals before searching sparse training captures."""
        end = parse_stamp(item['created_at'])
        area = next((a for a in self.store.settings()['areas'] if a['name'] == item['location']
                     and a['role'] in ('dirty', 'sink')), None)
        if not area:
            return False
        candidates = []
        for visitor in self.media.activity.recent(area, end):
            dishes = [d for d in visitor['dishes'] if compatible(d['kind'], item['dishes'][0]['kind'])
                      and box_iou(d['box'], item['box']) >= .5 and overlap(visitor['box'], d['box']) >= .75
                      and self._unique_target(item, d, items)]
            if len(dishes) == 1:
                candidates.append((visitor, dishes[0]))
        if not candidates:
            return False
        visitor, dish = candidates[-1]
        if len({v['track'] for v, _ in candidates if visitor['at']-v['at'] <= timedelta(seconds=10)}) != 1:
            return False
        saved = []
        for stage, data, at, box in (
            ('before', visitor.get('before'), visitor.get('before_at'), item['box']),
            ('using', visitor['frame'], visitor['at'], dish['box']),
        ):
            if stage in stages or not data or not at:
                continue
            self.media.save_snapshot(item['id'], data,
                'Earlier retained view' if stage == 'before' else 'Person near this dish; handling is unverified', at=at,
                capture={'stage':stage, 'box':box, 'box_kind':'expected_position' if stage == 'before' else 'candidate_item',
                         'recovered':True, 'association':'same_location_candidate'})
            saved.append(stage)
        if saved:
            self.store.event('activity_photos_recovered', 'Original activity photos attached; action and identity remain unverified.', item['id'], {'recovered':saved})
        return bool(saved)
