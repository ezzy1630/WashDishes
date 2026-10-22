import io
import shutil

import pytest
from PIL import Image
from test_proofs import found, late_dish
from test_sequences import jpeg

from wash_dishes import collage, ledger
from wash_dishes.proofs import Proofs

PERSON = [.25, .2, .2, .5]


def proof(store, media, proofs, resident_id=None):
    item = late_dish(store, media)
    assert proofs.run_once(found, identify=lambda *_: PERSON) == item["id"]
    if resident_id:
        store.action(item["id"], "confirm", resident_id=resident_id)
    return item["id"]


def test_only_named_proofs_count_and_washing_later_does_not_undo_them(store, media):
    proofs = Proofs(store.root, store, media)
    sam, kai = store.add_resident("Sam")["id"], store.add_resident("Kai")["id"]
    washed, kept = proof(store, media, proofs, sam), proof(store, media, proofs, sam)
    disputed, rejected = proof(store, media, proofs, sam), proof(store, media, proofs, sam)
    proof(store, media, proofs)  # nobody named: a guess is not a record
    store.action(washed, "resolve")
    store.action(disputed, "dispute", note="Not mine")
    proofs.reject(rejected)
    assert [(p["name"], p["count"]) for p in ledger.tally(store)] == [("Sam", 2), ("Kai", 0)]
    record = ledger.person(store, sam)
    assert record["count"] == 2 and {p["id"] for p in record["items"]} == {washed, kept}
    assert record["items"][0]["put_down_at"] >= record["items"][1]["put_down_at"]
    assert ledger.person(store, kai)["items"] == []


def test_named_pictures_outlive_the_dish_but_not_the_kept_photos(store, media):
    proofs = Proofs(store.root, store, media)
    sam = store.add_resident("Sam")["id"]
    oid = proof(store, media, proofs, sam)
    store.action(oid, "resolve")
    assert proofs.items() == []
    assert ledger.picture(store, oid).startswith(b"\xff\xd8")
    [entry] = ledger.person(store, sam)["items"]
    assert entry["image_url"] and entry["dish"] == "cup"
    shutil.rmtree(proofs.dir / str(oid))  # a week on, proofs.cleanup removes the photos
    [entry] = ledger.person(store, sam)["items"]
    assert entry["image_url"] is None and entry["put_down_at"]
    with pytest.raises(LookupError):
        ledger.picture(store, oid)


def test_people_routes_and_the_picture_of_a_named_dish_that_is_gone(authenticated):
    client, runtime = authenticated, authenticated.app.state.runtime
    sam = runtime.store.add_resident("Sam")["id"]
    oid = proof(runtime.store, runtime.media, runtime.proofs, sam)
    assert {p["name"]: p["count"] for p in client.get("/api/proofs").json()["people"]} == {"Sam": 1}
    runtime.store.action(oid, "resolve")
    [entry] = client.get(f"/api/people/{sam}").json()["items"]
    assert client.get(entry["image_url"]).headers["content-type"] == "image/jpeg"
    assert client.get("/api/people").json()["people"][0]["count"] == 1
    assert client.get("/api/people/999").status_code == 404


def test_a_new_design_is_never_hidden_behind_a_picture_cached_before_it(authenticated):
    client, runtime = authenticated, authenticated.app.state.runtime
    oid = proof(runtime.store, runtime.media, runtime.proofs)
    (runtime.proofs.dir / str(oid) / "collage-0.jpg").write_bytes(b"the previous design")
    [item] = client.get("/api/proofs").json()["items"]
    assert item["image_url"].endswith(f".{collage.VERSION}")
    assert client.get(item["image_url"]).content.startswith(b"\xff\xd8")


def test_reject_passes_its_reason_and_not_a_dish_closes_the_record(authenticated):
    client, runtime = authenticated, authenticated.app.state.runtime
    wrong, not_dish = proof(runtime.store, runtime.media, runtime.proofs), proof(runtime.store, runtime.media, runtime.proofs)
    assert client.post(f"/api/proofs/{wrong}/reject", json={"reason": "bored"}).status_code == 422
    assert client.post(f"/api/proofs/{wrong}/reject", json={"reason": "wrong_photos"}).status_code == 200
    assert client.post(f"/api/proofs/{not_dish}/reject", json={"reason": "not_a_dish"}).status_code == 200
    reasons = dict(runtime.store.conn.execute("SELECT obligation_id, json_extract(data,'$.reason') FROM proofs").fetchall())
    assert reasons == {wrong: "wrong_photos", not_dish: "not_a_dish"}
    assert runtime.store.obligation(not_dish)["lifecycle"] == "dismissed"
    assert runtime.store.obligation(wrong)["lifecycle"] == "open"
    assert client.get("/api/proofs").json()["items"] == []


def render(meta=None, name=None):
    at = 1792636372.0
    base = {"dish": "pan", "location": "Dining table (visible portion)", "box": [.70, .80, .04, .05],
            "before_at": at - 20, "putting_at": at, "left_at": at + 25, "late_at": at + 2 * 3600 + 90}
    pictures = {key: jpeg((40 + i * 50, 60, 60)) for i, key in enumerate(collage.STAGES)}
    data = collage.render(base | (meta or {}), pictures, name)
    with Image.open(io.BytesIO(data)) as image:
        return image.size, len(data)


def test_the_picture_takes_long_names_and_missing_person_or_late_boxes():
    plain = render()
    assert plain[0][0] == collage.WIDTH and plain[1] < 600_000
    assert render({"person_box": [.6, .5, .2, .5], "late_box": [.71, .8, .04, .05], "left_behind": True}, "Sam")[0] == plain[0]
    assert render({"dish": "2 cups, bowl"}, "Maximilian Alexander-Fitzgerald the Third of Kitchen")[0] == plain[0]
    assert render({"box": [.98, .97, .02, .03], "person_box": [0.0, .66, .11, .34]})[0] == plain[0]


def test_captions_say_what_happened_in_plain_words():
    assert collage._later(2 * 3600 + 59 * 60) == "2 hours later" and collage._later(3600) == "1 hour later"
    assert collage.place("Dining table (visible portion)") == "Dining table" and collage.place(None) == "Kitchen"
    # One dish is named only when the second look agrees; "bowl" on a picture of a pan is worse than "dish".
    one = [{"kind": "bowl", "quantity": 1}]
    assert collage.dish_name(one, "bowl") == "bowl" and collage.dish_name(one, "frying pan") == "dish"
    assert collage.dish_name([{"kind": "cup", "quantity": 1}], "drinking glass") == "cup"
    assert collage.dish_name([{"kind": "cup", "quantity": 2}], "bowl") == "2 cups"


def test_panels_leave_out_the_camera_clock_and_logo_when_the_dish_and_person_still_fit():
    aspect = collage.PANEL_W / collage.PANEL_H * 9 / 16
    def marked(crop):
        return any(crop[0] < x + w and x < crop[2] and crop[1] < y + h and y < crop[3] for x, y, w, h in collage.CAMERA_MARKS)
    beside_logo, person = [.20, .82, .05, .06], [.25, .45, .1, .45]
    for crop in (collage._crop([beside_logo], None, aspect), collage._crop([beside_logo], person, aspect)):
        assert not marked(crop) and crop[0] <= .20 and crop[2] >= .25 and crop[1] <= .82 and crop[3] >= .88
    # Under the logo itself, the dish wins.
    assert marked(collage._crop([[.02, .90, .05, .05]], None, aspect))
