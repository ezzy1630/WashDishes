import json
from types import SimpleNamespace

import pytest
from test_house_api import as_person, late, operator
from test_house_api import house_client as house_client
from test_sequences import jpeg

from wash_dishes import filmstrip
from wash_dishes.store import parse_stamp, utcnow

BOX = [.4, .6, .08, .06]
PERSON = {'kind': 'person', 'score': .9, 'box': [.3, .2, .3, .7], 'track_id': 7}


def arrival(client, monkeypatch, *, ambiguity=None, grouped=False):
    runtime, store = client.app.state.runtime, client.app.state.store
    item = late(client, kind='bowl')
    at = parse_stamp(item['created_at']).timestamp()
    with store.conn:
        store.conn.execute("UPDATE obligations SET box=?,source='vision' WHERE id=?", (json.dumps(BOX), item['id']))
    store.add_person_sample(item['id'], [0, 1], at=parse_stamp(item['created_at']))
    if grouped:
        other = late(client, kind='cup')
        with store.conn:
            store.conn.execute("UPDATE obligations SET box=?,source='vision' WHERE id=?", (json.dumps([.52, .6, .06, .06]), other['id']))
        store.add_person_sample(other['id'], [0, 1], at=parse_stamp(other['created_at']))
    for i in range(101):
        if ambiguity == 'gap' and 45 <= i < 50:
            continue
        people = [PERSON] if 10 <= i <= 80 else []
        if people and ambiguity == 'two-people' and i == 45:
            people = [PERSON, PERSON | {'track_id': 8}]
        if people and ambiguity == 'far-person':
            people = [PERSON, PERSON | {'track_id': 8, 'box': [.9, .01, .05, .1]}]
        if people and ambiguity == 'handoff' and i >= 45:
            people = [PERSON | {'track_id': 8}]
        if people and ambiguity == 'untracked':
            people = [{k: v for k, v in PERSON.items() if k != 'track_id'}]
        runtime.journal.record(at + i, people)
    frame = jpeg((50, 80, 100))
    background = jpeg((10, 20, 180))
    crop = jpeg((20, 40, 220))
    seen = []

    def describe(raw, box):
        seen.append((raw, box))
        return {'outfit': [1, 0], 'head': [.6, .8]}, crop

    def unavailable():
        raise RuntimeError('No model available')

    engine = SimpleNamespace(describe_person=describe, enrolled_faces=lambda: {},
                             people=lambda _: [PERSON] + ([PERSON | {'track_id': 8}] if ambiguity == 'exact-ambiguity' else
                                 [PERSON | {'track_id': 8, 'box': [.9, .01, .05, .1]}] if ambiguity == 'far-person' else []))
    monkeypatch.setattr(runtime, 'local_vision', unavailable if ambiguity == 'no-model' else lambda: engine)
    monkeypatch.setattr(filmstrip, 'plan', lambda *args, **kwargs: {
        'box': BOX, 'window': {'before': at, 'after': at + 99, 'appeared': at + 81,
                               'arrival_recorded': ambiguity != 'already-present'},
        'frames': [{'at': at + i, 'person_boxes': [PERSON['box']] if 10 <= i <= 80 else []}
                   for i in (0, 20, 30, 40, 60, 99)]})
    monkeypatch.setattr(filmstrip, 'footage_segments', lambda _: [])
    monkeypatch.setattr(filmstrip, '_photo_at', lambda segments, when: (when, frame if at+10 <= when <= at+80 else background))
    saved = runtime.filmstrips.generate(item['id'], now=utcnow())
    assert saved['status'] == 'saved'
    return item, store.arrival_visit(store.obligation(item['id'])['arrival_visit_id']), seen, frame, crop


