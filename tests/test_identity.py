import math
from datetime import datetime, timedelta, timezone

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
    morning = datetime(2026, 10, 21, 15, tzinfo=timezone.utc)
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


def test_a_face_guess_cannot_teach_an_outfit_without_a_confirmation(store):
    alex = store.add_resident("Alex")["id"]
    store.add_resident("Sam")
    face = unit(0.2, 0.9, 0.1, 0.3)
    now = utcnow()
    first = sighting(store, None, now - timedelta(hours=2), outfit=GREEN_SHIRT, face=face)
    resident, _, _, cues = store.suggest_identity({"outfit": GREEN_SHIRT, "face": face}, at=now - timedelta(hours=2), enrolled_faces={alex: [face]})
    assert resident == alex and cues["face"] > 0.99
    assert store.suggest_identity({"outfit": GREEN_SHIRT}, at=now)[0] is None
    store.hint_samples(first["id"], alex)
    assert store.suggest_identity({"outfit": GREEN_SHIRT}, at=now)[0] is None
    store.action(first["id"], "confirm", resident_id=alex)
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


def test_outfit_stops_at_local_midnight_even_two_minutes_apart(store):
    alex = store.add_resident('Alex')['id']
    before = datetime(2026, 10, 22, 6, 59, tzinfo=timezone.utc)
    sighting(store, alex, before, outfit=RED_SHIRT)
    assert store.suggest_identity({'outfit': RED_SHIRT}, at=before + timedelta(minutes=2))[0] is None


def test_same_local_day_outfit_can_propagate_more_than_sixteen_hours(store):
    alex = store.add_resident('Alex')['id']
    early = datetime(2026, 10, 21, 7, 1, tzinfo=timezone.utc)
    sighting(store, alex, early, outfit=RED_SHIRT)
    assert store.suggest_identity({'outfit': RED_SHIRT}, at=early + timedelta(hours=22))[0] == alex


def test_many_weak_cues_cannot_make_a_confident_guess(store):
    alex = store.add_resident('Alex')['id']
    at = utcnow()
    sighting(store, alex, at, face=unit(.6, .8, 0, 0), head=unit(.88, 0, .475, 0), outfit=unit(.9, 0, 0, .436))
    sighting(store, store.add_resident('Sam')['id'], at, face=unit(.55, .835, 0, 0), head=unit(.86, 0, .51, 0))
    assert store.suggest_identity({'face': RED_SHIRT, 'head': RED_SHIRT, 'outfit': RED_SHIRT}, at=at)[0] is None


def test_same_outfit_basis_names_the_reference_and_clears_on_abstention(store):
    alex = store.add_resident('Alex')['id']
    at = datetime(2026, 10, 22, 2, 32, tzinfo=timezone.utc)
    sighting(store, alex, at, outfit=RED_SHIRT)
    target = sighting(store, None, at + timedelta(minutes=5), outfit=RED_SHIRT)
    resident, score, _, cues = store.suggest_identity({'outfit': RED_SHIRT}, at=at)
    store.set_suggestion(target['id'], resident, score, cues)
    assert store.suggestion_basis([target['id']]) == {target['id']: 'same outfit as the 7:32 PM plate'}
    store.set_suggestion(target['id'], None, None)
    assert store.suggestion_basis([target['id']]) == {}


def test_proof_capture_replaces_old_named_person_and_rejects_later_log_samples(store):
    import pytest
    alex = store.add_resident('Alex')['id']
    at = utcnow()
    item = sighting(store, alex, at, outfit=BLUE_SHIRT)
    store.add_evidence(item['id'], 'proof', 'proof.jpg', 'snapshot', 'Who put it down (from footage)', at=at)
    store.replace_putdown_samples(item['id'], {'outfit': RED_SHIRT}, 'proof', at=at)
    assert store.conn.execute('SELECT resident_id FROM person_samples WHERE obligation_id=?', (item['id'],)).fetchone()[0] is None
    with pytest.raises(ValueError, match="put-down picture"):
        store.add_person_sample(item['id'], BLUE_SHIRT, at=at + timedelta(minutes=10))
    store.action(item['id'], 'confirm', resident_id=alex)
    assert store.sighting_cues(item['id'])[0] == {'outfit': RED_SHIRT}


def test_identical_whole_visit_clip_faces_cannot_bias_different_dishes(store):
    alex = store.add_resident('Alex')['id']
    at = utcnow()
    item = sighting(store, alex, at, outfit=BLUE_SHIRT)
    store.add_evidence(item['id'], 'wholevisit', 'same.mp4', 'clip', 'Putting the dish down', at=at)
    # Historical rows are retained, but never used as a query or teaching face.
    store.conn.execute("INSERT INTO person_samples(obligation_id,resident_id,created_at,embedding,evidence_id,cue) VALUES(?,?,?,?,?,'face')",
                       (item['id'], alex, at.isoformat(), '[1,0,0,0]', 'wholevisit'))
    store.conn.commit()
    assert 'face' not in store.sighting_cues(item['id'])[0]
    assert store.suggest_identity({'face': RED_SHIRT}, at=at)[0] is None
    assert store.conn.execute("SELECT count(*) FROM person_samples WHERE cue='face'").fetchone()[0] == 1
    assert not store.merge_sighting_face(item['id'], RED_SHIRT, 'wholevisit')


def test_matching_pose_cannot_outweigh_different_clothing_colours(store):
    alex = store.add_resident('Alex')['id']
    # Real descriptor layout: 512 deep features and 80 colour bins. Deep features are identical;
    # the colour match is only .91, producing a deceptively high combined similarity of .96.
    deep = [.75] + [0.0] * 511
    first = unit(*(deep + [.66] + [0.0] * 79))
    different = unit(*(deep + [.66 * .91, .66 * math.sqrt(1 - .91 ** 2)] + [0.0] * 78))
    at = utcnow()
    sighting(store, alex, at, outfit=first)
    assert store.suggest_identity({'outfit': different}, at=at)[0] is None
    assert store.suggest_identity({'outfit': first}, at=at)[0] == alex
