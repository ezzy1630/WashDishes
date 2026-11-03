"""Synthetic, repeatable store fixture; no cameras, private DBs or household images."""
from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

NAMES = "ezzy jadon sutton henry calder andrew elliot pedro daniel jacob".split()


def jpeg(title: str) -> bytes:
    image = Image.new("RGB", (960, 540), "#182630")
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 180, 900, 470), fill="#53616b")
    draw.ellipse((200, 220, 480, 430), fill="#cccccc", outline="#eeeeee", width=8)
    draw.rectangle((475, 305, 730, 335), fill="#151515")
    draw.text((50, 40), "SYNTHETIC E2E FIXTURE - NOT HOUSEHOLD EVIDENCE", fill="white", font_size=25)
    draw.text((50, 90), title, fill="#ffdb85", font_size=30)
    result = BytesIO()
    image.save(result, "JPEG", quality=85)
    return result.getvalue()


def filmstrip(title: str) -> bytes:
    strip = Image.new("RGB", (1440, 270))
    for index in range(3):
        frame = Image.open(BytesIO(jpeg(f"{title}: arrival frame {index + 1}"))).resize((480, 270))
        strip.paste(frame, (480 * index, 0))
    result = BytesIO()
    strip.save(result, "JPEG", quality=85)
    return result.getvalue()


def seed(app) -> dict:
    store, media = app.state.store, app.state.media
    if store.residents():
        raise RuntimeError("Refusing to seed a nonempty database")
    residents = {name: store.add_resident(name.title())["id"] for name in NAMES}
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    # Current-week times remain in the past, even on Monday morning. A short
    # fixture deadline avoids a wall-clock freeze or product test-only clock.
    store.set_settings({"deadline_minutes": 1})
    at = min(monday + timedelta(hours=14), now - timedelta(minutes=5))
    if at < monday:
        raise RuntimeError("Run after Monday 00:05 house time so current-week overdue fixtures can exist")
    visits = []
    for index, (kinds, when) in enumerate([(["pan", "plate", "bowl"], at), (["cup"], at + timedelta(seconds=45)),
                                          (["pot"], at + timedelta(seconds=90)),
                                          (["plate"], monday - timedelta(days=14) + timedelta(hours=14))]):
        ids = []
        visit_id = f"e2e-visit-{index + 1}"
        for kind in kinds:
            item = store.create_obligation(None, [{"kind": kind, "quantity": 1}], "Synthetic E2E",
                                           source="camera", at=when, candidate=True, location="Counter",
                                           handler_key=visit_id, track_key=f"{visit_id}-{kind}")
            oid = item["id"]
            ids.append(oid)
            media.save_snapshot(oid, jpeg(f"{visit_id}: {kind}"), "left unattended", at=when)
            media.save_snapshot(oid, jpeg(f"{visit_id}: {kind} still out"), "still_out", at=when + timedelta(minutes=2))
            store.record_seen(oid, when + timedelta(minutes=2))
            if index == 0:
                store.set_suggestion(oid, residents["andrew"], 0.95, {"face": 0.95})
        evidence_id = f"e2efilmstrip{index + 1}"
        filename = evidence_id + ".jpg"
        (store.root / "media" / filename).write_bytes(filmstrip(visit_id))
        # Lane A's arrival metadata uses the existing evidence capture field.
        frames = [{"at": (when + timedelta(seconds=offset)).isoformat(), "index": offset,
                   "evidence_id": evidence_id} for offset in range(3)]
        store.add_evidence(ids[0], evidence_id, filename, "filmstrip", "arrival", at=when,
                           expires=now + timedelta(days=30),
                           capture={"visit_id": visit_id, "obligation_ids": ids, "frames": frames})
        visits.append({"id": visit_id, "dish_ids": ids, "points": sum(2 if k in ("pot", "pan") else 1 for k in kinds),
                       "evidence_id": evidence_id, "closed": index == 3})
    return {"residents": residents, "visits": visits, "week_start": monday.date().isoformat(),
            "closed_start": (monday - timedelta(days=14)).date().isoformat(), "seeded_at": now.isoformat()}
