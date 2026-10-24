"""Dish inventory built from what changed while someone was at each area.

Dishes only move when a person handles them. So rather than re-identifying every dish in every
frame, each area is compared before and after it is used: once the area is clear and still again,
its picture is compared with the last settled one, and only the parts that changed are examined
by the dish detector. A dish nobody touched keeps its identity however the detector labels or
boxes it, and a dish taken from one area and put down in another within a short time is the same
dish, carrying its open item with it.

Settled areas are also re-detected every minute so an initial miss can recover.
Those discoveries have unknown deposit time and ownership.
"""
from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from .alignment import shift_box
from .media import Media
from .store import CARRY_WINDOW, Store, parse_stamp, stamp
from .vision import box_iou, merge_detections

# An area is settled once nobody has been in it and nothing has moved for this long.
QUIET_SECONDS = 3.0
# Texture difference (in units of the area's own typical edge strength) that marks a changed pixel.
# Measured on this kitchen (99th percentile pixel per frame): still areas stay below 0.9; a hand or dish moving reaches 2 to 20.
CHANGE_LEVEL = 1.5
# Brightness relative to the area's median. Over 111 pairs of empty frames 30 s apart on
# 2026-10-09, 90% of pairs changed no pixel above 0.15 in any area.
SHADE_LEVEL = 0.15
# Frame-to-frame changed fraction meaning a hand, a pan being stirred, or a person the detector missed.
MOTION_FRACTION = 0.015
# Settled-to-settled changed fraction: below the first nothing was moved; above the second the
# light (or the whole view) changed, which says nothing about dishes.
MIN_CHANGED = 0.004
# The largest changes between those empty frames covered 73-89% of an area at once, the size of a
# light switching; a big pan covers about 10% of the stove.
LIGHTING_CHANGED = 0.45
# Fraction of a dish's box that must have changed for it to count as put down or picked up.
TOUCHED = 0.3
START_SCORE = 0.35
KEEP_SCORE = 0.2
# A dish picked up and put down elsewhere within this time is the same dish.
MOVE_WINDOW = CARRY_WINDOW
ASSOCIATION_WINDOW = timedelta(seconds=90)
SEEN_INTERVAL = 60
# After a gap the detector can miss a dish that never moved (glasses on the table, 2026-10-12), so a
# saved dish not found on the first look is re-checked every SEEN_INTERVAL and only flagged as gone
# once it has stayed unseen for UNSEEN_GRACE.
PUT_DOWN_CLIP = "Putting the dish down"
# A put-down clip covers the whole visit, up to this long.
CLIP_MAX_SECONDS = 120
UNSEEN_GRACE = timedelta(minutes=10)
# A dish still out after its deadline gets a fresh photo this often while it is seen there.
OUT_PHOTO_EVERY = timedelta(hours=1)
MAX_ITEMS = 128
TEXTURE_WIDTH = 240
# A normalized view of the dish's own spot survives exposure changes. It only vetoes a
# disappearance; it cannot supply a fresh sighting or after-deadline evidence.
APPEARANCE_DISTANCE = .4
ROLES = ("storage", "clean", "sink", "dirty")


def appearance(image: Any, box: list[float]) -> Any:
    import cv2
    import numpy as np
    height, width = image.shape[:2]
    x, y, w, h = box
    crop = image[max(0, int((y-.1*h)*height)):min(height, int((y+1.1*h)*height)),
                 max(0, int((x-.1*w)*width)):min(width, int((x+1.1*w)*width))]
    if not crop.size:
        return None
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    grey = cv2.resize(grey, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32)
    grey = cv2.GaussianBlur(grey, (3, 3), 0)
    return (grey-grey.mean()) / (float(grey.std())+5)


def texture(image: Any, box: list[float]) -> Any:
    """Two views of one area at a fixed small size: fine edges, and brightness.

    Edges alone miss a smooth white plate on speckled granite: on 2026-10-09 a plate served onto
    the left counter changed 0.4% of the edge view and was never counted. Brightness catches it;
    edges catch a dark pan on a dark stove. Both are divided by the area's median, so dusk and
    lamplight do not look like change.
    """
    import cv2
    import numpy as np
    height, width = image.shape[:2]
    x, y, w, h = box
    crop = image[max(0, int(y * height)):int((y + h) * height), max(0, int(x * width)):int((x + w) * width)]
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    grey = grey.astype(np.float32)
    rows = max(8, round(TEXTURE_WIDTH * grey.shape[0] / max(1, grey.shape[1])))
    grey = cv2.GaussianBlur(cv2.resize(grey, (TEXTURE_WIDTH, rows), interpolation=cv2.INTER_AREA), (3, 3), 0)
    detail = grey - cv2.GaussianBlur(grey, (0, 0), 6)
    # The median, unlike the mean, barely moves when one new high-contrast dish appears.
    shade = cv2.GaussianBlur(grey, (0, 0), 1.5) / (float(np.median(grey)) + 1.0)
    return np.stack([detail / (float(np.median(np.abs(detail))) + 2.0), shade])


def changed(before: Any, after: Any) -> Any:
    import cv2
    import numpy as np
    difference = np.abs(after - before)
    mask = ((difference[0] > CHANGE_LEVEL) | (difference[1] > SHADE_LEVEL)).astype(np.uint8)
    # Isolated pixels are sensor noise and compression; a moved dish changes a solid patch.
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)


