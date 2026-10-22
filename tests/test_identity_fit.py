import math
from datetime import timedelta

from wash_dishes.identity_fit import add_faces
from wash_dishes.store import utcnow


def unit(*values):
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def test_faces_added_to_old_sightings_carry_their_names_into_later_days(store):
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    yesterday = utcnow() - timedelta(days=1)
    faces = {}
    for resident, shirt, face in ((alex, unit(1, 0, 0, 0), unit(1, 0.1, 0, 0)), (sam, unit(0, 1, 0, 0), unit(0, 0, 1, 0.1))):
        item = store.create_obligation(None, [{"kind": "plate", "quantity": 1}], candidate=True, at=yesterday)
        evidence = f"crop{resident}"
        store.add_evidence(item["id"], evidence, f"{evidence}.jpg", "snapshot", "Person", at=yesterday)
        store.add_person_sample(item["id"], shirt, evidence, at=yesterday, cue="outfit", context="sink")
        store.action(item["id"], "confirm", resident_id=resident)
        faces[f"{evidence}.jpg"] = face

    assert add_faces(store, lambda path: faces[path.name])["faces_added"] == 2
    # Today: new clothes, but the face matches the sighting already named Alex.
    assert store.suggest_identity({"outfit": unit(0, 0, 0, 1), "face": unit(1, 0.12, 0, 0)}, at=utcnow())[0] == alex
    # Running again adds nothing twice.
    assert add_faces(store, lambda path: faces[path.name])["faces_added"] == 0


def test_an_unanchored_put_down_clip_does_not_name_the_person_in_a_crop(store):
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    yesterday = utcnow() - timedelta(days=1)
    item = store.create_obligation(None, [{"kind": "plate", "quantity": 1}], candidate=True, at=yesterday)
    store.add_evidence(item["id"], "crop", "crop.jpg", "snapshot", "Who handled it (zoomed)", at=yesterday)
    store.add_evidence(item["id"], "clip", "clip.mp4", "clip", "Putting the dish down", at=yesterday)
    store.add_person_sample(item["id"], unit(1, 0, 0, 0), "crop", at=yesterday, cue="outfit", context="sink")
    store.action(item["id"], "confirm", resident_id=alex)
    other = store.create_obligation(None, [{"kind": "cup", "quantity": 1}], candidate=True, at=yesterday)
    store.add_person_sample(other["id"], unit(0, 1, 0, 0), at=yesterday, cue="outfit", context="sink")
    store.add_person_sample(other["id"], unit(0, 0, 1, 0), at=yesterday, cue="face", context="sink")
    store.action(other["id"], "confirm", resident_id=sam)

    # A face somewhere in the visit cannot establish whose body was labelled.
    first = add_faces(store, lambda path: None, lambda path: unit(1, 0.1, 0, 0))
    assert first["clip_faces_merged"] == 0
    assert store.suggest_identity({"outfit": unit(0, 0, 0, 1), "face": unit(1, 0.12, 0, 0)}, at=utcnow())[0] is None
    assert add_faces(store, lambda path: None, lambda path: unit(1, 0.1, 0, 0))["clip_faces_merged"] == 0


def test_rebuild_replaces_quarantined_clip_cue_without_deleting_history(store):
    import json
    alex = store.add_resident('Alex')['id']
    at = utcnow()
    item = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}], candidate=True, at=at)
    store.add_evidence(item['id'], 'body', 'body.jpg', 'snapshot', 'Who handled it (zoomed)', at=at)
    store.add_evidence(item['id'], 'visit', 'visit.mp4', 'clip', 'Putting the dish down', at=at)
    store.add_person_sample(item['id'], [1, 0, 0, 0], 'body', at=at)
    store.add_person_sample(item['id'], [0, 0, 1, 0], 'visit', at=at, cue='face')
    store.action(item['id'], 'confirm', resident_id=alex)
    report = add_faces(store, lambda _: [1, 0, 0, 0])
    assert report['faces_added'] == 1
    assert store.sighting_cues(item['id'])[0]['face'] == [1, 0, 0, 0]
    old = store.conn.execute("SELECT embedding FROM person_samples WHERE evidence_id='visit'").fetchone()
    assert json.loads(old[0]) == [0, 0, 1, 0]
    assert add_faces(store, lambda _: [1, 0, 0, 0])['faces_added'] == 0
