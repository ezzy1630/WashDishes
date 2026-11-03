from copy import deepcopy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from wash_dishes import league
from wash_dishes.store import parse_stamp, stamp, utcnow


def dish(owner=1, *, kind='plate', quantity=1, **patch):
    return dict(resident_id=owner, confidence='confirmed', lifecycle='open', human_named=True,
                dishes=[{'kind': kind, 'quantity': quantity}], **patch)


PEOPLE = [{'id': 1, 'name': 'Zoe', 'active': True}, {'id': 2, 'name': 'Alex', 'active': True},
          {'id': 3, 'name': 'Bo', 'active': False}]


@pytest.mark.parametrize('first,second,expected', [
    (0, 0, None), (3, 0, None), (4, 0, 1), (4, 2, 1), (4, 3, None),
    (4, 4, None), (8, 8, None), (5, 3, 1), (2, 4, 2), (0, 4, 2),
])
def test_pick_thresholds_ties_and_exact_lead(first, second, expected):
    rows = league.standings([dish(1, quantity=first), dish(2, quantity=second)], PEOPLE, league.DEFAULT_RULES)
    assert league.pick(rows, league.DEFAULT_RULES)['resident_id'] == expected


def test_scoring_weights_quantities_disputes_active_residents_and_sorting():
    settings = deepcopy(league.DEFAULT_RULES)
    settings['weights'] = {'pan': 3, 'pot': 4, 'default': 2}
    records = [dish(kind='pan', quantity=2), dish(kind='pot'), dish(kind='cup', quantity=3), dish(2, quantity=8),
               dish(3, quantity=100), dish(None, quantity=100), dish(1, quantity=100, dispute_state='open')]
    records += [dict(dish(), lifecycle='disputed'), dict(dish(), lifecycle='dismissed'), dict(dish(), confidence='candidate')]
    assert league.standings(records, PEOPLE, settings) == [
        {'resident_id': 2, 'name': 'Alex', 'points': 16, 'dishes': 8},
        {'resident_id': 1, 'name': 'Zoe', 'points': 16, 'dishes': 6}]
    assert league.standings([], PEOPLE, settings)[0]['name'] == 'Alex'
    assert league.pick([], settings)['resident_id'] is None


def test_zero_lead_still_requires_a_unique_nonzero_leader_and_disabled_keeps_scores():
    settings = deepcopy(league.DEFAULT_RULES) | {'min_lead': 0, 'min_points': 1}
    rows = league.standings([dish(), dish(2)], PEOPLE, settings)
    assert league.pick(rows, settings)['resident_id'] is None
    rows[0]['points'] += 1
    assert league.pick(rows, settings)['resident_id'] == 2
    settings['enabled'] = False
    assert league.pick(rows, settings)['resident_id'] is None
    assert league.standings([dish(quantity=4)], PEOPLE, settings)[0]['points'] == 4


def test_camera_guesses_and_automatic_names_never_score_without_human_naming():
    guessed = dict(dish(quantity=100), resident_id=None, suggested_resident_id=1, human_named=False)
    camera_named = dict(dish(quantity=100), attribution='camera', human_named=False)
    unproven_confirmed_flag = dict(dish(quantity=100), human_named=False)
    rows = league.standings([guessed, camera_named, unproven_confirmed_flag], PEOPLE, league.DEFAULT_RULES)
    assert sum(r['points'] for r in rows) == 0
    assert league.pick(rows, league.DEFAULT_RULES)['resident_id'] is None
    reviewed = dict(camera_named, attribution='camera_reviewed', human_named=True)
    assert league.standings([reviewed], PEOPLE, league.DEFAULT_RULES)[0]['points'] == 100


def test_empty_week_closes_and_freezes_zero_standings(store):
    store.add_resident('Alex')
    now = utcnow()
    prior = (datetime.fromisoformat(league.week_bounds(now=now)['start']) - timedelta(weeks=2)).date().isoformat()
    league.close_weeks(store, now=league.week_times(league.week_bounds(prior))[0])
    closed = league.result(store, prior, now=now)
    assert closed['week']['closed'] and closed['standings'][0]['points'] == 0
    assert closed['pick']['resident_id'] is None


def test_duty_carries_over_replacement_and_disabled():
    current = {'resident_id': 1, 'name': 'Zoe', 'since': 'then', 'why': 'vote'}
    empty = {'resident_id': None, 'name': None, 'reason': 'No one this week.'}
    chosen = {'resident_id': 2, 'name': 'Alex', 'reason': 'Alex leads.'}
    assert league.carry_duty(current, empty, league.DEFAULT_RULES, since='now') == current
    assert league.carry_duty(current, chosen, league.DEFAULT_RULES, since='now') == {
        'resident_id': 2, 'name': 'Alex', 'since': 'now', 'why': 'Weekly pick: Alex leads.'}
    assert league.carry_duty(current, dict(chosen, resident_id=1), league.DEFAULT_RULES, since='now') == current
    assert league.carry_duty(current, chosen, dict(league.DEFAULT_RULES, enabled=False), since='now') is None


