"""One dish record as a self-contained ZIP to show the person it concerns.

It holds a readable page, the original photos and clips, and record.json with a SHA-256 for every
file, so the export can be checked later. The page states what the evidence does and does not show.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import re
import zipfile
from datetime import datetime

from .media import Media
from .notify import timezone
from .sequences import SequenceCollection
from .store import Store, parse_stamp, stamp, utcnow

STYLE = """body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 20px;color:#1d1f21}
h1{font-size:24px;margin-bottom:4px}h2{font-size:18px;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:4px}
table{border-collapse:collapse}td{padding:4px 16px 4px 0;vertical-align:top}td:first-child{color:#666;white-space:nowrap}
.stage{margin:20px 0}.stage img,.stage video{max-width:100%;border:1px solid #ccc}.detail{max-width:280px!important}
.muted{color:#666}.late{color:#a32020;font-weight:600}code{font-size:12px;word-break:break-all}li{margin:4px 0}"""


def _when(value: str | None) -> str:
    return parse_stamp(value).astimezone(timezone()).strftime("%a %b %-d %Y, %-I:%M:%S %p %Z") if value else "—"


def _status(item: dict) -> tuple[str, str]:
    lifecycle = item["lifecycle"]
    if item.get("closed_by_camera"):
        return item["reason"], "late"
    if lifecycle == "resolved":
        return ("Washed late" if item["overdue_verified"] else "Washed in time") + f", marked by a person at {_when(item['resolved_at'])}", "late" if item["overdue_verified"] else ""
    if lifecycle == "cleared":
        return "Taken away before the deadline. Not late.", ""
    if lifecycle in ("dismissed", "disputed"):
        return f"{lifecycle.title()}: {item['reason'] or 'no reason given'}", "muted"
    if item["overdue_verified"]:
        return "Still out after the deadline, seen by the camera", "late"
    return "Still open", ""


def _owner(item: dict) -> str:
    if item["confidence"] != "confirmed" or not item["resident_name"]:
        return "Not decided"
    how = {"camera": "named by the camera from appearance; not reviewed by a person",
           "camera_reviewed": "named by the camera, then confirmed by a person"}.get(item.get("attribution") or "", "confirmed by a person")
    return f"{item['resident_name']} ({how})"


def proof_archive(store: Store, media: Media, obligation_id: int) -> tuple[str, bytes]:
    item = store.obligation(obligation_id)
    try:
        sequence = SequenceCollection(store, media).get(obligation_id)
    except LookupError:
        sequence = None
    files: dict[str, dict] = {}
    for index, entry in enumerate(item["evidence"], 1):
        saved = store.evidence(entry["id"])
        try:
            path = media.safe_path(saved["filename"])
            stale_collage = entry.get("capture", {}).get("derived_sequence") and (not sequence or sequence["stale"])
            data = None if stale_collage or saved["expired"] or parse_stamp(saved["expires_at"]) <= utcnow() else path.read_bytes()
        except (OSError, ValueError):
            data = None
        slug = re.sub(r"[^a-z0-9]+", "-", entry["label"].lower()).strip("-")[:40]
        name = f"{'clips' if entry['kind'] == 'clip' else 'photos'}/{index:02d}-{slug}{path.suffix if data else ''}"
        files[entry["id"]] = {"name": name, "data": data, "label": entry["label"], "kind": entry["kind"], "created_at": entry["created_at"],
                              "capture": entry.get("capture", {}),
                              "sha256": hashlib.sha256(data).hexdigest() if data else None}

    def show(evidence: dict | None, cls: str = "") -> str:
        file = files.get(evidence["id"]) if evidence else None
        if not file or not file["data"]:
            return '<p class="muted">Not available (expired or never captured).</p>' if evidence else ""
        tag = f'<video controls src="{html.escape(file["name"])}"></video>' if file["kind"] == "clip" else f'<img class="{cls}" src="{html.escape(file["name"])}" alt="{html.escape(file["label"])}">'
        return f'{tag}<div class="muted">{html.escape(file["label"])} · {_when(file["created_at"])}</div>'

    status, tone = _status(item)
    what = store.dish_description(item["dishes"])
    rows = [("Dishes", what), ("Where", item["location"] or "—"), ("Owner", _owner(item)),
            ("Set down" if item["timing_known"] else "First seen", _when(item["created_at"])),
            ("Deadline", _when(item["due_at"]) if item["timing_known"] else "No deadline (already there when monitoring began)"),
            ("Outcome", f'<span class="{tone}">{html.escape(status)}</span>')]
    stages = "".join(
        f'<div class="stage"><h3>{html.escape(s["title"])} <span class="muted">· {_when(s["at"])}</span></h3><p>{html.escape(s["summary"])}</p>'
        f'{show(s["photo"])}{show(s["detail"], "detail")}{"".join(show(c) for c in s["clips"])}</div>'
        for s in item["evidence_timeline"]["stages"])
    log = "".join(f"<li>{_when(e['created_at'])}: {html.escape(e['description'])}</li>" for e in sorted(item["events"], key=lambda e: e["id"]))
    hashes = "".join(f"<li>{html.escape(f['name'])}: <code>{f['sha256']}</code></li>" for f in files.values() if f["data"])
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Dish record #{item['id']}</title><style>{STYLE}</style></head><body>
<h1>Dish record #{item['id']}: {html.escape(what)}</h1><p class="muted">Exported from Dish Watch {_when(stamp())}.</p>
<table>{"".join(f"<tr><td>{k}</td><td>{v if k == 'Outcome' else html.escape(v)}</td></tr>" for k, v in rows)}</table>
<h2>Evidence, step by step</h2>{stages}
<h2>What this shows and doesn't</h2><ul>{"".join(f"<li>{html.escape(line)}</li>" for line in item["evidence_timeline"]["limitations"])}</ul>
<h2>Everything recorded</h2><ol>{log}</ol>
<h2>File checksums</h2><p class="muted">SHA-256 of each file in this export, also in record.json.</p><ul>{hashes or "<li>No files.</li>"}</ul>
</body></html>"""
    record = {key: item[key] for key in ("id", "dishes", "location", "created_at", "due_at", "resolved_at", "lifecycle", "status", "confidence",
                                         "attribution", "resident_name", "cleaner_name", "overdue_verified", "timing_known", "reason")}
    record |= {"exported_at": stamp(), "events": sorted(item["events"], key=lambda e: e["id"]),
               "files": [{k: f[k] for k in ("name", "label", "kind", "created_at", "sha256", "capture")} for f in files.values()]}
    if sequence:
        record["four_stage_sequence"] = sequence
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", page, zipfile.ZIP_DEFLATED)
        archive.writestr("record.json", json.dumps(record, indent=2), zipfile.ZIP_DEFLATED)
        for file in files.values():
            if file["data"]:
                archive.writestr(file["name"], file["data"], zipfile.ZIP_STORED)
    day = datetime.now(timezone()).strftime("%Y-%m-%d")
    return f"dish-record-{item['id']}-{day}.zip", buffer.getvalue()
