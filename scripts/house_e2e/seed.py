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
    draw.text((160, 185), "SYNTHETIC DEMO", fill="white", font_size=25)
    draw.text((50, 90), title, fill="#ffdb85", font_size=30)
    result = BytesIO()
    image.save(result, "JPEG", quality=85)
    return result.getvalue()


def filmstrip(title: str, when: datetime, kinds: list[str], *, arrival_seen: bool = True) -> tuple[bytes, dict]:
    from wash_dishes.filmstrip import render
    frames = [{'at': (when + timedelta(seconds=offset * 10)).timestamp(),
               'jpeg': jpeg(f"{title}: arrival frame {offset + 1}"), 'person_boxes': []} for offset in range(6)]
    boxes = [[.2 + i * .15, .5, .12, .22] for i in range(len(kinds))]
    item = {'dishes': [{'kind': k, 'quantity': 1} for k in kinds], 'location': 'Counter', 'box': boxes[0]}
    picture = render(item, frames, boxes=boxes, already_at=when.timestamp() if not arrival_seen else None)
    return picture, {'frames': [{k: v for k, v in f.items() if k != 'jpeg'} for f in frames],
                     'window': {'arrival_recorded': arrival_seen}}


def seed(app) -> dict:
    from wash_dishes import league

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
    for index, (kinds, when) in enumerate([(["pan", "plate", "bowl"], at + timedelta(seconds=90)), (["cup"], at + timedelta(seconds=45)),
                                          (["pot"], at),
                                          (["plate"], monday - timedelta(days=14) + timedelta(hours=14))]):
        ids = []
        visit_id = f"e2e-visit-{index + 1}"
        for kind in kinds:
            item = store.create_obligation(None, [{"kind": kind, "quantity": 1}], "Synthetic E2E",
                                           source="camera", at=when, candidate=True, location="Counter",
                                           handler_key=visit_id, track_key=f"{visit_id}-{kind}")
            oid = item["id"]
            ids.append(oid)
            media.save_snapshot(oid, jpeg(f"{visit_id}: {kind}"), "Dish left unattended", at=when)
            media.save_snapshot(oid, jpeg(f"{visit_id}: {kind} still out"),
                                "Matching dish still visible after the deadline", at=when + timedelta(minutes=2))
            store.record_seen(oid, when + timedelta(minutes=2))
            if index == 0:
                store.set_suggestion(oid, residents["andrew"], 0.95, {"face": 0.95})
        evidence_id = f"e2efilmstrip{index + 1}"
        filename = evidence_id + ".jpg"
        picture, capture = filmstrip(visit_id, when, kinds, arrival_seen=index != 1)
        (store.root / "media" / filename).write_bytes(picture)
        store.add_evidence(ids[0], evidence_id, filename, "filmstrip", "arrival", at=when,
                           expires=now + timedelta(days=30),
                           capture=capture | {"visit_id": visit_id, "obligation_ids": ids})
        visits.append({"id": visit_id, "dish_ids": ids, "points": sum(2 if k in ("pot", "pan") else 1 for k in kinds),
                       "evidence_id": evidence_id, "closed": index == 3})
    league.override(store, residents["ezzy"])
    return {"residents": residents, "visits": visits, "week_start": monday.date().isoformat(),
            "closed_start": (monday - timedelta(days=14)).date().isoformat(), "seeded_at": now.isoformat()}
