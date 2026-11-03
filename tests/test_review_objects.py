"""Missing or contradictory machine evidence must never hide an entire mixed visit."""
import json

import pytest

from wash_dishes.review_objects import probe, signals
from wash_dishes.store import Store


def model():
    return {'weights': [10.] + [0.] * 511, 'bias': 0., 'threshold': .35, 'retain_threshold': .65}


@pytest.mark.parametrize('embedding', [None, '[1]', '{}', 'null', '[NaN]', 'false', '"bad"'])
def test_missing_or_invalid_features_abstain(embedding):
    assert signals(embedding, model()) == {'non_dish': False, 'dish_likely': False}


def test_tight_crop_is_scored_separately_and_missing_probe_abstains():
    assert signals(json.dumps([1.] + [0.] * 511), model()) == {'non_dish': False, 'dish_likely': True}
    assert signals(json.dumps([-1.] + [0.] * 511), model()) == {'non_dish': True, 'dish_likely': False}
    assert signals(json.dumps([1.] + [0.] * 511), None) == {'non_dish': False, 'dish_likely': False}


def test_operator_replacement_and_invalid_model_abstention(tmp_path):
    path = tmp_path / 'models' / 'dish-review-probe.json'
    path.parent.mkdir()
    assert probe(tmp_path) is None  # the bundled candidate must not auto-adopt
    path.write_text(json.dumps(model()))
    assert probe(tmp_path) == model()
    for value in [{}, model() | {'weights': []}, model() | {'bias': float('nan')},
                  model() | {'threshold': .9, 'retain_threshold': .2}]:
        path.write_text(json.dumps(value))
        assert probe(tmp_path) is None


def test_bundled_candidate_is_valid_but_requires_explicit_adoption(tmp_path):
    from pathlib import Path

    from wash_dishes import review_objects

    candidate = Path(review_objects.__file__).with_name('review-probe.json').read_text()
    models = tmp_path / 'models'
    models.mkdir()
    (models / 'dish-review-probe.json').write_text(candidate)
    assert probe(tmp_path) is not None


def test_tight_features_survive_restart_without_changing_contextual_training(tmp_path):
    store = Store(tmp_path)
    sample = store.add_dish_sample([0., 1.] + [0.] * 510, .9, tight_embedding=[2.] + [0.] * 511)
    store.label_dish_sample(sample, True)
    store.conn.close()
    store = Store(tmp_path)
    assert json.loads(store.conn.execute('SELECT tight_embedding FROM dish_samples').fetchone()[0]) == [1.] + [0.] * 511
    assert store.labeled_dish_samples()[0]['embedding'] == [0., 1.] + [0.] * 510
    store.conn.close()


def test_live_verifier_keeps_its_existing_tight_crop_without_changing_admission(tmp_path):
    import numpy as np

    from wash_dishes.verifier import store_hook

    class Verifier:
        ready = True

        def features(self, image, box):
            return np.array([1.] + [0.] * 511)

        def score(self, vector):
            return .99, True

        def negative(self, image, box):
            return {'margin': 0., 'label': 'jar', 'embedding': [0., 1.] + [0.] * 510}

        def empty_spot(self, image, box, areas):
            return False

    store = Store(tmp_path)
    result = store_hook(Verifier(), store)(None, [.1, .1, .2, .2])
    assert result['dish'] is True
    saved = store.conn.execute('SELECT embedding,tight_embedding FROM dish_samples WHERE id=?', (result['sample'],)).fetchone()
    assert json.loads(saved['embedding']) == [1.] + [0.] * 511
    assert json.loads(saved['tight_embedding']) == [0., 1.] + [0.] * 510
    store.close()


@pytest.mark.parametrize('bad', [[float('nan')] * 512, [1., 0.]])
def test_bad_tight_features_defer_instead_of_crashing_the_camera(tmp_path, bad):
    import numpy as np

    from wash_dishes.verifier import store_hook

    class Verifier:
        ready = True

        def features(self, image, box):
            return np.array([1.] + [0.] * 511)

        def score(self, vector):
            return .99, True

        def negative(self, image, box):
            return {'margin': 0., 'label': 'jar', 'embedding': bad}

        def empty_spot(self, image, box, areas):
            return False

    store = Store(tmp_path)
    result = store_hook(Verifier(), store)(None, [.1, .1, .2, .2])
    assert result['dish'] is None
    assert store.conn.execute('SELECT count(*) FROM dish_samples').fetchone()[0] == 0
    store.close()
