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
        if people and ambiguity == 'handoff' and i >= 45:
            people = [PERSON | {'track_id': 8}]
        if people and ambiguity == 'untracked':
            people = [{k: v for k, v in PERSON.items() if k != 'track_id'}]
        runtime.journal.record(at + i, people)
    frame = jpeg((50, 80, 100))
    crop = jpeg((20, 40, 220))
    seen = []

    def describe(raw, box):
        seen.append((raw, box))
        return {'outfit': [1, 0], 'head': [.6, .8]}, crop

    def unavailable():
        raise RuntimeError('No model available')

    engine = SimpleNamespace(describe_person=describe, enrolled_faces=lambda: {},
                             people=lambda _: [PERSON] + ([PERSON | {'track_id': 8}] if ambiguity == 'exact-ambiguity' else []))
    monkeypatch.setattr(runtime, 'local_vision', unavailable if ambiguity == 'no-model' else lambda: engine)
    monkeypatch.setattr(filmstrip, 'plan', lambda *args, **kwargs: {
        'box': BOX, 'window': {'before': at, 'after': at + 99, 'appeared': at + 81,
                               'arrival_recorded': ambiguity != 'already-present'},
        'frames': [{'at': at + i, 'person_boxes': [PERSON['box']] if 10 <= i <= 80 else []}
                   for i in (0, 20, 30, 40, 60, 99)]})
    monkeypatch.setattr(filmstrip, 'footage_segments', lambda _: [])
    monkeypatch.setattr(filmstrip, '_photo_at', lambda segments, at: (at, frame))
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
