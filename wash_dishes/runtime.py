from __future__ import annotations

import os
import threading
import time
from collections import Counter, deque
from pathlib import Path

from . import checks
from .alignment import ViewAlignment, shift_areas
from .camera import Camera
from .config import Config, monitoring_areas
from .dataset import Dataset
from .incidents import Incidents
from .inventory import PUT_DOWN_CLIP, Inventory, area_for, in_area
from .media import Media
from .notify import Notifier
from .proofs import Proofs
from .putdown import find_putdown
from .recovery import EvidenceRecovery
from .sequences import SequenceCollection
from .store import Store, parse_stamp, stamp, utcnow
from .tracing import TraceSearch


class Runtime:
    def __init__(self, config: Config, store: Store, media: Media):
        self.config, self.store, self.media = config, store, media
        self.lock = threading.RLock()
        self.mailbox_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.pending: tuple[bytes, str, str] | None = None
        self.vision = None
        self.vision_path: str | None = None
        self.vision_generation: str | None = None
        self.vision_message = "Local vision is off. Manual events and review are available."
        self.inventory = Inventory(store, media)
        self.dataset = Dataset(config.root, store)
        self.traces = TraceSearch(config.root, store, media, self.dataset)
        self.sequences = SequenceCollection(store, media)
        self.recovery = EvidenceRecovery(store, media, self.dataset)
        self.incidents = Incidents(store, media, self.sequences)
        self.proofs = Proofs(config.root, store, media)
        self.proof_thread: threading.Thread | None = None
        self.media_thread: threading.Thread | None = None
        self.camera = Camera(config.root, store, media, self.offer_frame)
        self.notifier = Notifier(config.root, store)
        self.thread: threading.Thread | None = None
        self.notify_thread: threading.Thread | None = None
        # Live view: a few analyzed frames, so overlays are drawn on the exact image they describe.
        self.live_lock = threading.Lock()
        self.live: dict | None = None
        self.live_frames: deque[tuple[int, bytes, dict]] = deque(maxlen=24)
        self.live_id = 0
        self.count_history: deque[dict] = deque(maxlen=360)
        self.last_count_sample = 0.0
        # Zones follow small camera shifts; a large move pauses new items until the view is confirmed.
        self.alignment = ViewAlignment(config.root / "view-reference.npy")
        self.last_alignment = 0.0
        self.next_check_look = 0.0
        # What kept the due kitchen check waiting, logged if it is given up.
        self.check_slot: str | None = None
        self.check_waits: Counter[str] = Counter()

    def areas(self, settings: dict) -> list[dict]:
        """Monitored areas where they are in the current picture."""
        return shift_areas(monitoring_areas(settings), self.alignment.offset)

    def confirm_view(self, jpeg: bytes | None = None) -> bool:
        """The current picture becomes the one the saved zones describe."""
        frame = jpeg or self.camera.snapshot()
        with self.lock:
            return bool(frame) and self.alignment.set_reference(frame)

    def _align(self, jpeg: bytes, people: int) -> None:
        now = time.monotonic()
        if now - self.last_alignment < 2:
            return
        self.last_alignment = now
        if not self.alignment.has_reference:
            # Zones are drawn on the picture the camera shows when it is first set up.
            self.alignment.set_reference(jpeg)
            return
        before, reanchors = self.alignment.state, self.alignment.reanchors
        change = self.alignment.observe(jpeg, now=now, people=people)
        if self.alignment.reanchors != reanchors:
            self.store.event("camera_view_changed", "Camera view changed (usually the lights); zones kept where they were. If the camera was moved, confirm the new view in Setup")
        if change:
            with self.lock:
                self.inventory.shift(*change)
            dx, dy = self.alignment.offset
            if max(abs(change[0]), abs(change[1])) >= 0.01:
                self.store.event("camera_shifted", f"Camera shifted slightly; zones now follow it at {dx * 100:+.1f}% across, {dy * 100:+.1f}% down")
        if self.alignment.state != before and self.alignment.state == "moved":
            self.store.event("camera_moved", "Camera view moved too far to follow; new items are paused until it is put back or the new view is confirmed in Setup")
        elif before == "moved" and self.alignment.state == "aligned":
            self.store.event("camera_restored", "Camera view is back where the zones were drawn; detection resumed")

    def offer_frame(self, jpeg: bytes, generation: str, observed_at: str | None = None) -> None:
        # A one-frame mailbox drops old previews rather than accumulating a recording backlog.
        with self.mailbox_lock:
            self.pending = (jpeg, generation, observed_at or stamp())

    def local_vision(self):
        from .vision import LocalVision
        model = self.store.settings()["detector_path"]
        if self.vision is None or self.vision_path != model:
            self.vision = LocalVision(self.config.root, Path(model).expanduser() if model else None)
            self.vision_path = model
            self.vision_generation = None
            self.inventory.reset()
            self.inventory.describe = self.vision.describe_person
            self.inventory.enrolled_faces = self.vision.enrolled_faces
            self.media.clip_saved = self._clip_saved
            from .verifier import DishVerifier, store_hook
            self.inventory.verify = store_hook(DishVerifier(self.config.root), self.store)
        return self.vision

    def _clip_saved(self, obligation_id: int, label: str, evidence_id: str, path: Path) -> None:
        """The put-down clip usually shows the handler's face better than the one zoomed photo."""
        vision = self.vision
        if label != PUT_DOWN_CLIP or vision is None:
            return
        try:
            face = vision.clip_face(path)
            if face is not None and self.store.merge_sighting_face(obligation_id, face, evidence_id):
                self.store.refresh_identity_suggestions()
        except Exception:
            pass  # Appearance is a convenience; the clip stands without it.

    def reset_observations(self) -> None:
        with self.lock:
            if self.vision is not None:
                self.vision.reset_observations()
            self.vision_generation = None
            self.inventory.reset()

    def start(self) -> None:
        self.traces.start()
        if not self.config.demo:
            self.store.event("observation_gap", "Service started; previous unseen dish activity remains unverified")
            self.camera.start()
        self.thread = threading.Thread(target=self._run, name="household-worker", daemon=True)
        self.thread.start()
        # FFmpeg exports and file cleanup can take seconds; they must never stall frame analysis.
        self.media_thread = threading.Thread(target=self._media_worker, name="media-worker", daemon=True)
        self.media_thread.start()
        if not self.config.demo:
            # Network sends must never stall frame analysis; the demo's fictional people send nothing.
            self.notify_thread = threading.Thread(target=self._notify_worker, name="notify-worker", daemon=True)
            self.notify_thread.start()
            # Searching footage takes up to a minute per dish; it gets its own low-priority thread.
            self.proof_thread = threading.Thread(target=self._proof_worker, name="proof-worker", daemon=True)
            self.proof_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.traces.stop()
        self.camera.stop()
        for thread in (self.thread, self.media_thread, self.notify_thread, self.proof_thread):
            if thread:
                thread.join(timeout=30)

    def _media_worker(self) -> None:
        try:
            # Reading clips for faces is CPU-heavy; live analysis must keep priority. Linux niceness is per thread.
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except (AttributeError, OSError):
            pass
        next_cleanup = 0.0
        next_sequences = 0.0
        while not self.stop_event.wait(2):
            try:
                self.media.flush_event_clips()
                if time.monotonic() >= next_sequences:
                    try:
                        self.recovery.refresh()
                        self.sequences.refresh()
                        self.traces.enqueue_missing(self.sequences)
                    except Exception:
                        self.sequences.error = "Evidence collection could not update. Retrying shortly; original captures remain available."
                    next_sequences = time.monotonic() + 5
                if time.monotonic() >= next_cleanup:
                    self.media.cleanup()
                    next_cleanup = time.monotonic() + 60
            except Exception:
                # A transient file problem must not end retention or clip export for good.
                next_cleanup = time.monotonic() + 60

    def _proof_worker(self) -> None:
        try:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except (AttributeError, OSError):
            pass
        next_cleanup = 0.0
        while not self.stop_event.wait(20):
            try:
                if time.monotonic() >= next_cleanup:
                    self.proofs.cleanup()
                    next_cleanup = time.monotonic() + 3600
                with self.lock:
                    seeing = self.store.settings()["vision_enabled"] and self.local_vision().status()["ready"]
                self.proofs.run_once(lambda box, logged, until: find_putdown(self.media.buffer, box, logged, until),
                                     self._identify_putdown if seeing else None)
            except Exception:
                # One bad recording or picture must not stop proofs for every later dish.
                pass

    def _identify_putdown(self, obligation_id: int, jpeg: bytes, dish_box: list[float], at, *, person_box=None) -> list[float] | None:
        """Box of the person putting the dish down, or None if nobody is within reach of it.

        Their appearance replaces whatever the camera captured when it logged the dish (often a
        while later and of someone else), so naming this proof trains on the right person and the
        guess shown next to it is about them.
        """
        x, y, w, h = dish_box
        cx, cy = x + w / 2, y + h / 2
        def reach(person: dict) -> float:
            px, py, pw, ph = person["box"]
            return max(px - cx, 0, cx - px - pw) + max(py - cy, 0, cy - py - ph)
        with self.lock:
            engine = self.local_vision()
            supplied = {"box": person_box} if person_box is not None else None
            if supplied and (len(person_box) != 4 or any(not 0 <= v <= 1 for v in person_box)
                             or person_box[2] <= 0 or person_box[3] <= 0):
                supplied = None
            person = supplied if supplied and reach(supplied) <= 0.1 else min(engine.people(jpeg), key=reach, default=None)
            if person is None or reach(person) > 0.1:
                self.store.replace_putdown_samples(obligation_id, {}, "", at=at)
                self.store.set_suggestion(obligation_id, None, None)
                return None
            try:
                cues, crop = engine.describe_person(jpeg, person["box"])
                faces = engine.enrolled_faces()
            except Exception:
                self.store.replace_putdown_samples(obligation_id, {}, "", at=at)
                self.store.set_suggestion(obligation_id, None, None)
                return person["box"]
            try:
                face = engine.putdown_face(self.media.buffer, at.timestamp(), jpeg, person["box"], cues["outfit"])
                if face is not None:
                    cues["face"] = face
            except Exception:
                # Missing/expired recordings must not discard the valid anchor crop.
                pass
        settings = self.store.settings()
        location = self.store.obligation(obligation_id)["location"]
        context = next((a["id"] for a in monitoring_areas(settings) if a.get("name") == location), None)
        evidence_id = self.media.save_snapshot(obligation_id, crop, "Who put it down (from footage)", at=at,
                                               capture={"person_box": person["box"], "identity_source": "putdown"})
        self.store.replace_putdown_samples(obligation_id, cues, evidence_id, at=at, context=context)
        resident, evidence, _, seen = self.store.suggest_identity(cues, at=at, context=context, enrolled_faces=faces,
                                                                exclude_obligation=obligation_id)
        self.store.set_suggestion(obligation_id, resident, evidence, seen)
        return person["box"]

    def _notify_worker(self) -> None:
        while not self.stop_event.wait(30):
            try:
                self.notifier.poll()
            except Exception:
                # ntfy unreachable or a bad topic: the cursor did not move, so the next poll retries.
                pass

    def _run(self) -> None:
        next_scan = 0.0
        while not self.stop_event.wait(0.05):
            now = time.monotonic()
            if now >= next_scan:
                self.store.scan_deadlines()
                next_scan = now + 15
            settings = self.store.settings()
            if not settings["vision_enabled"] or self.config.demo:
                self.vision_message = "Demo uses illustrative events." if self.config.demo else "Local vision is off. Manual events and review are available."
                with self.mailbox_lock:
                    self.pending = None
                self.reset_observations()
                continue
            with self.mailbox_lock:
                frame, self.pending = self.pending, None
            with self.lock:
                try:
                    engine = self.local_vision()
                    status = engine.status()
                    self.vision_message = status["message"]
                    if frame and status["ready"]:
                        if frame[1] != self.vision_generation:
                            engine.reset_observations()
                            self.inventory.reset()
                            self.vision_generation = frame[1]
                        analyzed = time.monotonic()
                        analysis = engine.analyze(frame[0])
                        analysis["detector_latency_ms"] = round((time.monotonic() - analyzed) * 1000)
                    else:
                        analysis = None
                except Exception:
                    self.vision_message = "Local vision could not process this frame. Check the installed models and dependencies."
                    self.reset_observations()
                    analysis = None
            if analysis and frame:
                current = self.store.settings()
                observed_at = parse_stamp(frame[2])
                if not current["camera_enabled"] or not current["vision_enabled"] or frame[1] != self.camera.stream_generation or (utcnow() - observed_at).total_seconds() > 15:
                    self.reset_observations()
                    continue
                try:
                    self._align(frame[0], sum(d["kind"] == "person" for d in analysis["detections"]))
                except Exception:
                    pass
                areas = self.areas(current)
                try:
                    with self.lock:
                        # Zones cannot be trusted on a different view; watch, but open nothing new.
                        if self.alignment.settled:
                            self.inventory.process(analysis, getattr(engine, "last_image", None), frame[0], areas, frame[1], observed_at,
                                                   getattr(engine, "detect_area", lambda area: []))
                            self._kitchen_check(engine, frame[0], analysis, areas, observed_at)
                        inventory = self.inventory.listing()
                        people = [d["box"] for d in analysis["detections"] if d["kind"] == "person" and d["score"] >= 0.35]
                        analysis["fullness"] = engine.occupancy(areas, people)
                except Exception:
                    self.vision_message = "An observation could not be saved. Check data storage; manual review remains available."
                    continue
                analysis["latency_ms"] = round((time.monotonic() - analyzed) * 1000)
                self.publish_live(frame[0], frame[1], frame[2], analysis, areas, inventory)
                try:
                    by_area = self.live["by_area"] if self.live else {}
                    self.dataset.consider(frame[0], analysis, by_area, notable=self.inventory.notable, observed_at=observed_at)
                except OSError:
                    pass

    def _kitchen_check(self, engine, jpeg: bytes, analysis: dict, areas: list[dict], observed_at) -> None:
        """Run a due kitchen check once every area has been still, with nobody in it, for a minute."""
        now = time.monotonic()
        if now < self.next_check_look:
            return
        self.next_check_look = now + 15
        slot = checks.due(self.store, observed_at)
        if slot != self.check_slot:
            given_up = self.check_slot and (self.store.check(self.check_slot) or {}).get("status") == "missed"
            if given_up:
                print(f"Kitchen check {self.check_slot} missed; it waited on: {self.check_waits.most_common(5)}", flush=True)
            self.check_slot, self.check_waits = slot, Counter()
        if not slot:
            return
        quiet = self.inventory.quiet_since()
        if quiet is None or (observed_at - quiet).total_seconds() < checks.QUIET_SECONDS:
            busy = sorted(key for key, state in self.inventory.zone_states().items() if state != "settled")
            self.check_waits["busy: " + ",".join(busy) if busy else "settling"] += 1
            return
        present = sorted({a["id"] for d in analysis["detections"] for a in areas if d["kind"] == "person" and d["score"] >= 0.35 and in_area(d["box"], a["box"])})
        if present:
            self.check_waits["person at " + ",".join(present)] += 1
            return
        checks.run(self.store, self.media, self.dataset, slot, observed_at, engine.last_image, jpeg, areas, engine.detect_area,
                   self.inventory.listing(), self.inventory.verify)

    def publish_live(self, jpeg: bytes, generation: str, frame_at: str, analysis: dict, areas: list[dict], inventory: list[dict]) -> None:
        # People as detected in this frame; dishes as the inventory knows them, so boxes stay put between looks.
        detections = [{"kind": "person", "score": round(d["score"], 3), "box": [round(v, 4) for v in d["box"]], "track_id": d.get("track_id"),
                       "counted": d["score"] >= 0.35, "area_id": None, "role": None, "obligation_id": None}
                      for d in analysis.get("detections", []) if d["kind"] == "person"]
        for item in inventory:
            area = area_for(item["box"], areas)
            detections.append({"kind": item["kind"], "score": item.get("score", 1.0), "box": [round(v, 4) for v in item["box"]], "track_id": None,
                               "scene_key": item["key"],
                               "counted": True, "area_id": area["id"] if area else item["area_id"], "role": area["role"] if area else item["role"],
                               "obligation_id": item.get("obligation_id")})
        people = [d for d in detections if d["kind"] == "person" and d["counted"]]
        name = None
        if analysis.get("resident_id") and len(people) == 1:
            name = next((r["name"] for r in self.store.residents() if r["id"] == analysis["resident_id"]), None)
            people[0]["name"] = name
        counts = {role: 0 for role in ("sink", "dirty", "clean", "storage")}
        by_area = {area["id"]: 0 for area in areas}
        for item in inventory:
            if item["role"] in counts:
                counts[item["role"]] += 1
            if item["area_id"] in by_area:
                by_area[item["area_id"]] += 1
        counts["people"] = len(people)
        now = time.monotonic()
        with self.live_lock:
            self.live_id += 1
            self.live_frames.append((self.live_id, jpeg, {"analysis": analysis, "frame_at": frame_at,
                                                        "areas": areas, "detections": detections}))
            previous = self.live
            interval = now - previous["monotonic"] if previous else None
            fps = previous.get("fps") if previous else None
            if interval and interval > 0:
                fps = round(1 / interval if fps is None else 0.8 * fps + 0.2 / interval, 2)
            self.live = {"frame_id": self.live_id, "frame_at": frame_at, "analyzed_at": stamp(), "monotonic": now, "fps": fps,
                         "latency_ms": analysis.get("latency_ms"), "detector_latency_ms": analysis.get("detector_latency_ms"), "detections": detections, "counts": counts, "by_area": by_area,
                         "identity_score": analysis.get("identity_score"), "resident_name": name, "fullness": analysis.get("fullness", {}),
                         "zones": self.inventory.zone_states()}
            if now - self.last_count_sample >= 10:
                self.count_history.append({"at": frame_at, **counts})
                self.last_count_sample = now

    def live_state(self) -> dict:
        settings = self.store.settings()
        with self.live_lock:
            live = dict(self.live) if self.live else None
            history = list(self.count_history)
        fresh = bool(live and time.monotonic() - live["monotonic"] <= 10 and settings["vision_enabled"] and not self.config.demo)
        if live:
            live.pop("monotonic")
        vision = self.vision
        areas = [a | {"reference": bool(vision and vision.has_reference(a["id"]))} for a in self.areas(settings)]
        return {"fresh": fresh, "live": live if fresh else None, "history": history, "areas": areas,
                "vision_enabled": settings["vision_enabled"], "camera": self.camera_status()}

    def live_frame(self, frame_id: int) -> bytes | None:
        with self.live_lock:
            return next((jpeg for key, jpeg, _ in self.live_frames if key == frame_id), None)

    def capture_frame(self, frame_id: int, purpose: str) -> dict | None:
        with self.live_lock:
            found = next(((jpeg, meta) for key, jpeg, meta in self.live_frames if key == frame_id), None)
        if found is None:
            return None
        jpeg, meta = found
        if (utcnow() - parse_stamp(meta["frame_at"])).total_seconds() > 15:
            return None
        return self.dataset.capture(jpeg, meta["analysis"], observed_at=parse_stamp(meta["frame_at"]), purpose=purpose,
                                    capture={"live_frame_id": frame_id, "areas": meta["areas"], "detections": meta["detections"]})

    def camera_status(self) -> dict:
        status = self.camera.status()
        if self.config.demo:
            status.update(configured=False, enabled=False, connected=False, message="Demo mode. No real camera is connected.")
        message = self.vision_message
        if self.inventory.capacity_warning:
            message += " Inventory capacity reached; additional dishes need manual logging and review."
        if self.alignment.state == "moved":
            message = "Camera view moved. New items are paused until it is put back or the new view is confirmed in Setup."
        return status | {"vision_status": message, "view": self.alignment.status()}

    def residents(self) -> list[dict]:
        result = self.store.residents()
        for resident in result:
            path = self.config.root / "faces" / f"{resident['id']}.json"
            try:
                import json
                saved = json.loads(path.read_text()) if path.exists() else []
                resident["enrolled_samples"] = len(saved.get("vectors", [])) if isinstance(saved, dict) else len(saved)
            except (OSError, ValueError):
                resident["enrolled_samples"] = 0
        return result
