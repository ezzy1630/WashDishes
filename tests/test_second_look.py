from datetime import timedelta

import numpy as np
import pytest

from wash_dishes.store import CANT_TELL_NOTE, UNCLAIMED_NOTE, utcnow
from wash_dishes.verifier import MIN_EXAMPLES, train

pytest.importorskip("cv2")
from test_inventory import Kitchen  # noqa: E402


def separable(n, rng, *, overlap=0.0):
    """Dishes point one way in feature space and mistakes another, with some noise."""
    direction = rng.normal(size=16)
    y = rng.integers(0, 2, n).astype(float)
    X = rng.normal(size=(n, 16)) + np.outer(np.where(y == 1, 1.0, -1.0) * (1 - overlap), direction)
    return X / np.linalg.norm(X, axis=1, keepdims=True), y


def test_a_probe_is_trained_only_with_enough_labels_and_keeps_most_dishes():
    rng = np.random.default_rng(3)
    X, y = separable(2 * MIN_EXAMPLES - 10, rng)
    assert train(X[: MIN_EXAMPLES], y[: MIN_EXAMPLES]) is None
    X, y = separable(600, rng)
    probe = train(X, y)
    assert probe["held_out"]["auc"] > 0.9
    assert probe["held_out"]["dishes_kept"] >= 0.94 and probe["held_out"]["mistakes_dropped"] > 0.5


def test_a_box_the_second_look_rejects_opens_nothing_and_is_not_new_again(store, media):
    kitchen = Kitchen(store, media)
    kitchen.wait(5)
    looked = []

    def verify(image, box):
        looked.append(box)
        return {"dish": False, "score": 0.1, "sample": store.add_dish_sample([0.0] * 4, 0.1)}
    kitchen.inventory.verify = verify
    kitchen.visit("counter", change=kitchen.put("bottle", "cup", "counter"))
    kitchen.wait(40)
    assert kitchen.items() == [] and len(looked) == 1
    assert not any(c["change"] == "added" for c in kitchen.inventory.changes)
    # Someone works at the same counter again: the bottle is still known, not a new dish.
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter", dx=0.15))
    kitchen.wait(40)
    assert len(looked) == 2


def test_a_dish_the_second_look_accepts_opens_and_keeps_its_sample(store, media):
    kitchen = Kitchen(store, media)
    kitchen.wait(5)
    sample = store.add_dish_sample([0.5] * 4, 0.9)
    kitchen.inventory.verify = lambda image, box: {"dish": True, "score": 0.9, "sample": sample}
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    [added] = [c for c in kitchen.inventory.changes if c["change"] == "added"]
    assert added["dish_score"] == 0.9
    store.action(item["id"], "confirm", resident_id=store.add_resident("Casey")["id"])
    assert [s["label"] for s in store.labeled_dish_samples()] == [1]


@pytest.mark.parametrize(("action", "note", "label"), [("dismiss", "Not a dish", 0), ("dismiss", CANT_TELL_NOTE, 1), ("dismiss", UNCLAIMED_NOTE, None)])
def test_review_answers_become_second_look_labels(store, action, note, label):
    item = store.create_obligation(None, [{"kind": "plate", "quantity": 1}], source="vision", candidate=True, at=utcnow() - timedelta(minutes=5))
    store.link_dish_sample(store.add_dish_sample([0.1] * 4, 0.5), item["id"])
    store.action(item["id"], action, note=note)
    assert [s["label"] for s in store.labeled_dish_samples()] == ([] if label is None else [label])
