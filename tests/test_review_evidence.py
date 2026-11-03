"""Priority must surface useful photos without turning visibility into guilt."""
import json

import pytest

from wash_dishes.review_evidence import kitchen_probe, rate, signals


@pytest.mark.parametrize('arrival,people,confident,near,strip,photo,expected', [
    (True, True, True, False, True, True, 'strong'),
    (True, True, True, True, True, True, 'fair'),
    (False, True, True, False, True, True, 'fair'),
    (True, False, True, False, True, True, 'fair'),
    (True, True, False, False, True, True, 'fair'),
    (False, False, True, False, False, True, 'fair'),
    (False, False, False, False, True, True, 'weak'),
    (False, False, False, False, False, True, 'weak'),
    (True, True, True, False, False, False, 'weak'),
])
def test_rating(arrival, people, confident, near, strip, photo, expected):
    capture = {'window': {'arrival_recorded': arrival}, 'frames': [{'person_boxes': [[0, 0, 1, 1]] if people else []}]}
    result = rate(capture, has_filmstrip=strip, has_photo=photo,
                  dish_confident=confident, remembered_near_match=near)
    assert result['evidence'] == expected
    assert bool(result['reason']) == (expected == 'weak')


def test_weak_reasons_are_plain_and_missing_metadata_is_not_an_arrival():
    assert rate({}, has_filmstrip=False, has_photo=False, dish_confident=False, remembered_near_match=False) == {
        'evidence': 'weak', 'reason': 'Arrival not recorded'}
    result = rate({'frames': []}, has_filmstrip=True, has_photo=True, dish_confident=False, remembered_near_match=False)
    assert result['reason'] == 'Arrival not recorded · No one in the photos'
    assert rate({'window': {'arrival_recorded': True}}, has_filmstrip=False, has_photo=False,
                dish_confident=True, remembered_near_match=False)['reason'] == 'Arrival photos unavailable'


def test_saved_features_use_kitchen_confidence_and_same_area_memory(tmp_path):
    models = tmp_path / 'models'
    models.mkdir()
    path = models / 'dish-probe-kitchen.json'
    path.write_text(json.dumps({'weights': [2.] + [0.] * 511, 'bias': 0., 'threshold': .5, 'proof_threshold': .8}))
    probe = kitchen_probe(tmp_path)
    vector = json.dumps([1.] + [0.] * 511)
    area = {'id': 'counter', 'role': 'dirty'}
    memory = {'area_id': 'counter', 'area_role': 'dirty', 'embedding': [1.] + [0.] * 511}
    assert signals(vector, probe, [memory], area) == {'dish_confident': True, 'remembered_near_match': True}
    assert signals(vector, probe, [memory], {'id': 'other', 'role': 'dirty'})['remembered_near_match'] is False
    assert signals(vector, probe, [memory], {'id': 'counter', 'role': 'clean'})['remembered_near_match'] is False
    assert signals(json.dumps([-1.] + [0.] * 511), probe, [], area)['dish_confident'] is False
    path.write_text('{}')
    assert kitchen_probe(tmp_path) is None
    assert signals(None, probe, [], area)['dish_confident'] is False
    assert signals('[1]', probe, [], area)['dish_confident'] is False
    assert signals(vector, None, [], area)['dish_confident'] is False
