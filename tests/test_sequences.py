import io
from datetime import timedelta

import pytest
from PIL import Image

from wash_dishes.store import parse_stamp, utcnow


def jpeg(color):
    out = io.BytesIO()
    Image.new('RGB', (640, 360), color).save(out, 'JPEG')
    return out.getvalue()


def captured(store, media, *, complete=True):
    at = utcnow() - timedelta(hours=3)
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], source='vision', candidate=True, at=at,
                                   box=[.3, .5, .1, .1])
    roles = [('before', -60, 'Before it was put down'), ('using', -20, 'Person handling the dish just before it was left'),
             ('leaving', 10, 'Dish left unattended'), ('later', 7500, 'Matching dish still visible after the deadline; dirty status requires review')]
    for i, (role, seconds, label) in enumerate(roles):
        if not complete and role in ('before', 'using'):
            continue
        capture = {'stage': role, 'box': [.3, .5, .1, .1], 'box_kind': 'candidate_item'}
        media.save_snapshot(item['id'], jpeg((50+i*40, 40, 80)), label, at=at+timedelta(seconds=seconds), capture=capture)
    return item


def test_sequence_collection_keeps_originals_and_needs_semantic_review(store, media):
    from wash_dishes.sequences import SequenceCollection
    item = captured(store, media)
    collection = SequenceCollection(store, media)
    assert collection.refresh() == 1
    saved = collection.get(item['id'])
    assert saved['status'] == 'review'
    assert saved['captured'] == 4
    assert [s['key'] for s in saved['stages']] == ['before', 'using', 'leaving', 'later']
    assert len({s['sha256'] for s in saved['stages']}) == 4
    assert Image.open(collection.image(item['id'])).size[0] >= 2400
    assert collection.refresh() == 0
    assert len(store.obligation(item['id'])['evidence']) == 5  # Four originals and the derived collage.
    reviewed = collection.review(item['id'], saved['fingerprint'], {k: True for k in ('before','using','leaving','later')})
    assert reviewed['status'] == 'ready'
    assert SequenceCollection(store, media).get(item['id'])['status'] == 'ready'
    assert store.obligation(item['id'])['resident_id'] is None
    assert not store.obligation(item['id'])['overdue_verified']


def test_missing_and_repeated_sightings_never_pass_as_complete(store, media):
    from wash_dishes.sequences import SequenceCollection
    item = captured(store, media, complete=False)
    collection = SequenceCollection(store, media)
    collection.refresh()
    saved = collection.get(item['id'])
    assert saved['status'] == 'incomplete'
    assert saved['captured'] == 2
    with pytest.raises(ValueError, match='four'):
        collection.review(item['id'], saved['fingerprint'], {k: True for k in ('before','using','leaving','later')})
    assert collection.summary()['attention'] == 1


def test_changed_evidence_invalidates_review_and_expiry_is_explicit(store, media):
    from wash_dishes.sequences import SequenceCollection
    item = captured(store, media)
    collection = SequenceCollection(store, media)
    collection.refresh()
    saved = collection.get(item['id'])
    collection.review(item['id'], saved['fingerprint'], {k:True for k in ('before','using','leaving','later')})
    with store.conn:
        store.conn.execute("UPDATE obligations SET review_pending=1 WHERE id=?", (item['id'],))
    collection.refresh()
    assert collection.get(item['id'])['status'] == 'review'
    with pytest.raises(ValueError, match='changed'):
        collection.review(item['id'], saved['fingerprint'], {k:True for k in ('before','using','leaving','later')})
    current = collection.get(item['id'])
    store.expire_evidence(current['image_id'])
    with pytest.raises(LookupError, match='expired'):
        collection.image(item['id'])
    assert collection.get(item['id'])['image_url'] is None


