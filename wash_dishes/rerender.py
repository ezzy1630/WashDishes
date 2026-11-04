"""Re-render retained open/this-week images, without replaying detection or identity.

python -m wash_dishes.rerender --root /explicit/data/root --dry-run
python -m wash_dishes.rerender --root /explicit/data/root --apply
"""

from __future__ import annotations

import argparse
import copy
import fcntl
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


def inventory(root: Path, *, now=None, use_context=False, after_visit_id='', obligation_id=None):
    # The selected root must already exist. Dry runs never create a DB/settings.
    with sqlite3.connect((root / "history.db").resolve().as_uri() + "?mode=ro", uri=True) as conn:
        ids = targets(conn, now=now)
        visits = []
        for vid, eid, metadata in conn.execute(
            "SELECT id,evidence_id,metadata FROM arrival_visits WHERE evidence_id IS NOT NULL AND id>? ORDER BY id",
            (after_visit_id,),
        ):
            meta = json.loads(metadata)
            if obligation_id is not None and obligation_id not in meta['obligation_ids']:
                continue
            if use_context:
                if obligation_id is None and any(f.get('person_boxes') for f in meta.get('frames', [])):
                    continue
            elif not set(meta["obligation_ids"]).intersection(ids):
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
        if use_context:
            ids = sorted({oid for visit in visits for oid in visit['metadata']['obligation_ids']})
    return {"obligation_ids": ids, "visits": visits, "sequences": [] if use_context else sequences}


def run(root: Path, *, apply=False, limit=100, offset=0, now=None, use_context=False, after_visit_id='', obligation_id=None):
    if limit < 1 or offset < 0:
        raise ValueError("Limit must be positive and offset nonnegative.")
    root = root.resolve()
    if use_context and offset:
        raise ValueError('Use --after-visit-id, not --offset, for a resumable use-context pass.')
    if obligation_id is not None and (not use_context or obligation_id < 1):
        raise ValueError('Use a positive --obligation-id with --use-context to repair one visually empty picture.')
    plan = inventory(root, now=now, use_context=use_context, after_visit_id=after_visit_id, obligation_id=obligation_id)
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
    if use_context:
        result['next_visit_id'] = actions[-1]['target'] if actions and len(actions) < total else None
    if not apply:
        return result
    if use_context:
        with (root / '.filmstrip.lock').open('a') as claim:
            try:
                fcntl.flock(claim, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                result.update(busy=True, next_visit_id=after_visit_id)
                return result
            return _apply(root, plan, actions, result, now=now, use_context=True)
    return _apply(root, plan, actions, result, now=now, use_context=False)


def _apply(root, plan, actions, result, *, now, use_context):
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
            deadline = time.monotonic() + (30 if use_context else 60)
            item = store.obligation(meta["canonical_obligation_ids"][0])
            found = {"window": meta["window"], "frames": meta["frames"]}
            if use_context:
                found = filmstrip.plan(journal, media.buffer, item, deadline=deadline-10)
                if isinstance(found, str) or not any(f.get('person_boxes') for f in found['frames']):
                    result['skipped'].append(action | {'reason': found if isinstance(found, str) else
                                                       'No person within reach in the retained six-hour lookback.'})
                    continue
            frames = filmstrip.display_frames(
                journal,
                media.buffer,
                found,
                boxes,
                deadline=deadline-2 if use_context else deadline,
            )
            # Do not substitute a partial window or a nearby visitor for missing
            # original endpoints. Keep the old image when its sources have expired.
            endpoints = [f for f in found['frames'] if not f.get('use_visit')]
            decoded_endpoints = [f for f in frames if not f.get('use_visit')]
            if (
                not frames
                or not decoded_endpoints
                or abs(decoded_endpoints[0]["at"] - endpoints[0]["at"]) > 0.75
                or abs(decoded_endpoints[-1]["at"] - endpoints[-1]["at"]) > 0.75
            ):
                result["skipped"].append(
                    action | {"reason": "Arrival rerender reached its time budget; original image retained."
                              if time.monotonic() >= deadline else
                              "Original arrival endpoints could not be decoded within tolerance; original image retained.",
                              "expected_endpoints": [meta["frames"][0]["at"], meta["frames"][-1]["at"]],
                              "decoded_endpoints": [frames[0]["at"], frames[-1]["at"]] if frames else []}
                )
                continue
            if use_context and not any(f.get('person_boxes') for f in frames):
                result['skipped'].append(action | {'reason': 'Nearby-person frames could not be decoded; original retained.'})
                continue
            window = found['window']
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
                window=window,
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
                evidence = store.conn.execute('SELECT expired,expires_at FROM evidence WHERE id=?',
                                              (visit['evidence_id'],)).fetchone()
                if not evidence or evidence['expired'] or parse_stamp(evidence['expires_at']) <= (now or utcnow()):
                    result['skipped'].append(action | {'reason': 'Evidence expired while rendering.'})
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
    parser.add_argument('--use-context', action='store_true', help='Backfill saved no-person filmstrips from retained nearby visits; 30 seconds each')
    parser.add_argument('--after-visit-id', default='', help='Resume use-context pass after next_visit_id (stable even as records improve)')
    parser.add_argument('--obligation-id', type=int, help='Explicitly repair one visually empty filmstrip, including stale person metadata')
    args = parser.parse_args()
    print(json.dumps(run(args.root, apply=args.apply, limit=args.limit, offset=args.offset,
                         use_context=args.use_context, after_visit_id=args.after_visit_id, obligation_id=args.obligation_id), indent=2))


if __name__ == "__main__":
    main()
