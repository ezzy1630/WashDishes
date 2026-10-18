"""Twice-daily kitchen check: a look at every counter and the sink once the kitchen is quiet.

Whatever is still out late at night or first thing in the morning wasn't washed, however it got
there and whether or not the camera saw who left it. Each check links what it finds to the open
records, adds that photo to them as proof, and asks a person to confirm the outlines once. Those
answers are labels for the second look and, when nothing was missed, a fully labeled frame for
the detector.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .inventory import START_SCORE, area_for
from .store import Store
from .vision import box_iou

CHECK_TIMES = ("23:00", "07:00")
# The kitchen must have been still and empty this long, so nobody is mid-cleanup.
QUIET_SECONDS = 60
# A check that can't find a quiet moment within this long is recorded as missed.
GIVE_UP = timedelta(hours=3)
CHECK_ROLES = ("dirty", "sink")
# Recall matters more than precision here: a person confirms every outline. The detector is unsure
# of dishes sitting still in dim light, so a check looks lower than live tracking, but only when
# the second look is there to drop the junk that lets in.
CHECK_SCORE = 0.2


def timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))


def label(slot: str) -> str:
    """'11 PM' or '7 AM' from a slot key such as '2026-10-12 23:00'."""
    hour = int(slot[11:13])
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def due(store: Store, now: datetime) -> str | None:
    """The slot to check now, if any; a slot past its window is recorded as missed instead."""
    local = now.astimezone(timezone())
    for days in (0, 1):
        day = (local - timedelta(days=days)).date()
        for time in CHECK_TIMES:
            hour, minute = (int(part) for part in time.split(":"))
            start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone())
            slot = start.strftime("%Y-%m-%d %H:%M")
            if local < start or store.check(slot):
                continue
            if local - start >= GIVE_UP:
                store.save_check(slot, status="missed")
                continue
            return slot
    return None


def run(store: Store, media: Any, dataset: Any, slot: str, observed_at: datetime, image: Any, jpeg: bytes, areas: list[dict],
        detect: Callable[[dict], list[dict]], items: list[dict], verify: Callable[[Any, list[float]], dict | None] | None) -> dict:
    # One look per area: a crop the size of one counter shows the detector each dish at several
    # times the resolution of one pass over them all, which found nothing in a dim kitchen.
    found: list[dict] = []
    for watched in (a for a in areas if a["role"] in CHECK_ROLES):
        for detection in detect(watched):
            area = area_for(detection["box"], areas)
            if (detection["score"] < CHECK_SCORE or not area or area["role"] not in CHECK_ROLES
                    or detection.get("detail_area", area["id"]) != area["id"]):
                continue
            same = next((f for f in found if box_iou(f["box"], detection["box"]) > 0.5), None)
            if same is None:
                found.append(detection)
            elif detection["score"] > same["score"]:
                found[found.index(same)] = detection
    dishes = []
    for detection in sorted(found, key=lambda d: (d["box"][1], d["box"][0])):
        area = area_for(detection["box"], areas)
        verdict = verify(image, detection["box"]) if verify else None
        if (verdict and not verdict["dish"]) or (not verdict and detection["score"] < START_SCORE):
            continue
        known = max(((box_iou(detection["box"], item["box"]), item) for item in items if not item.get("ignored")), key=lambda pair: pair[0], default=(0.0, None))
        obligation = known[1].get("obligation_id") if known[1] and known[0] >= 0.3 else None
        dishes.append({"box": [round(v, 5) for v in detection["box"]], "kind": detection["kind"], "area": area["name"],
                       "score": round(verdict["score"], 4) if verdict else None, "sample": verdict["sample"] if verdict else None, "obligation_id": obligation})
    photo = media.save_check_photo(jpeg)
    frame_id = None
    try:
        height, width = image.shape[:2]
        frame_id = dataset.save(jpeg, {"detections": [{"kind": d["kind"], "box": d["box"], "score": 1.0} for d in dishes], "size": [width, height]},
                                "Kitchen check", observed_at=observed_at)
    except OSError:
        pass
    record = store.save_check(slot, at=observed_at, photo=photo, dishes=dishes, frame_id=frame_id)
    for obligation_id in sorted({d["obligation_id"] for d in dishes if d["obligation_id"]}):
        item = store.obligation(obligation_id)
        if item["lifecycle"] == "open":
            store.event("kitchen_check", f"Still out at the {label(slot)} kitchen check", obligation_id, {"check_id": record["id"]})
            media.save_snapshot(obligation_id, jpeg, f"Still out at the {label(slot)} kitchen check", at=observed_at)
    return record


def answer(store: Store, dataset: Any, check_id: int, verdicts: list[bool], missed: int) -> dict:
    """Confirm the outlines; with nothing missed, the photo is a fully labeled training frame."""
    record = store.answer_check(check_id, verdicts, missed)
    if record["frame_id"] and missed == 0:
        try:
            dataset.update(record["frame_id"], [{"kind": d["kind"], "box": d["box"]} for d, keep in zip(record["dishes"], verdicts) if keep], "labeled")
        except (LookupError, ValueError, OSError):
            pass
    return record


def summary(store: Store) -> list[dict]:
    """Recent checks for the dashboard: how many dishes were out, and how many have nobody named."""
    result = []
    for check in store.checks():
        kept = check["answer"]["verdicts"] if check["answer"] else [True] * len(check["dishes"])
        out = [d for d, keep in zip(check["dishes"], kept) if keep]
        result.append({"id": check["id"], "slot": check["slot"], "label": label(check["slot"]), "at": check["at"], "status": check["status"],
                       "photo_url": f"/api/checks/{check['id']}/photo" if check["photo"] else None, "dishes": check["dishes"],
                       "answer": check["answer"], "out": len(out) + (check["answer"]["missed"] if check["answer"] else 0),
                       "unlinked": sum(not d["obligation_id"] for d in out)})
    return result