def moving(before: Any, after: Any) -> bool:
    """Something is moving in an area between consecutive frames: its edges changed.

    This is the stillness the kitchen check waits for, not the busy/settled state tracking uses.
    In daylight this camera's encoder pulses brightness at each keyframe, about every two
    seconds. In the empty kitchen on 2026-10-13 that changed 10-25% of the sink's brightness view
    and almost none of its edges, so with brightness counted the 7 AM check found no still minute
    in three hours. Tracking keeps brightness: replayed over the labelled daylight morning,
    edge-only motion found no more left-out dishes and logged 39% more false changes.
    """
    import cv2
    import numpy as np
    mask = (np.abs(after[0] - before[0]) > CHANGE_LEVEL).astype(np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).mean() > MOTION_FRACTION


def overlap(a: list[float], b: list[float]) -> float:
    """Intersection over the smaller box: 1.0 when one box sits inside the other."""
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    smaller = min(a[2] * a[3], b[2] * b[3])
    return w * h / smaller if w > 0 and h > 0 and smaller > 0 else 0.0


def inside(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def rests_in(box: list[float], area: dict) -> bool:
    if area['role'] == 'clean' and covered_fraction(box, area) >= .3:
        return True
    # A dish rests near the bottom of its box. The camera looks down at an angle, so the center of
    # a plate standing in a rack is above the rack, and that of a tall pot is behind its burner.
    x, y = box[0] + box[2] / 2, box[1] + 0.8 * box[3]
    ax, ay, aw, ah = area["box"]
    if not (ax <= x <= ax + aw and ay <= y <= ay + ah):
        return False
    # The counter's front edge runs diagonally across the picture, so a rectangle drawn over a counter
    # also covers the cabinet doors below it. On 2026-10-13 an empty door and a jar in an open
    # cabinet were held as dishes left out past the deadline. An outline trims the area to the counter top.
    return "outline" not in area or inside((x, y), area["outline"])


def covered_fraction(box: list[float], area: dict) -> float:
    """Clip the area's polygon to the dish rectangle and measure covered dish area."""
    x, y, w, h = box
    if w <= 0 or h <= 0:
        return 0.0
    ax, ay, aw, ah = area['box']
    polygon = area.get('outline', [[ax, ay], [ax+aw, ay], [ax+aw, ay+ah], [ax, ay+ah]])
    for axis, edge, direction in ((0, x, 1), (0, x+w, -1), (1, y, 1), (1, y+h, -1)):
        clipped = []
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            a_in, b_in = direction*(a[axis]-edge) >= 0, direction*(b[axis]-edge) >= 0
            if a_in != b_in:
                t = (edge-a[axis])/(b[axis]-a[axis])
                clipped.append([a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])])
            if b_in:
                clipped.append(b)
        polygon = clipped
        if not polygon:
            return 0.0
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(polygon, polygon[1:]+polygon[:1]))) / (2*w*h)


def area_for(box: list[float], areas: list[dict]) -> dict | None:
    # Prefer the most specific area within a role too: a stove can sit inside a
    # larger counter rectangle. Configuration order must not change its location.
    return min((a for a in areas if rests_in(box, a)),
               key=lambda a: (ROLES.index(a["role"]), a["box"][2] * a["box"][3], a["id"]), default=None)


def in_area(person: list[float], area: list[float]) -> bool:
    w = min(person[0] + person[2], area[0] + area[2]) - max(person[0], area[0])
    h = min(person[1] + person[3], area[1] + area[3]) - max(person[1], area[1])
    if w <= 0 or h <= 0:
        return False
    return w * h >= 0.05 * area[2] * area[3] or w * h >= 0.1 * person[2] * person[3]


