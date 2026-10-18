"""Push alerts through ntfy: a dish verified late, and a weekly summary per person.

Messages carry names, dishes, places and times, never photos. Progress is kept in a small file
beside the database, so a restart neither repeats nor floods alerts: the first run starts from now.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .store import Store, parse_stamp, utcnow

ALERT_EVENTS = ("overdue_observed", "auto_attributed", "sequence_later_captured")
# Sunday evening, household time.
SUMMARY_WEEKDAY, SUMMARY_HOUR = 6, 18


def post_ntfy(url: str, title: str, message: str, tags: list[str]) -> None:
    base, topic = url.rstrip("/").rsplit("/", 1)
    body = json.dumps({"topic": topic, "title": title, "message": message, "tags": tags}).encode()
    request = urllib.request.Request(base + "/", data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise OSError(f"ntfy answered {response.status}")


class Notifier:
    def __init__(self, root: Path, store: Store, send: Callable[[str, str, str, list[str]], None] = post_ntfy):
        self.path, self.store, self.send = root / "notify-state.json", store, send

    def _state(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        from .media import atomic_write
        atomic_write(self.path, json.dumps(state).encode())

    def poll(self, now: datetime | None = None) -> int:
        """Send what is due. A failed send is retried on the next poll.

        The household topic gets late dishes and the weekly summary. A housemate's own topic gets
        their late dishes, any time the camera names them (so they can dispute it straight away),
        and their own line of the summary.
        """
        household = self.store.settings()["notify_url"]
        people = {r["id"]: r["notify_url"] for r in self.store.residents() if r["active"] and r["notify_url"]}
        if not household and not people:
            return 0
        now = now or utcnow()
        state = self._state()
        if "last_event_id" not in state:
            state["last_event_id"] = self.store.latest_event_id()
            self._save(state)
        sent = 0
        for event in self.store.events_after(state["last_event_id"], ALERT_EVENTS):
            item = self.store.obligation(event["obligation_id"])
            if item['archived_at']:
                state['last_event_id'] = event['id']
                state['delivered'] = []
                self._save(state)
                continue
            own = people.get(item["resident_id"]) if item["confidence"] == "confirmed" else None
            def deliver(url, title, text, tags):
                nonlocal sent
                receipt = str(event['id']) + ':' + hashlib.sha256(url.encode()).hexdigest()
                if receipt in state.get('delivered', []):
                    return
                self.send(url, title, text, tags)
                state.setdefault('delivered', []).append(receipt)
                self._save(state)
                sent += 1
            if event['event'] == 'sequence_later_captured':
                if household and item['lifecycle'] == 'open' and item['id'] not in self.store.confirmed_late_ids():
                    deliver(household, 'Dish evidence ready to check',
                            f"Record #{item['id']}: a later photo is available. Check the sequence before assigning responsibility.\n" + self.incident_link(item), ['plate_with_cutlery'])
            elif event["event"] == "overdue_observed" and item['id'] in self.store.confirmed_late_ids():

                title, message = self.late(item, personal=False)
                for url, text in ((household, message), (own, self.late(item, personal=True)[1])):
                    if url:
                        deliver(url, title, text, ["plate_with_cutlery", "warning"])
            elif own and event["event"] == "auto_attributed":
                deliver(own, "The camera named you", self.named(item), ["plate_with_cutlery"])
            state["last_event_id"] = event["id"]
            state["delivered"] = []
            self._save(state)
        local = now.astimezone(timezone())
        week = local.strftime("%G-W%V")
        if (local.weekday(), local.hour) >= (SUMMARY_WEEKDAY, SUMMARY_HOUR) and state.get("summary_week") != week:
            lines = self.summary_lines(now)
            if household:
                self.send(household, "Dish Watch · this week", "\n".join(lines.values()) or "No dishes were put down to anyone this week.", ["bar_chart"])
                sent += 1
            for resident, url in people.items():
                self.send(url, "Your week", lines.get(resident, "No dishes were put down to you this week."), ["bar_chart"])
                sent += 1
            state["summary_week"] = week
            self._save(state)
        return sent

    def test(self, url: str | None = None) -> None:
        url = url or self.store.settings()["notify_url"]
        if not url:
            raise ValueError("Save an ntfy topic address first.")
        self.send(url, "Dish Watch test", "Alerts from the kitchen will arrive here.", ["white_check_mark"])

    def _when_where(self, item: dict) -> str:
        local = parse_stamp(item["created_at"]).astimezone(timezone())
        return (f" on the {item['location'].lower()}" if item["location"] else "") + f" at {local:%-I:%M %p}"

    def incident_link(self, item: dict) -> str:
        base = self.store.settings().get('dashboard_url', '').rstrip('/')
        return f"{base}/#/incident/{item['id']}" if base else f"Open incident #{item['id']} on the household dashboard."

    def late(self, item: dict, *, personal: bool) -> tuple[str, str]:
        who = item["resident_name"] or "Someone"
        what = self.store.dish_description(item["dishes"])
        grace = self.store.settings()["deadline_minutes"]
        verb = "were" if sum(d["quantity"] for d in item["dishes"]) > 1 else "was"
        lines = [f"{'Your' if personal else who + chr(39) + 's'} {what} {verb} still out {grace} minutes after being set down{self._when_where(item)}."]
        if item.get("attribution") == "camera":
            lines.append("Named by the camera, not yet by a person. " + ("If it wasn't yours, dispute it from your link." if personal else "Dispute it on the dashboard if that's wrong."))
        lines.append(self.incident_link(item))
        return f"Late dishes: {who}", "\n".join(lines)

    def named(self, item: dict) -> str:
        what = self.store.dish_description(item["dishes"])
        return (f"The camera thinks the {what} set down{self._when_where(item)} is yours. "
                f"If it isn't, dispute it from your link; the photos are in record #{item['id']}.")

    def summary_lines(self, now: datetime) -> dict[int, str]:
        since = now - timedelta(days=7)
        late_ids = self.store.confirmed_late_ids()
        rows = []
        for stat in self.store.statistics(now):
            mine = [o for o in self.store.obligations(history=True, resident_id=stat["resident_id"])
                    if parse_stamp(o["created_at"]) >= since and o["confidence"] == "confirmed" and o["lifecycle"] in ("open", "resolved", "cleared")]
            late = sum(o['id'] in late_ids for o in mine)
            if mine:
                rows.append((late, len(mine), stat["name"], stat["resident_id"]))
        rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
        return {resident: f"{name}: late {late} of {count}" if late else f"{name}: {count} left out, none late" for late, count, name, resident in rows}


def timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("WASHDISHES_TIMEZONE", "America/Los_Angeles"))
