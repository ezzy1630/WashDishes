from __future__ import annotations

import html
import uuid
from datetime import timedelta

from .media import Media
from .store import Store, utcnow


def evidence(store: Store, media: Media, item: dict, label: str = "Illustrative demo evidence") -> None:
    """Synthetic illustrations are never mixed into the live camera data root."""
    name = html.escape(item["resident_name"] or "Unknown")
    kinds = html.escape(store.dish_description(item["dishes"]))
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="720" viewBox="0 0 1200 720">
    <rect width="1200" height="720" fill="#ddd7ca"/><rect y="290" width="1200" height="430" fill="#bcb5a5"/>
    <rect x="450" y="330" width="580" height="290" rx="35" fill="#858e88"/><rect x="472" y="352" width="536" height="246" rx="26" fill="#b9c3be"/>
    <path d="M745 360v-100c0-58 88-58 88 0v30" fill="none" stroke="#65746e" stroke-width="18"/>
    <ellipse cx="267" cy="470" rx="134" ry="42" fill="#f3efe7"/><ellipse cx="267" cy="465" rx="107" ry="28" fill="#e4ded1"/>
    <ellipse cx="659" cy="472" rx="100" ry="49" fill="#efebe0"/><ellipse cx="659" cy="460" rx="90" ry="30" fill="#dad2c2"/>
    <rect x="831" y="420" width="83" height="108" rx="12" fill="#758e7b"/><path d="M914 444c64-15 64 74 0 61" fill="none" stroke="#758e7b" stroke-width="15"/>
    <rect x="35" y="35" width="340" height="45" rx="8" fill="#34473b"/><text x="53" y="65" fill="white" font-family="sans-serif" font-size="23">DEMO • SYNTHETIC IMAGE</text>
    <text x="45" y="655" fill="#253a2d" font-family="sans-serif" font-size="30">{name} · {kinds}</text>
    <text x="45" y="690" fill="#46534b" font-family="sans-serif" font-size="20">Example event only. No real kitchen recording.</text></svg>'''
    evidence_id = uuid.uuid4().hex
    filename = evidence_id + ".svg"
    path = media.safe_path(filename)
    path.write_text(svg)
    path.chmod(0o600)
    store.add_evidence(item["id"], evidence_id, filename, "snapshot", label)


def seed(store: Store, media: Media) -> None:
    if store.residents():
        return
    residents = [store.add_resident(name) for name in ("Casey", "Riley", "Jordan", "Morgan")]
    now = utcnow()
    for day in range(1, 8):
        for index, resident in enumerate(residents):
            created = now - timedelta(days=day, hours=index)
            item = store.create_obligation(resident["id"], [{"kind": "bowl" if index % 2 else "plate", "quantity": 1}], source="demo", at=created)
            late = (day + index) % 4 == 0
            store.action(item["id"], "resolve", cleaner_id=residents[(index + 1) % 4]["id"] if late else resident["id"], resolved_at=created + timedelta(minutes=store.settings()["deadline_minutes"] + 25 if late else 35))
    pending = store.create_obligation(residents[0]["id"], [{"kind": "bowl", "quantity": 1}, {"kind": "mug", "quantity": 1}], source="demo", at=now - timedelta(minutes=30))
    overdue = store.create_obligation(residents[1]["id"], [{"kind": "plate", "quantity": 2}], source="demo", at=now - timedelta(minutes=store.settings()["deadline_minutes"] + 15))
    store.record_seen(overdue["id"], now)
    unknown = store.create_obligation(None, [{"kind": "pan", "quantity": 1}], source="demo", at=now - timedelta(minutes=20), candidate=True, reason="Two people were near the counter. Confirm ownership.")
    cleaned = store.create_obligation(residents[2]["id"], [{"kind": "cup", "quantity": 1}], source="demo", at=now - timedelta(minutes=55))
    store.action(cleaned["id"], "resolve", cleaner_id=residents[3]["id"], resolved_at=now - timedelta(minutes=10))
    for item in (pending, overdue, unknown, cleaned):
        evidence(store, media, item)
    store.scan_deadlines(now)


def scenario(store: Store, media: Media, name: str) -> dict:
    residents = [r for r in store.residents() if r["active"]]
    if not residents:
        raise ValueError("Add an example housemate first.")
    if name == "cleanup":
        pending = [o for o in store.obligations() if o["lifecycle"] == "open"]
        if not pending:
            raise ValueError("Add a dish event before demonstrating cleanup.")
        item = store.action(pending[0]["id"], "resolve", cleaner_id=residents[0]["id"])
    elif name in ("deposit", "overdue", "unknown"):
        item = store.create_obligation(None if name == "unknown" else residents[0]["id"], [{"kind": "mug" if name == "deposit" else "bowl", "quantity": 1}], source="demo", at=utcnow() - timedelta(minutes=store.settings()["deadline_minutes"] + 10 if name == "overdue" else 0), candidate=name == "unknown", reason="Ownership needs review." if name == "unknown" else "")
        if name == "overdue":
            store.record_seen(item["id"])
    else:
        raise ValueError("Choose deposit, overdue, unknown, or cleanup.")
    evidence(store, media, item)
    return store.obligation(item["id"])
