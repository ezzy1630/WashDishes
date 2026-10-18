import time
from datetime import timedelta

from fastapi.testclient import TestClient

from wash_dishes.app import create_app
from wash_dishes.config import Config
from wash_dishes.demo import evidence
from wash_dishes.store import utcnow


def test_api_login_csrf_origin_expiration_and_logout(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/session").json()["authenticated"] is False
    assert client.post("/api/login", json={"password": "test-household-key"}, headers={"Origin": "https://attacker.example"}).status_code == 403
    login = client.post("/api/login", json={"password": "test-household-key"})
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert client.post("/api/residents", json={"name": "A"}).status_code == 403
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.post("/api/residents", json={"name": "A"}).status_code == 201
    assert client.post("/api/residents", json={"name": "B"}, headers={"Origin": "https://attacker.example"}).status_code == 403
    token = client.cookies.get("dish_session")
    csrf = client.app.state.access.sessions[token][1]
    client.app.state.access.sessions[token] = (time.time() - 1, csrf)
    assert client.get("/api/dashboard").status_code == 401
    login = client.post("/api/login", json={"password": "test-household-key"})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/dashboard").status_code == 401


def test_bruteforce_and_upload_limits(client):
    for _ in range(10):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-household-key"}).status_code == 429
    assert client.post("/api/login", content=b"a" * (10 * 1024 * 1024)).status_code == 413


def test_chunked_upload_limit_and_invalid_length(client):
    assert client.post("/api/login", content=iter([b"a" * 1_000_000] * 10), headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"}).status_code == 413
    assert client.post("/api/login", content=b"{}", headers={"Content-Length": "invalid"}).status_code == 400


def test_full_manual_review_workflow(authenticated):
    client = authenticated
    owner = client.post("/api/residents", json={"name": "A"}).json()["id"]
    cleaner = client.post("/api/residents", json={"name": "B"}).json()["id"]
    item = client.post("/api/obligations", json={"resident_id": None, "dishes": [{"kind": "bowl", "quantity": 2}], "note": "Unknown owner"}).json()
    assert item["status"] == "review"
    item = client.post(f"/api/obligations/{item['id']}/action", json={"action": "confirm", "resident_id": owner}).json()
    assert item["status"] == "pending"
    resolved = client.post(f"/api/obligations/{item['id']}/action", json={"action": "resolve", "cleaner_id": cleaner}).json()
    assert resolved["status"] == "resolved"
    assert resolved["cleaner_name"] == "B"
    assert client.get("/api/dashboard").json()["summary"]["cleaned_today"] == 1
    assert len(client.get("/api/history?status=resolved").json()["obligations"]) == 1
    assert client.get("/api/history?status=unknown-status").status_code == 400
    assert client.get("/api/history?date=bad-date").status_code == 400
    assert client.get("/api/obligations/999999").status_code == 404
    assert client.post("/api/obligations", json={"dishes": [{"kind": "plate", "quantity": -1}]}).status_code == 422


def test_protected_evidence_expiry_and_private_camera(authenticated):
    client = authenticated
    item = client.post("/api/obligations", json={"dishes": [{"kind": "plate", "quantity": 1}]}).json()
    evidence(client.app.state.store, client.app.state.media, item)
    details = client.get(f"/api/obligations/{item['id']}").json()
    url = details["evidence"][0]["url"]
    assert client.get(url).status_code == 200
    assert client.get(url).headers["cache-control"] == "no-store"
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_expiry_and_credentials_do_not_leak(authenticated):
    client = authenticated
    store = client.app.state.store
    item = store.create_obligation(None, [{"kind": "plate", "quantity": 1}])
    evidence(store, client.app.state.media, item)
    evidence_id = store.obligation(item["id"])["evidence"][0]["id"]
    path = client.app.state.media.safe_path(store.evidence(evidence_id)["filename"])
    with store.lock, store.conn:
        store.conn.execute("UPDATE evidence SET expires_at=? WHERE id=?", ((utcnow() - timedelta(seconds=1)).isoformat(), evidence_id))
    assert client.get(f"/api/evidence/{evidence_id}").status_code == 410
    assert path.exists()
    assert client.post("/api/storage/cleanup", json={}).status_code == 200
    assert not path.exists()
    assert store.obligation(item["id"])["evidence"][0]["expired"]
    camera = {"host": "192.168.1.20", "username": "my-camera-user", "password": "my-camera-password", "stream": "stream1", "enabled": False}
    assert client.post("/api/camera", json=camera).status_code == 200
    dashboard = client.get("/api/dashboard")
    assert "my-camera-password" not in dashboard.text
    assert "my-camera-user" not in dashboard.text
    assert client.get("/api/camera/snapshot").status_code == 503
    assert client.post("/api/residents/1/enroll-camera", json={}).status_code == 503


def test_pause_immediately_removes_current_snapshot(authenticated):
    client = authenticated
    camera = client.app.state.runtime.camera
    client.app.state.store.set_settings({"camera_enabled": True})
    camera.frame, camera.frame_time = b"\xff\xd8fixture\xff\xd9", utcnow().isoformat()
    generation = camera.stream_generation
    assert client.get("/api/camera/snapshot").status_code == 200
    assert client.post("/api/camera/pause", json={"paused": True}).status_code == 200
    assert client.get("/api/camera/snapshot").status_code == 503
    assert camera.frame is None
    assert camera.stream_generation != generation


def test_archiving_and_model_errors_are_usable(authenticated):
    client = authenticated
    resident = client.post("/api/residents", json={"name": "A"}).json()["id"]
    upload = client.post(f"/api/residents/{resident}/enroll", files={"image": ("face.jpg", b"\xff\xd8bad", "image/jpeg")})
    assert upload.status_code == 400
    assert "model" in upload.json()["detail"].lower()
    assert client.delete(f"/api/residents/{resident}").status_code == 200
    assert client.get("/api/dashboard").json()["residents"][0]["active"] is False


def test_demo_isolated_and_live_demo_routes_disabled(tmp_path, authenticated):
    live_root = tmp_path / "base" / "live"
    live_root.mkdir(parents=True)
    marker = live_root / "protected.txt"
    marker.write_text("original")
    app = create_app(Config(tmp_path / "base" / "demo", demo=True, workers=False))
    with TestClient(app) as demo_client:
        token = demo_client.post("/api/login", json={"password": "demo"}).json()["csrf_token"]
        demo_client.headers["X-CSRF-Token"] = token
        data = demo_client.get("/api/dashboard").json()
        assert data["mode"] == "demo"
        assert data["camera"]["connected"] is False
        assert data["summary"]["needs_review"] > 0
        assert demo_client.post("/api/demo/scenario", json={"scenario": "overdue"}).status_code == 200
        assert demo_client.post("/api/storage/cleanup", json={}).status_code == 200
        assert demo_client.post("/api/camera", json={"host": "192.168.1.2", "username": "user", "password": "pass"}).status_code == 400
    assert marker.read_text() == "original"
    assert list(live_root.iterdir()) == [marker]
    assert authenticated.post("/api/demo/scenario", json={"scenario": "deposit"}).status_code == 404


def test_live_scene_tags_zones_counts_inventory_and_serves_exact_frame(authenticated):
    client = authenticated
    runtime = client.app.state.runtime
    assert client.get("/api/live").json()["live"] is None
    client.patch("/api/settings", json={"vision_enabled": True, "areas": [
        {"id": "sink", "name": "Sink", "role": "sink", "box": [0.5, 0.5, 0.3, 0.3]},
        {"id": "rack", "name": "Rack", "role": "clean", "box": [0.8, 0.5, 0.2, 0.3]}]})
    areas = client.get("/api/live").json()["areas"]
    plate = {"kind": "plate", "score": 0.8, "track_id": 4, "box": [0.6, 0.6, 0.05, 0.05]}
    faint = {"kind": "cup", "score": 0.3, "track_id": 5, "box": [0.1, 0.1, 0.05, 0.05]}
    person = {"kind": "person", "score": 0.9, "track_id": 9, "box": [0.1, 0.1, 0.2, 0.6]}
    inventory = [{"key": "a", "kind": "plate", "box": plate["box"], "area_id": "sink", "role": "sink", "obligation_id": 12, "last_seen_at": "x"}]
    runtime.publish_live(b"\xff\xd8frame\xff\xd9", "gen", "2026-10-09T22:00:00+00:00", {"detections": [plate, faint, person], "latency_ms": 250}, areas, inventory)
    state = client.get("/api/live").json()
    live = state["live"]
    assert state["fresh"] is True
    assert live["counts"] == {"sink": 1, "dirty": 0, "clean": 0, "storage": 0, "people": 1}
    assert live["by_area"] == {"sink": 1, "rack": 0}
    tagged = {d["kind"]: d for d in live["detections"]}
    assert (tagged["plate"]["role"], tagged["plate"]["obligation_id"], tagged["plate"]["counted"]) == ("sink", 12, True)
    # Dishes are drawn from the inventory; a raw detection it does not hold is not a dish.
    assert "cup" not in tagged
    assert tagged["person"]["role"] is None
    assert state["history"][0]["sink"] == 1
    frame = client.get(f"/api/live/frame/{live['frame_id']}")
    assert frame.status_code == 200 and frame.content == b"\xff\xd8frame\xff\xd9"
    assert client.get("/api/live/frame/999").status_code == 404
    client.patch("/api/settings", json={"vision_enabled": False})
    assert client.get("/api/live").json()["live"] is None


def test_training_frames_are_labeled_exported_and_kept_within_budget(authenticated, tmp_path):
    client = authenticated
    dataset = client.app.state.runtime.dataset
    analysis = {"size": [2304, 1296], "detections": [{"kind": "plate", "score": 0.8, "box": [0.1, 0.1, 0.1, 0.1]}, {"kind": "cup", "score": 0.1, "box": [0.5, 0.5, 0.1, 0.1]}]}
    ids = [dataset.save(b"\xff\xd8" + bytes([i]) * 100 + b"\xff\xd9", analysis, "test") for i in range(12)]
    summary = client.get("/api/dataset").json()
    assert summary["counts"] == {"unlabeled": 12, "labeled": 0, "skipped": 0}
    first = client.get("/api/dataset/next").json()["item"]
    assert first["id"] == ids[0] and [b["kind"] for b in first["boxes"]] == ["plate"]
    assert client.get(f"/api/dataset/{ids[0]}/image").content.startswith(b"\xff\xd8")
    for frame_id in ids:
        saved = client.put(f"/api/dataset/{frame_id}", json={"status": "labeled", "boxes": [{"kind": "bowl", "box": [0.2, 0.2, 0.1, 0.1]}]})
        assert saved.status_code == 200
    assert client.put(f"/api/dataset/{ids[0]}", json={"status": "labeled", "boxes": [{"kind": "toaster", "box": [0, 0, 1, 1]}]}).status_code == 400
    assert client.get("/api/dataset/../access-key/image").status_code == 404
    data_yaml = dataset.export(tmp_path / "export")
    labels = list((tmp_path / "export" / "labels").rglob("*.txt"))
    assert len(labels) == 12 and labels[0].read_text().startswith("2 0.250000 0.250000")
    assert "  1: plate" in data_yaml.read_text()
    client.patch("/api/settings", json={"dataset_limit_gb": 0.0000002})
    dataset.save(b"\xff\xd8" + b"x" * 300 + b"\xff\xd9", analysis, "test")
    assert client.get("/api/dataset").json()["counts"]["unlabeled"] == 0
