import json

import pytest
from fastapi.testclient import TestClient

from wash_dishes.review_packet import create_review_app, packet_digest, validate_answer

PACKET = {"cases": [{"case_id": "01"}], "residents": [{"name": "Casey"}]}
ANSWER = {"case_id": "01", "reviewed": True, "handler": "Casey", "owner": "unknown", "deposit": "yes", "dirty": "unknown", "continuity": "unknown", "outcome": "unknown", "basis": "footage", "note": ""}


def test_identity_never_implies_owner_or_cleanup():
    result = validate_answer(PACKET, ANSWER)
    assert result["handler"] == "Casey" and result["owner"] == "unknown"
    assert result["outcome"] == "unknown"


@pytest.mark.parametrize("field,value", [("deposit", ""), ("handler", "Missing person"), ("outcome", "probably washed"), ("reviewed", 1), ("case_id", "02")])
def test_invalid_or_unanswered_truth_cannot_be_marked_reviewed(field, value):
    with pytest.raises(ValueError):
        validate_answer(PACKET, ANSWER | {field: value})


def test_draft_can_remain_unanswered():
    assert not validate_answer(PACKET, ANSWER | {"reviewed": False, "owner": "", "outcome": ""})["reviewed"]


def test_answers_are_bound_to_frozen_packet_and_private_site(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "packet.json").write_text(json.dumps(PACKET))
    (site / "index.html").write_text("review")
    (tmp_path / "snapshot.db").write_text("not served")
    with TestClient(create_review_app(tmp_path)) as client:
        token = client.get("/api/session").json()["token"]
        body = {"packet_sha256": packet_digest(PACKET), "answer": ANSWER}
        assert client.post("/api/answers", json=body).status_code == 401
        headers = {"X-Review-Token": token}
        assert client.post("/api/answers", json=body, headers=headers | {"Origin": "https://untrusted.example"}).status_code == 403
        assert client.post("/api/answers", json=body | {"packet_sha256": "different"}, headers=headers).status_code == 422
        assert client.post("/api/answers", json=body, headers=headers).status_code == 200
        assert client.get("/snapshot.db").status_code == 404
        assert client.get("/answers.json").status_code == 404
        saved = client.get("/api/answers", headers=headers).json()
        assert saved["answers"][0]["owner"] == "unknown"
        assert saved["packet_sha256"] == packet_digest(PACKET)
        assert (tmp_path / "answers.json").stat().st_mode & 0o777 == 0o600