def pair(items: list[dict], detections: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Match a distinct position and scale; ties between adjacent dishes remain unresolved."""
    scored = sorted(((max(box_iou(i["box"], d["box"]), 0.8 * overlap(i["box"], d["box"])), n, m)
                     for n, i in enumerate(items) for m, d in enumerate(detections)
                     if .4 <= d['box'][2]*d['box'][3]/max(i['box'][2]*i['box'][3], 1e-9) <= 2.5), reverse=True)
    pairs, used_items, used_detections = [], set(), set()
    for score, n, m in scored:
        if score < 0.3:
            break
        competitors = [s for s, a, b in scored if (a == n and b != m) or (b == m and a != n)]
        if competitors and score-max(competitors) < .12:
            continue
        if n not in used_items and m not in used_detections:
            pairs.append((items[n], detections[m]))
            used_items.add(n)
            used_detections.add(m)
    return (pairs, [i for n, i in enumerate(items) if n not in used_items],
            [d for m, d in enumerate(detections) if m not in used_detections])


@dataclass
class Zone:
    area: dict
    baseline: Any = None
    previous: Any = None
    active_at: datetime | None = None
    settled_since: datetime | None = None
    touched: bool = False
    initialized: bool = False
    checked_at: datetime | None = None
    recheck: bool = False
    # The latest frame with nobody in the area, and, frozen when a visit begins, the one from just before it.
    quiet_jpeg: bytes | None = None
    quiet_at: datetime | None = None
    visit_started: datetime | None = None
    visit_before: bytes | None = None
    visit_before_at: datetime | None = None
    # Person track -> the latest frame showing them in this area: who handled what was put down.
    visitors: dict = field(default_factory=dict)
    handling_history: list = field(default_factory=list)
    # Since when nobody has been in the area and none of its edges have moved (see moving()).
    calm_since: datetime | None = None


class Inventory:
    def __init__(self, store: Store, media: Media, *, settle_seconds: float = 30):
        self.store, self.media = store, media
        self.settle_seconds = settle_seconds
        self.items: dict[str, dict] = {item["key"]: item for item in store.load_scene() if item.get("area_id") and item.get("box")}
        self.zones: dict[str, Zone] = {}
        self.in_hand: list[dict] = []
        # Working frames; localized handling originals are also journaled immediately.
        self.evidence: dict[str, dict] = {}
        self.generation: str | None = None
        # When this unbroken stretch of watching began: a dish seen since then and now gone was taken while watched.
        self.watching_since: datetime | None = None
        # Identity hooks: (frame, person box) -> (cues, crop), and enrolled face vectors per housemate.
        self.describe: Callable | None = None
        self.enrolled_faces: Callable[[], dict] = dict
        # Second look at a brand-new box: (frame, box) -> {"dish", "score", "sample"}, or None when not ready.
        self.verify: Callable[[Any, list[float]], dict | None] | None = None
        self.notable: str | None = None
        self.capacity_warning = False
        self.next_due_check: datetime | None = None
        self.next_handling_check: datetime | None = None
        self.handling_checked: dict[str, datetime] = {}
        # Every dish put down or picked up, for evaluation against hand-labeled footage.
        self.changes: deque[dict] = deque(maxlen=2000)
        # Dishes already there when watching began that have since gone: nobody can say whose they were.
        linked = {item.get("obligation_id") for item in self.items.values()}
        for obligation_id in store.unclaimed():
            if obligation_id not in linked:
                store.close_unclaimed(obligation_id)

    # Lifecycle

    def reset(self) -> None:
        """After a gap, every area is checked against the saved inventory before anything new is believed."""
        self.generation = None
        self.zones.clear()
        self.in_hand.clear()
        self.evidence.clear()

    def shift(self, dx: float, dy: float) -> None:
        """The camera was nudged: dishes moved on screen with it, and every area needs a fresh baseline."""
        for item in self.items.values():
            item["box"] = shift_box(item["box"], dx, dy)
            if item.get('appearance_box'):
                item['appearance_box'] = shift_box(item['appearance_box'], dx, dy)
        for zone in self.zones.values():
            zone.baseline = zone.previous = None
        self._save()

    def _save(self) -> None:
        self.store.save_scene(list(self.items.values()))

    def listing(self) -> list[dict]:
        return [dict(item) for item in self.items.values() if not item.get("ignored") and not item.get("unseen_since")]

    def quiet_since(self) -> datetime | None:
        """Since when every area has been still with nobody in it; None while any is in use or unchecked."""
        if not self.zones or any(zone.calm_since is None or not zone.initialized for zone in self.zones.values()):
            return None
        return max(zone.calm_since for zone in self.zones.values())

    # Frames

    def zone_states(self) -> dict[str, str]:
        return {key: "settled" if zone.settled_since else "busy" for key, zone in self.zones.items()}

    def process(self, analysis: dict, image: Any, jpeg: bytes, areas: list[dict], generation: str,
                observed_at: datetime, detect: Callable[[dict], list[dict]]) -> None:
        if image is None:
            return
        people = [d for d in analysis.get("detections", []) if d["kind"] == "person" and d.get("score", 0) >= 0.35]
        if generation != self.generation:
            self.reset()
            self.generation = generation
            self.watching_since = observed_at
            self.store.mark_watching(observed_at)
        # Correct saved inventory after a zone edit without inventing physical
        # movement, changing ownership, or opening another incident.
        relocated = False
        for item in self.items.values():
            area = area_for(item["box"], areas)
            if area and (item["area_id"], item["role"]) != (area["id"], area["role"]):
                item.update(area_id=area["id"], role=area["role"])
                relocated = True
        if relocated:
            self._save()
        self._forget_in_hand(observed_at)
        self.notable = None
        settling: list[Zone] = []
        handling_area = min((a for a in areas if a['role'] in ('dirty', 'sink')
                             and any(in_area(p['box'], a['box']) for p in people)),
                            key=lambda a: (self.handling_checked.get(a['id'], observed_at-timedelta(days=1)),
                                           a['box'][2]*a['box'][3]), default=None)
        handling_detections = analysis.get('detections', [])
        if handling_area and (self.next_handling_check is None or observed_at >= self.next_handling_check):
            # Small cookware is often missed in the full-room pass while someone
            # uses it. One detailed area pass every five seconds captures that stage.
            handling_detections = merge_detections([*handling_detections, *detect(handling_area)])
            self.handling_checked[handling_area['id']] = observed_at
            self.next_handling_check = observed_at + timedelta(seconds=5)
        for area in areas:
            zone = self.zones.get(area["id"])
            if zone is None or zone.area["box"] != area["box"]:
                zone = self.zones[area["id"]] = Zone(area, initialized=zone.initialized if zone else False)
                if area['role'] in ('dirty', 'sink'):
                    zone.handling_history = self.media.activity.recent(area, observed_at)
            zone.area = area
            current = texture(image, area["box"])
            same_shape = zone.previous is not None and zone.previous.shape == current.shape
            motion = same_shape and changed(zone.previous, current).mean() > MOTION_FRACTION
            present = [p for p in people if in_area(p["box"], area["box"])]
            if present or (same_shape and moving(zone.previous, current)):
                zone.calm_since = None
            elif zone.calm_since is None:
                zone.calm_since = observed_at
            zone.previous = current
            if present or motion:
                if zone.active_at is None or zone.settled_since is not None:
                    zone.visit_started, zone.visit_before = observed_at, zone.quiet_jpeg
                    zone.visit_before_at = zone.quiet_at
                zone.active_at, zone.settled_since, zone.touched = observed_at, None, True
                for person in present:
                    track = person.get('track_id')
                    visitor = {"frame": jpeg, "box": person["box"], "at": observed_at, "track": track,
                               "dishes": [d for d in handling_detections if d["kind"] != "person"
                                          and (area_for(d['box'], areas) or {}).get('id') == area['id']]}
                    # The last view often loses the dish behind a hand or body.
                    # Keep a bounded set of actual earlier dish/person frames.
                    samples = [s for s in zone.visitors.get(track, {}).get('samples', [])
                               if track is not None and observed_at - s['at'] <= ASSOCIATION_WINDOW]
                    if any(overlap(d['box'], person['box']) > 0 for d in visitor['dishes']):
                        samples.append(visitor.copy())
                    visitor['samples'] = samples[-16:]
                    zone.visitors[track] = visitor
                    history = [v for v in zone.handling_history if observed_at-v['at'] <= timedelta(minutes=30)]
                    localized = [d for d in visitor['dishes'] if d.get('score', 0) >= START_SCORE
                                 and overlap(d['box'], person['box']) >= .75
                                 and len([p for p in present if overlap(d['box'], p['box']) >= .75]) == 1]
                    if localized and area['role'] in ('dirty', 'sink'):
                        previous = next((v for v in reversed(history) if v['track'] == track), None)
                        new_dish = not previous or any(not self._handling_dishes(previous, d['box'], d['kind']) for d in localized)
                        if new_dish or observed_at-previous['at'] >= timedelta(seconds=20):
                            candidate = {k:v for k,v in visitor.items() if k != 'samples'} | self._visit(zone) | {'dishes': localized}
                            self.media.activity.save(area, generation, candidate)
                            history.append(candidate)
                    zone.handling_history = history[-48:]
                continue
            zone.quiet_jpeg = jpeg
            zone.quiet_at = observed_at
            if zone.active_at and (observed_at - zone.active_at).total_seconds() < QUIET_SECONDS:
                continue
            if zone.settled_since is None:
                zone.settled_since = observed_at
                settling.append(zone)
            elif zone.checked_at is None or (observed_at - zone.checked_at).total_seconds() >= SEEN_INTERVAL:
                # Still pixels cannot establish which objects are present. An
                # initial miss must recover even if nobody touches the area again.
                zone.recheck = True
                settling.append(zone)
        if settling:
            self._settle(settling, image, jpeg, areas, observed_at, detect, {z.area["id"]: texture(image, z.area["box"]) for z in settling})
        self._open_waiting(observed_at)
        self._capture_due(jpeg, observed_at, detect)
        self._forget_in_hand(observed_at)

    def _record_seen(self, item: dict, jpeg: bytes, observed_at: datetime) -> None:
        """Only a matched detection can refresh presence or support deadline evidence."""
        if not item.get("obligation_id") or item.get("ignored") or item["role"] not in ("dirty", "sink"):
            return
        record = self.store.obligation(item["obligation_id"], now=observed_at)
        self.store.record_seen(item["obligation_id"], observed_at)
        late = [parse_stamp(e["created_at"]) for e in record["evidence"]
                if (e["capture"].get("stage") == "later" or e["label"].startswith("Matching dish still visible after the deadline"))
                and record["followup_at"] and parse_stamp(e["created_at"]) >= parse_stamp(record["followup_at"])]
        if late:
            item["followup_saved_at"] = record["followup_at"]
        if record["lifecycle"] != "open" or record["archived_at"]:
            return
        if observed_at >= parse_stamp(record["followup_at"]) and not late:
            self.media.save_snapshot(item["obligation_id"], jpeg, "Matching dish still visible after the deadline; dirty status requires review", at=observed_at,
                                     capture={"stage": "later", "box": item["box"], "box_kind": "candidate_item"})
            self.media.request_event_clip(item["obligation_id"], "Around the after-deadline observation", at=observed_at, seconds=15)
            item["followup_saved_at"] = record["followup_at"]
        elif late and observed_at - max(late) >= OUT_PHOTO_EVERY:
            # The Send page shows a dish still out by its newest photo; the deadline photo alone made a
            # bowl out for a day read "still there 2 hours later". Only a settled look gets here once
            # the deadline photo is saved, so whoever was in the way has gone.
            self.media.save_snapshot(item["obligation_id"], jpeg, "Matching dish still visible after the deadline; still out", at=observed_at,
                                     capture={"stage": "later", "box": item["box"], "box_kind": "candidate_item"})

    def _capture_due(self, jpeg: bytes, observed_at: datetime, detect: Callable) -> None:
        # Deadline photos must not wait for an entire zone to become motionless.
        # Re-detect the actual item; cached inventory and a timer alone are insufficient.
        if self.next_due_check and observed_at < self.next_due_check:
            return
        self.next_due_check = observed_at + timedelta(seconds=5)
        due = {}
        for item in self.items.values():
            if not item.get('obligation_id') or item.get('ignored') or item.get('unseen_since') or item['role'] not in ('dirty', 'sink'):
                continue
            if item.get('followup_at') and (item.get('followup_saved_at') == item['followup_at'] or observed_at < parse_stamp(item['followup_at'])):
                continue
            record = self.store.obligation(item['obligation_id'], now=observed_at)
            item['followup_at'] = record['followup_at']
            if record['archived_at'] or record['lifecycle'] != 'open' or observed_at < parse_stamp(record['followup_at']):
                continue
            if any(e['capture'].get('stage') == 'later' and e['created_at'] >= record['followup_at'] for e in record['evidence']):
                continue
            due.setdefault(item['area_id'], []).append(item)
        for area_id, items in due.items():
            zone = self.zones.get(area_id)
            if not zone:
                continue
            found = [d for d in detect(zone.area) if d['kind'] != 'person' and d.get('score', 0) >= KEEP_SCORE
                     and (area_for(d['box'], [z.area for z in self.zones.values()]) or {}).get('id') == area_id]
            due_keys = {i['key'] for i in items}
            pairs, _, _ = pair(self._items_in(zone), found)
            for item, detection in pairs:
                if item['key'] not in due_keys or box_iou(item['box'], detection['box']) < .35:
                    continue
                self._update(item, detection, observed_at)
                self._record_seen(item, jpeg, observed_at)

    def _items_in(self, zone: Zone) -> list[dict]:
        return [item for item in self.items.values() if item["area_id"] == zone.area["id"]]

    def _settle(self, zones: list[Zone], image: Any, jpeg: bytes, areas: list[dict], observed_at: datetime,
                detect: Callable, textures: dict) -> None:
        work = []
        for zone in zones:
            current = textures[zone.area["id"]]
            if zone.recheck or not zone.initialized or zone.baseline is None or zone.baseline.shape != current.shape:
                work.append((zone, None))
            elif zone.touched:
                mask = changed(zone.baseline, current)
                if mask.mean() >= MIN_CHANGED:
                    work.append((zone, mask))
                else:
                    zone.visitors.clear()
            zone.baseline, zone.touched, zone.checked_at = current, False, observed_at
        if not work:
            return
        # A combined cabinet-to-floor crop made small, nested sink dishes vanish.
        # Look at each area with context, then remove cross-area duplicate boxes.
        found = merge_detections([d for zone, _ in work for d in detect(zone.area)
                                  if d["kind"] != "person" and d.get("score", 0) >= KEEP_SCORE
                                  and (not d.get("detail_area") or d["detail_area"] == (area_for(d["box"], areas) or {}).get("id"))])
        arrivals = []
        for zone, mask in work:
            here = [d for d in found if (area_for(d["box"], areas) or {}).get("id") == zone.area["id"]]
            if zone.recheck:
                zone.recheck = False
                self._initialize(zone, here, jpeg, observed_at, image=image)
            elif not zone.initialized:
                self._initialize(zone, here, jpeg, observed_at, image=image)
            elif mask is None or mask.mean() > LIGHTING_CHANGED:
                self._refresh(zone, here)
            else:
                arrivals.extend((zone, d) for d in self._compare(zone, here, mask, observed_at, image=image))
        # Register every pickup before matching arrivals. Area configuration order must
        # not decide whether a sink-to-rack transfer keeps the dish's history.
        for zone, detection in arrivals:
            self._put_down(zone, detection, observed_at, jpeg, image)
        for zone, _ in work:
            zone.visitors.clear()
        self._save()

    # What changed

    def _initialize(self, zone: Zone, detections: list[dict], jpeg: bytes, observed_at: datetime, *, image: Any = None) -> None:
        """First look after a start or gap: reconcile with the saved inventory; anything else was already there.

        A saved dish not found stays, unconfirmed, while its spot looks unchanged. Otherwise it
        is looked for again until UNSEEN_GRACE. Newly discovered objects have unknown deposit time and
        no handler; periodic discovery is not evidence of a new dirty deposit.
        """
        zone.initialized = True
        pairs, missing, extra = pair(self._items_in(zone), detections)
        for item, detection in pairs:
            self._update(item, detection, observed_at)
            self._remember(item, image)
            self._record_seen(item, jpeg, observed_at)
        for item in missing:
            if self._unchanged(item, image):
                item.pop('unseen_since', None)
                continue
            if item.get('background') or item.get('ignored'):
                # A detector miss is not an observed removal of a background object.
                since = parse_stamp(item.setdefault('unseen_since', stamp(observed_at)))
                if item.get('obligation_id') and observed_at - since >= UNSEEN_GRACE:
                    self.store.note_missing(item['obligation_id'], zone.area['name'])
                if observed_at - since > timedelta(hours=1):
                    if item.get('obligation_id'):
                        self.store.close_unclaimed(item['obligation_id'])
                    del self.items[item['key']]
                continue
            since = parse_stamp(item.setdefault("unseen_since", stamp(observed_at)))
            if observed_at - since < UNSEEN_GRACE:
                continue
            if item.get("obligation_id") and not self.store.close_unclaimed(item["obligation_id"]):
                if self.watching_since and parse_stamp(item["last_seen_at"]) >= self.watching_since:
                    self.store.note_gone(item["obligation_id"], zone.area["name"])
                else:
                    # It may have sat out unseen through the gap; only a person can say.
                    self.store.note_missing(item["obligation_id"], zone.area["name"])
            del self.items[item["key"]]
        for detection in extra:
            if any(box_iou(detection['box'], old['box']) >= .3 for old in missing):
                continue  # An ambiguous match does not establish a second dish.
            if detection["score"] >= START_SCORE:
                verdict = self.verify(image, detection["box"]) if self.verify and image is not None else None
                self._add(zone, detection, observed_at, jpeg, placed_at=None,
                          ignored=verdict if verdict and not verdict["dish"] else None,
                          verdict=verdict if verdict and verdict["dish"] else None, image=image)

    def _refresh(self, zone: Zone, detections: list[dict]) -> None:
        """The light or the view changed: follow dishes that are clearly still there, believe nothing new."""
        pairs, _, _ = pair(self._items_in(zone), detections)
        for item, detection in pairs:
            item["box"] = detection["box"]

    def _compare(self, zone: Zone, detections: list[dict], mask: Any, observed_at: datetime, *, image: Any = None) -> list[dict]:
        existing = self._items_in(zone)
        for item in existing:
            # A physical change in a long-empty background spot can be a new dish.
            # Keep ordinary periodic rechecks quiet, but do not absorb this arrival.
            if ((item.get('background') or item.get('ignored')) and item.get('unseen_since')
                    and observed_at - parse_stamp(item['unseen_since']) >= UNSEEN_GRACE
                    and self._touched(zone, mask, item['box']) >= TOUCHED):
                if item.get('obligation_id'):
                    self.store.close_unclaimed(item['obligation_id'])
                del self.items[item['key']]
        pairs, missing, extra = pair(self._items_in(zone), detections)
        for item, detection in pairs:
            self._update(item, detection, observed_at)
            self._remember(item, image)
        for item in missing:
            if any(box_iou(item['box'], d['box']) >= .3 for d in extra):
                continue
            if self._touched(zone, mask, item["box"]) >= TOUCHED and not self._unchanged(item, image):
                self._pick_up(zone, item, observed_at)
        return [d for d in sorted(extra, key=lambda d: -d["score"])
                if not any(box_iou(d['box'], old['box']) >= .3 for old in missing)
                if d["score"] >= START_SCORE and self._touched(zone, mask, d["box"]) >= TOUCHED]

    @staticmethod
    def _touched(zone: Zone, mask: Any, box: list[float]) -> float:
        ax, ay, aw, ah = zone.area["box"]
        rows, columns = mask.shape
        left, right = int((box[0] - ax) / aw * columns), int((box[0] + box[2] - ax) / aw * columns + 0.999)
        top, bottom = int((box[1] - ay) / ah * rows), int((box[1] + box[3] - ay) / ah * rows + 0.999)
        patch = mask[max(0, top):max(0, bottom), max(0, left):max(0, right)]
        return float(patch.mean()) if patch.size else 0.0

    def _update(self, item: dict, detection: dict, observed_at: datetime) -> None:
        item.pop("unseen_since", None)
        item.setdefault("first_seen_at", stamp(observed_at))
        votes = item.setdefault("kind_votes", {})
        votes[detection["kind"]] = round(votes.get(detection["kind"], 0) + detection["score"], 3)
        item.update(box=detection["box"], score=round(detection["score"], 3), kind=max(votes, key=votes.get), last_seen_at=stamp(observed_at))

    @staticmethod
    def _remember(item: dict, image: Any) -> None:
        if image is not None and (patch := appearance(image, item['box'])) is not None:
            # Saved with the scene so a restart does not turn detector flicker into a new clock.
            item['appearance'] = patch.round(3).tolist()
            item['appearance_box'] = list(item['box'])

    @staticmethod
    def _unchanged(item: dict, image: Any) -> bool:
        import numpy as np
        if image is None or 'appearance' not in item:
            return False
        patch = appearance(image, item.get('appearance_box', item['box']))
        return patch is not None and float(np.abs(patch-np.asarray(item['appearance'])).mean()) < APPEARANCE_DISTANCE

    @staticmethod
    def _handling_dishes(visitor: dict, box: list[float], kind: str) -> list[dict]:
        return [d for d in visitor.get('dishes', []) if (d['kind'] == kind or {d['kind'], kind} <= {'pan', 'pot', 'bowl'})
                and overlap(d['box'], visitor['box']) >= .75 and box_iou(d['box'], box) >= .35]

    def _handler(self, zone: Zone, box: list[float], kind: str | None = None) -> dict | None:
        """Keep one plausible visitor; overlapping visitors leave identity unresolved."""
        visitors = list(zone.visitors.values())
        # A cooking visit can span many brief departures. Preserve localized
        # handling across settled scans; never substitute a merely nearby person.
        end = zone.quiet_at or zone.active_at
        localized = [v for v in zone.handling_history if kind and (end is None or end-v['at'] <= timedelta(minutes=30))
                     and len(self._handling_dishes(v, box, kind)) == 1]
        if localized:
            latest = max(v['at'] for v in localized)
            recent = [v for v in localized if latest-v['at'] <= timedelta(seconds=10)]
            if len({v['track'] for v in recent}) == 1:
                return max(recent, key=lambda v:v['at'])
            return None
        if not visitors:
            return None
        reaching = [v for v in visitors if any(overlap(s['box'], box) > 0
                    for s in [v, *v.get('samples', [])])]
        candidates = reaching
        if not candidates:
            return None
        latest = max(v['at'] for v in candidates)
        recent = [v for v in candidates if (latest - v['at']).total_seconds() <= 10]
        if len(recent) != 1:
            return None
        visitor = recent[0]
        localized = [s for s in visitor.get('samples', [])
                     if kind and len(self._handling_dishes(s, box, kind)) == 1]
        return localized[-1] if localized else visitor

    def _log(self, change: str, zone: Zone, kind: str, observed_at: datetime, dish_score: float | None = None) -> None:
        entry = {"at": stamp(zone.active_at or observed_at), "area": zone.area["id"], "change": change, "kind": kind}
        if dish_score is not None:
            # Kept with replays' changes so a threshold can be tuned without replaying again.
            entry["dish_score"] = dish_score
        self.changes.append(entry)
        self.store.log_change(entry)

    def _pick_up(self, zone: Zone, item: dict, observed_at: datetime) -> None:
        del self.items[item["key"]]
        if item.get("ignored"):
            return
        self._log("removed", zone, item["kind"], observed_at, item.get("dish_score"))
        handler = self._handler(zone, item["box"], item['kind'])
        self.in_hand.append({"item": item, "at": observed_at, "from": zone.area, "handler": handler})
        if item.get("obligation_id"):
            self.store.event("dish_picked_up", f"{item['kind'].title()} picked up from {zone.area['name']}", item["obligation_id"])

    def _put_down(self, zone: Zone, detection: dict, observed_at: datetime, jpeg: bytes, image: Any = None) -> None:
        handler = self._handler(zone, detection["box"], detection['kind'])
        source = self._carried(detection, handler, observed_at)
        if source is None:
            # Only a brand-new box gets a second look; a dish seen being carried here is already known.
            verdict = self.verify(image, detection["box"]) if self.verify and image is not None else None
            if verdict and not verdict["dish"]:
                self._add(zone, detection, observed_at, jpeg, placed_at=None, ignored=verdict, image=image)
                return
            self._log("added", zone, detection["kind"], observed_at, verdict["score"] if verdict else None)
            self._add(zone, detection, observed_at, jpeg, placed_at=zone.active_at or observed_at, handler=handler, verdict=verdict, image=image)
            return
        self._log("added", zone, detection["kind"], observed_at)
        item, origin = source["item"], source["from"]
        self.items[item["key"]] = item
        item.update(area_id=zone.area["id"], role=zone.area["role"], box=detection["box"], last_seen_at=stamp(observed_at))
        item.pop('background', None)
        self.evidence.pop(item["key"], None)
        linked = self.store.obligation(item["obligation_id"]) if item.get("obligation_id") else None
        if linked and linked["lifecycle"] in ("resolved", "dismissed", "cleared"):
            item["obligation_id"], linked = None, None
        if origin["role"] in ("clean", "storage") and zone.area["role"] in ("dirty", "sink") and linked is None:
            # A clean dish taken out and put down is a new use, with a photo of where it came from.
            item.update(placed_at=stamp(zone.active_at or observed_at), timing_known=True, sink_seen=zone.area["role"] == "sink")
            self.evidence[item["key"]] = {"handler": handler, "taken": source.get("handler"), "taken_from": origin["name"], "taken_at": source["at"], "frame": jpeg, "frame_at": observed_at, **self._visit(zone)}
        elif zone.area["role"] == "sink":
            item["sink_seen"] = True
        elif zone.area["role"] in ("dirty",) and linked is None:
            item.update(placed_at=stamp(zone.active_at or observed_at), timing_known=True)
            self.evidence[item["key"]] = {"handler": handler, "frame": jpeg, "frame_at": observed_at, **self._visit(zone)}
        if linked:
            self.store.event("dish_moved", f"{item['kind'].title()} moved from {origin['name']} to {zone.area['name']}", linked["id"])
            if zone.area["role"] in ("clean", "storage") and self.store.propose_cleanup(linked["id"], at=observed_at):
                self.notable = "Put to dry or away"
                self.media.save_snapshot(linked["id"], jpeg, "Possible clean destination; washing quality unverified", at=observed_at)
                self.media.request_event_clip(linked["id"], "Possible cleanup transition", at=zone.active_at or observed_at)

    def _carried(self, detection: dict, handler: dict | None, observed_at: datetime) -> dict | None:
        """A short, unambiguous transfer by the same observed handler; kind alone is insufficient."""
        if not handler or handler.get('track') is None:
            return None
        candidates = [e for e in self.in_hand
                      if timedelta(0) <= observed_at - e['at'] <= ASSOCIATION_WINDOW
                      and e['handler'] and e['handler'].get('track') == handler['track']
                      and (e['item']['kind'] == detection['kind'] or detection['kind'] in e['item'].get('kind_votes', {}))]
        if len(candidates) != 1:
            return None
        chosen = candidates[0]
        self.in_hand.remove(chosen)
        return chosen

    def _add(self, zone: Zone, detection: dict, observed_at: datetime, jpeg: bytes, *, placed_at: datetime | None, handler: dict | None = None,
             verdict: dict | None = None, ignored: dict | None = None, image: Any = None) -> None:
        if len(self.items) >= MAX_ITEMS:
            self.capacity_warning = True
            return
        key = uuid.uuid4().hex
        self.items[key] = {"key": key, "kind": detection["kind"], "kind_votes": {detection["kind"]: round(detection["score"], 3)},
                           "box": detection["box"], "score": round(detection["score"], 3), "area_id": zone.area["id"], "role": zone.area["role"], "obligation_id": None,
                           "placed_at": stamp(placed_at) if placed_at else None, "timing_known": placed_at is not None,
                           "background": placed_at is None,
                           "sink_seen": zone.area["role"] == "sink", "last_seen_at": stamp(observed_at), "first_seen_at": stamp(observed_at)}
        self._remember(self.items[key], image)
        if ignored:
            # Judged not a dish: remembered where it sits, so the same thing isn't "new" at every look.
            self.items[key].update(ignored=True, dish_score=ignored["score"])
            return
        if verdict:
            self.items[key].update(dish_score=verdict["score"], sample=verdict["sample"])
        self.evidence[key] = {"handler": handler, "frame": jpeg, "frame_at": observed_at, **self._visit(zone)}

    @staticmethod
    def _visit(zone: Zone) -> dict:
        """When the visit that left a dish began, and the area just before it: the start of its clip and its before photo."""
        return {"before": zone.visit_before, "before_at": zone.visit_before_at, "visit_started": zone.visit_started}

    def _forget_in_hand(self, observed_at: datetime) -> None:
        # Carried off camera (a dishwasher, another room): the open item stays open; nothing is assumed,
        # except that a dish nobody was ever named for is closed.
        gone = [e for e in self.in_hand if observed_at - e["at"] > MOVE_WINDOW]
        self.in_hand = [e for e in self.in_hand if observed_at - e["at"] <= MOVE_WINDOW]
        for entry in gone:
            if entry["item"].get("obligation_id"):
                self.store.close_unclaimed(entry["item"]["obligation_id"])

    # Open items

    def _open_waiting(self, observed_at: datetime) -> None:
        opened = False
        for item in list(self.items.values()):
            if item.get("obligation_id") or item.get("unseen_since") or item.get("ignored") or item["role"] not in ("dirty", "sink"):
                continue
            zone = self.zones.get(item["area_id"])
            if zone is None or zone.settled_since is None:
                continue
            if not item.get("background") and (observed_at - zone.settled_since).total_seconds() < self.settle_seconds:
                continue
            self._open(item, zone, observed_at)
            opened = True
        if opened:
            self._save()

    def _open(self, item: dict, zone: Zone, observed_at: datetime) -> None:
        """Turn a dish left unattended into a review entry with the evidence a person needs to decide whose it is."""
        evidence = self.evidence.pop(item["key"], {})
        frame, handler = evidence.get("frame"), evidence.get("handler")
        handler = self._handler(zone, item["box"], item["kind"]) or handler
        if handler and handler.get("before_at") and (not evidence.get("before_at") or evidence["before_at"] >= handler["at"]):
            evidence.update(before=handler.get("before"), before_at=handler["before_at"])
        # The deposit frame can still contain the handler. Use the actual settled
        # view at opening for departure, and preserve its own capture timestamp.
        departure = zone.quiet_jpeg if zone.quiet_at and zone.quiet_at <= observed_at else None
        departure_at = zone.quiet_at if departure else evidence.get('frame_at') or observed_at
        frame = departure or frame
        baseline = not item.get("timing_known")
        self.notable = "Dish left unattended"
        handler_key = f"{self.generation}:{handler['track']}" if handler and handler.get("track") is not None else None
        placed_at = departure_at if not baseline else parse_stamp(item.get("first_seen_at") or stamp(observed_at))
        # Each dish owns its evidence and outcome. Putting one plate away must not
        # close another dish that the same person left during this visit.
        reason = ("First observed here. Deposit time and ownership are unknown." if baseline
                  else "Possible unattended dish. Confirm it is used/dirty and who is responsible; presence alone does not prove either.")
        record = self.store.track_obligation(item["key"]) or self.store.create_obligation(
            None, [{"kind": item["kind"], "quantity": 1}], source="baseline" if baseline else "vision", candidate=True, track_key=item["key"],
            at=placed_at, reason=reason, timing_known=not baseline, location=zone.area["name"],
            scene=list(self.items.values()), handler_key=handler_key, box=item["box"])
        item["obligation_id"] = record["id"]
        item["followup_at"] = record["followup_at"]
        if item.get("sample"):
            self.store.link_dish_sample(item.pop("sample"), record["id"])
        if frame:
            self.media.save_snapshot(record["id"], frame, "First observed here; deposit time unknown" if baseline else "Dish left unattended", at=departure_at,
                                     capture={"stage": "leaving", "box": item["box"], "box_kind": "candidate_item"})
        if not baseline or handler:
            started = evidence.get("visit_started") or placed_at
            if evidence.get("before") and evidence.get("before_at"):
                self.media.save_snapshot(record["id"], evidence["before"], "Before it was put down", at=evidence["before_at"],
                                         capture={"stage": "before", "box": item["box"], "box_kind": "expected_position", "recovered": baseline})
            # The whole visit, from arriving to the area going still: that's when the dish was put down.
            span = min(CLIP_MAX_SECONDS, (placed_at - started).total_seconds() + 15)
            self.media.request_event_clip(record["id"], PUT_DOWN_CLIP, at=placed_at + timedelta(seconds=5), seconds=int(span))
        if handler:
            dishes = [handler['matched_dish']] if handler.get('matched_dish') else self._handling_dishes(handler, item['box'], item['kind'])
            if len(dishes) == 1:
                self.media.save_snapshot(record["id"], handler["frame"], "Person near this dish; handling is unverified", at=handler["at"],
                                         capture={"stage": "using", "box": dishes[0]['box'], "box_kind": "candidate_item", "recovered": baseline})
            else:
                self.media.save_snapshot(record["id"], handler["frame"], "Nearby person; dish handling not localized", at=handler["at"])
            self._describe_handler(record["id"], handler, zone.area["id"])
        if evidence.get("taken_from"):
            taken = evidence.get("taken")
            if taken:
                self.media.save_snapshot(record["id"], taken["frame"], f"Taken from {evidence['taken_from']}", at=taken["at"])

    def _describe_handler(self, obligation_id: int, handler: dict, context: str) -> None:
        if not self.describe:
            return
        try:
            cues, crop = self.describe(handler["frame"], handler["box"])
            evidence_id = self.media.save_snapshot(obligation_id, crop, "Who handled it (zoomed)", at=handler["at"])
            for cue, vector in cues.items():
                self.store.add_person_sample(obligation_id, vector, evidence_id, at=handler["at"], cue=cue, context=context)
            resident, evidence, _, seen = self.store.suggest_identity(cues, at=handler["at"], context=context, enrolled_faces=self.enrolled_faces())
            if resident is not None:
                self.store.set_suggestion(obligation_id, resident, evidence, seen)
                if seen.get("face", 0) >= 0.5:
                    self.store.hint_samples(obligation_id, resident)
        except Exception:
            # Appearance is a convenience; the entry and its evidence stand without it.
            pass
