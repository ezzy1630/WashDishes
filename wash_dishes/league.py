"""Weekly dish points and immutable week results. Camera lifecycle stays in Store."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import TypedDict

from . import house_verdicts, house_visits
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


def left_out(store: Store, start: datetime, end: datetime, *, visits=None, ids: list[int] | None = None) -> list[dict]:
    """The ONE eligibility predicate for the house league (deadline week, not deposit week).

    Include open/disputed and resolved-after-deadline real dishes with an observation-based deadline and
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
            WHERE (? IS NULL OR o.id IN (SELECT value FROM json_each(?)))
            AND julianday(o.due_at)>=julianday(?) AND julianday(o.due_at)<julianday(?)
            AND julianday(o.due_at)<=julianday(?)
            AND o.lifecycle IN ('open','disputed','resolved')
            AND (o.resolved_at IS NULL OR julianday(o.resolved_at)>julianday(o.due_at))
            AND (o.lifecycle!='resolved' OR o.resolved_at IS NOT NULL)
            AND NOT EXISTS (SELECT 1 FROM house_verdicts v WHERE v.obligation_id=o.id)
            AND (o.overdue_verified=1 OR EXISTS (
                SELECT 1 FROM evidence e WHERE e.obligation_id=o.id
                AND e.kind='snapshot' AND e.label LIKE 'Matching dish still visible after the deadline%'
                AND julianday(e.created_at)>=julianday(o.due_at)
                AND (o.resolved_at IS NULL OR julianday(e.created_at)<=julianday(o.resolved_at))))
            AND NOT EXISTS (SELECT 1 FROM incident_decisions d WHERE d.obligation_id=o.id
                AND (json_extract(d.data,'$.condition') IN ('clean','not_dish')
                     OR json_extract(d.data,'$.checks.later')=0))
            ORDER BY o.due_at DESC,o.id DESC''', (json.dumps(ids) if ids is not None else None, json.dumps(ids), stamp(start), stamp(end), stamp(utcnow()))).fetchall()
        aliases = house_visits.duplicate_map(store, visits if visits is not None else house_visits.catalog(store, [r['id'] for r in rows]))
        return [dict(r) | {'dishes': json.loads(r['dishes'])} for r in rows if r['id'] not in aliases]


def points(dish: dict, rules: dict) -> int:
    if dish.get('duplicate_of') is not None:
        return 0
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
        if dish.get('duplicate_of') is not None or owner is None or not human_named(dish) or dish.get('confidence') != 'confirmed' or dish.get('lifecycle') in ('disputed', 'dismissed') or dish.get('dispute_state') == 'open':
            continue
        owner['points'] += points(dish, rules)
        owner['dishes'] += sum(d['quantity'] for d in dish['dishes'])
    return sorted(rows.values(), key=lambda r: (-r['points'], r['name'].casefold(), r['resident_id']))


def pick(rows: Standings, rules: dict) -> dict:
    """One decision for all views; needed_* are additional points/lead still required."""
    top = rows[0] if rows else None
    score = top['points'] if top else 0
    second = rows[1]['points'] if len(rows) > 1 else 0
    lead = score - second
    needed_points = max(0, rules['min_points'] - score)
    # Even a zero-lead rule requires a unique leader with at least one point.
    needed_lead = max(0, max(1, rules['min_lead']) - lead)
    point_text = f"{score} {'point' if score == 1 else 'points'}"
    if not rules['enabled']:
        state, reason = 'disabled', 'Standings still count.'
        needed_points = needed_lead = 0
    elif not score or needed_points:
        state = 'below_minimum'
        reason = f"{point_text if score else 'No named points'} · needs {rules['min_points']}"
    elif not lead:
        state, reason = 'tie', f'Tied for first · needs a {needed_lead}-point lead'
    elif needed_lead:
        state = 'too_close'
        reason = f"Too close — {lead} {'point' if lead == 1 else 'points'} ahead, needs {rules['min_lead']}"
    else:
        state, reason = 'pick', f'{point_text} · {lead} ahead'
    return {'resident_id': top['resident_id'] if state == 'pick' else None,
            'name': top['name'] if state == 'pick' else None, 'state': state, 'reason': reason,
            'points': score, 'lead': lead, 'needed_points': needed_points, 'needed_lead': needed_lead}


def carry_duty(current: dict | None, chosen: dict, rules: dict, *, since: str) -> dict | None:
    """A no-pick week retains duty; switching the league off clears it."""
    if not rules['enabled']:
        return None
    if chosen['resident_id'] is None:
        return current
    if current and current['resident_id'] == chosen['resident_id']:
        return current
    return {k: chosen[k] for k in ('resident_id', 'name')} | {'since': since, 'why': f"Weekly pick: {chosen['reason'].rstrip('.')}."}


def start_week(store: Store, *, now: datetime | None = None) -> str:
    """Initialize once, independently of the replaceable rules JSON."""
    with store.lock:
        row = store.conn.execute('SELECT start_week FROM league_start WHERE id=1').fetchone()
        if row:
            return row[0]
        # Join an existing transaction without committing its caller's pending writes.
        pending = store.conn.in_transaction
        store.conn.execute('INSERT OR IGNORE INTO league_start VALUES(1,?)', (week_bounds(now=now)['start'],))
        if not pending:
            store.conn.commit()
        return store.conn.execute('SELECT start_week FROM league_start WHERE id=1').fetchone()[0]


def rules(store: Store) -> dict:
    with store.lock:
        start_week(store)
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
    start, end = week_times(week)
    week_ids = [r[0] for r in store.conn.execute('SELECT id FROM obligations WHERE julianday(due_at)>=julianday(?) AND julianday(due_at)<julianday(?)', (stamp(start), stamp(end)))]
    catalog = house_visits.catalog(store, week_ids)
    dishes = left_out(store, start, end, visits=catalog)
    ids = {d['id'] for d in dishes}
    aliases = house_visits.duplicate_map(store, catalog)
    groups = [g for g in catalog if ids.intersection(g['dish_ids'])]
    return {'week': week, 'rules': settings, 'dishes': dishes,
            'residents': store.residents(), 'visit_groups': groups, 'duplicates': {oid: target for oid, target in aliases.items() if target in ids},
            'alias_guesses': {oid: store.obligation(oid)['suggested_resident_id']
                              for oid, target in aliases.items() if target in ids},
            'verdicts': house_verdicts.history(store, *week_times(week))}


def close_weeks(store: Store, *, now: datetime | None = None) -> None:
    """Close in order, once, at local Monday noon; catch up after a stopped Jetson."""
    now = now or utcnow()
    with store.lock, store.conn:
        # The reset CLI uses another connection. Hold the write transaction from
        # the boundary read onward so a close cannot recreate weeks it just removed.
        if not store.conn.in_transaction:
            store.conn.execute('BEGIN IMMEDIATE')
        first = date.fromisoformat(start_week(store, now=now))
        settings = rules(store)
        current = duty(store)
        while True:
            week = week_bounds(first.isoformat(), now=now)
            if parse_stamp(week['closes_at']) > now:
                break
            if not store.conn.execute('SELECT 1 FROM league_weeks WHERE start=?', (week['start'],)).fetchone():
                snapshot = project(_live(store, week))
                # Persist names and audit/media metadata too, so later edits cannot rewrite history.
                snapshot['residents'] = [{'id': r['id'], 'name': r['name']} for r in store.residents()]
                ids = [d['id'] for d in snapshot['dishes']]
                snapshot['guess_basis'] = store.suggestion_basis(ids)
                snapshot['evidence'] = [dict(r) for r in store.conn.execute(
                    f"""SELECT * FROM evidence WHERE kind IN ('snapshot','filmstrip') AND (
                        obligation_id IN ({','.join('?' for _ in ids)}) OR id IN (
                        SELECT v.evidence_id FROM arrival_visits v JOIN arrival_visit_members m ON m.visit_id=v.id
                        WHERE m.obligation_id IN ({','.join('?' for _ in ids)})) OR (kind='filmstrip' AND label='arrival'
                        AND EXISTS (SELECT 1 FROM json_each(evidence.capture,'$.obligation_ids')
                            WHERE value IN ({','.join('?' for _ in ids)})))) ORDER BY created_at,id""", [*ids, *ids, *ids])] if ids else []
                snapshot['week']['closed'] = True
                current = carry_duty(current, snapshot['pick'], settings, since=week['closes_at'])
                snapshot['on_duty'] = current
                store.conn.execute('INSERT INTO league_weeks VALUES(?,?,?,?,?)',
                                   (week['start'], week['end'], week['closes_at'], stamp(now), json.dumps(snapshot)))
                _save_duty(store, current)
                store._event(None, 'league_week_closed', f"Week of {week['start']} closed. {snapshot['pick']['reason']}",
                             {'start': week['start'], 'pick': snapshot['pick']}, at=now)
            first += timedelta(days=7)


def snapshot(store: Store, start: str | None = None, *, now: datetime | None = None) -> dict:
    """Copy one week's inputs; callers can project/format after releasing Store.lock."""
    now = now or utcnow()
    week = week_bounds(start, now=now)
    if week_times(week)[0] > now:
        raise ValueError('That week has not started yet.')
    with store.lock:
        close_weeks(store, now=now)
        beginning = start_week(store, now=now)
        if week['start'] < beginning:
            raise ValueError(f"That week is before the league started ({beginning}).")
        row = store.conn.execute('SELECT snapshot FROM league_weeks WHERE start=?', (week['start'],)).fetchone()
        value = json.loads(row[0]) if row else _live(store, week)
        # The duty banner always answers who is on duty now, also when browsing history.
        return value | {'on_duty': duty(store) if rules(store)['enabled'] else None,
                        'start_week': beginning}


def project(value: dict) -> dict:
    """Pure scoring/display projection; frozen standings and rules stay authoritative."""
    if 'standings' not in value:
        board = standings(value['dishes'], value['residents'], value['rules'])
        return value | {'standings': board, 'pick': pick(board, value['rules'])}
    if 'state' not in value['pick']:
        return value | {'pick': pick(value['standings'], value['rules'])}
    return value


def result(store: Store, start: str | None = None, *, now: datetime | None = None) -> dict:
    return project(snapshot(store, start, now=now))


def assert_unfrozen(store: Store, item: dict, *, now: datetime | None = None) -> None:
    due = item.get('due_at') or item.get('followup_at')
    if not due:
        return
    week = week_bounds(now=parse_stamp(due))
    beginning = store.conn.execute('SELECT start_week FROM league_start WHERE id=1').fetchone()
    if not beginning or week['start'] < beginning[0]:
        return
    frozen = store.conn.execute('SELECT 1 FROM league_weeks WHERE start=?', (week['start'],)).fetchone()
    # Once the league starts, operator edits also respect noon between worker ticks.
    if frozen or parse_stamp(week['closes_at']) <= (now or utcnow()):
        raise ValueError('That week is closed. Its names, disputes and standings are frozen.')


def reset_start(conn: sqlite3.Connection, *, dry_run: bool = False, now: datetime | None = None) -> dict:
    """Explicit repair only. The CLI opens dry runs read-only, without Store migrations."""
    now = now or utcnow()
    week = week_bounds(now=now)
    with conn:
        # Serialize the read/plan/write sequence against worker closes and rule changes.
        conn.execute('BEGIN' if dry_run else 'BEGIN IMMEDIATE')
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='league_start'").fetchone()
        row = conn.execute('SELECT start_week FROM league_start WHERE id=1').fetchone() if table else None
        previous = row[0] if row else None
        deleted = [r[0] for r in conn.execute('SELECT start FROM league_weeks WHERE start<? ORDER BY start', (week['start'],))]
        row = conn.execute('SELECT data FROM league_duty WHERE id=1').fetchone()
        current = json.loads(row[0]) if row and row[0] else None
        cleared = current if (deleted and current and current['why'].startswith('Weekly pick')
                              and parse_stamp(current['since']).astimezone(timezone()).date().isoformat() <= week['start']) else None
        report = {'dry_run': dry_run, 'previous_start_week': previous, 'start_week': week['start'],
                  'deleted_weeks': deleted, 'cleared_duty': cleared,
                  'changed': previous != week['start'] or bool(deleted) or cleared is not None}
        if not dry_run and report['changed']:
            conn.execute('CREATE TABLE IF NOT EXISTS league_start(id INTEGER PRIMARY KEY CHECK(id=1), start_week TEXT NOT NULL)')
            conn.execute('INSERT INTO league_start VALUES(1,?) ON CONFLICT(id) DO UPDATE SET start_week=excluded.start_week', (week['start'],))
            conn.execute('DELETE FROM league_weeks WHERE start<?', (week['start'],))
            if cleared is not None:
                conn.execute('UPDATE league_duty SET data=? WHERE id=1', (json.dumps(None),))
            conn.execute('INSERT INTO events(obligation_id,created_at,event,description,data) VALUES(NULL,?,?,?,?)',
                         (stamp(now), 'league_start_reset', f"League start reset to {week['start']}.", json.dumps(report)))
        return report


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
