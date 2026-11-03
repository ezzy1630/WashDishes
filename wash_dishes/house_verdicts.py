"""Temporary verdict persistence behind Lane G's exact Store method signatures.

Lane G integration: replace the clearly marked Store adapters with the real
training/lifecycle methods. These rows independently enforce league exclusion;
they deliberately do not teach a detector or infer washing from a verdict.
"""
from __future__ import annotations

from .store import stamp


def mark(store, obligation_ids: list[int], verdict: str, by: int | None, of_id: int | None = None) -> None:
    # Caller owns the transaction, including all visit members and the attributed audit event.
    for oid in obligation_ids:
        store.conn.execute('''INSERT INTO house_verdicts VALUES(?,?,?,?,?) ON CONFLICT(obligation_id)
            DO UPDATE SET verdict=excluded.verdict,by_id=excluded.by_id,at=excluded.at,of_id=excluded.of_id''',
            (oid, verdict, by, stamp(), of_id))


def history(store, start, end) -> list[dict]:
    return [dict(r) for r in store.conn.execute('''SELECT DISTINCT v.* FROM house_visit_verdicts v
        JOIN json_each(v.obligation_ids) members JOIN obligations o ON o.id=members.value
        WHERE julianday(o.due_at)>=julianday(?) AND julianday(o.due_at)<julianday(?) ORDER BY v.at DESC''',
        (stamp(start), stamp(end)))]
