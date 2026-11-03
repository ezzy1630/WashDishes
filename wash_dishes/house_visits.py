"""Arrival filmstrip metadata defines visits; a missing filmstrip means one dish per visit."""
from __future__ import annotations

import json


def metadata(evidence: dict) -> dict | None:
    if evidence['kind'] != 'filmstrip' or evidence['label'] != 'arrival':
        return None
    capture = evidence.get('capture', {})
    capture = json.loads(capture) if isinstance(capture, str) else capture
    ids = capture.get('obligation_ids')
    visit_id = capture.get('visit_id')
    if (not isinstance(visit_id, (str, int)) or not str(visit_id) or len(str(visit_id)) > 200
            or '/' in str(visit_id) or not isinstance(ids, list) or not ids
            or any(type(oid) is not int or oid <= 0 for oid in ids)):
        return None
    return {'id': str(visit_id), 'dish_ids': list(dict.fromkeys(ids)), 'filmstrip': evidence}


def groups(dishes: list[dict], evidence: list[dict]) -> list[dict]:
    ids = {d['id'] for d in dishes}
    assigned = set()
    visits = []
    # A regenerated filmstrip supersedes its earlier row, while every dish belongs to one visit.
    for source in reversed(evidence):
        group = metadata(source)
        if not group:
            continue
        members = [oid for oid in group['dish_ids'] if oid in ids and oid not in assigned]
        if members:
            visits.append(group | {'all_dish_ids': group['dish_ids'], 'dish_ids': members})
            assigned.update(members)
    visits += [{'id': f"dish-{d['id']}", 'dish_ids': [d['id']], 'all_dish_ids': [d['id']], 'filmstrip': None}
               for d in dishes if d['id'] not in assigned]
    order = {d['id']: i for i, d in enumerate(dishes)}
    return sorted(visits, key=lambda v: min(order[oid] for oid in v['dish_ids']))


def title(dishes: list[dict]) -> str:
    counts: dict[str, int] = {}
    for item in dishes:
        for dish in item['dishes']:
            counts[dish['kind']] = counts.get(dish['kind'], 0) + dish['quantity']
    names = [kind if n == 1 else f"{n} {kind}{'es' if kind == 'glass' else '' if kind == 'cutlery' else 's'}" for kind, n in counts.items()]
    text = ' and '.join(names) if len(names) <= 2 else ', '.join(names[:-1]) + ' and ' + names[-1]
    return text[:1].upper() + text[1:]


def serialize(group: dict, raw: dict[int, dict], dishes: dict[int, dict], *, guess: dict | None,
              filmstrip_url: str | None) -> dict:
    members = [dishes[oid] for oid in group['dish_ids']]
    first = members[0]
    def common(key):
        return first[key] if all(d[key] == first[key] for d in members) else None
    complete = set(group['dish_ids']) == set(group['all_dish_ids'])
    return {'id': group['id'], 'dish_ids': group['dish_ids'],
            'title': title([raw[oid] for oid in group['dish_ids']]),
            'area': ', '.join(dict.fromkeys(d['location'] for d in members if d['location'])),
            'at': min(d['out_since'] for d in members), 'points': sum(d['points'] for d in members),
            'filmstrip_url': filmstrip_url or first['filmstrip_url'], 'photo_url': first['photo_url'],
            'resident': common('resident'), 'named_by': common('named_by'), 'named_at': common('named_at'),
            'guess': guess, 'dispute': common('dispute'),
            **{key: complete and all(d[key] for d in members) for key in ('can_name', 'can_dispute', 'can_settle')}}
