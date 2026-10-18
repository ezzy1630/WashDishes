import pytest
from fastapi.testclient import TestClient

from wash_dishes.app import create_app
from wash_dishes.config import Config
from wash_dishes.media import Media
from wash_dishes.store import Store


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "live"
    Config(root).prepare()
    db = Store(root)
    # Keep the original 89/91-minute boundary cases explicit; household default is 120.
    db.set_settings({"deadline_minutes": 90})
    yield db
    db.close()


@pytest.fixture
def media(store):
    return Media(store.root, store)


@pytest.fixture
def approve_late(store, media):
    """Human review of four independent synthetic captures, through the real save path."""
    from datetime import timedelta

    from test_sequences import jpeg

    from wash_dishes.incidents import Incidents
    from wash_dishes.sequences import SequenceCollection
    from wash_dishes.store import parse_stamp
    def approve(item):
        created, due = parse_stamp(item['created_at']), parse_stamp(item['due_at'])
        for i, (stage, at) in enumerate(zip(('before','using','leaving','later'),
                                          (created-timedelta(seconds=60), created-timedelta(seconds=20), created+timedelta(seconds=10), due+timedelta(seconds=60)))):
            media.save_snapshot(item['id'], jpeg((40+i*40,40,80)), stage, at=at,
                                capture={'stage': stage, 'box':[.3,.5,.1,.1], 'box_kind':'candidate_item'})
        sequences = SequenceCollection(store, media)
        sequences.refresh(obligation_id=item['id'])
        incidents = Incidents(store, media, sequences)
        detail = incidents.detail(item['id'])
        body = dict(revision=detail['revision'], checks={k:True for k in ('before','using','leaving','later')},
                    identity='keep', person_id=item['resident_id'], responsibility='confirm', condition='used',
                    outcome='keep', cleaner_id=None, resolved_at=None, note='Synthetic test review')
        return incidents.review(item['id'], body)
    return approve


@pytest.fixture
def client(tmp_path):
    app = create_app(Config(tmp_path / "live", password="test-household-key", workers=False))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def authenticated(client):
    response = client.post("/api/login", json={"password": "test-household-key"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
