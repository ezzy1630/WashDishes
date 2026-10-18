"""Read-only incident timeline from retained captures and human decisions.

Legacy capture labels are classified here, once. A passed deadline, a named
handler, or a vanished dish never becomes proof of unwashed responsibility.
"""
from datetime import datetime


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _role(e: dict) -> str | None:
    if e['kind'] == 'snapshot':
        stage = e.get('capture', {}).get('stage')
        if stage in ('using', 'leaving', 'later'):
            return {'using':'handling', 'leaving':'left', 'later':'deadline'}[stage]
    label = e["label"]
    for role, prefixes in (
        ("detail", ("Who handled",)),
        ("handling", ("Person handling", "Person near the dish")),
        ("left", ("Dish left unattended", "Already present", "Possible unattended dish", "Manually recorded deposit")),
        ("deadline", ("Matching dish still visible after the deadline",)),
        ("outcome", ("Possible clean destination", "Cleanup confirmed by a person")),
    ):
        if e["kind"] != "clip" and label.startswith(prefixes):
            return role
    if e["kind"] == "clip":
        if label.startswith("Putting the dish down"):
            return "handling"
        if label.startswith(("Around the first unattended observation", "Camera near manual deposit")):
            return "left"
        if label.startswith("Around the after-deadline observation"):
            return "deadline"
        if label.startswith("Possible cleanup transition"):
            return "outcome"
    return None


def evidence_timeline(item: dict, names: dict[int, str], now: datetime) -> dict:
    evidence = sorted(item["evidence"], key=lambda e: _time(e["created_at"]))
    due = _time(item["due_at"]) if item["due_at"] else None
    resolved = _time(item["resolved_at"]) if item["resolved_at"] else None
    events = sorted(item["events"], key=lambda e: e["id"])
    last_reopen = max((e["id"] for e in events if e["event"] == "reopen"), default=0)

    def stage(key: str, title: str, summary: str, *, latest: bool = False) -> dict:
        matches = [e for e in evidence if _role(e) == key]
        # Only a capture after this timer's deadline belongs in the deadline stage.
        if key == "deadline":
            matches = [e for e in matches if due and _time(e["created_at"]) >= due
                       and (not resolved or _time(e["created_at"]) <= resolved)]
        if key == "outcome":
            reopened = [e for e in events if e["event"] == "reopen"]
            if reopened:
                # Capture times have second precision. At the same second we cannot
                # prove a capture followed the reopen, so do not reuse it.
                matches = [e for e in matches if _time(e["created_at"]) > _time(reopened[-1]["created_at"])]
        photos = [e for e in matches if e["kind"] != "clip"]
        available = [e for e in photos if not e["expired"] and e["url"]]
        choices = available or photos
        photo = (choices[-1] if latest else choices[0]) if choices else None
        return {"key": key, "title": title, "state": "expired" if photo and photo["expired"] else "captured" if photo else "missing",
                "at": photo["created_at"] if photo else None, "summary": summary, "photo": photo,
                "detail": None, "clips": [e for e in matches if e["kind"] == "clip"]}

    handling = stage("handling", "Handling", "A person near or handling a dish. Responsibility needs a separate decision.")
    # A crop is supplementary; it never replaces the full scene or a missing scene.
    details = [e for e in evidence if _role(e) == "detail"
               and (not handling["photo"] or e["created_at"] == handling["photo"]["created_at"])]
    handling["detail"] = next((e for e in details if not e["expired"] and e["url"]), details[0] if details else None)
    linked = {e["id"] for e in (handling["photo"], handling["detail"]) if e}
    identifications = [e for e in events if e["event"] == "handler_identified"
                       and e["data"].get("evidence_id") in linked]
    revoked = max((e["id"] for e in events if e["event"] in ("dispute", "dismiss")), default=0)
    identity = identifications[-1] if identifications and identifications[-1]["id"] > revoked else None
    name = names.get(identity["data"].get("resident_id")) if identity else None
    if name:
        handling["summary"] = f"Photo identified as {name} by a person. Dish ownership is a separate decision."
    elif item.get("suggested_name"):
        handling["summary"] = f"Camera suggests {item['suggested_name']}; identity and responsibility are unconfirmed."

    left = stage("left", "First seen" if item["source"] == "baseline" else "Left unattended",
                 "Already present when monitoring started; deposit time and handler are unknown."
                 if item["source"] == "baseline" else "Candidate unattended dish at this capture; dirty status needs review.")
    if item["source"] == "manual":
        left.update(title="Recorded deposit", summary="Deposit recorded by a person. The photo shows the camera view at the time of that report.")
    deadline = stage("deadline", "Past the deadline", "Matching dish visible at this capture. This does not establish that it remained dirty between captures.", latest=True)
    if deadline["photo"]:
        deadline["state"] = "review" if item["review_pending"] else deadline["state"]
    elif not due:
        deadline.update(state="no_timer", summary="No deadline was established for this observation.")
    elif due > now:
        deadline.update(state="not_due", at=item["due_at"], summary="The grace period has not ended.")
    else:
        deadline.update(at=item["due_at"], summary="Deadline passed, but no matching after-deadline photo is attached. Elapsed time is not proof.")

    outcome = stage("outcome", "Outcome", "No cleanup confirmed. Disappearance alone does not establish washing.", latest=True)
    if item.get("closed_by_camera"):
        outcome.update(state="taken_late", at=outcome["at"] or item["resolved_at"], decision_at=item["resolved_at"], summary=item["reason"])
    elif item["lifecycle"] == "resolved":
        outcome.update(state="human_confirmed", at=outcome["at"] or item["resolved_at"], decision_at=item["resolved_at"],
                       summary="Marked washed by a person; this is a human decision, not camera verification of washing quality.")
    elif item["lifecycle"] == "cleared":
        outcome.update(state="cleared", summary=item['reason'] or "Removed from the dirty area; washing was not observed.")
    elif item["lifecycle"] in ("dismissed", "disputed"):
        outcome.update(state=item["lifecycle"], summary="This incident was dismissed after review." if item["lifecycle"] == "dismissed" else "This incident is disputed and needs review.")
    elif outcome["photo"] or any(e["event"] == "cleanup_candidate" and e["id"] > last_reopen for e in events):
        outcome.update(state="review", summary="Possible cleanup transition. A person must confirm whether washing occurred.")

    limits = ["Photos and clips show separate moments. Missing footage and time between captures are not continuous proof."]
    if any(e["event"] in ("observation_gap", "dish_continuity_uncertain") for e in events):
        limits.append("The tracker lost sight of this dish. Check that later observations show the same dish.")
    if item["confidence"] != "confirmed" or not item["resident_id"]:
        limits.append("No responsible owner has been confirmed. A handler's identity alone does not establish ownership.")
    elif item.get("attribution") == "camera":
        limits.append("The owner was named by the camera from appearance, not by a person. It can be disputed.")
    return {"stages": [handling, left, deadline, outcome], "limitations": limits}
