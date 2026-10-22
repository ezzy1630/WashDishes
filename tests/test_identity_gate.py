import json
from datetime import timedelta

from wash_dishes.store import stamp, utcnow


def card(store, truth, guess, *, captured=None, ready=True, vector=None):
    at = captured or utcnow()
    item = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], source='vision', candidate=True, at=at)
    store.add_person_sample(item['id'], vector or [float(item['id']), 1], at=at)
    store.set_suggestion(item['id'], guess, 3)
    if ready:
        with store.conn:
            store.conn.execute('CREATE TABLE IF NOT EXISTS proofs(obligation_id INTEGER PRIMARY KEY,status TEXT,created_at TEXT,data TEXT)')
            store.conn.execute("INSERT INTO proofs VALUES(?,'ready',?,'{}')", (item['id'], stamp()))
    store.action(item['id'], 'confirm', resident_id=truth)
    return item


def test_gate_does_not_count_unshown_or_legacy_mutable_scores(store):
    alex = store.add_resident('Alex')['id']
    card(store, alex, alex, ready=False)
    assert store.attribution_gate()['checked'] == 0
    item = card(store, alex, alex)
    with store.conn:
        store.conn.execute("DELETE FROM events WHERE obligation_id=? AND event='identity_prediction'", (item['id'],))
    assert store.attribution_gate()['checked'] == 0


def test_gate_uses_first_checked_prediction_not_a_later_rescore(store):
    alex, sam = [store.add_resident(n)['id'] for n in ('Alex', 'Sam')]
    item = card(store, alex, sam)
    store.set_suggestion(item['id'], alex, 6)
    with store.conn:
        store.conn.execute('UPDATE obligations SET suggestion_correct=1 WHERE id=?', (item['id'],))
    assert store.attribution_gate()['checked'] == 1
    assert store.attribution_gate()['correct'] == 0
    events = store.conn.execute("SELECT data FROM events WHERE event='identity_prediction' ORDER BY id").fetchall()
    assert json.loads(events[0][0])['resident_id'] == sam


def test_gate_deduplicates_identical_captures_and_same_person_visit(store):
    alex, sam = [store.add_resident(n)['id'] for n in ('Alex', 'Sam')]
    now = utcnow()
    card(store, alex, alex, captured=now - timedelta(hours=4), vector=[1, 0])
    card(store, alex, sam, captured=now - timedelta(hours=3), vector=[1, 0])
    card(store, alex, alex, captured=now - timedelta(hours=3, minutes=-5), vector=[0, 1])
    gate = store.attribution_gate()
    assert gate['checked'] == 1 and gate['correct'] == 0
    card(store, alex, alex, captured=now - timedelta(hours=1), vector=[.2, .8])
    assert store.attribution_gate()['checked'] == 2


def test_gate_expires_old_captures_and_ignores_cards_ready_only_after_check(store):
    alex = store.add_resident('Alex')['id']
    card(store, alex, alex, captured=utcnow() - timedelta(days=31))
    item = card(store, alex, alex)
    with store.conn:
        store.conn.execute('UPDATE proofs SET created_at=? WHERE obligation_id=?', (stamp(utcnow() + timedelta(minutes=1)), item['id']))
    assert store.attribution_gate()['checked'] == 0