@pytest.mark.parametrize('surface', ['house', 'operator'])
@pytest.mark.parametrize('grouped', [False, True], ids=['single-dish', 'two-dishes'])
def test_visit_confirmation_teaches_the_selected_person_capture(house_client, monkeypatch, surface, grouped):
    client, store = house_client, house_client.app.state.store
    item, visit, seen, frame, crop = arrival(client, monkeypatch, grouped=grouped)
    assert len(visit['obligation_ids']) == (2 if grouped else 1)
    samples = store.conn.execute('SELECT * FROM person_samples WHERE obligation_id=?', (item['id'],)).fetchall()
    assert {r['cue']: json.loads(r['embedding']) for r in samples} == {'outfit': [1, 0], 'head': [.6, .8]}
    assert all(r['resident_id'] is None for r in samples)
    assert seen == [(frame, PERSON['box'])]
    evidence_ids = {r['evidence_id'] for r in samples}
    assert len(evidence_ids) == 1
    evidence = store.evidence(evidence_ids.pop())
    assert client.app.state.media.safe_path(evidence['filename']).read_bytes() == crop
    capture = json.loads(evidence['capture'])
    assert {k: capture[k] for k in ('identity_source', 'visit_id', 'person_box')} == {
        'identity_source': 'arrival_visit', 'visit_id': visit['visit_id'], 'person_box': PERSON['box']}
    assert parse_stamp(evidence['created_at']).timestamp() in [f['at'] for f in visit['frames'] if f['person_boxes']]
    with pytest.raises(ValueError):
        store.add_person_sample(item['id'], [0, 1])
    # A suggestion is not a teaching label, including when a human chooses someone else.
    store.set_suggestion(item['id'], client.people[1]['id'], .9, {'outfit_today': .99})
    assert store.conn.execute('SELECT count(*) FROM person_samples WHERE resident_id IS NOT NULL').fetchone()[0] == 0
    # The proof worker can finish later and replace the obligation's active capture.
    # Naming the displayed visit must still teach its own person, not that replacement.
    later = client.app.state.media.save_snapshot(item['id'], jpeg((220, 40, 20)), 'Who put it down (from footage)')
    store.replace_putdown_samples(item['id'], {'outfit': [0, 1]}, later, at=utcnow())
    if surface == 'operator':
        operator(client)
    base = '/house/api' if surface == 'house' else '/api/league'
    result = client.post(f"{base}/visits/{visit['visit_id']}/name", json={'resident_id': client.people[0]['id']})
    assert result.status_code == 200, result.text
    assert {r[0] for r in store.conn.execute('SELECT resident_id FROM person_samples WHERE obligation_id=?', (item['id'],))} == {client.people[0]['id']}
    assert store.sighting_cues(item['id'])[0] == {'outfit': [1, 0], 'head': [.6, .8]}
    assert store.conn.execute('SELECT count(DISTINCT obligation_id) FROM person_samples').fetchone()[0] == 1
    assert all(store.obligation(oid)['resident_id'] == client.people[0]['id'] for oid in visit['obligation_ids'])
    if surface == 'house':
        path = f"/house/api/visits/{visit['visit_id']}"
        assert client.post(path + '/dispute', json={'note': 'Check this visit'}).status_code == 200
        assert store.conn.execute('SELECT count(*) FROM person_samples WHERE resident_id IS NOT NULL').fetchone()[0] == 0
        as_person(client, 1)
        assert client.post(path + '/settle', json={'decision': 'them'}).status_code == 200
        assert {r[0] for r in store.conn.execute('SELECT resident_id FROM person_samples')} == {client.people[0]['id']}


@pytest.mark.parametrize('ambiguity', ['two-people', 'handoff', 'untracked', 'gap', 'already-present', 'no-model', 'exact-ambiguity'])
def test_unsafe_visit_can_be_named_without_teaching_a_stale_or_ambiguous_person(house_client, monkeypatch, ambiguity):
    client, store = house_client, house_client.app.state.store
    item, visit, seen, _, _ = arrival(client, monkeypatch, ambiguity=ambiguity)
    assert seen == []
    result = client.post(f"/house/api/visits/{visit['visit_id']}/name", json={'resident_id': client.people[0]['id']})
    assert result.status_code == 200, result.text
    assert store.obligation(item['id'])['resident_id'] == client.people[0]['id']
    assert store.conn.execute('SELECT count(*) FROM person_samples WHERE obligation_id=?', (item['id'],)).fetchone()[0] == 0


