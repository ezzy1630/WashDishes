import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from wash_dishes.config import validate_settings
from wash_dishes.media import Media
from wash_dishes.notify import Notifier
from wash_dishes.store import AUTO_MIN_CHECKED, utcnow

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64 + b"\xff\xd9"


def candidate(store, minutes_ago=10):
    return store.create_obligation(None, [{"kind": "bowl", "quantity": 1}], source="vision", candidate=True, at=utcnow() - timedelta(minutes=minutes_ago))


def movement(store, item, event, minutes_ago):
    with store.lock, store.conn:
        store._event(item["id"], event, event, at=utcnow() - timedelta(minutes=minutes_ago))


# Clearing dishes taken away before their deadline (grace is 90 minutes in these tests)

def test_a_dish_carried_away_before_its_deadline_is_cleared_not_washed(store):
    item = candidate(store, 60)
    movement(store, item, "dish_picked_up", 40)
    store.scan_deadlines()
    cleared = store.obligation(item["id"])
    assert cleared["status"] == "cleared"
    assert cleared["evidence_timeline"]["stages"][-1]["state"] == "cleared"
    assert store.obligations() == []
    assert store.cleaned_today() == 0


@pytest.mark.parametrize("picked_up, then_moved", [(10, False), (40, True)])
def test_a_dish_still_in_hand_or_put_down_again_stays_open(store, picked_up, then_moved):
    item = candidate(store, 60)
    movement(store, item, "dish_picked_up", picked_up)
    if then_moved:
        movement(store, item, "dish_moved", picked_up - 1)
    store.scan_deadlines()
    assert store.obligation(item["id"])["lifecycle"] == "open"


def test_a_pickup_after_the_deadline_closes_the_dish_but_keeps_it_late(store):
    owner = store.add_resident("Casey")["id"]
    item = store.create_obligation(owner, [{"kind": "plate", "quantity": 1}], at=utcnow() - timedelta(minutes=200))
    unproven = store.create_obligation(owner, [{"kind": "cup", "quantity": 1}], at=utcnow() - timedelta(minutes=200))
    store.add_evidence(item["id"], "late", "late.jpg", "snapshot", "Matching dish still visible after the deadline", at=utcnow() - timedelta(minutes=100))
    store.record_seen(item["id"], utcnow() - timedelta(minutes=100))
    for dish in (item, unproven):
        movement(store, dish, "dish_picked_up", 60)
    store.scan_deadlines()
    closed = store.obligation(item["id"])
    assert closed["status"] == "resolved" and closed["closed_by_camera"] and closed["overdue_verified"]
    assert closed["evidence_timeline"]["stages"][-1]["state"] == "taken_late"
    # No photo after the deadline: closed, but never counted late.
    assert store.obligation(unproven["id"])["status"] == "resolved" and not store.obligation(unproven["id"])["overdue_verified"]


def test_reopening_a_cleared_dish_keeps_it_open(store):
    item = candidate(store, 60)
    movement(store, item, "dish_picked_up", 40)
    store.scan_deadlines()
    store.action(item["id"], "reopen", note="It went into the sink")
    store.scan_deadlines()
    assert store.obligation(item["id"])["lifecycle"] == "open"


# Camera naming owners once its guesses are measured

def ready_card(store, item):
    # Independent captures on ready cards are the denominator, not unseen predictions.
    store.add_person_sample(item["id"], [float(item["id"]), 1.0], at=utcnow() - timedelta(minutes=31 * item["id"]))
    with store.conn:
        store.conn.execute("CREATE TABLE IF NOT EXISTS proofs(obligation_id INTEGER PRIMARY KEY,status TEXT,created_at TEXT,data TEXT)")
        store.conn.execute("INSERT INTO proofs VALUES(?,'ready',?,'{}')", (item["id"], utcnow().isoformat(timespec="seconds")))


def checked_guesses(store, resident, count):
    for _ in range(count):
        item = candidate(store)
        ready_card(store, item)
        store.set_suggestion(item["id"], resident, 3.0)
        store.action(item["id"], "confirm", resident_id=resident)


def fresh_unchecked(store):
    item = candidate(store)
    while item["id"] % 5 == 0:  # every fifth record is a human spot check
        item = candidate(store)
    return item


def test_guesses_stay_suggestions_until_enough_have_been_checked(store):
    alex = store.add_resident("Alex")["id"]
    checked_guesses(store, alex, AUTO_MIN_CHECKED - 1)
    item = fresh_unchecked(store)
    store.set_suggestion(item["id"], alex, 3.0)
    assert store.obligation(item["id"])["confidence"] == "candidate"
    assert store.learning()["auto"]["active"] is False


def test_accurate_guesses_name_the_owner_without_teaching_the_camera(store):
    alex = store.add_resident("Alex")["id"]
    checked_guesses(store, alex, AUTO_MIN_CHECKED)
    item = fresh_unchecked(store)
    store.add_person_sample(item["id"], [1.0, 0.0], cue="outfit")
    store.set_suggestion(item["id"], alex, 3.0)
    named = store.obligation(item["id"])
    assert (named["resident_id"], named["confidence"], named["attribution"], named["status"]) == (alex, "confirmed", "camera", "pending")
    assert any(e["event"] == "auto_attributed" for e in named["events"])
    assert store.conn.execute("SELECT resident_id FROM person_samples WHERE obligation_id=?", (item["id"],)).fetchone()[0] is None
    # A person correcting it is recorded, but camera-named records stay out of the accuracy sample.
    sam = store.add_resident("Sam")["id"]
    assert store.action(item["id"], "reassign", resident_id=sam)["attribution"] == "camera_reviewed"
    assert store.attribution_gate()["checked"] == AUTO_MIN_CHECKED


