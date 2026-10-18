import json
import math
import random
from datetime import timedelta
from pathlib import Path

from wash_dishes.dataset import Dataset
from wash_dishes.identity_fit import LAST_RUN_FILE, MIN_CASES, refit
from wash_dishes.improve import HISTORY, KITCHEN, PREVIOUS, combine, improve, rollback
from wash_dishes.media import Media
from wash_dishes.notify import Notifier
from wash_dishes.store import IDENTITY_WEIGHTS_FILE, utcnow

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64 + b"\xff\xd9"
BOX = [0.2, 0.5, 0.1, 0.08]


def camera_record(store, minutes_ago=20, box=BOX):
    return store.create_obligation(None, [{"kind": "bowl", "quantity": 1}], source="vision", candidate=True,
                                   at=utcnow() - timedelta(minutes=minutes_ago), box=box)


# 1. Scoreboard

def test_scoreboard_counts_false_alarms_checked_guesses_and_disputes_that_changed_the_outcome(store):
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    store.action(camera_record(store)["id"], "dismiss", note="Not a dish")
    store.action(camera_record(store)["id"], "dismiss", note="Already here when monitoring began; dismissed together")
    guessed = camera_record(store)
    store.set_suggestion(guessed["id"], alex, 3.0)
    store.action(guessed["id"], "confirm", resident_id=alex)
    store.action(guessed["id"], "dispute", note="Was Sam's")
    store.action(guessed["id"], "reassign", resident_id=sam)
    week = store.scoreboard()["weeks"][-1]
    assert (week["camera_records"], week["false_alarms"], week["real"], week["false_alarm_rate"]) == (3, 1, 1, 0.5)
    assert (week["identity_checked"], week["identity_correct"]) == (1, 1)
    assert (week["disputes"], week["disputes_overturned"]) == (1, 1)


# 2. Personal links

def test_a_personal_link_shows_and_disputes_only_that_persons_records(authenticated):
    store = authenticated.app.state.store
    alex, sam = store.add_resident("Alex")["id"], store.add_resident("Sam")["id"]
    mine = store.create_obligation(alex, [{"kind": "plate", "quantity": 1}])
    theirs = store.create_obligation(sam, [{"kind": "cup", "quantity": 1}])
    media = Media(store.root, store)
    mine_photo = media.save_snapshot(mine["id"], JPEG, "Dish left unattended")
    their_photo = media.save_snapshot(theirs["id"], JPEG, "Dish left unattended")
    old = authenticated.post(f"/api/residents/{alex}/link").json()["token"]
    token = authenticated.post(f"/api/residents/{alex}/link").json()["token"]
    assert "link_hash" not in authenticated.get("/api/dashboard").json()["residents"][0]

    visitor = authenticated.__class__(authenticated.app)  # no session cookie
    assert visitor.get("/api/me").status_code == 401
    assert visitor.get("/api/me", headers={"X-Personal-Token": old}).status_code == 401
    me = {"X-Personal-Token": token}
    page = visitor.get("/api/me", headers=me).json()
    assert page["name"] == "Alex" and [o["id"] for o in page["records"]] == [mine["id"]]
    assert visitor.get(f"/api/me/evidence/{mine_photo}", headers=me).content == JPEG
    assert visitor.get(f"/api/me/evidence/{their_photo}", headers=me).status_code == 404
    assert visitor.post(f"/api/me/dispute/{theirs['id']}", headers=me, json={"note": "no"}).status_code == 404
    assert visitor.post(f"/api/me/dispute/{mine['id']}", headers=me, json={"note": "Washed it at 9"}).status_code == 200
    disputed = store.obligation(mine["id"])
    assert disputed["status"] == "disputed" and disputed["reason"] == "Disputed by Alex: Washed it at 9"
    assert visitor.get("/me").status_code == 200


def test_housemates_get_their_own_alerts_and_the_household_gets_late_ones(store, approve_late):
    sent = []
    store.set_settings({"notify_url": "https://ntfy.example/house-x1"})
    alex = store.add_resident("Alex")["id"]
    store.set_resident_notify(alex, "https://ntfy.example/alex-x1")
    notifier = Notifier(store.root, store, lambda url, title, message, tags: sent.append((url, title, message)))
    notifier.poll()
    item = store.create_obligation(alex, [{"kind": "bowl", "quantity": 1}], at=utcnow() - timedelta(minutes=100))
    with store.lock, store.conn:
        store._event(item["id"], "auto_attributed", "Named as Alex by the camera")
    store.record_seen(item["id"])
    approve_late(item)
    notifier.poll()
    assert [(url.rsplit("/", 1)[1], title) for url, title, _ in sent] == [("alex-x1", "The camera named you"), ("house-x1", "Late dishes: Alex"), ("alex-x1", "Late dishes: Alex")]
    assert sent[2][2].startswith("Your 1 bowl was still out")


# 3. Review answers become training data

def test_review_answers_queue_corrected_frames_for_training_first(authenticated):
    store, dataset = authenticated.app.state.store, authenticated.app.state.runtime.dataset
    alex = store.add_resident("Alex")["id"]
    media = Media(store.root, store)
    dataset.save(JPEG, {"detections": []}, "Periodic sample")
    false_alarm, real = camera_record(store), camera_record(store)
    for item in (false_alarm, real):
        media.save_snapshot(item["id"], JPEG, "Dish left unattended")
    authenticated.post(f"/api/obligations/{false_alarm['id']}/action", json={"action": "dismiss", "note": "Not a dish"})
    authenticated.post(f"/api/obligations/{real['id']}/action", json={"action": "confirm", "resident_id": alex})
    authenticated.post(f"/api/obligations/{real['id']}/action", json={"action": "reassign", "resident_id": alex})
    reviewed = {i["review"]["obligation_id"]: i for i in dataset._labels() if i.get("review")}
    assert set(reviewed) == {false_alarm["id"], real["id"]}  # once per record
    assert reviewed[false_alarm["id"]]["boxes"] == [] and reviewed[false_alarm["id"]]["review"]["verdict"] == "not_dish"
    assert reviewed[real["id"]]["boxes"][0]["box"] == BOX and reviewed[real["id"]]["boxes"][0]["kind"] == "bowl"
    assert dataset.next()["review"] and dataset.summary()["from_review"] == 2


