from datetime import datetime, timedelta

import numpy as np

from wash_dishes import checks
from wash_dishes.store import utcnow

AREAS = [
    {"id": "counter", "name": "Counter", "role": "dirty", "box": [0.0, 0.5, 0.4, 0.4]},
    {"id": "sink", "name": "Sink", "role": "sink", "box": [0.4, 0.5, 0.2, 0.4]},
    {"id": "rack", "name": "Rack", "role": "clean", "box": [0.6, 0.5, 0.2, 0.4]},
]
JPEG = b"\xff\xd8check\xff\xd9"


def local(hour, minute=0, day=12):
    return datetime(2026, 10, day, hour, minute, tzinfo=checks.timezone())


def test_a_check_is_due_from_its_time_until_it_is_done_or_given_up(store):
    assert checks.due(store, local(22, 59)) is None
    assert checks.due(store, local(23, 5)) == "2026-10-12 23:00"
    # After midnight, last night's check is still the one due.
    assert checks.due(store, local(0, 30, day=13)) == "2026-10-12 23:00"
    assert checks.due(store, local(2, 1, day=13)) is None
    assert store.check("2026-10-12 23:00")["status"] == "missed"
    assert checks.due(store, local(7, 10, day=13)) == "2026-10-13 07:00"


class Dataset:
    def __init__(self):
        self.saved, self.updated = [], []

    def save(self, jpeg, analysis, reason, *, observed_at=None):
        self.saved.append(analysis["detections"])
        return "frame-1"

    def update(self, frame_id, boxes, status):
        self.updated.append((frame_id, boxes, status))


def test_a_check_links_what_is_out_to_open_records_and_its_answer_teaches(store, media):
    left = store.create_obligation(None, [{"kind": "plate", "quantity": 1}], source="vision", candidate=True, at=utcnow() - timedelta(hours=3))
    detections = [{"kind": "plate", "score": 0.8, "box": [0.1, 0.6, 0.05, 0.05]},   # the open record's plate
                  {"kind": "cup", "score": 0.7, "box": [0.45, 0.6, 0.04, 0.05]},    # in the sink, nobody's yet
                  {"kind": "bowl", "score": 0.9, "box": [0.65, 0.6, 0.05, 0.05]},   # on the clean rack: not counted
                  {"kind": "cup", "score": 0.8, "box": [0.2, 0.7, 0.04, 0.05]}]     # the second look says no
    rejected = [0.2, 0.7, 0.04, 0.05]

    def verify(image, box):
        return {"dish": box != rejected, "score": 0.9 if box != rejected else 0.1, "sample": store.add_dish_sample([0.1] * 4, 0.5)}
    dataset = Dataset()
    record = checks.run(store, media, dataset, "2026-10-12 23:00", utcnow(), np.zeros((90, 160, 3), np.uint8), JPEG, AREAS,
                        lambda region: detections, [{"box": [0.1, 0.6, 0.05, 0.05], "obligation_id": left["id"]}], verify)
    assert [(d["kind"], d["obligation_id"]) for d in record["dishes"]] == [("plate", left["id"]), ("cup", None)]
    proof = store.obligation(left["id"])
    assert any(e["event"] == "kitchen_check" for e in proof["events"])
    assert any(e["label"] == "Still out at the 11 PM kitchen check" for e in proof["evidence"])
    [summary] = checks.summary(store)
    assert summary["out"] == 2 and summary["unlinked"] == 1 and summary["label"] == "11 PM"

    checks.answer(store, dataset, record["id"], [True, False], 0)
    [summary] = checks.summary(store)
    assert summary["out"] == 1 and summary["status"] == "answered"
    assert dataset.updated == [("frame-1", [{"kind": "plate", "box": [0.1, 0.6, 0.05, 0.05]}], "labeled")]
    assert sorted(s["label"] for s in store.labeled_dish_samples()) == [0, 1]


def test_a_check_answer_with_missed_dishes_does_not_label_the_frame(store, media):
    record = checks.run(store, media, Dataset(), "2026-10-13 07:00", utcnow(), np.zeros((90, 160, 3), np.uint8), JPEG, AREAS,
                        lambda region: [{"kind": "plate", "score": 0.8, "box": [0.1, 0.6, 0.05, 0.05]}], [], None)
    dataset = Dataset()
    checks.answer(store, dataset, record["id"], [True], 2)
    assert dataset.updated == [] and checks.summary(store)[0]["out"] == 3


def test_the_dashboard_shows_checks_and_takes_answers(authenticated):
    assert authenticated.get("/api/dashboard").json()["checks"] == []
    assert authenticated.post("/api/checks/99", json={"verdicts": [], "missed": 0}).status_code == 404


def test_a_check_looks_lower_only_when_the_second_look_can_drop_junk(store, media):
    faint = [{"kind": "pot", "score": 0.25, "box": [0.1, 0.6, 0.05, 0.05]}]
    image = np.zeros((90, 160, 3), np.uint8)
    blind = checks.run(store, media, Dataset(), "2026-10-12 23:00", utcnow(), image, JPEG, AREAS, lambda region: faint, [], None)
    assert blind["dishes"] == []
    looked = checks.run(store, media, Dataset(), "2026-10-13 07:00", utcnow(), image, JPEG, AREAS, lambda region: faint, [],
                        lambda image, box: {"dish": True, "score": 0.8, "sample": None})
    assert [d["kind"] for d in looked["dishes"]] == ["pot"]
