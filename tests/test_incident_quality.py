"""Regressions for the rejected real-world incident feed, including record 303."""
import json
from datetime import timedelta

from test_incidents import payload
from test_sequences import captured

from wash_dishes.incidents import Incidents
from wash_dishes.inventory import Inventory, Zone, area_for
from wash_dishes.sequences import SequenceCollection
from wash_dishes.store import utcnow


def test_person_context_is_not_a_dish_box(store, media):
    item = captured(store, media)
    photo = next(e for e in store.obligation(item['id'])['evidence'] if e['capture'].get('stage') == 'using')
    with store.conn:
        store.conn.execute('UPDATE evidence SET capture=? WHERE id=?',
                           (json.dumps({'stage': 'using', 'box': [.45, .61, .14, .39], 'box_kind': 'person_context'}), photo['id']))
    sequence = SequenceCollection(store, media)
    sequence.refresh()
    using = sequence.get(item['id'])['stages'][1]
    assert using['box'] is None
    assert not sequence.get(item['id'])['object_localized']


def test_clean_review_leaves_active_views_without_claiming_washing(authenticated):
    c = authenticated
    item = captured(c.app.state.store, c.app.state.media)
    url = f"/api/incidents/{item['id']}"
    body = payload(c.get(url).json())
    body['condition'] = 'clean'
    assert c.put(url + '/review', json=body).status_code == 200
    assert c.get('/api/incidents?filter=out').json()['total'] == 0
    assert c.get('/api/incidents?filter=attention').json()['total'] == 0
    assert c.app.state.store.obligation(item['id'])['lifecycle'] != 'resolved'


def test_missing_after_gap_is_not_currently_out(store, media):
    item = captured(store, media)
    store.note_missing(item['id'], 'Counter beside sink')
    sequences = SequenceCollection(store, media)
    sequences.refresh()
    incidents = Incidents(store, media, sequences)
    assert incidents.page(filter='out')['total'] == 0
    assert incidents.page(filter='attention')['total'] == 0


def test_archive_preserves_labels_and_suppresses_restart_backlog(store, media):
    person = store.add_resident('A')['id']
    item = store.create_obligation(person, [{'kind': 'cup', 'quantity': 1}])
    store.add_person_sample(item['id'], [.2, .3], None)
    with store.conn:
        store.conn.execute('UPDATE person_samples SET resident_id=? WHERE obligation_id=?', (person, item['id']))
    scene = [{'key': 'old', 'kind': 'cup', 'box': [.2, .5, .1, .1], 'role': 'dirty', 'area_id': 'counter',
              'obligation_id': item['id']}]
    store.save_scene(scene)
    store.archive_through(item['id'])
    assert store.obligation(item['id'])['resident_id'] == person
    assert store.conn.execute('SELECT resident_id FROM person_samples').fetchone()[0] == person
    [background] = store.load_scene()
    assert background['key'] == 'old' and background['box'] == scene[0]['box']
    assert background['background'] and background['obligation_id'] is None
    assert store.obligations() == []
    incidents = Incidents(store, media, SequenceCollection(store, media))
    assert incidents.page(filter='all')['total'] == 0
    assert incidents.page(filter='archive')['total'] == 1


def test_closed_records_never_enter_current_views(store, media):
    for lifecycle in ('cleared', 'resolved'):
        item = captured(store, media)
        store.conn.execute('UPDATE obligations SET lifecycle=? WHERE id=?', (lifecycle, item['id']))
    store.conn.commit()
    sequences = SequenceCollection(store, media)
    sequences.refresh()
    incidents = Incidents(store, media, sequences)
    assert incidents.page(filter='attention')['total'] == 0
    assert incidents.page(filter='out')['total'] == 0
    assert incidents.page(filter='all')['total'] == 2


def test_archive_does_not_keep_generating_deadlines_or_refreshes(store, media):
    item = captured(store, media)
    store.archive_through(item['id'])
    before = store.obligation(item['id'])
    store.scan_deadlines()
    store.record_seen(item['id'])
    assert SequenceCollection(store, media).refresh() == 0
    after = store.obligation(item['id'])
    assert after['events'] == before['events']
    assert after['last_seen_at'] == before['last_seen_at']


def test_delayed_or_untracked_kind_only_transfer_is_not_the_same_dish(store, media):
    inventory = Inventory(store, media)
    now = utcnow()
    detection = {'kind': 'bowl', 'box': [.3, .5, .05, .05]}
    source = {'item': {'kind': 'bowl'}, 'handler': {'track': 7}, 'at': now-timedelta(minutes=10)}
    inventory.in_hand = [source]
    assert inventory._carried(detection, {'track': 7}, now) is None
    source['at'] = now-timedelta(seconds=30)
    assert inventory._carried(detection, None, now) is None
    assert inventory._carried(detection, {'track': 9}, now) is None
    assert inventory._carried(detection, {'track': 7}, now) == source


