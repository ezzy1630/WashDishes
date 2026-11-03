from datetime import timedelta

import numpy as np
import pytest

from wash_dishes.inventory import Inventory, Zone
from wash_dishes.store import parse_stamp, utcnow
from wash_dishes.vision import merge_detections


def detection(box, kind='pan', score=.8):
    return {'box': box, 'kind': kind, 'score': score}


def test_partial_cookware_box_does_not_replace_whole_or_hide_person():
    whole = detection([.2, .5, .2, .15], score=.6)
    rim = detection([.22, .5, .16, .015], 'pot', .9)
    person = detection([.2, .4, .2, .4], 'person')
    assert merge_detections([rim, whole, person]) == [person, whole]


def test_containment_keeps_separate_cup_inside_pan():
    pan = detection([.2, .5, .2, .15])
    cup = detection([.24, .54, .03, .04], 'cup')
    assert len(merge_detections([pan, cup])) == 2


def test_containment_keeps_compact_bowl_nested_in_pan():
    pan = detection([.2, .5, .2, .15])
    bowl = detection([.24, .54, .07, .06], 'bowl')
    assert len(merge_detections([pan, bowl])) == 2


def test_saved_visual_anchor_prevents_duplicate_after_neighbor_drift(store, media):
    inventory = Inventory(store, media)
    now = utcnow()
    zone = Zone({'id': 'counter', 'name': 'Counter', 'role': 'dirty', 'box': [0, .4, .6, .5]}, settled_since=now)
    inventory.zones['counter'] = zone
    image = np.random.default_rng(4).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    whole = detection([.2, .5, .2, .15])
    neighbor = detection([.22, .53, .13, .10])
    inventory.verify = lambda im, box: {'dish': True, 'score': .9}
    inventory._put_down(zone, whole, now, b'', image)
    [item] = inventory.items.values()
    inventory._open(item, zone, now)
    original = item['obligation_id']
    # A cropped look pulled the live rectangle toward a smaller neighbor.
    inventory._update(item, neighbor, now+timedelta(seconds=10))
    inventory._remember(item, image)
    inventory._initialize(zone, [neighbor, whole], b'', now+timedelta(seconds=30), image=image)
    inventory._open_waiting(now+timedelta(seconds=60))
    assert inventory.items[item['key']]['obligation_id'] == original
    assert inventory.items[item['key']]['box'] == whole['box']
    assert len(store.obligations(history=True)) == 1


