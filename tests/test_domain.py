from datetime import timedelta

import pytest

from wash_dishes.config import camera_url, validate_settings
from wash_dishes.store import Store, utcnow, validate_dishes


def deposit(store, owner=None, minutes=0, candidate=False):
    return store.create_obligation(owner, [{"kind": "plate", "quantity": 1}], at=utcnow() - timedelta(minutes=minutes), candidate=candidate)


def test_deadline_is_not_proof_and_scan_is_idempotent(store):
    owner = store.add_resident("Casey")["id"]
    item = deposit(store, owner, 100)
    assert item["status"] == "overdue"
    assert item["overdue_verified"] is False
    assert store.statistics()[0]["overdue"] == 0
    assert store.statistics()[0]["uses"] == 0
    assert store.statistics()[0]["pending_unverified"] == 1
    assert store.scan_deadlines() == 1
    assert store.scan_deadlines() == 0
    assert len([e for e in store.activity() if e["event"] == "deadline_passed"]) == 1


def test_unknown_confirmation_dispute_and_reassignment_recompute_stats(store):
    a = store.add_resident("A")["id"]
    b = store.add_resident("B")["id"]
    item = deposit(store, None, 100, candidate=True)
    store.record_seen(item["id"])
    assert item["status"] == "review"
    assert sum(s["uses"] for s in store.statistics()) == 0
    store.action(item["id"], "confirm", resident_id=a)
    store.record_seen(item["id"])
    assert store.statistics()[0]["overdue"] == 0  # Naming alone does not confirm dirty-dish evidence.
    store.action(item["id"], "dispute", note="Not mine")
    assert sum(s["uses"] for s in store.statistics()) == 0
    corrected = store.action(item["id"], "reassign", resident_id=b)
    assert corrected["original_resident_id"] is None
    assert store.statistics()[0]["overdue"] == 0
    assert store.statistics()[1]["overdue"] == 0
    assert [e["event"] for e in corrected["events"]][:2] == ["reassign", "dispute"]


@pytest.mark.parametrize("minutes,late", [(89, False), (91, True)])
def test_cleanup_boundary_without_deadline_worker(store, minutes, late):
    owner = store.add_resident("A")["id"]
    created = utcnow() - timedelta(hours=4)
    item = store.create_obligation(owner, [{"kind": "bowl", "quantity": 1}], at=created)
    resolved = store.action(item["id"], "resolve", resolved_at=created + timedelta(minutes=minutes))
    assert resolved["overdue_verified"] is late
    assert store.statistics()[0]["overdue"] == int(late)
    if late:
        corrected = store.action(item["id"], "resolve", resolved_at=created + timedelta(minutes=80), note="Actually washed earlier")
        assert corrected["overdue_verified"] is False
        assert store.statistics()[0]["overdue"] == 0


def test_other_cleaner_and_repeat_resolution_count_one_obligation(store):
    owner = store.add_resident("A")["id"]
    cleaner = store.add_resident("B")["id"]
    item = deposit(store, owner, 10)
    for _ in range(2):
        store.action(item["id"], "resolve", cleaner_id=cleaner)
    assert store.obligation(item["id"])["resident_id"] == owner
    assert store.statistics()[1]["cleaned_for_others"] == 1
    assert store.cleaned_today() == 1


@pytest.mark.parametrize("action", ["confirm", "reassign"])
def test_correcting_disputed_resolved_ownership_preserves_cleanup(store, action):
    first = store.add_resident("A")["id"]
    corrected_owner = store.add_resident("B")["id"]
    cleaner = store.add_resident("C")["id"]
    item = deposit(store, first, 100)
    resolved = store.action(item["id"], "resolve", cleaner_id=cleaner)
    store.action(item["id"], "dispute", note="Wrong owner")
    corrected = store.action(item["id"], action, resident_id=corrected_owner)
    assert corrected["status"] == "resolved"
    assert corrected["resolved_at"] == resolved["resolved_at"]
    assert corrected["cleaner_id"] == cleaner
    stats = {row["resident_id"]: row for row in store.statistics()}
    assert stats[first]["uses"] == 0
    assert stats[corrected_owner]["uses"] == 1
    assert stats[corrected_owner]["overdue"] == 1
    assert stats[cleaner]["cleaned_for_others"] == 1