def test_distant_visitor_is_not_a_handler(store, media):
    zone = Zone({'id': 'counter'})
    zone.visitors[7] = {'track': 7, 'box': [.447, .611, .142, .389], 'at': utcnow()}
    assert Inventory(store, media)._handler(zone, [.246, .785, .022, .026]) is None


def test_retained_handling_frames_do_not_choose_between_two_people(store, media):
    now = utcnow()
    dish = {'kind': 'cup', 'box': [.1, .6, .05, .05]}
    zone = Zone({'id': 'counter'})
    for track in (7, 8):
        sample = {'track': track, 'box': [0, .2, .3, .65], 'at': now,
                  'dishes': [dish], 'frame': b'photo'}
        zone.visitors[track] = dict(sample, dishes=[], samples=[sample])
    assert Inventory(store, media)._handler(zone, dish['box'], 'cup') is None


def test_retained_frame_of_another_dish_does_not_fill_handling(store, media):
    now = utcnow()
    sample = {'track': 7, 'box': [0, .2, .3, .65], 'at': now,
              'dishes': [{'kind': 'cup', 'box': [.2, .7, .05, .05]}], 'frame': b'other cup'}
    latest = dict(sample, at=now+timedelta(seconds=1), dishes=[], frame=b'occluded', samples=[sample])
    zone = Zone({'id': 'counter'}, visitors={7: latest})
    inventory = Inventory(store, media)
    handler = inventory._handler(zone, [.1, .6, .05, .05], 'cup')
    assert handler['frame'] == b'occluded'
    assert inventory._handling_dishes(handler, [.1, .6, .05, .05], 'cup') == []


def test_rack_calibration_keeps_303_counter_object_separate_from_rack():
    rack = {'id': 'rack', 'role': 'clean', 'box': [.575, .602, .121, .175],
            'outline': [[.600,.657],[.645,.602],[.665,.605],[.696,.739],[.631,.777],[.575,.744],[.578,.685]]}
    counter = {'id': 'counter', 'role': 'dirty', 'box': [.485,.63,.505,.37]}
    assert area_for([.561217,.630342,.023994,.033120], [counter, rack])['id'] == 'counter'
    assert area_for([.635,.605,.041,.11], [counter, rack])['id'] == 'rack'
    # Beside the rack remains counter; overlap must not swallow a whole countertop.
    assert area_for([.51,.68,.02,.03], [counter, rack])['id'] == 'counter'


def test_partial_overlap_handles_a_standing_dish_outside_rack_base():
    rack = {'id': 'rack', 'role': 'clean', 'box': [.5,.5,.1,.2]}
    counter = {'id': 'counter', 'role': 'dirty', 'box': [0,0,1,1]}
    assert area_for([.47,.51,.05,.08], [counter, rack])['id'] == 'rack'


def test_warning_copy_does_not_invalidate_human_review(store, media):
    from wash_dishes.incidents import sequence_signature
    item = captured(store, media)
    manifest, _ = SequenceCollection(store, media)._draft(store.obligation(item['id']))
    before = sequence_signature(manifest)
    manifest['warnings'] = ['Different explanation of the same facts']
    assert sequence_signature(manifest) == before


def test_feed_stats_and_alerts_agree_before_review_and_after_rack_cleanup(store, media, approve_late):
    from wash_dishes.notify import Notifier
    store.set_settings({'notify_url': 'https://ntfy.example/test'})
    sent = []
    notifier = Notifier(store.root, store, lambda *args: sent.append(args))
    notifier.poll()
    sent.clear()  # An optional scheduled weekly summary is unrelated to this incident.
    person = store.add_resident('Owner')['id']
    item = store.create_obligation(person, [{'kind':'plate','quantity':1}], source='vision', at=utcnow()-timedelta(hours=3))
    store.record_seen(item['id'])
    notifier.poll()
    assert sent == [] and not store.confirmed_late_ids()
    assert store.statistics()[0]['overdue'] == 0
    approve_late(item)
    notifier.poll()
    assert len(sent) == 1 and sent[0][1] == 'Late dishes: Owner'
    assert store.confirmed_late_ids() == {item['id']}
    assert store.statistics()[0]['overdue'] == 1
    store.propose_cleanup(item['id'])
    sequences = SequenceCollection(store, media)
    sequences.refresh()
    incidents = Incidents(store, media, sequences)
    assert incidents.page(filter='out')['total'] == 0
    assert incidents.page(filter='late')['total'] == 1
    assert incidents.detail(item['id'])['outcome'] == 'removed'
    assert store.cleaned_today() == 0
    notifier.poll()
    assert len(sent) == 1
