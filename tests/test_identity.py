import math
from datetime import timedelta

from wash_dishes.store import utcnow


def unit(*values):
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


RED_SHIRT, BLUE_SHIRT, GREEN_SHIRT = unit(1, 0, 0, 0), unit(0, 1, 0, 0), unit(0, 0, 1, 0)
CURLY, STRAIGHT = unit(1, 0.1, 0, 0), unit(0, 0, 0.1, 1)


def sighting(store, resident, at, **cues):
    item = store.create_obligation(None, [{"kind": "plate", "quantity": 1}], candidate=True, at=at)
    for cue, vector in cues.items():
        store.add_person_sample(item["id"], vector, at=at, cue=cue, context="sink")
    if resident:
        store.action(item["id"], "confirm", resident_id=resident)
    return item


def test_one_confirmation_names_the_same_outfit_for_the_rest_of_the_day(store):
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    morning = utcnow() - timedelta(hours=10)
    sighting(store, alex, morning, outfit=RED_SHIRT, head=CURLY)
    sighting(store, sam, morning, outfit=BLUE_SHIRT, head=STRAIGHT)
    resident, _, _, cues = store.suggest_identity({"outfit": unit(1, 0.05, 0, 0)}, at=morning + timedelta(hours=6))
    assert resident == alex and "outfit_today" in cues


def test_tomorrows_outfit_needs_a_lasting_cue(store):
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    yesterday = utcnow() - timedelta(hours=30)
    sighting(store, alex, yesterday, outfit=RED_SHIRT, head=CURLY)
    sighting(store, sam, yesterday, outfit=BLUE_SHIRT, head=STRAIGHT)
    now = utcnow()
    # Same red shirt a day later is weak evidence on its own.
    assert store.suggest_identity({"outfit": RED_SHIRT}, at=now)[0] is None
    # A new outfit, but the same hair from above.
    resident, _, _, cues = store.suggest_identity({"outfit": GREEN_SHIRT, "head": unit(1, 0.12, 0, 0)}, at=now)
    assert resident == alex and "head" in cues


def test_a_face_match_names_the_outfit_so_later_sightings_without_a_face_are_recognised(store):
    alex = store.add_resident("Alex")["id"]
    store.add_resident("Sam")
    face = unit(0.2, 0.9, 0.1, 0.3)
    now = utcnow()
    first = sighting(store, None, now - timedelta(hours=2), outfit=GREEN_SHIRT, face=face)
    resident, _, _, cues = store.suggest_identity({"outfit": GREEN_SHIRT, "face": face}, at=now - timedelta(hours=2), enrolled_faces={alex: [face]})
    assert resident == alex and cues["face"] > 0.99
    assert store.suggest_identity({"outfit": GREEN_SHIRT}, at=now)[0] is None
    store.hint_samples(first["id"], alex)
    assert store.suggest_identity({"outfit": GREEN_SHIRT}, at=now)[0] == alex


def test_two_housemates_who_look_alike_get_no_suggestion(store):
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    now = utcnow()
    sighting(store, alex, now - timedelta(hours=1), outfit=RED_SHIRT)
    sighting(store, sam, now - timedelta(hours=1), outfit=unit(1, 0.02, 0, 0))
    assert store.suggest_identity({"outfit": RED_SHIRT}, at=now)[0] is None


def test_a_dismissed_sighting_teaches_nothing(store):
    alex = store.add_resident("Alex")["id"]
    store.add_resident("Sam")
    now = utcnow()
    item = sighting(store, alex, now - timedelta(hours=1), outfit=RED_SHIRT)
    store.action(item["id"], "dismiss")
    assert store.suggest_identity({"outfit": RED_SHIRT}, at=now)[0] is None


def test_confirming_a_person_also_updates_older_waiting_camera_suggestions(store):
    alex = store.add_resident("Alex")["id"]
    now = utcnow()
    older = store.create_obligation(None, [{"kind": "cup", "quantity": 1}], candidate=True, source="vision", at=now - timedelta(hours=2))
    store.add_person_sample(older["id"], RED_SHIRT, at=now - timedelta(hours=2), context="sink")
    first = sighting(store, None, now, outfit=RED_SHIRT)
    store.action(first["id"], "confirm", resident_id=alex)
    updated = store.obligation(older["id"])
    assert updated["suggested_resident_id"] == alex
    assert updated["resident_id"] is None and updated["confidence"] == "candidate"
    assert not updated["overdue_verified"]
    # Correcting the teaching example must remove a suggestion it no longer supports.
    store.action(first["id"], "dismiss")
    assert store.obligation(older["id"])["suggested_resident_id"] is None


def test_future_outfit_is_not_a_same_day_match_for_old_footage(store):
    alex = store.add_resident("Alex")["id"]
    now = utcnow()
    sighting(store, alex, now, outfit=RED_SHIRT)
    assert store.suggest_identity({"outfit": RED_SHIRT}, at=now - timedelta(hours=30))[0] is None
