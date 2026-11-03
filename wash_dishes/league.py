"""Weekly dish points and immutable week results. Camera lifecycle stays in Store."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import TypedDict

from . import house_verdicts
from .notify import timezone
from .store import Store, parse_stamp, stamp, utcnow

DEFAULT_RULES = {'enabled': True, 'weights': {'pan': 2, 'pot': 2, 'default': 1}, 'min_points': 4, 'min_lead': 2}


class Standing(TypedDict):
    resident_id: int
    name: str
    points: int
    dishes: int


Standings = list[Standing]


def week_bounds(start: str | None = None, *, now: datetime | None = None) -> dict:
    tz = timezone()
    today = (now or utcnow()).astimezone(tz).date()
    day = date.fromisoformat(start) if start else today - timedelta(days=today.weekday())
    if day.weekday() != 0 or (start and day.isoformat() != start):
        raise ValueError('Choose the Monday that starts the week (YYYY-MM-DD).')
    first = datetime.combine(day, time(), tz)
    end = first + timedelta(days=7)
    # Calendar arithmetic in the house zone, not 168 UTC hours: DST weeks are shorter/longer.
    return {'start': day.isoformat(), 'end': end.date().isoformat(),
            'closes_at': stamp(end + timedelta(hours=12)), 'closed': False, 'timezone': str(tz)}


def week_times(week: dict) -> tuple[datetime, datetime]:
    tz = timezone()
    return tuple(datetime.combine(date.fromisoformat(week[k]), time(), tz) for k in ('start', 'end'))


def left_out(store: Store, start: datetime, end: datetime) -> list[dict]:
    """The ONE eligibility predicate for the house league (deadline week, not deposit week).

    Include open/disputed and resolved-after-deadline real dishes with known timing and
    positive after-deadline observation: overdue_verified OR the matching late photo used
    by Store's confirm path for unnamed records. Elapsed timers/last_seen alone never
    establish lateness. Exclude dismissed, cleared-before-deadline and unverified records.
    Disputes remain visible here but scoring excludes them. Feed archiving is cosmetic.
    Eligibility is only a review queue, NOT guilt. A dish scores ONLY after a human
    names it (an operator's manual record or an explicit confirmation). Camera
    guesses AND automatic camera names contribute zero, even with a late photo.
    Non-dish/clean/duplicate human verdicts exclude records for everyone.
    Lane A retains gone unnamed late records as resolved; removal does not erase points.
    """
    with store.lock:
        rows = store.conn.execute('''SELECT o.*, EXISTS(SELECT 1 FROM events n WHERE n.obligation_id=o.id
                AND n.event IN ('confirm','reassign','house_named')) human_named FROM obligations o
            WHERE julianday(o.due_at)>=julianday(?) AND julianday(o.due_at)<julianday(?)
            AND julianday(o.due_at)<=julianday(?) AND o.timing_known=1
            AND o.lifecycle IN ('open','disputed','resolved')
            AND (o.resolved_at IS NULL OR julianday(o.resolved_at)>julianday(o.due_at))
            AND (o.lifecycle!='resolved' OR o.resolved_at IS NOT NULL)
            AND NOT EXISTS (SELECT 1 FROM house_verdicts v WHERE v.obligation_id=o.id)
            AND (o.overdue_verified=1 OR EXISTS (
                SELECT 1 FROM evidence e WHERE e.obligation_id=o.id
                AND e.label LIKE 'Matching dish still visible after the deadline%'
                AND julianday(e.created_at)>=julianday(o.due_at)))
            AND NOT EXISTS (SELECT 1 FROM incident_decisions d WHERE d.obligation_id=o.id
                AND (json_extract(d.data,'$.condition') IN ('clean','not_dish')
                     OR json_extract(d.data,'$.checks.later')=0))
            ORDER BY o.due_at DESC,o.id DESC''', (stamp(start), stamp(end), stamp(utcnow()))).fetchall()
        return [dict(r) | {'dishes': json.loads(r['dishes'])} for r in rows]


def points(dish: dict, rules: dict) -> int:
    return sum(rules['weights'].get(d['kind'], rules['weights']['default']) * d['quantity'] for d in dish['dishes'])


def human_named(dish: dict) -> bool:
    """A camera guess/name never substitutes for a person's responsibility decision."""
    return dish.get('attribution') != 'camera' and bool(dish.get('named_at') or dish.get('human_named')
        or (dish.get('source') in ('manual', 'demo') and dish.get('resident_id') is not None and dish.get('confidence') == 'confirmed'))


def standings(dishes: list[dict], residents: list[dict], rules: dict) -> Standings:
    """Pure scoring over eligible records; an open dispute contributes zero."""
    rows = {r['id']: Standing(resident_id=r['id'], name=r['name'], points=0, dishes=0)
            for r in residents if r.get('active', True)}
    for dish in dishes:
        owner = rows.get(dish.get('resident_id'))
        if owner is None or not human_named(dish) or dish.get('confidence') != 'confirmed' or dish.get('lifecycle') in ('disputed', 'dismissed') or dish.get('dispute_state') == 'open':
            continue
        owner['points'] += points(dish, rules)
        owner['dishes'] += sum(d['quantity'] for d in dish['dishes'])
    return sorted(rows.values(), key=lambda r: (-r['points'], r['name'].casefold(), r['resident_id']))


def pick(rows: Standings, rules: dict) -> dict:
    no_pick = {'resident_id': None, 'name': None}
    if not rules['enabled']:
        return no_pick | {'reason': 'Kitchen duty is switched off by the house.'}
    if not rows or rows[0]['points'] == 0:
        return no_pick | {'reason': 'No one this week: no named points.'}
    top = rows[0]
    second = rows[1]['points'] if len(rows) > 1 else 0
    if top['points'] < rules['min_points']:
        return no_pick | {'reason': f"No one this week: nobody reached {rules['min_points']} points."}
    if top['points'] == second or top['points'] - second < rules['min_lead']:
        return no_pick | {'reason': f"No one this week: the lead must be at least {rules['min_lead']} points, with no tie."}
    return {'resident_id': top['resident_id'], 'name': top['name'],
            'reason': f"{top['name']} is the pick for next week's kitchen duty with {top['points']} points, {top['points'] - second} ahead."}


def carry_duty(current: dict | None, chosen: dict, rules: dict, *, since: str) -> dict | None:
    """A no-pick week retains duty; switching the league off clears it."""
    if not rules['enabled']:
        return None
    if chosen['resident_id'] is None:
        return current
    if current and current['resident_id'] == chosen['resident_id']:
        return current
    return {k: chosen[k] for k in ('resident_id', 'name')} | {'since': since, 'why': chosen['reason']}


def rules(store: Store) -> dict:
    row = store.conn.execute('SELECT data FROM league_rules WHERE id=1').fetchone()
    return json.loads(row[0]) if row else json.loads(json.dumps(DEFAULT_RULES))


def duty(store: Store) -> dict | None:
    row = store.conn.execute('SELECT data FROM league_duty WHERE id=1').fetchone()
    return json.loads(row[0]) if row and row[0] else None


def _save_duty(store: Store, value: dict | None) -> None:
    store.conn.execute('INSERT INTO league_duty VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (json.dumps(value),))


def _live(store: Store, week: dict) -> dict:
    settings = rules(store)
    dishes = left_out(store, *week_times(week))
    board = standings(dishes, store.residents(), settings)
    return {'week': week, 'rules': settings, 'standings': board, 'pick': pick(board, settings), 'dishes': dishes,
            'verdicts': house_verdicts.history(store, *week_times(week))}


def close_weeks(store: Store, *, now: datetime | None = None) -> None:
    """Close in order, once, at local Monday noon; catch up after a stopped Jetson."""
    now = now or utcnow()
    with store.lock, store.conn:
        earliest = store.conn.execute('SELECT min(due_at) FROM obligations WHERE timing_known=1').fetchone()[0]
        first = date.fromisoformat(week_bounds(now=now)['start']) - timedelta(weeks=7)
        if earliest:
            oldest = parse_stamp(earliest).astimezone(timezone()).date()
            oldest -= timedelta(days=oldest.weekday())
            first = min(first, oldest)
        settings = rules(store)
        current = duty(store)
        while True:
            week = week_bounds(first.isoformat(), now=now)
            if parse_stamp(week['closes_at']) > now:
                break
            if not store.conn.execute('SELECT 1 FROM league_weeks WHERE start=?', (week['start'],)).fetchone():
                snapshot = _live(store, week)
                # Persist names and audit/media metadata too, so later edits cannot rewrite history.
                snapshot['residents'] = [{'id': r['id'], 'name': r['name']} for r in store.residents()]
                ids = [d['id'] for d in snapshot['dishes']]
                snapshot['guess_basis'] = store.suggestion_basis(ids)
                snapshot['evidence'] = [dict(r) for r in store.conn.execute(
                    f"""SELECT * FROM evidence WHERE kind IN ('snapshot','filmstrip') AND (
                        obligation_id IN ({','.join('?' for _ in ids)}) OR (kind='filmstrip' AND label='arrival'
                        AND EXISTS (SELECT 1 FROM json_each(evidence.capture,'$.obligation_ids')
                            WHERE value IN ({','.join('?' for _ in ids)})))) ORDER BY created_at,id""", [*ids, *ids])] if ids else []
                snapshot['week']['closed'] = True
                current = carry_duty(current, snapshot['pick'], settings, since=week['closes_at'])
                snapshot['on_duty'] = current
                store.conn.execute('INSERT INTO league_weeks VALUES(?,?,?,?,?)',
                                   (week['start'], week['end'], week['closes_at'], stamp(now), json.dumps(snapshot)))
                _save_duty(store, current)
                store._event(None, 'league_week_closed', f"Week of {week['start']} closed. {snapshot['pick']['reason']}",
                             {'start': week['start'], 'pick': snapshot['pick']}, at=now)
            first += timedelta(days=7)


def result(store: Store, start: str | None = None, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    week = week_bounds(start, now=now)
    if week_times(week)[0] > now:
        raise ValueError('That week has not started yet.')
    with store.lock:
        close_weeks(store, now=now)
        row = store.conn.execute('SELECT snapshot FROM league_weeks WHERE start=?', (week['start'],)).fetchone()
        value = json.loads(row[0]) if row else _live(store, week)
        # The duty banner always answers who is on duty now, also when browsing history.
        return value | {'on_duty': duty(store) if rules(store)['enabled'] else None}


def assert_unfrozen(store: Store, item: dict, *, now: datetime | None = None) -> None:
    if not item.get('timing_known') or not item.get('due_at'):
        return
    week = week_bounds(now=parse_stamp(item['due_at']))
    frozen = store.conn.execute('SELECT 1 FROM league_weeks WHERE start=?', (week['start'],)).fetchone()
    # Once the league starts, operator edits also respect noon between worker ticks.
    initialized = store.conn.execute('SELECT 1 FROM league_weeks LIMIT 1').fetchone()
    if frozen or (initialized and parse_stamp(week['closes_at']) <= (now or utcnow())):
        raise ValueError('That week is closed. Its names, disputes and standings are frozen.')


def configure(store: Store, settings: dict, *, now: datetime | None = None) -> dict:
    with store.lock, store.conn:
        close_weeks(store, now=now)
        store.conn.execute('INSERT INTO league_rules VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (json.dumps(settings),))
        if not settings['enabled']:
            _save_duty(store, None)
        store._event(None, 'league_rules_changed', 'Kitchen duty rules updated', settings, at=now)
        return settings


def override(store: Store, resident_id: int | None, *, now: datetime | None = None) -> dict | None:
    now = now or utcnow()
    with store.lock, store.conn:
        close_weeks(store, now=now)
        if resident_id is not None and not rules(store)['enabled']:
            raise ValueError('Switch kitchen duty on before assigning someone.')
        resident = store._resident(resident_id)
        value = ({'resident_id': resident['id'], 'name': resident['name'], 'since': stamp(now),
                  'why': 'Set by the operator after a house vote.'} if resident else None)
        _save_duty(store, value)
        store._event(None, 'league_duty_override', 'Kitchen duty set by the operator after a house vote.',
                     {'resident_id': resident_id}, at=now)
        return value
