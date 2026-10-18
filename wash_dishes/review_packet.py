"""Local validation answers, kept separate from the live ledger and training data."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

CHOICES = {
    "deposit": {"yes", "no", "unknown"},
    "dirty": {"yes", "no", "unknown"},
    "continuity": {"same", "different", "unknown"},
    "outcome": {"washed", "left_dirty", "moved", "unknown"},
    "basis": {"footage", "personal_knowledge", "both", "unknown"},
}


def packet_digest(packet: dict) -> str:
    return hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_answer(packet: dict, answer: dict) -> dict:
    if not isinstance(answer, dict) or set(answer) != {"case_id", "reviewed", "handler", "owner", "note", *CHOICES}:
        raise ValueError("The answer must include every review question.")
    if not isinstance(answer["case_id"], str) or answer["case_id"] not in {c["case_id"] for c in packet["cases"]}:
        raise ValueError("That case is not in this review packet.")
    if type(answer["reviewed"]) is not bool:
        raise ValueError("Review status must be true or false.")
    people = {r["name"] for r in packet["residents"]}
    for field, values in CHOICES.items():
        if not isinstance(answer[field], str) or answer[field] not in values | ({""} if not answer["reviewed"] else set()):
            raise ValueError("Answer every question or choose Cannot tell before saving the case.")
    for field, extras in (("handler", {"unknown", "none"}), ("owner", {"unknown", "shared", "none"})):
        if not isinstance(answer[field], str) or answer[field] not in people | extras | ({""} if not answer["reviewed"] else set()):
            raise ValueError("Choose a listed person or Cannot tell.")
    if not isinstance(answer["note"], str) or len(answer["note"]) > 2000:
        raise ValueError("Notes can contain at most 2,000 characters.")
    return dict(answer) | {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def create_review_app(root: Path) -> FastAPI:
    """Serve only the selected site/media, with authenticated local answer writes."""
    site = root / "site"
    packet = json.loads((site / "packet.json").read_text())
    digest = packet_digest(packet)
    token = secrets.token_urlsafe(32)
    answers_path = root / "answers.json"
    lock = threading.Lock()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    def authenticate(request: Request):
        if not secrets.compare_digest(request.headers.get("X-Review-Token", ""), token):
            raise HTTPException(401, "Open the local review page to save answers.")
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            raise HTTPException(403, "Answers must come from the local review page.")

    def answers():
        data = json.loads(answers_path.read_text()) if answers_path.exists() else {"schema_version": 1, "packet_sha256": digest, "answers": []}
        if data["packet_sha256"] != digest:
            raise HTTPException(409, "Saved answers belong to a different packet. Preserve them and start a new review directory.")
        return data

    @app.get("/api/session")
    def session():
        return {"token": token, "packet_sha256": digest}

    @app.get("/api/answers")
    def get_answers(request: Request):
        authenticate(request)
        with lock:
            return answers()

    @app.post("/api/answers")
    async def save_answer(request: Request):
        authenticate(request)
        raw = await request.body()
        if len(raw) > 12000:
            raise HTTPException(413, "The answer is too large.")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("packet_sha256") != digest:
                raise ValueError("The packet changed. Reload before saving.")
            answer = validate_answer(packet, body.get("answer"))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        with lock:
            data = answers()
            data["answers"] = [a for a in data["answers"] if a["case_id"] != answer["case_id"]] + [answer]
            temporary = answers_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2))
            temporary.chmod(0o600)
            os.replace(temporary, answers_path)
            return data

    @app.get("/")
    def index():
        return FileResponse(site / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/", StaticFiles(directory=site), name="review")
    return app