def test_collection_api_is_private_and_records_review_without_assigning_owner(authenticated):
    client = authenticated
    assert client.get('/api/sequences').status_code == 200
    item = captured(client.app.state.store, client.app.state.media)
    client.app.state.runtime.sequences.refresh()
    saved = client.get('/api/sequences').json()['items'][0]
    response = client.get(saved['image_url'])
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert 'attachment' in response.headers['content-disposition']
    assert client.put(f"/api/sequences/{item['id']}/review", json={'fingerprint':saved['fingerprint'],'checks':{'using':False}}).json()['status'] == 'rejected'
    assert client.get(f"/api/obligations/{item['id']}").json()['resident_id'] is None
    client.cookies.clear()
    assert client.get('/api/sequences').status_code == 401
    assert client.get(saved['image_url']).status_code == 401


def test_inventory_capture_times_are_the_times_of_the_original_frames(store, media):
    from wash_dishes.inventory import Inventory, Zone
    at = utcnow() - timedelta(hours=3)
    inventory = Inventory(store, media)
    zone = Zone({'id':'stove','name':'Stove','role':'dirty','box':[0,0,1,1]},
                visit_started=at, visit_before=jpeg('black'), visit_before_at=at-timedelta(seconds=5))
    detection = {'kind':'pan','score':.9,'box':[.3,.5,.1,.1]}
    handler = {'frame':jpeg('red'),'box':[.2,.1,.3,.8],'at':at+timedelta(seconds=4),'track':7,'dishes':[detection]}
    inventory._add(zone, detection, at+timedelta(seconds=10), jpeg('blue'), placed_at=at+timedelta(seconds=5), handler=handler)
    item = next(iter(inventory.items.values()))
    inventory._open(item, zone, at+timedelta(seconds=60))
    captures = {e['capture'].get('stage'):e for e in store.obligation(item['obligation_id'])['evidence']}
    from wash_dishes.store import stamp
    assert captures['before']['created_at'] == stamp(at-timedelta(seconds=5))
    assert captures['leaving']['created_at'] == stamp(at+timedelta(seconds=10))
    assert captures['using']['capture']['box'] == detection['box']


def test_duplicate_source_pixels_and_predeadline_views_do_not_report_an_issue(store, media):
    from wash_dishes.sequences import SequenceCollection
    item = captured(store, media)
    collection = SequenceCollection(store, media)
    photos = store.obligation(item['id'])['evidence']
    first = media.safe_path(store.evidence(photos[0]['id'])['filename']).read_bytes()
    for e in photos:
        media.safe_path(store.evidence(e['id'])['filename']).write_bytes(first)
    collection.refresh()
    assert collection.get(item['id'])['status'] == 'incomplete'
    other = captured(store, media)
    with store.conn:
        store.conn.execute('UPDATE obligations SET due_at=? WHERE id=?', ((utcnow()+timedelta(hours=1)).isoformat(), other['id']))
    collection.refresh()
    sequence = collection.get(other['id'])
    assert sequence['captured'] == 3 and not sequence['past_deadline']
    assert sequence['stages'][-1]['url'] is None
    assert not sequence['complete'] and not sequence['followup_captured']
    assert collection.summary()['attention'] == 1  # Only the first, post-deadline record.


def test_changed_record_blocks_stale_collage_in_original_endpoint_and_zip(authenticated):
    import json
    import zipfile
    client = authenticated
    item = captured(client.app.state.store, client.app.state.media)
    collection = client.app.state.runtime.sequences
    collection.refresh()
    saved = collection.get(item['id'])
    client.app.state.store.action(item['id'], 'dispute')
    assert client.get('/api/evidence/'+saved['image_id']).status_code == 409
    response = client.get(f"/api/obligations/{item['id']}/proof")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        record = json.loads(archive.read('record.json'))
        assert record['four_stage_sequence']['stale']
        collage = next(f for f in record['files'] if f['capture'].get('derived_sequence'))
        assert collage['sha256'] is None


def test_legacy_times_are_explicitly_approximate_and_never_rewritten(store, media):
    from wash_dishes.sequences import SequenceCollection
    item = captured(store, media)
    with store.conn:
        store.conn.execute("UPDATE evidence SET capture='{}' WHERE obligation_id=?", (item['id'],))
    collection = SequenceCollection(store, media)
    collection.refresh()
    saved = collection.get(item['id'])
    assert all(s['time_basis'] == 'legacy_record' for s in saved['stages'])
    assert any('camera clock' in warning for warning in saved['warnings'])
    assert all(s['box'] is None for s in saved['stages'])