def test_failed_group_confirmation_rolls_back_identity_replacement(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    item, visit, _, _, _ = arrival(client, monkeypatch, grouped=True)
    later = client.app.state.media.save_snapshot(item['id'], jpeg((220, 40, 20)), 'Who put it down (from footage)')
    store.replace_putdown_samples(item['id'], {'outfit': [0, 1]}, later, at=utcnow())
    other = next(oid for oid in visit['obligation_ids'] if oid != item['id'])
    store.action(other, 'confirm', resident_id=client.people[1]['id'])
    result = client.post(f"/house/api/visits/{visit['visit_id']}/name", json={'resident_id': client.people[0]['id']})
    assert result.status_code == 409
    assert store.sighting_cues(item['id'])[0] == {'outfit': [0, 1]}
    assert store.conn.execute('SELECT resident_id FROM person_samples WHERE obligation_id=?', (item['id'],)).fetchone()[0] is None
    assert store.obligation(item['id'])['resident_id'] is None


def teach_gallery(store, people, count=40):
    """Independent, unique human-labelled heads across five days, with no noisy face cue."""
    from datetime import timedelta

    from wash_dishes.identity_fit import refit

    for i in range(count):
        at = utcnow() - timedelta(days=5 - i // 8, hours=8 - i % 8)
        item = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], candidate=True, at=at)
        vector = [1, 0, i / 10000] if i % 2 == 0 else [0, 1, i / 10000]
        store.add_person_sample(item['id'], vector, at=at, cue='head')
        store.action(item['id'], 'confirm', resident_id=people[i % 2]['id'])
    return refit(store)


@pytest.mark.parametrize('surface', ['house', 'operator'])
def test_visit_guess_is_hidden_until_independent_day_out_checks_earn_it(house_client, monkeypatch, surface):
    client, store = house_client, house_client.app.state.store
    item, visit, _, _, _ = arrival(client, monkeypatch)
    path = '/house/api/week' if surface == 'house' else '/api/league/week'
    if surface == 'operator':
        operator(client)
    def displayed():
        return next(v for v in client.get(path).json()['visits'] if v['id'] == visit['visit_id'])['guess']
    store.set_suggestion(item['id'], client.people[0]['id'], 99)
    assert displayed() is None
    report = teach_gallery(store, client.people)
    assert report['display_gate']['checked'] == 40
    assert store.identity_display_gate()['active']
    # Use the same arrival anchor with a head that matches the earned gallery.
    eid = visit['identity_evidence_id']
    capture = json.loads(store.evidence(eid)['capture'])
    capture['cues'] = {'head': [1, 0, 0]}
    with store.conn:
        store.conn.execute('UPDATE evidence SET capture=? WHERE id=?', (json.dumps(capture), eid))
    guess = displayed()
    assert guess['resident_id'] == client.people[0]['id']
    assert store.obligation(item['id'])['resident_id'] is None
    assert not any(r['points'] for r in client.get(path).json()['standings'])  # gallery is outside this week
    # Removing one human label invalidates calibration immediately, before nightly retraining.
    oid = store.conn.execute('SELECT obligation_id FROM person_samples WHERE resident_id IS NOT NULL LIMIT 1').fetchone()[0]
    store.action(oid, 'dispute')
    assert displayed() is None


@pytest.mark.parametrize('verdict', ['not_dish', 'clean', 'duplicate', 'cant_tell'])
def test_visit_verdict_retracts_identity_labels(house_client, monkeypatch, verdict):
    client, store = house_client, house_client.app.state.store
    item, visit, _, _, _ = arrival(client, monkeypatch)
    path = f"/api/league/visits/{visit['visit_id']}"
    operator(client)
    assert client.post(path + '/name', json={'resident_id': client.people[0]['id']}).status_code == 200
    assert store.conn.execute('SELECT count(*) FROM person_samples WHERE resident_id IS NOT NULL').fetchone()[0] == 2
    body = {'verdict': verdict}
    if verdict == 'duplicate':
        other = late(client, kind='bowl')
        body['of_visit_id'] = f"dish-{other['id']}"
    response = client.post(path + '/verdict', json=body)
    assert response.status_code == 200, response.text
    assert store.conn.execute('SELECT count(*) FROM person_samples WHERE resident_id IS NOT NULL').fetchone()[0] == 0
    if verdict != 'cant_tell':
        assert client.post(path + '/undo-verdict', json={}).status_code == 200
        # Undoing a verdict restores the original human-confirmed label, not a camera prediction.
        assert {r[0] for r in store.conn.execute('SELECT resident_id FROM person_samples')} == {client.people[0]['id']}


