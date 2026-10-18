from datetime import datetime, timedelta, timezone

from wash_dishes.store import stamp

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


def incident(store, *, source="vision", owner=None):
    return store.create_obligation(owner, [{"kind": "plate", "quantity": 1}],
                                   at=NOW - timedelta(hours=3), source=source,
                                   candidate=owner is None, timing_known=source != "baseline")


def photo(store, item, eid, label, at, *, kind="snapshot"):
    store.add_evidence(item["id"], eid, f"{eid}.jpg", kind, label, at=at)


def stages(store, item):
    return {s["key"]: s for s in store.obligation(item["id"], now=NOW)["evidence_timeline"]["stages"]}


def test_deadline_event_and_elapsed_time_do_not_create_photo_proof(store):
    item = incident(store)
    store.scan_deadlines(now=NOW)
    result = stages(store, item)["deadline"]
    assert result["state"] == "missing"
    assert result["photo"] is None
    assert "not proof" in result["summary"]


def test_only_matching_after_deadline_photo_is_used(store):
    owner = store.add_resident("Casey")["id"]
    item = incident(store, owner=owner)
    due = datetime.fromisoformat(item["due_at"])
    label = "Matching dish still visible after the deadline; dirty status requires review"
    photo(store, item, "early", label, due - timedelta(seconds=1))
    assert stages(store, item)["deadline"]["photo"] is None
    photo(store, item, "later", label, due + timedelta(seconds=1))
    store.record_seen(item["id"], at=due + timedelta(seconds=1))
    assert stages(store, item)["deadline"]["photo"]["id"] == "later"
    store.expire_evidence("later")
    result = stages(store, item)["deadline"]
    assert result["state"] == "expired"
    assert result["photo"]["url"] is None


def test_identity_is_linked_to_matching_capture_without_assigning_ownership(store):
    item = incident(store)
    owner = store.add_resident("Casey")["id"]
    at = NOW - timedelta(hours=3, seconds=10)
    photo(store, item, "scene", "Person handling the dish just before it was left", at)
    photo(store, item, "detail", "Who handled it (zoomed)", at)
    photo(store, item, "wrong-detail", "Who handled it (zoomed)", at + timedelta(seconds=1))
    with store.conn:
        store._event(item["id"], "handler_identified", "Identified", {"evidence_id": "detail", "resident_id": owner}, NOW)
    result = stages(store, item)["handling"]
    assert result["photo"]["id"] == "scene"
    assert result["detail"]["id"] == "detail"
    assert "Photo identified as Casey" in result["summary"]
    assert store.obligation(item["id"])["resident_id"] is None
    store.action(item["id"], "dispute", now=NOW)
    assert "Photo identified" not in stages(store, item)["handling"]["summary"]


def test_baseline_does_not_invent_deposit_time_or_handler(store):
    item = incident(store, source="baseline")
    result = stages(store, item)
    assert result["left"]["title"] == "First seen"
    assert "unknown" in result["left"]["summary"]
    assert result["handling"]["photo"] is None
    assert result["deadline"]["state"] == "no_timer"


def test_cleanup_candidate_is_not_camera_verified_washing_and_reopen_clears_outcome(store):
    item = incident(store)
    assert store.propose_cleanup(item["id"])
    at = NOW - timedelta(minutes=1)
    photo(store, item, "clean", "Possible clean destination; washing quality unverified", at)
    photo(store, item, "clip", "Possible cleanup transition", at, kind="clip")
    result = stages(store, item)["outcome"]
    assert result["state"] == "cleared"
    assert result["clips"][0]["id"] == "clip"
    store.action(item["id"], "resolve", now=NOW, resolved_at=at)
    result = stages(store, item)["outcome"]
    assert result["state"] == "human_confirmed"
    assert result["at"] == stamp(at)
    assert "human decision" in result["summary"]
    store.action(item["id"], "reopen", now=NOW)
    result = stages(store, item)["outcome"]
    assert result["state"] == "missing"
    assert result["clips"] == []


def test_gaps_remain_visible_even_after_owner_is_confirmed(store):
    item = incident(store)
    owner = store.add_resident("Casey")["id"]
    with store.conn:
        store._event(item["id"], "observation_gap", "Lost track", at=NOW)
    store.action(item["id"], "confirm", resident_id=owner, now=NOW)
    limits = store.obligation(item["id"], now=NOW)["evidence_timeline"]["limitations"]
    assert any("lost sight" in line for line in limits)
    assert not any("No responsible owner" in line for line in limits)


def test_full_detail_has_timeline_but_polling_payload_stays_small(store):
    item = incident(store)
    assert len(store.obligation(item["id"])["evidence_timeline"]["stages"]) == 4
    assert "evidence_timeline" not in store.obligations()[0]


def test_manual_report_photo_is_not_backdated_to_reported_wash_time(store):
    item = incident(store, source="manual")
    photo(store, item, "deposit", "Manually recorded deposit", NOW - timedelta(hours=3))
    washed = NOW - timedelta(minutes=20)
    store.action(item["id"], "resolve", resolved_at=washed, now=NOW)
    photo(store, item, "reported-clean", "Cleanup confirmed by a person", NOW)
    result = stages(store, item)
    assert result["left"]["title"] == "Recorded deposit"
    assert result["left"]["photo"]["id"] == "deposit"
    assert result["outcome"]["photo"]["id"] == "reported-clean"
    assert result["outcome"]["at"] == stamp(NOW)
    assert result["outcome"]["decision_at"] == stamp(washed)
    store.action(item["id"], "reopen", now=NOW)
    assert stages(store, item)["outcome"]["photo"] is None