def test_attention_filter_finds_old_cases_before_pagination(store, media):
    import json

    from wash_dishes.sequences import SequenceCollection
    old = captured(store, media)
    collection = SequenceCollection(store, media)
    collection.refresh()
    # Newer baseline records must not bury an unresolved after-deadline case.
    for _ in range(205):
        item = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], source='baseline', candidate=True, timing_known=False)
        manifest = {'obligation_id': item['id'], 'past_deadline': False, 'complete': False, 'status': 'incomplete', 'outcome': 'open'}
        with store.conn:
            store.conn.execute('INSERT INTO evidence_sequences(obligation_id,fingerprint,manifest) VALUES(?,?,?)', (item['id'], 'x', json.dumps(manifest)))
    page = collection.page(filter='attention', limit=10)
    assert page['total'] == collection.summary()['attention'] == 1
    assert [i['obligation_id'] for i in page['items']] == [old['id']]
    assert page['next_offset'] is None


def test_stage_selection_skips_expired_first_choice(store, media):
    from wash_dishes.sequences import SequenceCollection
    item = captured(store, media)
    old = next(e for e in store.obligation(item['id'])['evidence'] if e['capture']['stage'] == 'using')
    store.expire_evidence(old['id'])
    media.save_snapshot(item['id'], jpeg('green'), old['label'], at=parse_stamp(old['created_at']), capture=old['capture'])
    collection = SequenceCollection(store, media)
    collection.refresh()
    assert collection.get(item['id'])['complete']


def test_departure_uses_settled_scene_not_earlier_handler_frame(store, media):
    from wash_dishes.inventory import Inventory, Zone
    at = utcnow() - timedelta(minutes=5)
    quiet = jpeg('green')
    zone = Zone({'id': 'counter', 'name': 'Counter', 'role': 'dirty', 'box': [0,0,1,1]},
                quiet_jpeg=quiet, quiet_at=at+timedelta(seconds=60))
    inv = Inventory(store, media)
    inv._add(zone, {'kind': 'cup', 'box': [.3,.5,.1,.1], 'score': .9}, at, jpeg('red'), placed_at=at)
    item = next(iter(inv.items.values()))
    inv._open(item, zone, at+timedelta(seconds=60))
    photo = next(e for e in store.obligation(item['obligation_id'])['evidence'] if e['capture'].get('stage') == 'leaving')
    assert photo['created_at'] == zone.quiet_at.isoformat(timespec='seconds')
    assert media.safe_path(store.evidence(photo['id'])['filename']).read_bytes() == quiet


def test_two_overlapping_visitors_do_not_choose_a_handler(store, media):
    from wash_dishes.inventory import Inventory, Zone
    at = utcnow()
    zone = Zone({'id': 'counter', 'name': 'Counter', 'role': 'dirty', 'box': [0,0,1,1]})
    zone.visitors = {i: {'at': at, 'track': i, 'box': [.1,.1,.8,.8]} for i in (1,2)}
    assert Inventory(store, media)._handler(zone, [.3,.5,.1,.1]) is None


def test_corrupt_first_capture_does_not_hide_valid_replacement(store, media):
    from wash_dishes.sequences import SequenceCollection
    item = captured(store, media)
    old = next(e for e in store.obligation(item['id'])['evidence'] if e['capture'].get('stage') == 'using')
    media.safe_path(store.evidence(old['id'])['filename']).write_bytes(b'broken jpeg')
    replacement = media.save_snapshot(item['id'], jpeg('green'), old['label'], at=parse_stamp(old['created_at']), capture=old['capture'])
    collection = SequenceCollection(store, media)
    collection.refresh()
    saved = collection.get(item['id'])
    assert saved['complete']
    assert saved['stages'][1]['evidence_id'] == replacement
