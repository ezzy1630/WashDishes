from datetime import timedelta

from test_sequences import jpeg

from wash_dishes.proofs import Proofs
from wash_dishes.store import stamp, utcnow

LATE = "Matching dish still visible after the deadline; dirty status requires review"
BOX = [.3, .5, .1, .1]


def late_dish(store, media, *, seen_minutes_ago=5, timing_known=True):
    now = utcnow()
    item = store.create_obligation(None, [{"kind": "cup", "quantity": 1}], candidate=True, source="vision",
                                   at=now - timedelta(hours=3), box=BOX, location="Dining table", timing_known=timing_known)
    media.save_snapshot(item["id"], jpeg((90, 90, 90)), LATE, at=now - timedelta(minutes=20), capture={"stage": "later", "box": BOX})
    seen(store, item["id"], seen_minutes_ago)
    return item


def seen(store, obligation_id, minutes_ago):
    with store.lock, store.conn:
        store.conn.execute("UPDATE obligations SET last_seen_at=? WHERE id=?", (stamp(utcnow() - timedelta(minutes=minutes_ago)), obligation_id))


def found(box, logged, until):
    return {key: {"at": logged - 60 + i * 10, "diff": 1.0, "jpeg": jpeg((40 + i * 60, 40, 40))}
            for i, key in enumerate(("before", "putting", "left"))}


def test_a_late_dish_is_traced_once_and_shown_only_while_it_is_out(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    assert proofs.run_once(found) == item["id"]
    assert proofs.run_once(found) is None
    [shown] = proofs.items()
    assert shown["dish"] == "cup" and shown["resident_name"] is None
    assert proofs.image(item["id"]).startswith(b"\xff\xd8")
    seen(store, item["id"], 45)
    assert proofs.items() == []
    seen(store, item["id"], 1)
    store.action(item["id"], "resolve")
    assert proofs.items() == []


def test_dishes_first_seen_already_out_are_searched_too_and_progress_is_counted(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media, timing_known=False)
    store.create_obligation(None, [{"kind": "bowl", "quantity": 1}], candidate=True, source="vision", box=BOX)
    assert proofs.progress() == {"checking": 1, "upcoming": 1}
    assert proofs.run_once(found) == item["id"]
    assert proofs.progress()["checking"] == 0 and len(proofs.items()) == 1


def test_dishes_without_a_visible_put_down_are_never_shown_or_retried(store, media):
    proofs = Proofs(store.root, store, media)
    untraced, nobody = late_dish(store, media), late_dish(store, media)
    assert proofs.run_once(lambda *_: None) == untraced["id"]
    assert proofs.run_once(found, identify=lambda *_: None) == nobody["id"]
    assert proofs.run_once(found) is None
    assert proofs.items() == []


def test_naming_puts_the_name_on_a_new_picture_and_rejecting_removes_it(store, media):
    proofs = Proofs(store.root, store, media)
    item = late_dish(store, media)
    proofs.run_once(found, identify=lambda *_: [.25, .2, .2, .5])
    unnamed, before = proofs.items()[0], proofs.image(item["id"])
    sam = store.add_resident("Sam")["id"]
    store.action(item["id"], "confirm", resident_id=sam)
    named = proofs.items()[0]
    assert named["resident_name"] == "Sam" and named["image_url"] != unnamed["image_url"]
    assert proofs.image(item["id"]) != before
    proofs.reject(item["id"])
    assert proofs.items() == []


def test_proof_api_lists_serves_and_rejects(authenticated):
    client = authenticated
    runtime = client.app.state.runtime
    item = late_dish(runtime.store, runtime.media)
    runtime.proofs.run_once(found)
    data = client.get("/api/proofs").json()
    assert [p["id"] for p in data["items"]] == [item["id"]]
    image = client.get(data["items"][0]["image_url"])
    assert image.headers["content-type"] == "image/jpeg" and "max-age" in image.headers["cache-control"]
    assert client.post(f"/api/proofs/{item['id']}/reject").status_code == 200
    assert client.get("/api/proofs").json()["items"] == []
    assert client.get(f"/api/proofs/{item['id']}/image").status_code == 404
