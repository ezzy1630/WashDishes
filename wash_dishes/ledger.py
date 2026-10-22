"""Each person's record: the proof pictures someone named as theirs, newest first; and the picture
of any kept proof, drawn with the name currently on it.

A proof counts against a person once their name was picked on it (its dish is confirmed to them).
Proofs marked wrong and dishes later disputed or dismissed never count. The dish does not have to
still be out: washing it two days later does not undo having left it. Proof photos are kept a week
(proofs.KEEP); older entries keep their date and dish but have no picture.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from datetime import timezone as utc
from pathlib import Path

from .collage import STAGES, VERSION, place, render
from .store import Store, stamp

COUNTS = "o.confidence='confirmed' AND o.lifecycle NOT IN ('disputed','dismissed')"
NAMED = f"""FROM proofs p JOIN obligations o ON o.id=p.obligation_id JOIN residents r ON r.id=o.resident_id
    WHERE p.status='ready' AND {COUNTS}"""
_render_lock = threading.Lock()


def tally(store: Store) -> list[dict]:
    """Everyone in the house with how many named proofs they have, most first."""
    with store.lock:
        counts = {row[0]: (row[1], row[2]) for row in store.conn.execute(
            f"SELECT o.resident_id, count(*), max(json_extract(p.data,'$.putting_at')) {NAMED} GROUP BY o.resident_id")}
    people = [{"id": r["id"], "name": r["name"], "count": counts.get(r["id"], (0, None))[0],
               "last_at": _at(counts.get(r["id"], (0, None))[1])} for r in store.residents() if r["active"]]
    return sorted(people, key=lambda p: (-p["count"], p["name"].lower()))


def person(store: Store, resident_id: int) -> dict:
    """One person's named proofs with a picture where the photos are still kept."""
    resident = next((r for r in store.residents() if r["id"] == resident_id), None)
    if resident is None:
        raise LookupError("No one with that name.")
    with store.lock:
        rows = store.conn.execute(f"SELECT p.obligation_id id, p.data, o.dishes, o.location {NAMED} AND o.resident_id=?",
                                  (resident_id,)).fetchall()
    folder = Path(store.root) / "proofs"
    items = []
    for row in rows:
        data = json.loads(row["data"])
        kept = all((folder / str(row["id"]) / f"{key}.jpg").exists() for key in STAGES)
        items.append({"id": row["id"], "dish": _dish(store, row["dishes"]), "location": place(row["location"]),
                      "put_down_at": _at(data["putting_at"]), "late_at": _at(data["late_at"]),
                      "image_url": image_url(row["id"], resident_id) if kept else None})
    items.sort(key=lambda p: p["put_down_at"], reverse=True)
    return {"id": resident["id"], "name": resident["name"], "count": len(items), "items": items}


def image_url(obligation_id: int, resident_id: int | None) -> str:
    # Changes with the name on the picture and with its design, so a cached copy is never stale.
    return f"/api/proofs/{obligation_id}/image?v={resident_id or 0}.{VERSION}"


def picture(store: Store, obligation_id: int) -> bytes:
    """The proof picture with the name currently on it (none until someone picks one), drawn from
    the kept photos once per design and name."""
    with store.lock:
        row = store.conn.execute(f"""SELECT p.data, o.dishes, o.location, r.id resident_id, r.name FROM proofs p JOIN obligations o ON o.id=p.obligation_id
            LEFT JOIN residents r ON r.id=o.resident_id AND {COUNTS} WHERE p.status='ready' AND p.obligation_id=?""", (obligation_id,)).fetchone()
    folder = Path(store.root) / "proofs" / str(obligation_id)
    if row is None or not all((folder / f"{key}.jpg").exists() for key in STAGES):
        raise LookupError("This proof picture is no longer kept.")
    cached = folder / f"collage-v{VERSION}-{row['resident_id'] or 0}.jpg"
    with _render_lock:
        if not cached.exists():
            meta = json.loads(row["data"]) | {"dish": _dish(store, row["dishes"]), "location": place(row["location"])}
            tmp = cached.with_suffix(".tmp")
            tmp.write_bytes(render(meta, {key: (folder / f"{key}.jpg").read_bytes() for key in STAGES}, row["name"]))
            tmp.replace(cached)
        return cached.read_bytes()


def _at(value: float | None) -> str | None:
    return stamp(datetime.fromtimestamp(value, utc.utc)) if value is not None else None


def _dish(store: Store, dishes: str) -> str:
    # Same wording as the Send page: one dish is "cup", not "1 cup".
    description = store.dish_description(json.loads(dishes))
    return description[2:] if description.startswith("1 ") and "," not in description else description