def test_anchor_uses_the_recorded_departure_box_not_an_earlier_proposal(store, media):
    import cv2
    inventory = Inventory(store, media)
    now = utcnow()
    zone = Zone({'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .5, .5]},
                quiet_at=now, settled_since=now)
    image = np.random.default_rng(4).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    inventory._add(zone, detection([.2, .5, .2, .15]), now, b'', placed_at=None,
                   verdict={'dish': True, 'score': .9}, image=image)
    [item] = inventory.items.values()
    inventory._update(item, detection([.22, .52, .2, .15]), now)
    zone.quiet_jpeg = cv2.imencode('.jpg', image)[1].tobytes()
    inventory._open(item, zone, now)
    assert item['identity_anchor']['box'] == store.obligation(item['obligation_id'])['box']


@pytest.mark.parametrize('changed', [False, True])
def test_overlapping_proposal_needs_fresh_visual_evidence_for_second_record(store, media, changed):
    inventory = Inventory(store, media)
    now = utcnow()
    zone = Zone({'id': 'counter', 'name': 'Counter', 'role': 'dirty', 'box': [0, .4, .6, .5]}, settled_since=now)
    inventory.zones['counter'] = zone
    image = np.random.default_rng(4).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    whole = detection([.2, .5, .2, .15])
    inventory.verify = lambda im, box: {'dish': True, 'score': .9}
    inventory._put_down(zone, whole, now, b'', image)
    [item] = inventory.items.values()
    inventory._open(item, zone, now)
    # A neighboring detector box still captures the positional match. A second
    # overlapping outline on unchanged pixels cannot establish a second dish.
    current = detection([.21, .51, .2, .15])
    extra = detection([.25, .46, .16, .12])
    if changed:
        image = np.random.default_rng(19).integers(0, 255, image.shape, dtype=np.uint8)
    inventory._initialize(zone, [current, extra], b'', now+timedelta(seconds=30), image=image)
    inventory._open_waiting(now+timedelta(seconds=60))
    assert len(store.obligations(history=True)) == (2 if changed else 1)


@pytest.mark.parametrize('verify', [None, lambda image, box: None,
                                   lambda image, box: {'dish': True},
                                   lambda image, box: {'dish': True, 'score': float('nan')},
                                   lambda image, box: 1 / 0])
def test_unknown_verdict_defers_and_retries(store, media, verify):
    from test_inventory import Kitchen
    kitchen = Kitchen(store, media)
    kitchen.inventory.verify = verify
    kitchen.put('pan', 'pan', 'counter')()
    kitchen.wait(40)
    assert kitchen.items() == []
    assert store.events_after(0, ('dish_verification_deferred',))
    verifier_ready_at = kitchen.at
    kitchen.inventory.verify = lambda image, box: {'dish': True, 'score': .9}
    kitchen.wait(100)
    assert len(kitchen.items()) == 1
    assert parse_stamp(kitchen.items()[0]['created_at']) >= verifier_ready_at.replace(microsecond=0)


def test_restart_does_not_open_legacy_candidate_without_verdict(store, media):
    now = utcnow()
    area = {'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .5, .5]}
    store.save_scene([{'key': 'legacy', 'kind': 'pan', 'box': [.2, .5, .1, .1],
                      'area_id': 'stove', 'role': 'dirty', 'background': True,
                      'first_seen_at': now.isoformat(), 'obligation_id': None}])
    inventory = Inventory(store, media)
    inventory.zones['stove'] = Zone(area, settled_since=now)
    inventory._open_waiting(now+timedelta(seconds=40))
    assert store.obligations(history=True) == []


def test_same_spot_occlusion_relinks_across_kind_without_handler(store, media):
    inventory = Inventory(store, media)
    area = {'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .5, .5]}
    zone = Zone(area)
    now = utcnow()
    image = np.random.default_rng(4).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    inventory.verify = lambda im, box: {'dish': True, 'score': .9}
    inventory._put_down(zone, detection([.2, .5, .2, .15]), now, b'', image)
    [item] = inventory.items.values()
    inventory._open(item, zone, now)
    key, obligation_id = item['key'], item['obligation_id']
    inventory._pick_up(zone, item, now + timedelta(seconds=1))
    inventory._put_down(zone, detection([.205, .5, .2, .15], 'pot'), now + timedelta(seconds=25), b'', image)
    [returned] = inventory.items.values()
    assert (returned['key'], returned['obligation_id']) == (key, obligation_id)
    assert len(store.obligations(history=True)) == 1


def test_same_geometry_with_changed_appearance_is_a_different_dish(store, media):
    inventory = Inventory(store, media)
    zone = Zone({'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .5, .5]})
    now = utcnow()
    image = np.random.default_rng(4).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    inventory.verify = lambda im, box: {'dish': True, 'score': .9}
    dish = detection([.2, .5, .2, .15])
    inventory._put_down(zone, dish, now, b'', image)
    [item] = inventory.items.values()
    inventory._open(item, zone, now)
    key = item['key']
    inventory._pick_up(zone, item, now)
    replacement = image.copy()
    replacement[100:130, 60:90] = 0
    replacement[100:130, 90:120] = 255
    inventory._put_down(zone, dish, now+timedelta(seconds=10), b'', replacement)
    [new_item] = inventory.items.values()
    assert new_item['key'] != key
    inventory._open(new_item, zone, now+timedelta(seconds=40))
    assert len(store.obligations(history=True)) == 2


def make_record(store):
    item = store.create_obligation(None, [{'kind': 'pan', 'quantity': 1}], source='vision', candidate=True)
    store.link_dish_sample(store.add_dish_sample([.2, .3], .8), item['id'])
    return item['id']


def test_review_corrections_are_atomic_labeled_and_undoable(store):
    a, b = make_record(store), make_record(store)
    with pytest.raises(LookupError):
        store.mark_not_dish([a, 999999], 'operator')
    assert store.obligation(a)['lifecycle'] == 'open'
    store.mark_not_dish([a], 'operator')
    store.mark_clean([b], 'housemate')
    assert [s['label'] for s in store.labeled_dish_samples()] == [0, 1]
    assert store.obligation(b)['lifecycle'] == 'dismissed'
    store.undo_dish_correction(a, 'operator')
    store.undo_dish_correction(b, 'operator')
    assert store.labeled_dish_samples() == []
    assert store.obligation(a)['lifecycle'] == store.obligation(b)['lifecycle'] == 'open'


def test_duplicate_correction_preserves_canonical_and_learns_nothing(store):
    a, b = make_record(store), make_record(store)
    store.mark_duplicate(b, a, 'operator')
    assert store.obligation(b)['lifecycle'] == 'dismissed'
    assert store.obligation(a)['lifecycle'] == 'open'
    assert store.labeled_dish_samples() == []
    with pytest.raises(ValueError):
        store.mark_duplicate(a, b, 'operator')
    store.undo_dish_correction(b, 'operator')
    assert store.obligation(b)['lifecycle'] == 'open'


@pytest.mark.parametrize('same_person', [True, False])
def test_sink_return_requires_same_person_and_localized_dish_history(store, media, same_person):
    inventory = Inventory(store, media)
    now = utcnow()
    sink = Zone({'id': 'sink', 'name': 'Sink', 'role': 'sink', 'box': [.5, .5, .3, .3]})
    stove = Zone({'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .5, .3, .3]}, settled_since=now)
    inventory.zones = {'sink': sink, 'stove': stove}
    image = np.zeros((200, 300, 3), np.uint8)
    origin = detection([.55, .55, .1, .1])
    target = detection([.15, .55, .1, .1], 'pot')
    inventory._add(sink, origin, now, b'', placed_at=None, image=image)
    [item] = inventory.items.values()
    handler = {'track': 7, 'box': [.1, .2, .7, .7], 'at': now, 'dishes': [target], 'frame': b'\xff\xd8test\xff\xd9'}
    source_handler = handler | {'track': 7 if same_person else 8}
    sink.handling_history = [source_handler | {'at': now-timedelta(seconds=n), 'dishes': [origin]} for n in (12, 5)]
    stove.visitors[7] = handler
    inventory.items.clear()
    inventory.in_hand = [{'item': item, 'from': sink.area, 'at': now-timedelta(seconds=2), 'handler': source_handler}]
    inventory.verify = lambda im, box: {'dish': True, 'score': .9}
    inventory._put_down(stove, target, now, b'', image)
    inventory._open_waiting(now+timedelta(seconds=40))
    assert len(store.obligations(history=True)) == (0 if same_person else 1)


def test_undo_does_not_overwrite_later_human_decision(store):
    item = make_record(store)
    store.mark_not_dish([item], 'operator')
    store.action(item, 'reopen')
    with pytest.raises(ValueError):
        store.undo_dish_correction(item, 'operator')


def test_generic_cant_tell_is_not_a_training_label(store):
    item = make_record(store)
    store.action(item, 'dismiss', note="Can't tell")
    assert store.labeled_dish_samples() == []


def test_empty_reference_rejects_only_unchanged_spot(store):
    import cv2

    from wash_dishes.verifier import DishVerifier
    from wash_dishes.vision import LocalVision
    image = np.random.default_rng(8).integers(50, 200, (200, 300, 3), dtype=np.uint8)
    area = {'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .5, .5]}
    box = [.2, .5, .2, .15]
    engine = LocalVision(store.root)
    engine._cv, engine._np = cv2, np
    (store.root/'references').mkdir()
    np.save(store.root/'references/stove.npy', engine._zone_texture(image, area['box']))
    verifier = DishVerifier(store.root)
    assert verifier.empty_spot(image, box, [area])
    with_dish = image.copy()
    cv2.rectangle(with_dish, (60, 100), (120, 130), (240, 240, 240), -1)
    assert not verifier.empty_spot(with_dish, box, [area])


def test_source_negative_veto_and_failed_verifier_defer(store):
    from wash_dishes.verifier import store_hook

    class Verifier:
        ready = True
        def features(self, image, box):
            return np.array([.2, .3])
        def score(self, vector):
            return .99, True
        def negative(self, image, box):
            return {'margin': .12, 'label': 'capped bottle'}
        def empty_spot(self, image, box, areas):
            return False

    verifier = Verifier()
    hook = store_hook(verifier, store)
    verdict = hook(np.zeros((40, 40, 3), np.uint8), [.1, .1, .2, .2])
    assert verdict['dish'] is False and 'bottle' in verdict['reason']
    verifier.ready = False
    assert hook(None, [.1, .1, .2, .2])['dish'] is None


def test_kind_change_during_transfer_does_not_split_cookware(store, media):
    inventory = Inventory(store, media)
    now = utcnow()
    source = {'item': {'kind': 'pan'}, 'handler': {'track': 7}, 'at': now-timedelta(seconds=10)}
    inventory.in_hand = [source]
    assert inventory._carried({'kind': 'lid'}, {'track': 7}, now) is source


@pytest.mark.parametrize('localized', [True, False])
def test_occluded_sink_dish_abstention_needs_destination_handling(store, media, localized):
    inventory = Inventory(store, media)
    now = utcnow()
    area = {'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .4, .4]}
    dish = detection([.2, .5, .1, .1])
    handler = {'track': 7, 'at': now-timedelta(seconds=15), 'box': [.1, .3, .4, .5],
               'dishes': [dish] if localized else []}
    zone = Zone(area, settled_since=now, visitors={7: handler})
    inventory.sink_visits[7] = [{'start': now-timedelta(seconds=35), 'end': now-timedelta(seconds=20)}]
    inventory._add(zone, dish, now, b'', placed_at=None, verdict={'dish': True, 'score': .9})
    [item] = inventory.items.values()
    assert bool(item.get('clean_return')) is localized


def test_human_negative_survives_track_relink_and_undo(store, media):
    inventory = Inventory(store, media)
    now = utcnow()
    zone = Zone({'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .4, .4]}, settled_since=now)
    inventory.zones['stove'] = zone
    dish = detection([.2, .5, .1, .1])
    image = np.zeros((100, 100, 3), np.uint8)
    inventory.verify = lambda im, box: {'dish': True, 'score': .9}
    inventory._put_down(zone, dish, now, b'', image)
    [item] = inventory.items.values()
    inventory._open(item, zone, now)
    original = item['obligation_id']
    store.mark_not_dish([original], 'operator')
    inventory._initialize(zone, [dish], b'', now, image=image)
    inventory._pick_up(zone, item, now)
    inventory._put_down(zone, dish, now+timedelta(seconds=5), b'', image)
    inventory._open_waiting(now+timedelta(seconds=40))
    assert len(store.obligations(history=True)) == 1
    assert inventory.listing() == []
    store.undo_dish_correction(original, 'operator')
    inventory._initialize(zone, [dish], b'', now+timedelta(seconds=50), image=image)
    assert len(inventory.listing()) == 1


def test_sink_return_with_outstretched_arm_survives_delayed_area_scan(store, media):
    inventory = Inventory(store, media)
    now = utcnow()
    area = {'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.25, .65, .2, .2]}
    zone = Zone(area, settled_since=now)
    inventory.zones['stove'] = zone
    dish = detection([.328, .72, .057, .057], 'pot')
    # The detector's torso excludes the outstretched arm in the actual clean-return case.
    visitor = {'track': 7, 'at': now, 'box': [.403, .53, .106, .46], 'dishes': [dish]}
    zone.visitors[7] = visitor
    inventory.sink_visits[7] = [{'start': now-timedelta(seconds=25), 'end': now-timedelta(seconds=4)}]
    image = np.random.default_rng(14).integers(0, 255, (300, 500, 3), dtype=np.uint8)
    inventory._remember_sink_return(zone, visitor, image, now)
    zone.visitors.clear()
    inventory.verify = lambda im, box: {'dish': True, 'score': .9}
    inventory._initialize(zone, [dish], b'', now+timedelta(minutes=6), image=image)
    inventory._open_waiting(now+timedelta(minutes=7))
    assert store.obligations(history=True) == []
    assert store.events_after(0, ('clean_return_deferred',))


def test_later_use_of_clean_return_gets_a_fresh_clock(store, media):
    inventory = Inventory(store, media)
    now = utcnow()
    zone = Zone({'id': 'stove', 'name': 'Stove', 'role': 'dirty', 'box': [.1, .4, .5, .5]}, settled_since=now)
    inventory.zones['stove'] = zone
    dish = detection([.2, .5, .2, .15])
    image = np.random.default_rng(4).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    inventory._add(zone, dish, now-timedelta(hours=3), b'', placed_at=None,
                   verdict={'dish': True, 'score': .9}, image=image)
    [item] = inventory.items.values()
    item.update(clean_return=True, returned_at=(now-timedelta(hours=3)).isoformat())
    replacement = image.copy()
    replacement[100:130, 60:90] = 0
    replacement[100:130, 90:120] = 255
    mask = np.zeros((100, 100), bool)
    mask[20:50, 20:60] = True
    inventory._compare(zone, [dish], mask, now, image=replacement)
    inventory._open_waiting(now+timedelta(seconds=40))
    [record] = store.obligations(history=True)
    assert parse_stamp(record['created_at']) >= now.replace(microsecond=0)
