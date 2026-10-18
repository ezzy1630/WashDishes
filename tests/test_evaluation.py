import json
from datetime import datetime, timedelta, timezone

from wash_dishes.config import Config
from wash_dishes.evaluation import evaluate, score

AREAS = [
    {"id": "counter", "role": "dirty", "box": [0.0, 0.5, 0.4, 0.4]},
    {"id": "sink", "role": "sink", "box": [0.3, 0.6, 0.2, 0.2]},
    {"id": "rack", "role": "clean", "box": [0.7, 0.5, 0.2, 0.3]},
]
TRUTH = {"from": "2026-10-09T19:00:00-07:00", "to": "2026-10-09T20:00:00-07:00", "changes": [
    {"at": "2026-10-09T19:10:00-07:00", "area": "counter", "added": ["plate", "cup"]},
    {"at": "2026-10-09T19:30:00-07:00", "area": "rack", "removed": ["bowl"]},
]}


def change(at, area, direction, kind):
    return {"at": f"2026-10-09T{at}-07:00", "area": area, "change": direction, "kind": kind}


def test_dishes_match_by_direction_place_and_time_and_kind_is_scored_apart():
    predicted = [change("19:10:40", "sink", "added", "bowl"),      # overlapping area, wrong kind: still found
                 change("19:10:20", "counter", "added", "cup"),
                 change("19:30:10", "rack", "added", "bowl"),      # wrong direction
                 change("19:45:00", "counter", "added", "plate")]  # nothing happened then
    result = score(predicted, TRUTH, AREAS)
    assert (result["truth"], result["predicted"], result["matched"]) == (3, 4, 2)
    assert result["kind_accuracy"] == 0.5 and result["area_accuracy"] == 0.5
    assert [m["change"] for m in result["missed"]] == ["removed"]
    # A long visit: whatever the pipeline stamps inside it is on time.
    visit = {**TRUTH, "changes": [{"start": "2026-10-09T19:05:00-07:00", **TRUTH["changes"][0]}]}
    assert score([change("19:06:00", "counter", "added", "plate")], visit, AREAS)["matched"] == 1
    assert score([change("19:06:00", "counter", "added", "plate")], TRUTH, AREAS)["matched"] == 0
    left_out = score(predicted, TRUTH, AREAS, roles=("dirty", "sink"))
    assert (left_out["truth"], left_out["matched"]) == (2, 2)


def test_the_deployed_record_is_the_live_change_log_and_absent_before_logging_began(store):
    at = datetime(2026, 10, 10, 2, 10, tzinfo=timezone.utc)
    store.log_change({"at": "2026-10-10T02:10:00+00:00", "area": "counter", "change": "added", "kind": "plate"})
    store.log_change({"at": "2026-10-10T02:20:00+00:00", "area": "counter", "change": "removed", "kind": "plate"})
    store.log_change({"at": "2026-10-10T04:00:00+00:00", "area": "sink", "change": "added", "kind": "cup"})
    found = store.changes_between(at, at + timedelta(minutes=50))
    assert [(f["change"], f["kind"]) for f in found] == [("added", "plate"), ("removed", "plate")]
    assert store.changes_between(at - timedelta(minutes=1), at + timedelta(hours=3)) is None


def test_a_reused_report_is_scored_only_inside_the_new_window(store, tmp_path):
    truth, report = tmp_path / "truth.json", tmp_path / "report.json"
    truth.write_text(json.dumps(TRUTH))
    report.write_text(json.dumps({"detector": "current", "predicted": [change("18:30:00", "counter", "added", "plate"),
                                                                       change("19:10:00", "counter", "added", "plate")]}))
    result = evaluate(Config(store.root), store, truth, None, reuse=report)
    assert result["changes"]["predicted"] == 1 and result["changes"]["matched"] == 1
