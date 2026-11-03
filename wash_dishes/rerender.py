"""Re-render retained open/this-week images, without replaying detection or identity.

python -m wash_dishes.rerender --root /explicit/data/root --dry-run
python -m wash_dishes.rerender --root /explicit/data/root --apply
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import sqlite3
import time
from pathlib import Path

from PIL import Image

from . import evidence_image, filmstrip, league
from .media import Media, atomic_write
from .store import Store, parse_stamp, stamp, utcnow


class ReadJournal:
    """No schema preparation, WAL changes or writes, including during a dry run."""

    def __init__(self, path: Path, fallback=()):
        self.path, self.fallback = path, fallback

    def bounds(self):
        if not self.path.is_file():
            return None
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            first = conn.execute("SELECT at FROM frames ORDER BY at LIMIT 1").fetchone()
            last = conn.execute("SELECT at FROM frames ORDER BY at DESC LIMIT 1").fetchone()
            return (first[0], last[0]) if first else None

    def frames(self, start, end):
        if self.path.is_file():
            with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                for at, detections in conn.execute(
                    "SELECT at,detections FROM frames WHERE at BETWEEN ? AND ? ORDER BY at", (start, end)
                ):
                    yield {"at": at, "detections": json.loads(detections)}
        else:
            for f in self.fallback:
                if start <= f["at"] <= end:
                    yield {
                        "at": f["at"],
                        "detections": [{"kind": "person", "score": 1.0, "box": b} for b in f.get("person_boxes", [])],
                    }


def targets(conn, *, now=None):
    now = now or utcnow()
    week = league.week_bounds(now=now)
    start, end = league.week_times(week)
    return [
        r[0]
        for r in conn.execute(
            """SELECT id FROM obligations WHERE archived_at IS NULL AND
        lifecycle NOT IN ('dismissed','disputed') AND
        (lifecycle='open' OR (due_at>=? AND due_at<?)) ORDER BY id""",
            (stamp(start), stamp(end)),
        )
    ]


def inventory(root: Path, *, now=None):
    # The selected root must already exist. Dry runs never create a DB/settings.
    with sqlite3.connect((root / "history.db").resolve().as_uri() + "?mode=ro", uri=True) as conn:
        ids = targets(conn, now=now)
        visits = []
        for vid, eid, metadata in conn.execute(
            "SELECT id,evidence_id,metadata FROM arrival_visits WHERE evidence_id IS NOT NULL"
        ):
            meta = json.loads(metadata)
            if not set(meta["obligation_ids"]).intersection(ids):
                continue
            row = conn.execute(
                "SELECT filename,expired,expires_at FROM evidence WHERE id=?", (eid,)
            ).fetchone()
            if not row or row[1] or parse_stamp(row[2]) <= (now or utcnow()):
                continue
            visits.append({"visit_id": vid, "evidence_id": eid, "filename": row[0], "metadata": meta})
        sequences = [
            r[0] for r in conn.execute("SELECT obligation_id FROM evidence_sequences") if r[0] in ids
        ]
    return {"obligation_ids": ids, "visits": visits, "sequences": sequences}


def run(root: Path, *, apply=False, limit=100, offset=0, now=None):
    if limit < 1 or offset < 0:
        raise ValueError("Limit must be positive and offset nonnegative.")
    root = root.resolve()
    plan = inventory(root, now=now)
    actions = [{"kind": "filmstrip", "target": v["visit_id"]} for v in plan["visits"]] + [
        {"kind": "sequence", "target": oid} for oid in plan["sequences"]
    ]
    total = len(actions)
    actions = actions[offset : offset + limit]
    result = {
        "root": str(root),
        "dry_run": not apply,
        "eligible_records": len(plan["obligation_ids"]),
        "actions": actions,
        "total_actions": total,
        "next_offset": offset + limit if offset + limit < total else None,
        "updated": [],
        "skipped": [],
    }
    if not apply:
        return result
    # Only explicit --apply opens the existing root for mutation. No runtime,
    # models, replay, identity callback or new visit membership is constructed.
    store = Store(root)
    try:
        media = Media(root, store)
        from .sequences import SequenceCollection

        collection = SequenceCollection(store, media)
        for action in actions:
            if action["kind"] == "sequence":
                count = collection.refresh(limit=1, obligation_id=action["target"], force=True)
                (result["updated"] if count else result["skipped"]).append(action)
                continue
            visit = next(v for v in plan["visits"] if v["visit_id"] == action["target"])
            meta = copy.deepcopy(visit["metadata"])
            if not meta.get("frames"):
                result["skipped"].append(action | {"reason": "No saved arrival frame times."})
                continue
            boxes = [meta["dish_boxes"][str(oid)] for oid in meta["canonical_obligation_ids"]]
            journal = ReadJournal(root / "journal.db", meta["frames"])
            deadline = time.monotonic() + 60
            frames = filmstrip.display_frames(
                journal,
                media.buffer,
                {"window": meta["window"], "frames": meta["frames"]},
                boxes,
                deadline=deadline,
            )
            # Do not substitute a partial window or a nearby visitor for missing
            # original endpoints. Keep the old image when its sources have expired.
            if (
                not frames
                or abs(frames[0]["at"] - meta["frames"][0]["at"]) > 0.75
                or abs(frames[-1]["at"] - meta["frames"][-1]["at"]) > 0.75
            ):
                result["skipped"].append(
                    action | {"reason": "Arrival rerender reached its time budget; original image retained."
                              if time.monotonic() >= deadline else
                              "Original arrival endpoints could not be decoded within tolerance; original image retained.",
                              "expected_endpoints": [meta["frames"][0]["at"], meta["frames"][-1]["at"]],
                              "decoded_endpoints": [frames[0]["at"], frames[-1]["at"]] if frames else []}
                )
                continue
            item = store.obligation(meta["canonical_obligation_ids"][0])
            window = meta["window"]
            data = filmstrip.render(
                item,
                frames,
                boxes=boxes,
                approximate=window.get("approximate", False),
                already_at=window.get("first_visible_at")
                if not window.get("arrival_recorded", True)
                else None,
            )
            with Image.open(io.BytesIO(data)) as image:
                meta["image_width"], meta["image_height"] = image.size
            meta.update(
                render_version=evidence_image.VERSION,
                layout="stacked",
                frames=[{k: v for k, v in f.items() if k != "jpeg"} for f in frames],
            )
            # Read back before replacing; a concurrent membership/identity edit
            # must not be lost. Expiry is never extended by re-rendering.
            with media.lock, store.lock, store.conn:
                current = store.conn.execute(
                    "SELECT metadata FROM arrival_visits WHERE id=?", (visit["visit_id"],)
                ).fetchone()
                if not current or json.loads(current[0]) != visit["metadata"]:
                    result["skipped"].append(action | {"reason": "Visit changed while rendering."})
                    continue
                atomic_write(media.safe_path(visit["filename"]), data)
                store.conn.execute(
                    "UPDATE arrival_visits SET metadata=? WHERE id=?", (json.dumps(meta), visit["visit_id"])
                )
                store.conn.execute(
                    "UPDATE evidence SET capture=? WHERE id=?", (json.dumps(meta), visit["evidence_id"])
                )
            result["updated"].append(action | {"bytes": len(data)})
    finally:
        store.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Exact existing Dish Watch data root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report only (the default)")
    mode.add_argument(
        "--apply", action="store_true", help="Replace derived images and render metadata locally"
    )
    parser.add_argument("--limit", type=int, default=100, help="Maximum derived images this invocation")
    parser.add_argument("--offset", type=int, default=0, help="Continue at this action offset")
    args = parser.parse_args()
    print(json.dumps(run(args.root, apply=args.apply, limit=args.limit, offset=args.offset), indent=2))


if __name__ == "__main__":
    main()