def test_not_them_leaves_arrival_samples_unlabelled(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    _, visit, _, _, _ = arrival(client, monkeypatch)
    path = f"/house/api/visits/{visit['visit_id']}"
    assert client.post(path + '/name', json={'resident_id': client.people[0]['id']}).status_code == 200
    assert client.post(path + '/dispute', json={'note': 'Wrong person'}).status_code == 200
    as_person(client, 1)
    assert client.post(path + '/settle', json={'decision': 'not_them'}).status_code == 200
    assert {r[0] for r in store.conn.execute('SELECT resident_id FROM person_samples')} == {None}


@pytest.mark.parametrize('tamper', ['outside-window', 'multiple-people', 'wrong-box', 'no-arrival'])
def test_review_rechecks_persisted_arrival_anchor(house_client, monkeypatch, tamper):
    client, store = house_client, house_client.app.state.store
    item, visit, _, _, _ = arrival(client, monkeypatch)
    row = store.conn.execute('SELECT * FROM arrival_visits WHERE id=?', (visit['visit_id'],)).fetchone()
    capture = json.loads(row['metadata'])
    if tamper == 'outside-window':
        capture['window']['after'] = capture['window']['before'] - 1
    elif tamper == 'multiple-people':
        crowded = next(f for f in capture['frames'] if f.get('person_boxes'))
        crowded['person_boxes'] = crowded['person_boxes'] * 2
    elif tamper == 'wrong-box':
        for frame in capture['frames']:
            frame['person_boxes'] = [[.9, .9, .01, .01]]
    else:
        capture['window']['arrival_recorded'] = False
    with store.conn:
        store.conn.execute('UPDATE arrival_visits SET metadata=? WHERE id=?', (json.dumps(capture), visit['visit_id']))
    response = client.post(f"/house/api/visits/{visit['visit_id']}/name", json={'resident_id': client.people[0]['id']})
    assert response.status_code == 200, response.text
    assert store.obligation(item['id'])['resident_id'] == client.people[0]['id']
    assert store.conn.execute('SELECT count(*) FROM person_samples').fetchone()[0] == 0


def test_later_proof_cannot_erase_a_human_named_arrival(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    item, visit, _, _, _ = arrival(client, monkeypatch)
    assert client.post(f"/house/api/visits/{visit['visit_id']}/name", json={'resident_id': client.people[0]['id']}).status_code == 200
    later = client.app.state.media.save_snapshot(item['id'], jpeg((220, 40, 20)), 'Who put it down (from footage)')
    store.replace_putdown_samples(item['id'], {'outfit': [0, 1]}, later, at=utcnow())
    assert store.sighting_cues(item['id'])[0] == {'outfit': [1, 0], 'head': [.6, .8]}
    assert {r[0] for r in store.conn.execute('SELECT resident_id FROM person_samples')} == {client.people[0]['id']}


def test_far_bystander_is_not_labelled_or_confused_with_arrival_visitor(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    item, visit, seen, frame, _ = arrival(client, monkeypatch, ambiguity='far-person')
    assert seen == [(frame, PERSON['box'])]
    assert client.post(f"/house/api/visits/{visit['visit_id']}/name", json={'resident_id': client.people[0]['id']}).status_code == 200
    assert store.sighting_cues(item['id'])[0] == {'outfit': [1, 0], 'head': [.6, .8]}
    assert {r[0] for r in store.conn.execute('SELECT resident_id FROM person_samples')} == {client.people[0]['id']}


@pytest.mark.parametrize('ambiguity', [None, 'two-people'])
def test_legacy_operator_confirmation_uses_only_the_safe_visit_anchor(house_client, monkeypatch, ambiguity):
    client, store = house_client, house_client.app.state.store
    item, _, _, _, _ = arrival(client, monkeypatch, ambiguity=ambiguity)
    later = client.app.state.media.save_snapshot(item['id'], jpeg((220, 40, 20)), 'Who put it down (from footage)')
    store.replace_putdown_samples(item['id'], {'outfit': [0, 1]}, later, at=utcnow())
    operator(client)
    response = client.post(f"/api/obligations/{item['id']}/action", json={'action': 'confirm', 'resident_id': client.people[0]['id']})
    assert response.status_code == 200, response.text
    samples = store.conn.execute('SELECT * FROM person_samples WHERE obligation_id=?', (item['id'],)).fetchall()
    if ambiguity:
        assert not samples
    else:
        assert store.sighting_cues(item['id'])[0] == {'outfit': [1, 0], 'head': [.6, .8]}
        assert {r['resident_id'] for r in samples} == {client.people[0]['id']}