# 4. Identity weights relearned from confirmed names

def unit(values):
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def test_identity_weights_are_relearned_only_when_they_beat_the_current_ones_on_held_out_sightings(store):
    rng = random.Random(3)
    people = [store.add_resident(name)["id"] for name in ("Alex", "Sam")]
    # Hair tells them apart, weakly. Faces from the overhead camera are noise that looks confident.
    heads = {people[0]: [1, 0.6, 0.6, 0], people[1]: [0.6, 0.6, 1, 0]}
    at = utcnow() - timedelta(hours=2)

    def sighting(index):
        who = people[index % 2]
        item = camera_record(store)
        store.add_person_sample(item["id"], unit([v + rng.gauss(0, 0.1) for v in heads[who]]), at=at, cue="head", context="sink")
        store.add_person_sample(item["id"], unit([rng.gauss(0, 1) for _ in range(4)]), at=at, cue="face", context="sink")
        store.action(item["id"], "confirm", resident_id=who)

    for index in range(MIN_CASES - 10):
        sighting(index)
    report = refit(store)
    assert report["adopted"] is False and "Needs" in report["reason"]
    assert json.loads((store.root / LAST_RUN_FILE).read_text())["cases"] == MIN_CASES - 10
    for index in range(MIN_CASES - 10, MIN_CASES + 30):
        sighting(index)
    report = refit(store)
    assert report["adopted"] is True and report["held_out_accuracy"] > report["current_accuracy"]
    assert report["held_out_accuracy"] >= 0.9 > report["current_accuracy"]
    assert report["weights"]["head"] > 1 > report["weights"]["face"]
    assert store.identity_weights() == json.loads((store.root / IDENTITY_WEIGHTS_FILE).read_text())["weights"]
    # Already-adopted weights are the new bar: refitting the same data does not adopt again.
    assert refit(store)["adopted"] is False


# 5. Weekly detector retraining with a promotion gate

def scores(recall, precision):
    f1 = 2 * recall * precision / (recall + precision)
    return {"left_out": {"truth": 7, "matched": round(recall * 7), "predicted": 10, "recall": recall, "precision": precision, "f1": f1, "missed": [], "extra": []}}


def test_retrained_detector_is_adopted_only_when_better_and_can_be_rolled_back(store, tmp_path):
    models = store.root / "models"
    (models / KITCHEN).write_bytes(b"model-v1")
    calls = []

    def run(candidate_bytes, candidate_score, labeled, current_score=scores(0.6, 0.4), truth_id="evening"):
        candidate = tmp_path / "candidate.pt"
        candidate.write_bytes(candidate_bytes)

        def evaluate(model):
            calls.append(model)
            return candidate_score if model else current_score
        return improve(store.root, store, labeled, train=lambda: candidate, evaluate=evaluate, truth_id=truth_id)

    assert run(b"x", scores(1, 1), 5)["reason"].startswith("Skipped")
    worse = run(b"model-v2", scores(0.4, 0.9), 30)  # fewer dishes found, even if more precise
    assert worse["promoted"] is False and (models / KITCHEN).read_bytes() == b"model-v1"
    better = run(b"model-v3", scores(0.6, 0.6), 60)
    assert better["promoted"] is True and (models / KITCHEN).read_bytes() == b"model-v3" and (models / PREVIOUS).read_bytes() == b"model-v1"
    calls.clear()
    run(b"model-v4", scores(0.6, 0.5), 90)  # the adopted model's score is reused, not replayed again
    assert calls == [tmp_path / "candidate.pt"] and (models / KITCHEN).read_bytes() == b"model-v3"
    calls.clear()
    run(b"model-v5", scores(0.4, 0.9), 120, truth_id="evening+morning")  # new truth: the model in use is scored again
    assert calls == [None, tmp_path / "candidate.pt"]
    rollback(store.root, store)
    assert (models / KITCHEN).read_bytes() == b"model-v1"
    assert len(Path(models / HISTORY).read_text().splitlines()) == 6


def test_scores_from_several_recorded_periods_are_pooled_before_scoring():
    pooled = combine([scores(1, 0.5), {"left_out": {"truth": 3, "matched": 0, "predicted": 0}}])["left_out"]
    assert (pooled["truth"], pooled["matched"], pooled["predicted"]) == (10, 7, 10)
    assert pooled["recall"] == 0.7 and pooled["precision"] == 0.7 and round(pooled["f1"], 6) == 0.7


def test_dataset_keeps_review_frames_ahead_of_plain_ones_under_budget(store):
    dataset = Dataset(store.root, store)
    plain = dataset.save(JPEG, {"detections": []}, "Periodic sample")
    reviewed = dataset.from_review(7, JPEG, BOX, "plate", "dish")
    store.set_settings({"dataset_limit_gb": 0.0000001})
    dataset.enforce_budget()
    remaining = {i["id"] for i in dataset._labels()}
    assert plain not in remaining and (reviewed in remaining or not remaining)
