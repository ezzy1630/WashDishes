from datetime import timedelta

import pytest

from wash_dishes.incidents import outcome
from wash_dishes.store import MISSING_NOTE, UNCLAIMED_NOTE, UNPROVABLE_NOTE, parse_stamp, stamp, utcnow

LATE = 'Matching dish still visible after the deadline; dirty status requires review'


def dish(store, *, timing_known=False, age=4, resident=None):
    return store.create_obligation(resident, [{'kind': 'pan', 'quantity': 1}], candidate=resident is None,
        source='vision', timing_known=timing_known, at=utcnow() - timedelta(hours=age))


def photo(store, item, *, at=None):
    at = at or parse_stamp(item['followup_at']) + timedelta(seconds=1)
    store.add_evidence(item['id'], f"late-{item['id']}", 'late.jpg', 'snapshot', LATE, at=at,
                       capture={'stage': 'later', 'box': [.3, .5, .1, .1]})
    return at


@pytest.mark.parametrize('timing_known', [False, True])
def test_unnamed_left_out_episode_survives_removal(store, timing_known):
    item = dish(store, timing_known=timing_known)
    at = photo(store, item)
    store.record_seen(item['id'], at)
    if timing_known:
        store.note_gone(item['id'], 'Stove')
        store.scan_deadlines()
    else:
        assert store.close_unclaimed(item['id'])
    saved = store.obligation(item['id'])
    assert saved['lifecycle'] == 'resolved' and saved['left_out_at'] == stamp(at)
    assert saved['resident_id'] is None and saved['cleaner_id'] is None
    assert outcome(saved) == 'removed'
    assert saved['closed_by_camera'] and store.cleaned_today() == 0


def test_early_startup_removal_keeps_existing_dismissal_and_never_teaches_not_a_dish(store):
    item = dish(store)
    sample = store.add_dish_sample([1, 0], .8)
    store.link_dish_sample(sample, item['id'])
    assert store.close_unclaimed(item['id'], at=parse_stamp(item['created_at']) + timedelta(minutes=20))
    saved = store.obligation(item['id'])
    assert saved['lifecycle'] == 'dismissed' and saved['reason'] == UNCLAIMED_NOTE
    assert store.labeled_dish_samples() == []


@pytest.mark.parametrize('gap', [True, False])
def test_elapsed_or_unverified_deadline_does_not_become_left_out(store, gap):
    item = dish(store)
    if gap:
        with store.lock, store.conn:
            store._event(None, 'observation_gap', 'Camera stopped', at=parse_stamp(item['created_at']) + timedelta(minutes=15))
        photo(store, item)
        store.record_seen(item['id'], parse_stamp(item['followup_at']) + timedelta(minutes=2))
    store.close_unclaimed(item['id'])
    saved = store.obligation(item['id'])
    assert saved['lifecycle'] == 'dismissed' and saved['left_out_at'] is None
    assert not saved['overdue_verified']


@pytest.mark.parametrize('note', [UNCLAIMED_NOTE, UNPROVABLE_NOTE])
def test_repair_is_dry_by_default_idempotent_and_excludes_human_dismissals(store, note):
    item, early, human, unverified = [dish(store) for _ in range(4)]
    photo(store, item)
    photo(store, human)
    with store.lock, store.conn:
        store._event(unverified['id'], 'dish_not_seen', 'Not visible after a gap', at=parse_stamp(unverified['created_at']) + timedelta(minutes=15))
    photo(store, unverified)
    for entry, reason in ((item, note), (early, note), (human, 'Not a dish'), (unverified, note)):
        store.action(entry['id'], 'dismiss', note=reason)
    sample = store.add_dish_sample([1, 0], .9)
    store.link_dish_sample(sample, item['id'])
    before = store.conn.total_changes
    proposed = store.repair_left_out()
    assert store.conn.total_changes == before
    assert proposed['counts'] == {'restored_left_out': 1, 'stale_left_out': 0, 'stale_missing': 0}
    applied = store.repair_left_out(dry_run=False)
    assert proposed['changes'] == applied['changes']
    saved = store.obligation(item['id'])
    assert saved['lifecycle'] == 'resolved' and saved['left_out_at']
    assert store.labeled_dish_samples() == []
    assert all(store.obligation(i['id'])['lifecycle'] == 'dismissed' for i in (early, human, unverified))
    assert store.repair_left_out(dry_run=False)['changes'] == []