def late_record(store, *, at=None, owner=None, verified=True):
    at = at or utcnow() - timedelta(hours=4)
    item = store.create_obligation(owner, [{'kind': 'pan', 'quantity': 2}], at=at)
    with store.lock, store.conn:
        store.conn.execute('UPDATE obligations SET overdue_verified=? WHERE id=?', (int(verified), item['id']))
    return store.obligation(item['id'])


def test_first_run_does_not_backfill_old_obligations_and_closes_only_start_week(store):
    now = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
    owner = store.add_resident('Alex')['id']
    late_record(store, at=now - timedelta(weeks=10), owner=owner)
    league.close_weeks(store, now=now)
    assert not store.conn.execute('SELECT * FROM league_weeks').fetchall()
    assert league.duty(store) is None
    league.close_weeks(store, now=now + timedelta(weeks=1))
    assert [r[0] for r in store.conn.execute('SELECT start FROM league_weeks')] == ['2026-11-02']
    assert league.duty(store) is None


def test_rules_first_touch_persists_start_across_reopen(store, monkeypatch):
    from wash_dishes.store import Store
    now = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)  # Still Sunday in the house.
    monkeypatch.setenv('WASHDISHES_TIMEZONE', 'America/Los_Angeles')
    monkeypatch.setattr(league, 'utcnow', lambda: now)
    assert league.rules(store) == league.DEFAULT_RULES
    reopened = Store(store.root)
    try:
        assert league.start_week(reopened, now=now + timedelta(weeks=1)) == '2026-10-26'
    finally:
        reopened.close()


def test_pre_start_week_is_not_a_frozen_league_result_even_with_legacy_rows(store):
    now = utcnow()
    prior = league.week_bounds(now=now - timedelta(weeks=2))
    resident = store.add_resident('Alex')['id']
    item = late_record(store, at=now - timedelta(weeks=2))
    with store.conn:
        store.conn.execute('INSERT INTO league_weeks VALUES(?,?,?,?,?)',
                           (prior['start'], prior['end'], prior['closes_at'], stamp(now), '{}'))
    league.close_weeks(store, now=now)
    assert store.action(item['id'], 'confirm', resident_id=resident)['resident_id'] == resident
    for read in (league.snapshot, league.result):
        with pytest.raises(ValueError, match='before the league started'):
            read(store, prior['start'], now=now)
    assert league.duty(store) is None


def test_left_out_predicate_includes_gone_unknown_but_excludes_unverified_and_in_time(store):
    now = utcnow()
    valid = [late_record(store) for _ in range(4)]
    with store.lock, store.conn:
        store.conn.execute("UPDATE obligations SET lifecycle='resolved',resolved_at=? WHERE id=?", (stamp(now), valid[1]['id']))
        store.conn.execute("UPDATE obligations SET lifecycle='disputed' WHERE id=?", (valid[2]['id'],))
        store.conn.execute("UPDATE obligations SET archived_at=? WHERE id=?", (stamp(now), valid[3]['id']))
    for change in ("lifecycle='dismissed'", "lifecycle='cleared'", 'overdue_verified=0',
                   "lifecycle='resolved',resolved_at=created_at"):
        item = late_record(store)
        with store.lock, store.conn:
            store.conn.execute(f'UPDATE obligations SET {change} WHERE id=?', (item['id'],))
    # Unnamed late photographs can establish lateness before anyone confirms an owner.
    photographed = late_record(store, verified=False)
    store.add_evidence(photographed['id'], 'matching', 'matching.jpg', 'snapshot',
                       'Matching dish still visible after the deadline', at=parse_stamp(photographed['due_at']))
    assert {r['id'] for r in league.left_out(store, now - timedelta(days=1), now)} == {d['id'] for d in valid} | {photographed['id']}
    # Human evidence corrections win over a historical late photograph.
    with store.lock, store.conn:
        store.conn.execute('INSERT INTO incident_decisions VALUES(?,?)', (photographed['id'], '{"condition":"clean"}'))
    assert photographed['id'] not in {r['id'] for r in league.left_out(store, now - timedelta(days=1), now)}


