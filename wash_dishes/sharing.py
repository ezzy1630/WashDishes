"""A finished photo is a presentation of observations, never an automatic accusation."""
from __future__ import annotations

from .store import parse_stamp
from .vision import box_iou


def readiness(item: dict, sequence: dict | None, decision: dict) -> tuple[bool, str]:
    if item['archived_at'] or item['lifecycle'] in ('dismissed', 'disputed') or decision.get('condition') in ('clean', 'not_dish'):
        return False, 'This record is not a dirty-dish photo.'
    if not sequence or sequence.get('stale') or not sequence.get('image_url'):
        return False, 'The saved photos are updating or unavailable.'
    if sequence.get('status') == 'rejected' or any(v is False for v in sequence.get('checks', {}).values()):
        return False, 'A photo in this sequence was marked incorrect.'
    if item['review_pending'] or sequence.get('continuity_events'):
        return False, 'The dish could not be followed reliably between views.'
    if not sequence.get('complete'):
        missing = [s['key'] for s in sequence.get('stages', []) if not s.get('sha256')]
        if set(missing) <= {'later'} and not sequence.get('followup_captured'):
            return False, 'Waiting for the two-hour photo.'
        return False, 'Photos do not clearly box this dish.' if sequence.get('captured', 0) >= 4 else 'Earlier photos are not attached yet.'
    stages = {s['key']:s for s in sequence['stages']}
    left, later = stages['leaving'], stages['later']
    seconds = (parse_stamp(later['at'])-parse_stamp(left['at'])).total_seconds()
    if seconds < 7200 or not sequence.get('followup_captured'):
        return False, 'Waiting for the two-hour photo.'
    if box_iou(left['box'], later['box']) < .3:
        return False, 'The later photo does not reliably match the dish’s position.'
    return True, ''


def render_share(manifest: dict, pictures: dict[str, bytes]) -> bytes:
    from .sequences import render_collage
    return render_collage(manifest, pictures)
