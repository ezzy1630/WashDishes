from __future__ import annotations

import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import checks, demo
from .config import Config, camera_url
from .incidents import ReviewConflict
from .media import Media
from .proof import proof_archive
from .runtime import Runtime
from .security import Access
from .store import NOT_A_DISH_NOTES, Store, parse_stamp, stamp, utcnow
from .vision import VisionError


class Login(BaseModel):
    password: str = Field(max_length=256)


class Dish(BaseModel):
    kind: str
    quantity: int = Field(ge=1, le=100, strict=True)


class Deposit(BaseModel):
    resident_id: int | None = None
    dishes: list[Dish] = Field(min_length=1, max_length=12)
    note: str = Field(default="", max_length=1000)


class Action(BaseModel):
    action: str
    resident_id: int | None = None
    cleaner_id: int | None = None
    note: str = Field(default="", max_length=1000)
    resolved_at: str | None = None


class Resident(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ResidentPatch(BaseModel):
    notify_url: str = Field(max_length=512)


class Dispute(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class CameraSettings(BaseModel):
    host: str = Field(max_length=253)
    username: str = Field(max_length=128)
    password: str = Field(max_length=256)
    stream: str = "stream1"
    enabled: bool = True


class Pause(BaseModel):
    paused: bool


class Scenario(BaseModel):
    scenario: str


class TrainingBox(BaseModel):
    kind: str
    box: list[float] = Field(min_length=4, max_length=4)


class TrainingLabel(BaseModel):
    status: str
    boxes: list[TrainingBox] = Field(max_length=300)


class LiveCapture(BaseModel):
    live_frame_id: int = Field(ge=1)
    purpose: str


class TraceRequest(BaseModel):
    frame_id: str = Field(max_length=20)
    area_id: str = Field(max_length=80)
    obligation_id: int | None = Field(default=None, ge=1)
    hours: float = Field(default=12, ge=.1, le=48, allow_inf_nan=False)


class TraceKeep(BaseModel):
    candidate: int = Field(ge=0, le=11)


class SequenceReview(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]+$")
    checks: dict[str, bool | None]


class IncidentReview(BaseModel):
    revision: str = Field(min_length=64, max_length=64)
    checks: dict[Literal['before', 'using', 'leaving', 'later'], bool | None] = Field(default_factory=dict)
    identity: Literal['keep', 'named', 'unknown'] = 'keep'
    person_id: int | None = Field(default=None, ge=1)
    responsibility: Literal['keep', 'confirm', 'unknown'] = 'keep'
    condition: Literal['unknown', 'used', 'clean', 'not_dish'] = 'unknown'
    outcome: Literal['keep', 'out', 'removed', 'washed', 'disputed'] = 'keep'
    cleaner_id: int | None = Field(default=None, ge=1)
    resolved_at: str | None = None
    note: str = Field(default='', max_length=1000)


class CheckAnswer(BaseModel):
    verdicts: list[bool] = Field(max_length=200)
    missed: int = Field(ge=0, le=50)


class BodyLimit:
    """Also bound chunked uploads, which have no Content-Length header."""

    def __init__(self, app, maximum: int = 9 * 1024 * 1024):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum:
                    raise HTTPException(413, "Uploads must be smaller than 8 MB.")
            return message

        return await self.app(scope, bounded_receive, send)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.environment()
    config.prepare()
    store = Store(config.root)
    media = Media(config.root, store)
    access = Access(config.root, config.password, config.demo)
    runtime = Runtime(config, store, media)
    if config.demo:
        demo.seed(store, media)

    @asynccontextmanager
    async def lifespan(app):
        if config.workers:
            runtime.start()
        try:
            yield
        finally:
            runtime.stop()
            store.close()

    app = FastAPI(title="Dish watch", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.media, app.state.runtime, app.state.access, app.state.config = store, media, runtime, access, config
    hosts = ["localhost", "127.0.0.1", "[::1]", "*.ts.net", "testserver"]
    hosts += [h.strip() for h in os.getenv("WASHDISHES_ALLOWED_HOSTS", "").split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def guards(request: Request, call_next):
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid request length."}, status_code=400)
        if length > 9 * 1024 * 1024:
            return JSONResponse({"detail": "Uploads must be smaller than 8 MB."}, status_code=413)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-origin changes are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def authenticated(request: Request) -> str:
        csrf = access.session(request.cookies.get("dish_session"))
        if not csrf:
            raise HTTPException(401, "Enter the household access key to continue.")
        if request.method not in ("GET", "HEAD") and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            raise HTTPException(403, "Your session changed. Reload the dashboard and try again.")
        return csrf

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(VisionError)
    async def vision_unavailable(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(LookupError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": "snapshot" if config.snapshot else "demo" if config.demo else "live"}

    @app.get("/api/session")
    def session(request: Request):
        csrf = access.session(request.cookies.get("dish_session"))
        return {"authenticated": bool(csrf), "demo": config.demo, "csrf_token": csrf}

    @app.post("/api/login")
    def login(body: Login, request: Request):
        try:
            token, csrf = access.login(body.password, request.client.host if request.client else "local")
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from None
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie("dish_session", token, max_age=12 * 3600, httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/")
        return response

    @app.post("/api/logout", dependencies=[Depends(authenticated)])
    def logout(request: Request):
        access.logout(request.cookies.get("dish_session"))
        response = JSONResponse({"ok": True})
        response.delete_cookie("dish_session")
        return response

    @app.get("/api/dashboard", dependencies=[Depends(authenticated)])
    def dashboard():
        obligations = store.obligations()
        return {"mode": "snapshot" if config.snapshot else "demo" if config.demo else "live", "server_time": stamp(), "summary": {"pending": sum(o["status"] == "pending" for o in obligations), "overdue": sum(o["lifecycle"] == "open" and o["timing_known"] and parse_stamp(o["due_at"]) <= utcnow() for o in obligations), "needs_review": sum(o["status"] in ("review", "disputed") for o in obligations), "cleaned_today": store.cleaned_today()}, "obligations": obligations, "residents": runtime.residents(), "activity": store.activity(), "statistics": store.statistics(), "camera": runtime.camera_status(), "storage": media.status(), "settings": store.settings(), "learning": store.learning(), "dataset": runtime.dataset.summary(), "checks": checks.summary(store), "sequences": runtime.sequences.summary(), "capture_jobs": media.capture_status(), "incident_counts": runtime.incidents.page(limit=0)["counts"]}

    @app.get("/api/history", dependencies=[Depends(authenticated)])
    def history(status: str | None = None, resident_id: int | None = None, date: str | None = None):
        if status and status not in ("pending", "overdue", "review", "resolved", "disputed", "dismissed", "cleared"):
            raise ValueError("Choose a valid history status.")
        return {"obligations": store.obligations(history=True, status=status or None, resident_id=resident_id, date=date or None)}

    @app.get("/api/obligations/{obligation_id}", dependencies=[Depends(authenticated)])
    def obligation(obligation_id: int):
        return store.obligation(obligation_id)

    @app.get("/api/obligations/{obligation_id}/proof", dependencies=[Depends(authenticated)])
    def proof(obligation_id: int):
        name, data = proof_archive(store, media, obligation_id)
        store.event("proof_exported", "Evidence exported as a ZIP", obligation_id)
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get('/api/incidents', dependencies=[Depends(authenticated)])
    def incidents(filter: Literal['all', 'attention', 'late', 'out', 'unknown', 'archive', 'photos', 'tracking'] = 'attention',
                  resident_id: int | None = Query(None, ge=1), limit: int = Query(24, ge=1, le=100),
                  offset: int = Query(0, ge=0), days: int | None = Query(None, ge=1, le=3650)):
        return runtime.incidents.page(filter=filter, resident_id=resident_id, limit=limit, offset=offset, days=days)

    @app.get('/api/incidents/{obligation_id}', dependencies=[Depends(authenticated)])
    def incident(obligation_id: int):
        detail = runtime.incidents.detail(obligation_id)
        detail['searches'] = [j for j in runtime.traces.recent(limit=None) if j['obligation_id'] == obligation_id]
        return detail

    @app.put('/api/incidents/{obligation_id}/review', dependencies=[Depends(authenticated)])
    def incident_review(obligation_id: int, body: IncidentReview):
        try:
            detail = runtime.incidents.review(obligation_id, body.model_dump())
            if body.condition == 'not_dish':
                learn_from_review(detail['record'], 'dismiss', 'Not a dish')
            elif body.condition in ('used', 'clean'):
                learn_from_review(detail['record'], 'confirm', '')
            detail['searches'] = [j for j in runtime.traces.recent(limit=None) if j['obligation_id'] == obligation_id]
            return detail
        except ReviewConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/sequences", dependencies=[Depends(authenticated)])
    def sequences(filter: Literal['all', 'attention', 'ready'] = 'all', limit: int = Query(40, ge=1, le=100), offset: int = Query(0, ge=0)):
        return runtime.sequences.page(filter=filter, limit=limit, offset=offset)

    @app.get("/api/sequences/{obligation_id}", dependencies=[Depends(authenticated)])
    def sequence(obligation_id: int):
        return runtime.sequences.get(obligation_id)

    @app.get('/api/sequences/{obligation_id}/share', dependencies=[Depends(authenticated)])
    def share_photo(obligation_id: int):
        from .sharing import render_share
        with runtime.sequences.lock, media.lock, store.lock:
            detail = runtime.incidents.detail(obligation_id)
            if not detail['share_ready']:
                raise HTTPException(409, detail['share_reason'])
            manifest, pictures = runtime.sequences._draft(detail['record'])
            image = render_share(manifest, pictures)
        return Response(image, media_type='image/jpeg', headers={
            'Content-Disposition': f'attachment; filename="dish-{obligation_id}.jpg"', 'Cache-Control': 'no-store'})

    @app.get("/api/sequences/{obligation_id}/image", dependencies=[Depends(authenticated)])
    def sequence_image(obligation_id: int):
        return FileResponse(runtime.sequences.image(obligation_id), media_type="image/jpeg", filename=f"dish-{obligation_id}-four-stage.jpg")

    @app.put("/api/sequences/{obligation_id}/review", dependencies=[Depends(authenticated)])
    def sequence_review(obligation_id: int, body: SequenceReview):
        if set(body.checks) - {"before", "using", "leaving", "later"}:
            raise ValueError("Review only Before, Using, Leaving and Later.")
        return runtime.sequences.review(obligation_id, body.fingerprint, body.checks)

    @app.post("/api/notify/test", dependencies=[Depends(authenticated)])
    def notify_test(body: dict):
        resident = body.get("resident_id")
        url = None
        if resident is not None:
            url = next((r["notify_url"] for r in store.residents() if r["id"] == resident), None)
            if not url:
                raise ValueError("Save this person's ntfy topic first.")
        try:
            runtime.notifier.test(url)
        except OSError as exc:
            raise HTTPException(502, f"Couldn't reach ntfy: {exc}") from None
        return {"ok": True}

    @app.post("/api/obligations", dependencies=[Depends(authenticated)], status_code=201)
    def deposit(body: Deposit):
        item = store.create_obligation(body.resident_id, [d.model_dump() for d in body.dishes], body.note)
        frame = runtime.camera.snapshot()
        if frame:
            media.save_snapshot(item["id"], frame, "Manually recorded deposit")
            media.save_recent_clip(item["id"], "Camera near manual deposit")
        return store.obligation(item["id"])

    @app.post("/api/obligations/{obligation_id}/action", dependencies=[Depends(authenticated)])
    def action(obligation_id: int, body: Action):
        item = store.action(obligation_id, body.action, resident_id=body.resident_id, cleaner_id=body.cleaner_id, note=body.note, resolved_at=parse_stamp(body.resolved_at) if body.resolved_at else None)
        if body.action == "resolve":
            frame = runtime.camera.snapshot()
            if frame:
                media.save_snapshot(item["id"], frame, "Cleanup confirmed by a person")
        learn_from_review(item, body.action, body.note)
        return store.obligation(item["id"])

    def learn_from_review(item: dict, action: str, note: str) -> None:
        """A person's answer about a camera record becomes a labeled example for the detector."""
        verdict = "dish" if action in ("confirm", "reassign", "resolve") else "not_dish" if action == "dismiss" and note.strip() in NOT_A_DISH_NOTES else None
        if verdict is None or item["source"] != "vision" or not item.get("box"):
            return
        photo = next((e for e in item["evidence"] if e["kind"] == "snapshot" and e["label"] == "Dish left unattended" and not e["expired"]), None)
        if not photo:
            return
        try:
            jpeg = media.safe_path(store.evidence(photo["id"])["filename"]).read_bytes()
            runtime.dataset.from_review(item["id"], jpeg, item["box"], item["dishes"][0]["kind"], verdict, observed_at=parse_stamp(photo["created_at"]))
        except (OSError, ValueError):
            # Training data is a by-product; the answer itself is already saved.
            pass

    @app.get("/api/scoreboard", dependencies=[Depends(authenticated)])
    def scoreboard():
        return scoreboard_body()

    def scoreboard_body() -> dict:
        from .identity_fit import LAST_RUN_FILE
        from .improve import history
        try:
            identity = json.loads((config.root / LAST_RUN_FILE).read_text())
        except (OSError, ValueError):
            identity = None
        return store.scoreboard() | {"detector_runs": history(config.root), "identity_fit": identity}

    @app.post("/api/residents/{resident_id}/link", dependencies=[Depends(authenticated)])
    def personal_link(resident_id: int):
        return {"token": store.create_link(resident_id)}

    @app.patch("/api/residents/{resident_id}", dependencies=[Depends(authenticated)])
    def resident_patch(resident_id: int, body: ResidentPatch):
        store.set_resident_notify(resident_id, body.notify_url.strip())
        return {"ok": True}

    # Personal pages: a housemate's own records, opened from their link. The token travels in a
    # header (the link keeps it after '#'), so it never appears in request paths or logs.
    def housemate(x_personal_token: str = Header(default="")) -> dict:
        resident = store.resident_for_link(x_personal_token)
        if not resident:
            raise HTTPException(401, "This personal link is no longer valid. Ask for a new one.")
        return resident

    @app.get("/api/me")
    def me(resident: dict = Depends(housemate)):
        return {"name": resident["name"], "demo": config.demo, "records": store.personal_records(resident["id"]),
                "settings": {"deadline_minutes": store.settings()["deadline_minutes"]}, "scoreboard": scoreboard_body()}

    @app.get("/api/me/evidence/{evidence_id}")
    def my_evidence(evidence_id: str, resident: dict = Depends(housemate)):
        item = store.evidence(evidence_id)
        owner = store.obligation(item["obligation_id"]) if item and item["obligation_id"] else None
        if not owner or owner["resident_id"] != resident["id"] or owner["confidence"] != "confirmed":
            raise HTTPException(404, "Evidence not found.")
        return evidence(evidence_id)

    @app.post("/api/me/dispute/{obligation_id}")
    def my_dispute(obligation_id: int, body: Dispute, resident: dict = Depends(housemate)):
        store.dispute_own(resident["id"], obligation_id, body.note)
        return {"ok": True}

    @app.get("/me")
    def me_page():
        return FileResponse(Path(__file__).parent / "static" / "me.html", headers={"Cache-Control": "no-cache"})

    @app.post("/api/residents", dependencies=[Depends(authenticated)], status_code=201)
    def resident(body: Resident):
        return store.add_resident(body.name) | {"enrolled_samples": 0}

    @app.delete("/api/residents/{resident_id}", dependencies=[Depends(authenticated)])
    def archive(resident_id: int):
        store.archive_resident(resident_id)
        with runtime.lock:
            runtime.local_vision().clear_enrollment(resident_id)
        return {"ok": True}

    def enroll(resident_id: int, data: bytes):
        with store.lock:
            store._resident(resident_id)
        with runtime.lock:
            count = runtime.local_vision().enroll(resident_id, data)
        store.event("face_enrolled", "A housemate face sample was saved locally", data={"resident_id": resident_id})
        return {"enrolled_samples": count}

    @app.post("/api/residents/{resident_id}/enroll", dependencies=[Depends(authenticated)])
    async def enroll_upload(resident_id: int, image: UploadFile = File()):
        data = await image.read(8 * 1024 * 1024 + 1)
        await image.close()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "Face images must be smaller than 8 MB.")
        return await run_in_threadpool(enroll, resident_id, data)

    @app.post("/api/residents/{resident_id}/enroll-camera", dependencies=[Depends(authenticated)])
    def enroll_camera(resident_id: int):
        frame = runtime.camera.snapshot()
        if not frame:
            raise HTTPException(503, "Connect the camera and stand in view before capturing a face sample.")
        return enroll(resident_id, frame)

    @app.delete("/api/residents/{resident_id}/enrollment", dependencies=[Depends(authenticated)])
    def unenroll(resident_id: int):
        with store.lock:
            store._resident(resident_id, active=False)
        with runtime.lock:
            runtime.local_vision().clear_enrollment(resident_id)
        store.event("face_removed", "A housemate's local face samples were removed", data={"resident_id": resident_id})
        return {"enrolled_samples": 0}

    @app.patch("/api/settings", dependencies=[Depends(authenticated)])
    def settings(body: dict):
        saved = store.set_settings(body)
        if "areas" in body or "zones" in body:
            # Zones are drawn on the current snapshot, so that picture becomes the reference view.
            runtime.confirm_view()
        if "camera_enabled" in body:
            runtime.camera.restart()
        if any(key in body for key in ("areas", "zones", "vision_enabled", "detector_path")):
            runtime.reset_observations()
        return saved

    @app.post("/api/camera", dependencies=[Depends(authenticated)])
    def camera(body: CameraSettings):
        if config.demo:
            raise ValueError("Camera connections are disabled in the isolated demo. Start live mode to connect.")
        data = body.model_dump()
        camera_url(data)
        store.set_camera(data)
        runtime.camera.restart()
        return {"ok": True, "message": "Camera configuration saved locally."}

    @app.post("/api/camera/pause", dependencies=[Depends(authenticated)])
    def pause(body: Pause):
        store.set_settings({"camera_enabled": not body.paused})
        runtime.camera.restart()
        return {"paused": body.paused}

    @app.get("/api/camera/snapshot", dependencies=[Depends(authenticated)])
    def snapshot():
        frame = runtime.camera.snapshot() if not config.demo else None
        if not frame:
            raise HTTPException(503, "No recent camera frame. Connect the camera or resume monitoring.")
        return Response(frame, media_type="image/jpeg")

    @app.get("/api/live", dependencies=[Depends(authenticated)])
    def live():
        return runtime.live_state()

    @app.get("/api/live/frame/{frame_id}", dependencies=[Depends(authenticated)])
    def live_frame(frame_id: int):
        frame = runtime.live_frame(frame_id)
        if not frame:
            raise HTTPException(404, "This live frame is no longer available.")
        return Response(frame, media_type="image/jpeg")

    @app.get("/api/live/video", dependencies=[Depends(authenticated)])
    def live_video(quality: str = "hd"):
        """Smooth video from the local relay; the browser never talks to the camera or go2rtc directly."""
        from urllib.request import urlopen
        if config.demo or not runtime.camera.relayed or not runtime.camera.relay.running():
            raise HTTPException(503, "Smooth video needs the local go2rtc relay. Frame-by-frame view is still available.")
        try:
            upstream = urlopen(runtime.camera.relay.mp4("kitchen_sd" if quality == "sd" else "kitchen"), timeout=10)
        except OSError:
            raise HTTPException(503, "The video relay is not responding.") from None

        def chunks():
            try:
                while chunk := upstream.read(64 * 1024):
                    yield chunk
            finally:
                upstream.close()
        return StreamingResponse(chunks(), media_type="video/mp4", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/areas/{area_id}/reference", dependencies=[Depends(authenticated)])
    def capture_reference(area_id: str):
        area = next((a for a in runtime.areas(store.settings()) if a["id"] == area_id), None)
        if area is None:
            raise LookupError("Monitoring area not found.")
        frame = runtime.camera.snapshot()
        if not frame:
            raise HTTPException(503, "Connect the camera before capturing an empty reference.")
        with runtime.lock:
            runtime.local_vision().capture_reference(frame, area["id"], area["box"])
        store.event("reference_captured", f"Empty reference captured for {area['name']}")
        return {"ok": True}

    @app.post("/api/view/confirm", dependencies=[Depends(authenticated)])
    def confirm_view():
        if config.demo or not runtime.confirm_view():
            raise HTTPException(503, "Connect the camera before confirming its view.")
        store.event("camera_view_confirmed", "Current camera view confirmed as the reference for the zones")
        return runtime.alignment.status()

    @app.get("/api/dataset", dependencies=[Depends(authenticated)])
    def dataset_summary():
        return runtime.dataset.summary()

    @app.post("/api/dataset/capture", dependencies=[Depends(authenticated)], status_code=201)
    def capture_live(body: LiveCapture):
        item = runtime.capture_frame(body.live_frame_id, body.purpose)
        if item is None:
            raise HTTPException(409, "That live frame has expired. Return to Live and capture a fresh picture.")
        return item

    @app.get("/api/traces", dependencies=[Depends(authenticated)])
    def trace_list():
        return {"searches": runtime.traces.recent()}

    @app.post("/api/traces", dependencies=[Depends(authenticated)], status_code=201)
    def trace_start(body: TraceRequest):
        return runtime.traces.create(body.frame_id, body.area_id, body.hours, body.obligation_id)

    @app.get("/api/traces/{job_id}", dependencies=[Depends(authenticated)])
    def trace_status(job_id: str):
        return runtime.traces.get(job_id)

    @app.get("/api/traces/{job_id}/pictures/{index}/{side}", dependencies=[Depends(authenticated)])
    def trace_picture(job_id: str, index: int, side: str):
        return FileResponse(runtime.traces.picture(job_id, index, side), media_type="image/jpeg")

    @app.get("/api/traces/{job_id}/clips/{index}", dependencies=[Depends(authenticated)])
    def trace_clip(job_id: str, index: int):
        return FileResponse(runtime.traces.clip(job_id, index), media_type="video/mp4")

    @app.post("/api/traces/{job_id}/keep", dependencies=[Depends(authenticated)])
    def trace_keep(job_id: str, body: TraceKeep):
        return runtime.traces.keep(job_id, body.candidate)

    @app.get("/api/dataset/next", dependencies=[Depends(authenticated)])
    def dataset_next(after: str | None = None, status: str = "unlabeled"):
        return {"item": runtime.dataset.next(after=after, status=status)}

    @app.get("/api/dataset/{frame_id}", dependencies=[Depends(authenticated)])
    def dataset_item(frame_id: str):
        return runtime.dataset.item(frame_id)

    @app.get("/api/dataset/{frame_id}/image", dependencies=[Depends(authenticated)])
    def dataset_image(frame_id: str):
        return FileResponse(runtime.dataset.image(frame_id), media_type="image/jpeg")

    @app.put("/api/dataset/{frame_id}", dependencies=[Depends(authenticated)])
    def dataset_label(frame_id: str, body: TrainingLabel):
        return runtime.dataset.update(frame_id, [b.model_dump() for b in body.boxes], body.status)

    @app.delete("/api/dataset/{frame_id}", dependencies=[Depends(authenticated)])
    def dataset_delete(frame_id: str):
        runtime.dataset.delete(frame_id)
        return {"ok": True}

    @app.get("/api/evidence/{evidence_id}", dependencies=[Depends(authenticated)])
    def evidence(evidence_id: str):
        item = store.evidence(evidence_id)
        if not item:
            raise HTTPException(404, "Evidence not found.")
        if json.loads(item["capture"]).get("derived_sequence") and runtime.sequences.get(item["obligation_id"])["stale"]:
            raise HTTPException(409, "This record changed. Its collage is being updated; check again shortly.")
        if item["expired"] or parse_stamp(item["expires_at"]) <= utcnow():
            store.expire_evidence(evidence_id)
            raise HTTPException(410, "This recording expired. Its event history is retained.")
        path = media.safe_path(item["filename"])
        if not path.exists():
            store.expire_evidence(evidence_id)
            raise HTTPException(410, "This recording is no longer available.")
        return FileResponse(path)

    @app.post("/api/checks/{check_id}", dependencies=[Depends(authenticated)])
    def answer_check(check_id: int, body: CheckAnswer):
        checks.answer(store, runtime.dataset, check_id, body.verdicts, body.missed)
        return checks.summary(store)

    @app.get("/api/checks/{check_id}/photo", dependencies=[Depends(authenticated)])
    def check_photo(check_id: int):
        item = store.check_by_id(check_id)
        path = media.safe_path(item["photo"]) if item and item["photo"] else None
        if not path or not path.exists():
            raise HTTPException(410, "This check photo is no longer available.")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/storage/cleanup", dependencies=[Depends(authenticated)])
    def cleanup():
        return media.cleanup()

    @app.post("/api/demo/scenario", dependencies=[Depends(authenticated)])
    def demo_scenario(body: Scenario):
        if not config.demo:
            raise HTTPException(404, "Demo scenarios are unavailable in live mode.")
        return demo.scenario(store, media, body.scenario)

    static = Path(__file__).parent / "static"
    static.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app