def test_spot_checks_inaccurate_guesses_and_the_setting_keep_a_person_deciding(store):
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    checked_guesses(store, alex, AUTO_MIN_CHECKED)
    spot = candidate(store)
    while spot["id"] % 5:
        spot = candidate(store)
    store.set_suggestion(spot["id"], alex, 3.0)
    assert store.obligation(spot["id"])["confidence"] == "candidate"
    store.set_settings({"auto_attribute": False})
    item = fresh_unchecked(store)
    store.set_suggestion(item["id"], alex, 3.0)
    assert store.obligation(item["id"])["confidence"] == "candidate"
    store.set_settings({"auto_attribute": True})
    # Spot checks are how mistakes reach the measure: 30 right and 4 wrong is below 90%.
    wrong = 0
    while wrong < 4:
        item = candidate(store)
        ready_card(store, item)
        store.set_suggestion(item["id"], alex, 3.0)
        if store.obligation(item["id"])["confidence"] == "candidate":
            store.action(item["id"], "confirm", resident_id=sam)
            wrong += 1
    assert store.attribution_gate()["active"] is False
    item = fresh_unchecked(store)
    store.set_suggestion(item["id"], alex, 3.0)
    assert store.obligation(item["id"])["confidence"] == "candidate"


# Alerts

def test_alerts_start_from_now_send_each_late_dish_once_and_retry_failures(store, approve_late):
    sent, failing = [], [False]

    def send(url, title, message, tags):
        if failing[0]:
            raise OSError("offline")
        sent.append((title, message))

    casey = store.add_resident("Casey")["id"]
    old = store.create_obligation(casey, [{"kind": "plate", "quantity": 1}], at=utcnow() - timedelta(minutes=100))
    store.record_seen(old["id"])
    notifier = Notifier(store.root, store, send)
    assert notifier.poll() == 0  # no topic configured
    store.set_settings({"notify_url": "https://ntfy.example/kitchen-abc"})
    monday = datetime(2026, 10, 12, 17, tzinfo=timezone.utc)
    assert notifier.poll(monday) == 0  # history before alerts were set up is not replayed
    late = store.create_obligation(casey, [{"kind": "bowl", "quantity": 2}], at=utcnow() - timedelta(minutes=100))
    failing[0] = True
    store.record_seen(late["id"])
    approve_late(late)
    with pytest.raises(OSError):
        notifier.poll(monday)
    failing[0] = False
    assert notifier.poll(monday) == 1 and notifier.poll(monday) == 0
    assert sent[0][0] == "Late dishes: Casey" and "2 bowls were still out" in sent[0][1]


def test_weekly_summary_goes_out_once_on_sunday_evening(store):
    sent = []
    store.set_settings({"notify_url": "https://ntfy.example/kitchen-abc"})
    notifier = Notifier(store.root, store, lambda *args: sent.append(args))
    sunday = datetime(2026, 10, 19, 1, 30, tzinfo=timezone.utc)  # 18:30 Pacific
    assert notifier.poll(sunday - timedelta(hours=1)) == 0
    assert notifier.poll(sunday) == 1 and notifier.poll(sunday + timedelta(hours=1)) == 0
    assert sent[0][1] == "Dish Watch · this week"


def test_alert_topic_must_be_an_ntfy_address():
    validate_settings({"notify_url": ""})
    validate_settings({"notify_url": "https://ntfy.sh/dishes-x7"})
    for bad in ("ntfy.sh/dishes", "https://user:pw@ntfy.sh/a", "https://ntfy.sh/a/b"):
        with pytest.raises(ValueError):
            validate_settings({"notify_url": bad})


# Proof export

def test_proof_export_contains_the_page_files_and_their_checksums(authenticated):
    store = authenticated.app.state.store
    owner = store.add_resident("Casey")["id"]
    item = store.create_obligation(owner, [{"kind": "plate", "quantity": 1}], at=utcnow() - timedelta(minutes=150))
    Media(store.root, store).save_snapshot(item["id"], JPEG, "Dish left unattended")
    store.record_seen(item["id"])
    response = authenticated.get(f"/api/obligations/{item['id']}/proof")
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    record = json.loads(archive.read("record.json"))
    (photo,) = record["files"]
    assert hashlib.sha256(archive.read(photo["name"])).hexdigest() == photo["sha256"] == hashlib.sha256(JPEG).hexdigest()
    page = archive.read("index.html").decode()
    assert "Casey (confirmed by a person)" in page and "Still out after the deadline" in page and photo["name"] in page


def test_naming_a_dish_closed_after_its_deadline_counts_it_late_only_with_the_photo(store):
    owner = store.add_resident("Casey")["id"]
    photographed, unproven = candidate(store, 200), candidate(store, 200)
    store.add_evidence(photographed["id"], "late", "late.jpg", "snapshot", "Matching dish still visible after the deadline", at=utcnow() - timedelta(minutes=100))
    for dish in (photographed, unproven):
        movement(store, dish, "dish_picked_up", 60)
    store.scan_deadlines()
    assert store.action(photographed["id"], "confirm", resident_id=owner)["overdue_verified"]
    assert not store.action(unproven["id"], "confirm", resident_id=owner)["overdue_verified"]