def test_deadline_week_half_open_bounds_not_deposit_week(store):
    tz = ZoneInfo('America/Los_Angeles')
    boundary = datetime(2025, 10, 27, tzinfo=tz)
    # Deposit Sunday night, deadline Monday: belongs to the new week.
    item = late_record(store, at=boundary - timedelta(minutes=90))
    assert parse_stamp(item['due_at']) == boundary
    assert not league.left_out(store, boundary - timedelta(weeks=1), boundary)
    assert [d['id'] for d in league.left_out(store, boundary, boundary + timedelta(weeks=1))] == [item['id']]


@pytest.mark.parametrize('start,hours,close', [
    ('2025-03-03', 167, '2025-03-10T19:00:00+00:00'),
    ('2025-10-27', 169, '2025-11-03T20:00:00+00:00'),
])
def test_house_timezone_dst_and_noon_grace(monkeypatch, start, hours, close):
    monkeypatch.setenv('WASHDISHES_TIMEZONE', 'America/Los_Angeles')
    week = league.week_bounds(start)
    first, end = league.week_times(week)
    assert (end.astimezone(timezone.utc) - first.astimezone(timezone.utc)).total_seconds() == hours * 3600
    assert week['closes_at'] == close
    assert league.week_bounds(now=end - timedelta(seconds=1))['start'] == start
    assert league.week_bounds(now=end)['start'] == week['end']


def test_week_close_grace_freezes_scores_rules_names_pick_and_carries_duty(store, monkeypatch):
    monkeypatch.setenv('WASHDISHES_TIMEZONE', 'America/Los_Angeles')
    resident = store.add_resident('Zoe')
    sunday = datetime(2025, 11, 2, 21, tzinfo=ZoneInfo('America/Los_Angeles'))
    league.close_weeks(store, now=sunday)
    item = late_record(store, at=sunday, owner=resident['id'])
    week = league.week_bounds('2025-10-27')
    closes = parse_stamp(week['closes_at'])
    assert not league.result(store, week['start'], now=closes - timedelta(seconds=1))['week']['closed']
    # Naming during Monday's grace is still accepted while no snapshot exists.
    store.action(item['id'], 'confirm', resident_id=resident['id'], now=closes - timedelta(seconds=1))
    frozen = league.result(store, week['start'], now=closes)
    assert frozen['week']['closed'] and frozen['pick']['name'] == 'Zoe'
    assert frozen['standings'][0]['points'] == 4
    assert frozen['on_duty']['resident_id'] == resident['id']
    with pytest.raises(ValueError, match='closed'):
        store.action(item['id'], 'reassign', resident_id=resident['id'], now=closes)
    with pytest.raises(ValueError, match='closed'):
        store.action(item['id'], 'dispute', now=closes)
    # Out-of-band changes cannot rewrite a snapshot; rule changes affect future closes only.
    with store.lock, store.conn:
        store.conn.execute("UPDATE residents SET name='Changed' WHERE id=?", (resident['id'],))
        store.conn.execute("UPDATE obligations SET lifecycle='dismissed' WHERE id=?", (item['id'],))
    league.configure(store, dict(league.DEFAULT_RULES, min_points=100), now=closes)
    again = league.result(store, week['start'], now=closes)
    assert again['standings'] == frozen['standings']
    assert again['pick'] == frozen['pick'] and again['rules'] == frozen['rules']
    next_week = league.result(store, '2025-11-03', now=closes + timedelta(weeks=1))
    assert next_week['pick']['resident_id'] is None
    assert next_week['on_duty'] == frozen['on_duty']
    # Re-closing is idempotent, including the audit event.
    league.close_weeks(store, now=closes + timedelta(weeks=1))
    assert store.conn.execute("SELECT count(*) FROM events WHERE event='league_week_closed' AND json_extract(data,'$.start') IN ('2025-10-27','2025-11-03')").fetchone()[0] == 2


def test_override_disabled_and_reenable_do_not_resurrect_old_duty(store):
    resident = store.add_resident('Alex')
    assert league.override(store, resident['id'])['name'] == 'Alex'
    league.configure(store, dict(league.DEFAULT_RULES, enabled=False))
    assert league.duty(store) is None
    with pytest.raises(ValueError, match='Switch'):
        league.override(store, resident['id'])
    league.configure(store, league.DEFAULT_RULES)
    assert league.duty(store) is None
    assert league.override(store, None) is None


def test_operator_names_refuse_exact_noon_between_week_closing_ticks(store):
    person = store.add_resident('Alex')
    sunday = datetime(2025, 11, 2, 21, tzinfo=ZoneInfo('America/Los_Angeles'))
    league.close_weeks(store, now=sunday)
    item = late_record(store, at=sunday)
    closes = parse_stamp(league.week_bounds('2025-10-27')['closes_at'])
    league.result(store, '2025-10-27', now=closes - timedelta(seconds=1))
    assert not store.conn.execute("SELECT 1 FROM league_weeks WHERE start='2025-10-27'").fetchone()
    store.action(item['id'], 'confirm', resident_id=person['id'], now=closes - timedelta(seconds=1))
    with pytest.raises(ValueError, match='closed'):
        store.action(item['id'], 'reassign', resident_id=person['id'], now=closes)