def test_stale_named_archived_and_backlog_close_without_inventing_lateness(store):
    resident = store.add_resident('Alex')['id']
    items = [dish(store, age=72, resident=resident), dish(store, age=72), dish(store, age=72, timing_known=True)]
    with store.lock, store.conn:
        store.conn.execute('UPDATE obligations SET archived_at=?', (stamp(),))
    assert store.repair_left_out(since=utcnow()-timedelta(days=1))['counts']['stale_missing'] == 3
    store.scan_deadlines()
    for item in items:
        saved = store.obligation(item['id'])
        assert saved['lifecycle'] == 'resolved' and saved['reason'] == MISSING_NOTE
        assert saved['left_out_at'] is None and not saved['overdue_verified']
        assert outcome(saved) == 'unknown'
        assert saved['evidence_timeline']['stages'][-1]['state'] == 'missing'
    # Naming an unknown outcome later cannot manufacture a late washing time.
    named = store.action(items[1]['id'], 'confirm', resident_id=resident)
    assert not named['overdue_verified'] and named['reason'] == MISSING_NOTE
    assert store.cleaned_today() == 0


def test_stale_closure_preserves_existing_observed_lateness_and_never_trains_negative(store):
    item = dish(store, age=72, timing_known=True)
    photo(store, item)
    sample = store.add_dish_sample([.2, .8], .9)
    store.link_dish_sample(sample, item['id'])
    store.scan_deadlines()
    saved = store.obligation(item['id'])
    assert saved['left_out_at'] and saved['lifecycle'] == 'resolved'
    assert store.labeled_dish_samples() == []


def test_legacy_retirement_without_dismiss_event_is_not_negative_training(store):
    item = dish(store)
    sample = store.add_dish_sample([1, 0], .8)
    store.link_dish_sample(sample, item['id'])
    with store.lock, store.conn:
        store.conn.execute("UPDATE obligations SET lifecycle='dismissed',reason=? WHERE id=?", (UNPROVABLE_NOTE, item['id']))
    assert store.labeled_dish_samples() == []


def test_naming_with_a_note_cannot_turn_missing_into_washed_or_an_on_time_success(store):
    item = dish(store,age=72,timing_known=True)
    store.scan_deadlines()
    alex = store.add_resident('Alex')['id']
    saved = store.action(item['id'],'confirm',resident_id=alex,note='Recognized the person from the photo')
    assert saved['closure_kind']=='missing' and outcome(saved)=='unknown'
    assert not saved['overdue_verified'] and saved['left_out_at'] is None
    assert store.cleaned_today()==0
    assert store.statistics()[0]['on_time']==0
    store.action(item['id'],'resolve',cleaner_id=alex)
    assert store.obligation(item['id'])['closure_kind'] is None


def test_post_deadline_pickup_without_a_post_deadline_sighting_stays_unverified(store):
    item=dish(store,timing_known=True)
    with store.lock,store.conn:
        store._event(item['id'],'dish_picked_up','Taken away',at=parse_stamp(item['followup_at'])+timedelta(minutes=10))
    store.scan_deadlines()
    saved=store.obligation(item['id'])
    assert saved['lifecycle']=='resolved'
    assert saved['left_out_at'] is None and not saved['overdue_verified']
    assert store.observed_left_out_at(saved) is None
    assert not store.repair_left_out(dry_run=False)['changes']