def test_persistence_and_dismissed_ownership_history(store):
    owner = store.add_resident("A")["id"]
    item = deposit(store, owner, 100)
    store.action(item["id"], "dismiss")
    assert store.statistics()[0]["uses"] == 0
    second = Store(store.root)
    try:
        assert second.obligation(item["id"])["status"] == "dismissed"
        restored = second.action(item["id"], "reopen")
        assert restored["status"] == "overdue"
        assert not restored["overdue_verified"]
    finally:
        second.close()


def test_archive_preserves_history_and_rejects_new_assignment(store):
    owner = store.add_resident("A")["id"]
    item = deposit(store, owner)
    store.archive_resident(owner)
    assert store.obligation(item["id"])["resident_name"] == "A"
    with pytest.raises(ValueError, match="active"):
        deposit(store, owner)
    assert not store.residents()[0]["active"]


def test_future_and_predeposit_cleanup_rejected(store):
    owner = store.add_resident("A")["id"]
    item = deposit(store, owner)
    with pytest.raises(ValueError):
        store.action(item["id"], "resolve", resolved_at=utcnow() + timedelta(days=1))
    with pytest.raises(ValueError):
        store.action(item["id"], "resolve", resolved_at=utcnow() - timedelta(days=1))


def test_which_similar_dish_is_which_is_only_asked_once_someone_answers_for_it(store):
    unowned = deposit(store, candidate=True)
    store.flag_continuity_gap(unowned["id"])
    assert not store.obligation(unowned["id"])["review_pending"]
    owned = deposit(store, store.add_resident("Casey")["id"])
    store.flag_continuity_gap(owned["id"])
    assert store.obligation(owned["id"])["review_pending"]


def test_private_camera_settings_never_leak(store):
    camera = {"host": "192.168.1.5", "username": "secret-user", "password": "secret-value", "stream": "stream1", "enabled": True}
    store.set_camera(camera)
    assert "camera" not in store.settings()
    assert store.settings(private=True)["camera"] == camera
    assert "secret" not in str(store.activity())
    url = camera_url(camera | {"password": "p:@/word"})
    assert "p%3A%40%2Fword" in url


@pytest.mark.parametrize("host", ["https://example.com", "8.8.8.8", "-localhost", "0.0.0.0", "127.0.0.1;touch x", "192.168.1.5:9999"])
def test_invalid_camera_targets(host):
    with pytest.raises(ValueError):
        camera_url({"host": host, "username": "user", "password": "password"})


@pytest.mark.parametrize("patch", [{"deadline_minutes": 0}, {"storage_limit_gb": float("nan")}, {"camera_enabled": "false"}, {"zones": {"dirty": [0, 0, 1, 1]}}, {"zones": {name: [0.8, 0, 0.5, 1] for name in ("dirty", "sink", "clean")}}, {"random": True}, {"areas": [{"id": "c", "name": "C", "role": "dirty", "box": [0, 0, 0.5, 0.5], "outline": [[0, 0], [1.2, 0], [0, 1]]}]}])
def test_invalid_settings(patch):
    with pytest.raises(ValueError):
        validate_settings(patch)


@pytest.mark.parametrize("dishes", [[], [{"kind": "plate", "quantity": 0}], [{"kind": "plate", "quantity": True}], [{"kind": "fridge", "quantity": 1}], [{"kind": "plate", "quantity": 80}, {"kind": "plate", "quantity": 80}]])
def test_invalid_dish_counts(dishes):
    with pytest.raises(ValueError):
        validate_dishes(dishes)


def test_unnamed_dishes_gone_for_a_day_close_even_if_photographed_late(store):
    now = utcnow()
    old = now - timedelta(days=2)
    unprovable = store.create_obligation(None, [{"kind": "cup", "quantity": 1}], candidate=True, source="vision", at=old)
    photographed = store.create_obligation(None, [{"kind": "plate", "quantity": 1}], candidate=True, source="vision", at=old)
    store.add_evidence(photographed["id"], "late-photo", "late-photo.jpg", "snapshot", "Matching dish still visible after the deadline; dirty status requires review", at=old + timedelta(hours=3))
    named = store.create_obligation(store.add_resident("Sam")["id"], [{"kind": "bowl", "quantity": 1}], source="vision", at=old)
    recent = store.create_obligation(None, [{"kind": "pan", "quantity": 1}], candidate=True, source="vision", at=now - timedelta(hours=3))
    store.scan_deadlines(now)
    assert all(store.obligation(item["id"])["lifecycle"] == "dismissed" for item in (unprovable, photographed))
    assert all(store.obligation(item["id"])["lifecycle"] == "open" for item in (named, recent))