def test_non_monday_and_future_week_are_refused(store):
    with pytest.raises(ValueError):
        league.week_bounds('2025-11-04')
    with pytest.raises(ValueError, match='not started'):
        league.result(store, '2099-01-05')


def test_late_photo_requires_matching_still_before_removal(store):
    now = utcnow()
    valid, clip, after_removal = [late_record(store, verified=False) for _ in range(3)]
    for item, kind in ((valid, 'snapshot'), (clip, 'clip'), (after_removal, 'snapshot')):
        store.add_evidence(item['id'], f"late-{item['id']}", 'late.jpg', kind,
                           'Matching dish still visible after the deadline', at=now)
    with store.conn:
        store.conn.execute("UPDATE obligations SET lifecycle='resolved',resolved_at=? WHERE id=?",
                           (stamp(now - timedelta(seconds=30)), after_removal['id']))
    assert [d['id'] for d in league.left_out(store, now - timedelta(days=1), now)] == [valid['id']]


@pytest.mark.parametrize('first,second,enabled,state,needed_points,needed_lead,reason', [
    (6, 2, True, 'pick', 0, 0, '6 points · 4 ahead'),
    (4, 3, True, 'too_close', 0, 1, 'Too close — 1 point ahead, needs 2'),
    (3, 0, True, 'below_minimum', 1, 0, '3 points · needs 4'),
    (4, 4, True, 'tie', 0, 2, 'Tied for first · needs a 2-point lead'),
    (3, 3, True, 'below_minimum', 1, 2, '3 points · needs 4'),
    (0, 0, True, 'below_minimum', 4, 2, 'No named points · needs 4'),
    (6, 2, False, 'disabled', 0, 0, 'Standings still count.'),
])
def test_pick_exposes_shortfalls_and_a_name_free_reason(first, second, enabled, state, needed_points, needed_lead, reason):
    settings = league.DEFAULT_RULES | {'enabled': enabled}
    rows = league.standings([dish(1, quantity=first), dish(2, quantity=second)], PEOPLE, settings)
    chosen = league.pick(rows, settings)
    assert {key: chosen[key] for key in ('state', 'points', 'lead', 'needed_points', 'needed_lead', 'reason')} == {
        'state': state, 'points': first, 'lead': first - second,
        'needed_points': needed_points, 'needed_lead': needed_lead, 'reason': reason}
    assert all(p['name'] not in chosen['reason'] for p in PEOPLE)
    assert (chosen['resident_id'] is not None) == (state == 'pick')


def test_tie_needs_one_point_of_lead_even_when_minimum_lead_is_zero():
    settings = league.DEFAULT_RULES | {'min_lead': 0}
    rows = league.standings([dish(quantity=4), dish(2, quantity=4)], PEOPLE, settings)
    chosen = league.pick(rows, settings)
    assert chosen['state'] == 'tie' and chosen['needed_lead'] == 1
    assert chosen['reason'] == 'Tied for first · needs a 1-point lead'


def test_legacy_closed_pick_is_projected_from_frozen_totals_without_rewriting_snapshot(store):
    import json
    store.add_resident('Zoe')
    now = utcnow()
    prior = (datetime.fromisoformat(league.week_bounds(now=now)['start']) - timedelta(weeks=2)).date().isoformat()
    league.close_weeks(store, now=league.week_times(league.week_bounds(prior))[0])
    league.result(store, prior, now=now)
    snapshot = json.loads(store.conn.execute('SELECT snapshot FROM league_weeks WHERE start=?', (prior,)).fetchone()[0])
    snapshot['standings'][0]['points'] = 6
    snapshot['pick'] = {'resident_id': 1, 'name': 'Zoe', 'reason': "Zoe is the pick for next week's kitchen duty with 6 points, 6 ahead."}
    saved = json.dumps(snapshot)
    with store.lock, store.conn:
        store.conn.execute('UPDATE league_weeks SET snapshot=? WHERE start=?', (saved, prior))
    league.configure(store, league.DEFAULT_RULES | {'min_points': 100}, now=now)
    chosen = league.result(store, prior, now=now)['pick']
    assert chosen['state'] == 'pick' and chosen['points'] == 6 and chosen['needed_points'] == 0
    assert chosen['reason'] == '6 points · 6 ahead'
    assert store.conn.execute('SELECT snapshot FROM league_weeks WHERE start=?', (prior,)).fetchone()[0] == saved
