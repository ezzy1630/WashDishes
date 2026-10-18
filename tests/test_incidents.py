from test_sequences import captured


def payload(detail, **changes):
    return dict(revision=detail['revision'], checks={}, identity='keep', person_id=None,
                responsibility='keep', condition='unknown', outcome='keep', cleaner_id=None,
                resolved_at=None, note='', **changes)


def test_review_identity_does_not_assign_responsibility(authenticated):
    c = authenticated
    store, rt = c.app.state.store, c.app.state.runtime
    person = store.add_resident('Alex')
    item = captured(store, c.app.state.media)
    rt.sequences.refresh()
    url = f"/api/incidents/{item['id']}"
    detail = c.get(url).json()
    body = payload(detail)
    body.update(identity='named', person_id=person['id'])
    saved = c.put(url+'/review', json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()['record']['resident_id'] is None
    assert not saved.json()['record']['overdue_verified']
    assert c.put(url+'/review', json=body).status_code == 409


def test_recovered_activity_photo_does_not_inherit_another_photos_name(authenticated):
    from datetime import timedelta

    from test_sequences import jpeg

    from wash_dishes.store import parse_stamp

    c = authenticated
    store, rt, media = c.app.state.store, c.app.state.runtime, c.app.state.media
    person = store.add_resident('Alex')
    item = captured(store, media)
    rt.sequences.refresh()
    url = f"/api/incidents/{item['id']}"
    body = payload(c.get(url).json())
    body.update(identity='named', person_id=person['id'])
    assert c.put(url+'/review', json=body).status_code == 200
    old = rt.sequences.get(item['id'])
    assert old['visible_person'] == 'Alex'
    using = next(s for s in old['stages'] if s['key'] == 'using')
    media.save_snapshot(item['id'], jpeg('green'), at=parse_stamp(using['at'])+timedelta(seconds=1),
                        capture={'stage':'using','box':using['box'],'box_kind':'candidate_item','recovered':True})
    rt.sequences.refresh()
    assert rt.sequences.get(item['id'])['visible_person'] is None
    assert c.get(url).json()['identified_person_id'] is None
    # An unrelated refresh must not restore the stale name.
    store.event('observation_gap', 'Test gap', item['id'])
    rt.sequences.refresh()
    assert rt.sequences.get(item['id'])['visible_person'] is None


def test_review_atomic_rollback_and_persistent_approval(authenticated):
    c = authenticated
    store, rt = c.app.state.store, c.app.state.runtime
    person = store.add_resident('Alex')
    item = captured(store, c.app.state.media)
    rt.sequences.refresh()
    url = f"/api/incidents/{item['id']}"
    body = payload(c.get(url).json())
    body.update(responsibility='confirm', person_id=person['id'], condition='used',
                checks={k: True for k in ('before','using','leaving','later')}, outcome='removed', resolved_at='2099-01-01T00:00:00+00:00')
    assert c.put(url+'/review', json=body).status_code == 400
    assert store.obligation(item['id'])['resident_id'] is None
    body.update(outcome='keep', resolved_at=None)
    response = c.put(url+'/review', json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['sequence']['status'] == 'ready'
    assert not saved['sequence']['stale']
    assert saved['record']['overdue_verified']
    assert c.get('/api/incidents?filter=attention').json()['total'] == 0
    assert c.get(f"/api/incidents?filter=late&resident_id={person['id']}").json()['total'] == 1
    rt.sequences.refresh()
    assert rt.sequences.get(item['id'])['status'] == 'ready'


def test_removal_does_not_establish_washing_or_lateness(authenticated):
    c = authenticated
    item = captured(c.app.state.store, c.app.state.media)
    url = f"/api/incidents/{item['id']}"
    body = payload(c.get(url).json())
    body.update(outcome='removed')
    saved = c.put(url+'/review', json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()['outcome'] == 'removed'
    assert not saved.json()['record']['overdue_verified']


def test_clean_dish_stays_out_of_late_counts_after_next_sighting(authenticated):
    c = authenticated
    store, rt = c.app.state.store, c.app.state.runtime
    person = store.add_resident('Alex')
    item = captured(store, c.app.state.media)
    url = f"/api/incidents/{item['id']}"
    body = payload(c.get(url).json())
    body.update(responsibility='confirm', person_id=person['id'], condition='clean',
                checks={k: True for k in ('before','using','leaving','later')})
    assert c.put(url+'/review', json=body).status_code == 200
    store.record_seen(item['id'])
    assert not store.obligation(item['id'])['overdue_verified']
    assert rt.incidents.page(filter='late')['total'] == 0


def test_person_filter_and_pagination_have_exact_counts(authenticated):
    c = authenticated
    s = c.app.state.store
    a, b = s.add_resident('A')['id'], s.add_resident('B')['id']
    old = s.create_obligation(a, [{'kind': 'cup', 'quantity': 1}])
    for _ in range(30):
        s.create_obligation(b, [{'kind': 'cup', 'quantity': 1}])
    data = c.get(f'/api/incidents?filter=all&resident_id={a}&limit=1').json()
    assert data['total'] == data['counts']['all'] == 1
    assert data['items'][0]['id'] == old['id'] and data['next_offset'] is None
    data = c.get('/api/incidents?filter=all&limit=1').json()
    assert data['total'] == 31 and data['next_offset'] == 1


def test_removal_is_not_an_on_time_wash(store, media):
    from wash_dishes.incidents import Incidents
    from wash_dishes.sequences import SequenceCollection
    person = store.add_resident('A')['id']
    item = store.create_obligation(person, [{'kind': 'cup', 'quantity': 1}])
    incidents = Incidents(store, media, SequenceCollection(store, media))
    body = payload(incidents.detail(item['id']))
    body.update(outcome='removed')
    incidents.review(item['id'], body)
    assert store.statistics()[0]['on_time'] == 0
    assert store.statistics()[0]['uses'] == 0


def test_automatic_lookback_is_bounded_deduplicated_and_keeps_unknown(store, media):
    from test_sequences import jpeg

    from wash_dishes.dataset import Dataset
    from wash_dishes.sequences import SequenceCollection
    from wash_dishes.tracing import TraceSearch
    area = {'id': 'counter', 'name': 'Counter', 'role': 'dirty', 'box': [0, 0, 1, 1]}
    store.set_settings({'areas': [area]})
    item = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], location='Counter', candidate=True)
    media.save_snapshot(item['id'], jpeg('blue'), 'Dish left unattended', capture={'stage': 'leaving'})
    sequences = SequenceCollection(store, media)
    traces = TraceSearch(store.root, store, media, Dataset(store.root, store))
    job = traces.enqueue_missing(sequences)
    assert job['obligation_id'] == item['id'] and job['automatic']
    assert traces.enqueue_missing(sequences) is None
    traces.run(job['id'])
    assert traces.get(job['id'])['coverage']['segments'] == 0
    restarted = TraceSearch(store.root, store, media, Dataset(store.root, store))
    assert restarted.enqueue_missing(sequences) is None
    assert store.obligation(item['id'])['resident_id'] is None


def test_notification_partial_failure_does_not_repeat_acknowledged_destination(store, approve_late):
    from datetime import timedelta

    from wash_dishes.notify import Notifier
    from wash_dishes.store import utcnow
    sent, fail = [], [True]
    store.set_settings({'notify_url': 'https://ntfy.example/house', 'dashboard_url': 'https://kitchen.example'})
    person = store.add_resident('A')['id']
    store.set_resident_notify(person, 'https://ntfy.example/person')
    def send(url, title, message, tags):
        if url.endswith('/person') and fail[0]:
            raise OSError('offline')
        sent.append((url, message))
    n = Notifier(store.root, store, send)
    n.poll()
    item = store.create_obligation(person, [{'kind': 'cup', 'quantity': 1}], at=utcnow()-timedelta(hours=3))
    store.record_seen(item['id'])
    approve_late(item)
    import pytest
    with pytest.raises(OSError):
        n.poll()
    fail[0] = False
    n = Notifier(store.root, store, send)
    n.poll()
    assert [u for u, _ in sent] == ['https://ntfy.example/house', 'https://ntfy.example/person']
    assert all(f'https://kitchen.example/#/incident/{item["id"]}' in message for _, message in sent)
