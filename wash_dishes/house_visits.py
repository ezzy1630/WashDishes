"""Persistent arrival naming units, canonical physical dishes, and audit-only aliases."""
from __future__ import annotations

import json


def metadata(evidence: dict) -> dict | None:
    if evidence['kind'] != 'filmstrip' or evidence['label'] != 'arrival':
        return None
    return _metadata(evidence.get('capture', {}), evidence)


def _metadata(capture, filmstrip=None) -> dict | None:
    try:
        capture = json.loads(capture) if isinstance(capture, str) else capture
    except (ValueError, TypeError):
        return None
    if not isinstance(capture, dict):
        return None
    ids = capture.get('obligation_ids')
    visit_id = capture.get('visit_id')
    if (not isinstance(visit_id, (str, int)) or not str(visit_id) or len(str(visit_id)) > 200
            or '/' in str(visit_id) or not isinstance(ids, list) or not ids
            or any(type(oid) is not int or oid <= 0 for oid in ids)):
        return None
    aliases = capture.get('duplicate_of', {})
    if not isinstance(aliases, dict):
        return None
    aliases = {int(k): v for k, v in aliases.items() if str(k).isdigit() and type(v) is int
               and int(k) in ids and v in ids and int(k) != v}
    canonical = capture.get('canonical_obligation_ids', [oid for oid in ids if oid not in aliases])
    if not isinstance(canonical, list) or set(canonical) != set(ids) - set(aliases):
        return None
    return {'id': str(visit_id), 'dish_ids': list(dict.fromkeys(ids)), 'filmstrip': filmstrip,
            'duplicate_of': aliases, 'arrival_seen': capture.get('window', {}).get('arrival_recorded') is True}


def catalog(store, ids: list[int] | None = None) -> list[dict]:
    """Read complete membership in batches, restricted to the requested week's dishes.

    Persistent membership wins over legacy/superseded strips even if media expired.
    Include every member of a matching visit so incomplete visits remain unwritable.
    """
    if ids == []:
        return []
    params = [json.dumps(ids)] if ids is not None else []
    where = "WHERE id IN (SELECT visit_id FROM arrival_visit_members WHERE obligation_id IN (SELECT value FROM json_each(?)))" if ids is not None else ''
    rows = list(store.conn.execute(f'SELECT * FROM arrival_visits {where} ORDER BY created_at DESC,id DESC', params))
    members = {}
    for row in store.conn.execute('SELECT visit_id,obligation_id FROM arrival_visit_members WHERE visit_id IN (SELECT value FROM json_each(?)) ORDER BY obligation_id', (json.dumps([r['id'] for r in rows]),)):
        members.setdefault(row['visit_id'], []).append(row['obligation_id'])
    strips = {r['id']: dict(r) for r in store.conn.execute('SELECT * FROM evidence WHERE id IN (SELECT value FROM json_each(?))', (json.dumps([r['evidence_id'] for r in rows]),))}
    table = []
    for row in rows:
        source = json.loads(row['metadata'])
        if not members.get(row['id']) or set(source['obligation_ids']) != set(members[row['id']]):
            continue
        if group := _metadata(source | {'visit_id': row['id']}, strips.get(row['evidence_id'])):
            table.append(group)
    legacy_where = " AND EXISTS (SELECT 1 FROM json_each(evidence.capture,'$.obligation_ids') WHERE value IN (SELECT value FROM json_each(?)))" if ids is not None else ''
    legacy = [group for row in store.conn.execute(
        "SELECT * FROM evidence WHERE kind='filmstrip' AND label='arrival'" + legacy_where + " ORDER BY created_at DESC,id DESC", params)
        if (group := metadata(dict(row)))]
    selected, assigned, visit_ids = [], set(), set()
    for group in [*table, *legacy]:
        if group['id'] in visit_ids or assigned.intersection(group['dish_ids']):
            continue
        selected.append(group)
        assigned.update(group['dish_ids'])
        visit_ids.add(group['id'])
    return selected


def duplicate_map(store, visits=None) -> dict[int, int]:
    aliases = {oid: target for group in (catalog(store) if visits is None else visits)
               for oid, target in group['duplicate_of'].items()}
    # A human's explicit same-dish answer wins over geometric legacy grouping.
    aliases.update({r['obligation_id']: r['of_id'] for r in store.conn.execute(
        "SELECT obligation_id,of_id FROM house_verdicts WHERE verdict='duplicate' AND of_id IS NOT NULL")})
    aliases.update({r['obligation_id']: r['of_id'] for r in store.conn.execute(
        "SELECT obligation_id,of_id FROM dish_reviews WHERE verdict='duplicate' AND of_id IS NOT NULL")})
    for oid in aliases:
        target, seen = aliases[oid], {oid}
        while target in aliases and target not in seen:
            seen.add(target)
            target = aliases[target]
        if target in seen:
            raise ValueError('Duplicate dishes form a cycle; correct the same-dish answers before scoring.')
        aliases[oid] = target
    return aliases


def groups(dishes: list[dict], evidence: list[dict], *, visits=None, duplicates=None) -> list[dict]:
    ids = {d['id'] for d in dishes}
    duplicates = duplicates or {}
    # JSON snapshots stringify integer keys.
    duplicates = {int(k): v for k, v in duplicates.items()}
    assigned, result = set(), []
    sources = visits if visits is not None else [g for e in reversed(evidence) if (g := metadata(e))]
    for group in sources:
        canonical = [oid for oid in group['dish_ids'] if oid not in duplicates]
        members = [oid for oid in canonical if oid in ids and oid not in assigned]
        if members:
            result.append(group | {'all_dish_ids': canonical, 'dish_ids': members})
            assigned.update(members)
    result += [{'id': f"dish-{d['id']}", 'dish_ids': [d['id']], 'all_dish_ids': [d['id']],
                'filmstrip': None, 'arrival_seen': False}
               for d in dishes if d['id'] not in assigned]
    for group in result:
        group['alias_ids'] = sorted(oid for oid, target in duplicates.items() if target in group['dish_ids'])
        group['duplicate_of'] = {oid: duplicates[oid] for oid in group['alias_ids']}
    order = {d['id']: i for i, d in enumerate(dishes)}
    return sorted(result, key=lambda v: min(order[oid] for oid in v['dish_ids']))


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
            'arrival_seen': group['arrival_seen'], 'alias_ids': group['alias_ids'],
            'aliases': [dict(dishes[target], id=oid, duplicate_of=target, points=0,
                             can_name=False, can_dispute=False, can_settle=False)
                        for oid, target in group['duplicate_of'].items()],
            'filmstrip_url': filmstrip_url, 'photo_url': first['photo_url'],
            **{key: first[key] for key in ('filmstrip_width', 'filmstrip_height', 'photo_width', 'photo_height')},
            'resident': common('resident'), 'named_by': common('named_by'), 'named_at': common('named_at'),
            'guess': guess, 'dispute': common('dispute'),
            **{key: complete and all(d[key] for d in members) for key in ('can_name', 'can_dispute', 'can_settle')}}
