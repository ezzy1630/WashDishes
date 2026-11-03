import json
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from wash_dishes.app import create_app
from wash_dishes.config import Config
from wash_dishes.inventory import Inventory, Zone
from wash_dishes.remembered import MAX_EXEMPLARS, SIMILARITY_THRESHOLD, unit_embedding
from wash_dishes.store import utcnow
from wash_dishes.verifier import DishVerifier, store_hook

AREA = {'id': 'table', 'name': 'Table', 'role': 'dirty', 'box': [0, 0, 1, 1]}
VECTOR = [1.] + [0.] * 511


def corrected(store, verdict='not_dish', vector=VECTOR):
    store.set_settings({'areas': [AREA]})
    item = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], location='Table', candidate=True)
    if vector is not None:
        store.link_dish_sample(store.add_dish_sample(vector, .9), item['id'])
    getattr(store, 'mark_' + verdict)([item['id']], 'resident:1')
    return item


@pytest.mark.parametrize('verdict,label', [('not_dish', 0), ('clean', 1)])
def test_correction_remembers_immediately_persists_and_undo_removes(store, verdict, label):
    item = corrected(store, verdict)
    [memory] = store.remembered_objects()
    assert memory['by'] == 'resident:1' and memory['created_at'] and memory['area_id'] == 'table'
    assert store.remembered_match(VECTOR, AREA)['id'] == memory['id']
    assert store.labeled_dish_samples()[0]['label'] == label
    # Idempotent repeated answer doesn't grow memory.
    getattr(store, 'mark_' + verdict)([item['id']], 'resident:1')
    assert len(store.remembered_objects()) == 1
    store.undo_dish_correction(item['id'], 'operator')
    assert store.remembered_objects() == [] and store.remembered_match(VECTOR, AREA) is None
    assert store.labeled_dish_samples() == []


def test_matching_is_near_exact_and_area_scoped(store):
    corrected(store)
    assert store.remembered_match(VECTOR, AREA | {'id': 'sink'}) is None
    assert store.remembered_match(VECTOR, AREA | {'role': 'clean'}) is None
    assert store.remembered_match(VECTOR, None) is None
    for score in (SIMILARITY_THRESHOLD - .001, SIMILARITY_THRESHOLD + .001):
        vector = [score, (1-score**2)**.5] + [0.] * 510
        assert bool(store.remembered_match(vector, AREA)) == (score >= SIMILARITY_THRESHOLD)


def test_missing_features_duplicate_and_cap(store):
    corrected(store, vector=None)
    assert not store.remembered_objects()
    for _ in range(MAX_EXEMPLARS + 1):
        corrected(store)
    assert len(store.remembered_objects()) == MAX_EXEMPLARS
    original = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}])
    extra = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], location='Table')
    store.link_dish_sample(store.add_dish_sample(VECTOR, .9), extra['id'])
    store.mark_duplicate(extra['id'], original['id'], 'operator')
    assert len(store.remembered_objects()) == MAX_EXEMPLARS


def test_stacked_undo_and_later_decisions(store):
    item = corrected(store)
    store.mark_clean([item['id']], 'operator')
    store.undo_dish_correction(item['id'], 'operator')
    assert store.remembered_objects()[0]['verdict'] == 'not_dish'
    store.action(item['id'], 'reopen')
    with pytest.raises(ValueError):
        store.undo_dish_correction(item['id'], 'operator')
    assert not store.remembered_objects()


@pytest.mark.parametrize('bad', [[0.] * 512, [float('nan')] * 512, [1.], [float('inf')] * 512])
def test_invalid_embeddings(bad):
    with pytest.raises(ValueError):
        unit_embedding(bad)


def test_live_hook_pending_until_forget_then_record(store, media, monkeypatch):
    corrected(store)
    verifier = DishVerifier(store.root, embedder=lambda crops: np.array([VECTOR]))
    monkeypatch.setattr(DishVerifier, 'ready', property(lambda self: True))
    monkeypatch.setattr(verifier, 'score', lambda vector: (.9, True))
    monkeypatch.setattr(verifier, 'negative', lambda *args: None)
    monkeypatch.setattr(verifier, 'empty_spot', lambda *args: False)
    inventory = Inventory(store, media)
    inventory.verify = store_hook(verifier, store)
    zone = Zone(AREA, settled_since=utcnow())
    inventory.zones[AREA['id']] = zone
    image = np.zeros((100, 100, 3), np.uint8)
    inventory._put_down(zone, {'box': [.2, .2, .1, .1], 'kind': 'cup', 'score': .9}, utcnow(), b'', image)
    [candidate] = inventory.items.values()
    assert candidate['verification_pending'] and candidate['obligation_id'] is None
    assert any('Remembered object' in e['description'] for e in store.activity())
    store.forget_object(store.remembered_objects()[0]['id'])
    inventory._retry_verification(candidate, image, utcnow() + timedelta(seconds=30))
    inventory._open(candidate, zone, utcnow() + timedelta(seconds=60))
    assert candidate['obligation_id'] is not None


def test_operator_only_list_remove_and_durable_storage(tmp_path):
    app = create_app(Config(tmp_path/'data', password='operator', workers=False))
    store = app.state.store
    corrected(store)
    assert json.loads(store.conn.execute('SELECT embedding FROM remembered_objects').fetchone()[0]) == VECTOR
    with TestClient(app) as client:
        assert client.get('/api/remembered-objects').status_code == 401
        assert client.delete('/api/remembered-objects/1').status_code == 401
        result = client.post('/api/login', json={'password': 'operator'})
        client.headers['X-CSRF-Token'] = result.json()['csrf_token']
        [memory] = client.get('/api/remembered-objects').json()['items']
        assert 'embedding' not in memory
        assert client.delete(f"/api/remembered-objects/{memory['id']}").status_code == 200
        assert client.get('/api/remembered-objects').json() == {'items': []}
