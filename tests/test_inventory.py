from datetime import timedelta

import numpy as np
import pytest

from wash_dishes.inventory import Inventory, area_for
from wash_dishes.store import parse_stamp, utcnow

cv2 = pytest.importorskip("cv2")

JPEG = b"\xff\xd8test\xff\xd9"
AREAS = [
    {"id": "counter", "name": "Counter", "role": "dirty", "box": [0.0, 0.5, 0.3, 0.3]},
    {"id": "sink", "name": "Sink", "role": "sink", "box": [0.3, 0.5, 0.2, 0.3]},
    {"id": "rack", "name": "Rack", "role": "clean", "box": [0.5, 0.5, 0.15, 0.3]},
    {"id": "cabinet", "name": "Cabinet", "role": "storage", "box": [0.8, 0.5, 0.15, 0.3]},
]
HEIGHT, WIDTH = 360, 640


class Kitchen:
    """A synthetic camera: a textured counter, drawn dishes, and a detector that sees them."""

    def __init__(self, store, media, *, settle=30):
        self.store = store
        self.inventory = Inventory(store, media, settle_seconds=settle)
        noise = np.random.default_rng(1).integers(60, 200, (HEIGHT, WIDTH), dtype=np.uint8)
        self.background = cv2.GaussianBlur(noise, (0, 0), 2)
        self.dishes: dict[str, tuple[str, list[float]]] = {}
        self.light = 1.0
        # A brightness field the camera's encoder applies on every other frame, as at each keyframe in daylight.
        self.pulse = None
        self.steps = 0
        # Drawn dishes have a dark rim; a real white plate has a soft edge and a smooth face.
        self.rims = True
        self.at = utcnow() - timedelta(hours=3)
        self.relabel: dict[str, str] = {}
        self.hidden: set[str] = set()
        self.phantoms: list[dict] = []
        self.detections = 0

    def image(self):
        frame = np.clip(self.background.astype(np.float32) * self.light, 0, 255).astype(np.uint8)
        for kind, (x, y, w, h) in self.dishes.values():
            center = (int((x + w / 2) * WIDTH), int((y + h / 2) * HEIGHT))
            axes = (int(w * WIDTH / 2), int(h * HEIGHT / 2))
            if self.rims:
                cv2.ellipse(frame, center, axes, 0, 0, 360, int(235 * self.light), -1)
                cv2.ellipse(frame, center, axes, 0, 0, 360, int(30 * self.light), 3)
            else:
                shape = np.zeros(frame.shape, np.float32)
                cv2.ellipse(shape, center, axes, 0, 0, 360, 1.0, -1)
                shape = cv2.GaussianBlur(shape, (0, 0), 5)
                frame = (frame * (1 - shape) + 185 * self.light * shape).astype(np.uint8)
        if self.pulse is not None and self.steps % 2:
            frame = np.clip(frame * self.pulse, 0, 255).astype(np.uint8)
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    def detect(self, area):
        self.detections += 1
        x, y, w, h = area["box"]
        seen = [{"kind": self.relabel.get(name, kind), "score": 0.8, "box": list(b)} for name, (kind, b) in self.dishes.items() if name not in self.hidden]
        return [d for d in seen + self.phantoms if x <= d["box"][0] + d["box"][2] / 2 <= x + w and y <= d["box"][1] + d["box"][3] / 2 <= y + h]

    def step(self, people=(), seconds=1.0):
        self.at += timedelta(seconds=seconds)
        self.steps += 1
        analysis = {"detections": [{"kind": "person", "score": 0.9, "track_id": track, "box": box} for track, box in people]}
        self.inventory.process(analysis, self.image(), JPEG, AREAS, "camera-1", self.at, self.detect)

    def wait(self, seconds):
        for _ in range(int(seconds)):
            self.step()

    def visit(self, area, *, track=7, change=None, seconds=6):
        """Someone stands at an area; whatever they do happens halfway through."""
        box = next(a["box"] for a in AREAS if a["id"] == area)
        person = [box[0], 0.2, box[2], 0.65]
        for second in range(seconds):
            if second == seconds // 2 and change:
                change()
            self.step([(track, person)])

    def put(self, name, kind, area, dx=0.03, dy=0.08, size=(0.06, 0.08)):
        box = next(a["box"] for a in AREAS if a["id"] == area)
        return lambda: self.dishes.__setitem__(name, (kind, [box[0] + dx, box[1] + dy, *size]))

    def take(self, name):
        return lambda: self.dishes.pop(name)

    def items(self):
        return sorted(self.store.obligations(history=True), key=lambda o: o["id"])


@pytest.fixture
def kitchen(store, media):
    kitchen = Kitchen(store, media)
    kitchen.wait(5)
    return kitchen


def test_handling_photo_survives_later_frame_without_visible_dish(kitchen):
    person = {'kind': 'person', 'score': .9, 'track_id': 7, 'box': [0, .2, .3, .65]}
    kitchen.step([(7, person['box'])])
    kitchen.put('cup', 'cup', 'counter')()
    kitchen.at += timedelta(seconds=1)
    handling_at = kitchen.at
    dish = kitchen.detect(AREAS[0])[0]
    kitchen.inventory.process({'detections': [person, dish]}, kitchen.image(), JPEG, AREAS,
                              'camera-1', kitchen.at, kitchen.detect)
    # The last frame of the person has an occluded dish. It must not erase the
    # earlier retained moment where both the person and target dish were visible.
    kitchen.step([(7, person['box'])])
    kitchen.wait(40)
    [item] = kitchen.items()
    using = [e for e in item['evidence'] if e['capture'].get('stage') == 'using']
    assert len(using) == 1
    assert parse_stamp(using[0]['created_at']) == handling_at.replace(microsecond=0)
    assert using[0]['capture']['box'] == dish['box']


def test_handling_photos_are_saved_before_departure_and_survive_restart(kitchen, media):
    from wash_dishes.media import Media
    from wash_dishes.store import Store

    kitchen.visit('counter', change=kitchen.put('cup', 'cup', 'counter'), seconds=12)
    assert kitchen.items() == []  # Still in use, not yet an unattended-dish record.
    saved = list(media.media.glob('activity-*.jpg'))
    assert saved, 'Handling photos must reach disk while the person is still there'
    # Reopen both persistence layers, without retaining the old tracker or its bytes.
    reopened = Store(kitchen.store.root)
    try:
        kitchen.inventory = Inventory(reopened, Media(reopened.root, reopened))
        kitchen.wait(40)
        [record] = kitchen.items()
        stages = {e['capture'].get('stage'): e for e in record['evidence']}
        assert {'before', 'using', 'leaving'} <= stages.keys()
        assert stages['using']['created_at'] < stages['leaving']['created_at']
        assert record['resident_id'] is None
        assert not record['timing_known']  # A restart must not invent a witnessed deposit.
        saved_evidence = reopened.evidence(stages['using']['id'])
        assert media.safe_path(saved_evidence['filename']).read_bytes() == JPEG
    finally:
        reopened.close()


def test_activity_capture_skips_clean_rack_and_ambiguous_people(kitchen, media):
    kitchen.visit('rack', change=kitchen.put('cup', 'cup', 'rack'), seconds=12)
    assert not list(media.media.glob('activity-*.jpg'))
    kitchen.put('plate', 'plate', 'counter')()
    people = [(7, [0, .2, .3, .65]), (8, [0, .2, .3, .65])]
    for _ in range(12):
        kitchen.step(people)
    assert not list(media.media.glob('activity-*.jpg'))


def test_activity_capture_checks_each_busy_area_and_new_dish_without_waiting(kitchen, media):
    kitchen.put('cup', 'cup', 'counter')()
    kitchen.put('bowl', 'bowl', 'sink')()
    person = [(7, [0, .2, .5, .65])]
    for _ in range(12):
        kitchen.step(person)
    rows = kitchen.store.conn.execute('SELECT area_id,count(*) FROM activity_photos GROUP BY area_id').fetchall()
    assert {r[0] for r in rows} == {'counter', 'sink'}
    assert all(r[1] == 1 for r in rows)  # Stable handling does not write every frame.
    kitchen.put('plate', 'plate', 'counter', dx=.15)()
    for _ in range(10):
        kitchen.step(person)
    assert kitchen.store.conn.execute("SELECT count(*) FROM activity_photos WHERE area_id='counter'").fetchone()[0] == 2


def test_activity_retention_is_bounded_without_expiring_attached_evidence(kitchen, media):
    kitchen.visit('counter', change=kitchen.put('cup', 'cup', 'counter'), seconds=12)
    kitchen.wait(40)
    [record] = kitchen.items()
    using = next(e for e in record['evidence'] if e['capture'].get('stage') == 'using')
    photo = kitchen.store.evidence(using['id'])
    media.activity.MAX_ENTRIES = 0
    media.activity.cleanup(now=kitchen.at)
    assert not list(media.media.glob('activity-*.jpg'))
    assert media.safe_path(photo['filename']).read_bytes() == JPEG
    assert kitchen.store.conn.execute('SELECT count(*) FROM activity_photos').fetchone()[0] == 0


def test_nearby_person_and_restart_do_not_postpone_two_hour_photo(kitchen, media):
    kitchen.store.set_settings({'deadline_minutes': 120})
    kitchen.visit('counter', change=kitchen.put('cup', 'cup', 'counter'), seconds=12)
    kitchen.wait(40)
    [original] = kitchen.items()
    kitchen.visit('counter', seconds=30)
    kitchen.inventory = Inventory(kitchen.store, media)
    kitchen.wait(40)
    [continued] = kitchen.items()
    assert continued['id'] == original['id']
    assert continued['followup_at'] == original['followup_at']
    leaving_at = max(parse_stamp(e['created_at']) for e in continued['evidence'] if e['capture'].get('stage') == 'leaving')
    assert parse_stamp(continued['followup_at']) - leaving_at == timedelta(hours=2)
    assert not any(e['capture'].get('stage') == 'later' for e in continued['evidence'])
    kitchen.step(seconds=7201)
    [completed] = kitchen.items()
    later = [e for e in completed['evidence'] if e['capture'].get('stage') == 'later']
    assert len(later) == 1
    assert parse_stamp(later[0]['created_at']) >= parse_stamp(continued['followup_at'])


def test_dish_left_by_a_person_opens_one_item_with_who_handled_it(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    left_at = kitchen.at
    kitchen.wait(20)
    assert kitchen.items() == []
    kitchen.wait(20)
    [item] = kitchen.items()
    assert item["source"] == "vision" and item["timing_known"] and item["location"] == "Counter"
    leaving = next(e for e in item["evidence"] if e["capture"].get("stage") == "leaving")
    assert item["created_at"] == leaving["created_at"]
    assert parse_stamp(item["created_at"]) >= left_at.replace(microsecond=0)
    assert any(e['capture'].get('stage') == 'using' for e in item['evidence'])
    assert item["resident_id"] is None  # Capturing handling does not assign responsibility.


def test_a_dish_left_comes_with_a_before_photo_and_a_clip_of_the_whole_visit(kitchen, media):
    arrived = kitchen.at
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"), seconds=20)
    left_at = kitchen.at
    kitchen.wait(40)
    [item] = kitchen.items()
    [before] = [e for e in item["evidence"] if e["label"] == "Before it was put down"]
    assert abs((parse_stamp(before["created_at"]) - arrived).total_seconds()) <= 2
    [(_, obligation_id, _, clip_end, seconds)] = media.pending_clips
    assert obligation_id == item["id"] and clip_end >= left_at and clip_end - timedelta(seconds=seconds) <= arrived


def test_a_smooth_plate_on_a_speckled_counter_is_noticed(kitchen):
    kitchen.rims = False
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    assert [d["kind"] for i in kitchen.items() for d in i["dishes"]] == ["plate"]


def test_a_plate_standing_in_a_rack_belongs_to_the_rack():
    # Its box reaches far above the rack; its center is above the rack's top edge.
    assert area_for([0.55, 0.3, 0.05, 0.3], AREAS)["id"] == "rack"


def test_a_specific_landing_area_wins_over_an_overlapping_counter_in_either_order():
    counter = {"id": "counter", "name": "Counter", "role": "dirty", "box": [0.0, 0.5, 0.8, 0.4]}
    stove = {"id": "stove", "name": "Stove", "role": "dirty", "box": [0.2, 0.5, 0.3, 0.2]}
    dish = [0.25, 0.55, 0.08, 0.08]
    assert area_for(dish, [counter, stove])["id"] == "stove"
    assert area_for(dish, [stove, counter])["id"] == "stove"


def test_a_still_dish_missed_on_the_first_look_is_rediscovered_without_inventing_a_deposit(store, media):
    kitchen = Kitchen(store, media)
    kitchen.put("pan", "pan", "counter")()
    kitchen.hidden.add("pan")
    kitchen.wait(5)
    assert kitchen.inventory.listing() == []
    kitchen.hidden.clear()
    kitchen.wait(100)
    assert len(kitchen.items()) == 1
    [item] = kitchen.inventory.listing()
    assert item['background'] and not item['timing_known'] and item['obligation_id'] == kitchen.items()[0]['id']
    assert len(kitchen.inventory.listing()) == 1
    kitchen.wait(130)
    assert len(kitchen.items()) == 1 and len(kitchen.inventory.listing()) == 1
    assert not any(e["event"] == "dish_picked_up" for e in kitchen.store.activity())


def test_unchanged_pixels_cannot_supply_deadline_evidence_when_the_dish_is_no_longer_detected(kitchen):
    alex = kitchen.store.add_resident("Alex")["id"]
    kitchen.visit("counter", change=kitchen.put("pan", "pan", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    kitchen.store.action(item["id"], "confirm", resident_id=alex, now=kitchen.at)
    kitchen.hidden.add("pan")
    kitchen.step(seconds=91 * 60)
    kitchen.wait(5)
    item = kitchen.store.obligation(item["id"], now=kitchen.at)
    assert not item["overdue_verified"]
    assert not any(e["label"].startswith("Matching dish still visible after the deadline") for e in item["evidence"])
    assert item["lifecycle"] == "open"


def test_an_outlined_counter_does_not_claim_the_cabinet_doors_below_it():
    # The counter's front edge runs diagonally from (0, 0.75) to (0.3, 0.6); below it are cabinet doors.
    counter = {**AREAS[0], "outline": [[0.0, 0.5], [0.3, 0.5], [0.3, 0.6], [0.0, 0.75]]}
    assert area_for([0.04, 0.6, 0.04, 0.05], [counter])["id"] == "counter"
    assert area_for([0.24, 0.66, 0.02, 0.05], [counter]) is None


@pytest.mark.parametrize("already_there", [False, True])
def test_drying_mat_inside_counter_does_not_open_dirty_record(store, media, monkeypatch, already_there):
    # The cloth beside the rack overlaps the broad counter zone. Its clean
    # polygon must win both at startup and after someone places cookware there.
    mat = {"id": "mat", "name": "Drying cloth", "role": "clean",
           "box": [0.16, 0.62, 0.14, 0.18],
           "outline": [[0.16, 0.64], [0.3, 0.62], [0.3, 0.8], [0.16, 0.8]]}
    monkeypatch.setitem(globals(), "AREAS", [*AREAS, mat])
    kitchen = Kitchen(store, media)
    put = kitchen.put("pan", "pan", "counter", dx=0.19, dy=0.17)
    if already_there:
        put()
    kitchen.wait(5)
    if not already_there:
        kitchen.visit("counter", change=put)
    kitchen.wait(45)
    assert kitchen.items() == []
    assert any(item["area_id"] == "mat" and item["role"] == "clean"
               for item in kitchen.inventory.items.values())


def test_untouched_dish_keeps_one_item_however_the_detector_names_it(kitchen):
    kitchen.visit("counter", change=kitchen.put("pot", "pot", "counter"))
    kitchen.wait(40)
    # Next to it, a second dish arrives while the detector now calls the first one a bowl.
    kitchen.relabel["pot"] = "bowl"
    kitchen.visit("counter", change=kitchen.put("cup", "cup", "counter", dx=0.18, size=(0.04, 0.06)))
    kitchen.wait(40)
    assert sorted(d["kind"] for i in kitchen.items() for d in i["dishes"]) == ["cup", "pot"]


def test_detector_mistakes_away_from_what_changed_are_ignored(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    # While a cup is put down at the far end, the detector misses the plate and imagines a bowl.
    kitchen.hidden.add("plate")
    kitchen.phantoms.append({"kind": "bowl", "score": 0.7, "box": [0.12, 0.7, 0.05, 0.06]})
    kitchen.visit("counter", change=kitchen.put("cup", "cup", "counter", dx=0.22, dy=0.02, size=(0.04, 0.06)))
    kitchen.wait(40)
    assert sorted(d["kind"] for i in kitchen.items() for d in i["dishes"]) == ["cup", "plate"]
    assert not any(e["event"] == "dish_picked_up" for i in kitchen.items() for e in kitchen.store.obligation(i["id"])["events"])


def test_walking_past_uses_bounded_handling_checks_without_new_records(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    # Let the scheduled presence rescan finish before observing short visits.
    kitchen.wait(70)
    passes = kitchen.detections
    for _ in range(3):
        kitchen.visit("counter")
        kitchen.wait(5)
    assert kitchen.detections - passes <= 6
    assert len(kitchen.items()) == 1
    assert len(kitchen.items()) == 1


def test_counter_to_sink_to_rack_proposes_cleanup_of_the_same_item(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    kitchen.visit("counter", change=kitchen.take("plate"))
    kitchen.visit("sink", change=kitchen.put("plate", "plate", "sink"))
    kitchen.wait(5)
    kitchen.visit("sink", change=kitchen.take("plate"))
    kitchen.visit("rack", change=kitchen.put("plate", "plate", "rack"))
    kitchen.wait(40)
    assert [i["id"] for i in kitchen.items()] == [item["id"]]
    saved = kitchen.store.obligation(item["id"])
    assert saved["lifecycle"] == "cleared" and not saved["review_pending"]
    assert saved['cleaner_id'] is None and not saved['overdue_verified']
    assert any(e["event"] == "cleanup_candidate" for e in saved["events"])


def test_transfer_keeps_the_item_when_destination_is_processed_before_origin(kitchen):
    # Clean/storage areas can precede the sink in the configured area list.
    kitchen.visit("sink", change=kitchen.put("plate", "plate", "sink"))
    kitchen.wait(40)
    [item] = kitchen.items()
    sink = next(a for a in AREAS if a["id"] == "sink")
    rack = next(a for a in AREAS if a["id"] == "rack")
    areas = [rack, sink, *[a for a in AREAS if a["id"] not in ("sink", "rack")]]
    person = {"kind": "person", "score": 0.9, "track_id": 7, "box": [0.3, 0.2, 0.35, 0.65]}
    for second in range(6):
        if second == 3:
            kitchen.take("plate")()
            kitchen.put("plate", "plate", "rack")()
        kitchen.at += timedelta(seconds=1)
        kitchen.inventory.process({"detections": [person]}, kitchen.image(), JPEG, areas, "camera-1", kitchen.at, kitchen.detect)
    for _ in range(40):
        kitchen.at += timedelta(seconds=1)
        kitchen.inventory.process({"detections": []}, kitchen.image(), JPEG, areas, "camera-1", kitchen.at, kitchen.detect)
    [tracked] = kitchen.inventory.listing()
    assert tracked["obligation_id"] == item["id"]
    assert tracked["role"] == "clean"
    assert any(e["event"] == "cleanup_candidate" for e in kitchen.store.obligation(item["id"])["events"])


def test_matching_kind_does_not_transfer_between_different_people(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"), track=7)
    kitchen.wait(40)
    [first] = kitchen.items()
    kitchen.visit("counter", change=kitchen.take("plate"), track=7)
    kitchen.wait(5)
    kitchen.visit("sink", change=kitchen.put("other_plate", "plate", "sink"), track=9)
    kitchen.wait(40)
    assert len(kitchen.items()) == 2
    assert kitchen.inventory.listing()[0]["obligation_id"] != first["id"]


def test_missing_track_ids_are_not_evidence_that_two_handlers_are_the_same(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"), track=None)
    kitchen.wait(40)
    [first] = kitchen.items()
    kitchen.visit("counter", change=kitchen.take("plate"), track=None)
    kitchen.wait(5)
    kitchen.visit("sink", change=kitchen.put("cup", "cup", "sink"), track=None)
    kitchen.wait(40)
    assert len(kitchen.items()) == 2
    assert kitchen.inventory.listing()[0]["obligation_id"] != first["id"]


def test_dish_carried_out_of_view_stays_open(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    kitchen.visit("counter", change=kitchen.take("plate"))
    kitchen.wait(60 * 40)
    [item] = kitchen.items()
    assert item["lifecycle"] == "open"
    assert any(e["event"] == "dish_picked_up" for e in kitchen.store.obligation(item["id"])["events"])


def test_dish_taken_from_storage_and_left_out_is_a_new_use(kitchen):
    kitchen.dishes["bowl"] = ("bowl", [0.83, 0.58, 0.06, 0.08])
    kitchen.inventory.reset()
    kitchen.wait(5)
    assert kitchen.items() == []
    kitchen.visit("cabinet", change=kitchen.take("bowl"))
    kitchen.visit("counter", change=kitchen.put("bowl", "bowl", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    assert item["timing_known"] and item["source"] == "vision"
    assert any(e["label"] == "Taken from Cabinet" for e in item["evidence"])


def test_existing_dishes_have_unknown_departure_and_restart_keeps_one_record(store, media):
    kitchen = Kitchen(store, media)
    kitchen.dishes["plate"] = ("plate", [0.05, 0.6, 0.06, 0.08])
    kitchen.wait(40)
    assert len(kitchen.items()) == 1
    assert not kitchen.items()[0]['timing_known'] and kitchen.items()[0]['due_at'] is None
    [item] = kitchen.inventory.listing()
    assert item['background'] and not item['timing_known']
    restarted = Kitchen(store, media)
    restarted.dishes = kitchen.dishes
    restarted.at = kitchen.at
    restarted.wait(60)
    assert len(restarted.items()) == 1
    assert restarted.items()[0]['id'] == kitchen.items()[0]['id']
    assert [i['key'] for i in restarted.inventory.listing()] == [item['key']]


def test_dish_gone_after_a_restart_needs_review_and_is_not_cleaned(kitchen, store, media):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    restarted = Kitchen(store, media)
    restarted.at = kitchen.at
    restarted.wait(5 * 60)
    [item] = restarted.items()
    assert not item["review_pending"]  # not yet: it may only have been missed
    restarted.wait(6 * 60)
    [item] = restarted.items()
    assert item["lifecycle"] == "open" and item["review_pending"]


def test_background_detector_miss_keeps_one_record_without_training_labels(store, media):
    kitchen = Kitchen(store, media)
    kitchen.dishes["plate"] = ("plate", [0.05, 0.6, 0.06, 0.08])
    kitchen.wait(40)
    restarted = Kitchen(store, media)
    restarted.dishes = kitchen.dishes
    restarted.hidden = {'plate'}  # Pixels stay put; only the detector misses the dish.
    restarted.at = kitchen.at
    restarted.wait(11 * 60)
    assert len(restarted.items()) == 1
    assert restarted.items()[0]['id'] == kitchen.items()[0]['id']
    restarted.hidden.clear()
    restarted.wait(90)
    assert len(restarted.items()) == 1
    assert restarted.items()[0]['id'] == kitchen.items()[0]['id']
    assert [i['key'] for i in restarted.inventory.listing()] == [i['key'] for i in kitchen.inventory.listing()]
    assert not store.conn.execute("SELECT id FROM events WHERE event='dismiss'").fetchall()


def test_new_dish_in_a_long_missing_background_spot_is_not_absorbed(store, media):
    kitchen = Kitchen(store, media)
    kitchen.put('old', 'bowl', 'counter')()
    kitchen.wait(40)
    restarted = Kitchen(store, media)
    restarted.at = kitchen.at
    restarted.wait(15 * 60)
    restarted.visit('counter', change=restarted.put('new', 'plate', 'counter'))
    restarted.wait(40)
    [item] = [r for r in restarted.items() if r['lifecycle'] == 'open']
    assert item['source'] == 'vision' and item['timing_known']
    assert item['dishes'][0]['kind'] == 'plate'


def test_dish_the_detector_misses_right_after_a_restart_is_found_again_quietly(store, media):
    kitchen = Kitchen(store, media)
    kitchen.dishes["glass"] = ("cup", [0.05, 0.6, 0.06, 0.08])
    kitchen.wait(40)
    assert len(kitchen.items()) == 1
    assert not kitchen.items()[0]['timing_known'] and kitchen.items()[0]['due_at'] is None
    restarted = Kitchen(store, media)
    restarted.dishes = kitchen.dishes
    restarted.at = kitchen.at
    restarted.hidden = {"glass"}  # missed on the first look after the restart
    restarted.wait(20)
    restarted.hidden = set()
    restarted.wait(15 * 60)
    assert len(restarted.items()) == 1
    assert restarted.items()[0]['id'] == kitchen.items()[0]['id']
    assert [i["key"] for i in restarted.inventory.listing()] == [i["key"] for i in kitchen.inventory.listing()]


def test_lights_turning_on_are_not_dishes(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    kitchen.light = 0.45
    kitchen.wait(40)
    kitchen.light = 1.0
    kitchen.wait(40)
    assert len(kitchen.items()) == 1


@pytest.mark.parametrize('restart', [False, True])
def test_stationary_dish_keeps_clock_through_long_detector_dropout(kitchen, store, media, restart):
    kitchen.visit('counter', change=kitchen.put('plate', 'plate', 'counter'))
    kitchen.wait(40)
    [record] = kitchen.items()
    [tracked] = kitchen.inventory.listing()
    if restart:
        resumed = Kitchen(store, media)
        resumed.at, resumed.dishes = kitchen.at, kitchen.dishes
        kitchen = resumed
    kitchen.hidden.add('plate')
    kitchen.light = .65
    # Many settled rechecks, well beyond both disappearance timeouts.
    for _ in range(70):
        kitchen.step(seconds=60)
    kitchen.hidden.clear()
    kitchen.wait(65)
    [after] = kitchen.items()
    assert (after['id'], after['created_at'], after['due_at']) == (record['id'], record['created_at'], record['due_at'])
    assert [i['key'] for i in kitchen.inventory.listing()] == [tracked['key']]
    assert not any(e['event'] in ('dish_picked_up', 'dismiss') for e in store.obligation(after['id'])['events'])


def test_background_dish_survives_dropout_but_real_removal_clears(store, media):
    kitchen = Kitchen(store, media)
    kitchen.put('cup', 'cup', 'counter')()
    kitchen.wait(40)
    [record] = kitchen.items()
    kitchen.hidden.add('cup')
    for _ in range(70):
        kitchen.step(seconds=60)
    kitchen.hidden.clear()
    kitchen.wait(65)
    assert [r['id'] for r in kitchen.items()] == [record['id']]
    kitchen.visit('counter', change=kitchen.take('cup'))
    kitchen.wait(5)
    assert kitchen.inventory.listing() == []


def test_long_occlusion_preserves_dish_and_deadline(kitchen):
    kitchen.visit('counter', change=kitchen.put('cup', 'cup', 'counter'))
    kitchen.wait(40)
    [record] = kitchen.items()
    kitchen.hidden.add('cup')
    for _ in range(15):
        kitchen.step([(7, [0, .2, .3, .65])], seconds=60)
    kitchen.hidden.clear()
    kitchen.wait(65)
    [after] = kitchen.items()
    assert (after['id'], after['due_at']) == (record['id'], record['due_at'])


def test_a_camera_pulsing_brightness_every_other_frame_still_lets_the_kitchen_be_still(kitchen):
    field = cv2.GaussianBlur(np.random.default_rng(2).normal(0, 1, (HEIGHT, WIDTH)).astype(np.float32), (0, 0), 25)
    kitchen.pulse = 1 + 0.5 * field / np.abs(field).max()
    kitchen.wait(10)
    still = kitchen.inventory.quiet_since()
    assert still is not None
    kitchen.wait(60)
    assert kitchen.inventory.quiet_since() == still
    kitchen.step([(7, [0.05, 0.2, 0.3, 0.65])])
    assert kitchen.inventory.quiet_since() is None


def test_one_person_leaving_two_dishes_keeps_independent_outcomes(kitchen):
    def both():
        kitchen.put("plate", "plate", "counter")()
        kitchen.put("cup", "cup", "counter", dx=0.18, size=(0.04, 0.06))()
    kitchen.visit("counter", change=both)
    kitchen.wait(40)
    items = kitchen.items()
    assert len(items) == 2
    assert sorted(d["kind"] for i in items for d in i['dishes']) == ["cup", "plate"]
    plate = next(i for i in items if i['dishes'][0]['kind'] == 'plate')
    cup = next(i for i in items if i['dishes'][0]['kind'] == 'cup')
    kitchen.visit('counter', change=kitchen.take('plate'))
    kitchen.visit('rack', change=kitchen.put('plate', 'plate', 'rack'))
    kitchen.wait(40)
    assert kitchen.store.obligation(plate['id'])['lifecycle'] == 'cleared'
    assert kitchen.store.obligation(cup['id'])['lifecycle'] == 'open'


def test_confirmed_dish_still_there_after_the_deadline_is_verified_overdue(kitchen):
    kitchen.store.set_settings({"deadline_minutes": 5})
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    kitchen.store.action(item["id"], "confirm", resident_id=kitchen.store.add_resident("Owner")["id"], now=kitchen.at)
    kitchen.wait(60 * 4)
    assert not kitchen.store.obligation(item["id"])["overdue_verified"]
    kitchen.wait(60 * 2)
    assert kitchen.store.obligation(item["id"])["overdue_verified"]


def test_a_late_dish_taken_away_stops_being_out_but_stays_late(kitchen):
    kitchen.store.set_settings({"deadline_minutes": 5})
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    owner = kitchen.store.add_resident("Owner")["id"]
    kitchen.store.action(item["id"], "confirm", resident_id=owner, now=kitchen.at)
    kitchen.wait(360)
    kitchen.visit("counter", change=kitchen.take("plate"))
    kitchen.wait(40)
    kitchen.store.scan_deadlines(utcnow() + timedelta(minutes=31))
    done = kitchen.store.obligation(item["id"])
    assert done["status"] == "resolved" and done["closed_by_camera"] and done["overdue_verified"]
    [stats] = [s for s in kitchen.store.statistics() if s["resident_id"] == owner]
    assert stats["overdue"] == 0 and stats["dishes_unwashed"] == 0  # Camera sightings alone are not confirmed dirty incidents.


def test_a_dish_washed_through_the_sink_before_its_deadline_was_taken_away_in_time(kitchen):
    kitchen.store.set_settings({"deadline_minutes": 240})
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    kitchen.visit("counter", change=kitchen.take("plate"))
    kitchen.visit("sink", change=kitchen.put("plate", "plate", "sink"))
    kitchen.wait(5)
    kitchen.visit("sink", change=kitchen.take("plate"))
    kitchen.visit("rack", change=kitchen.put("plate", "plate", "rack"))
    kitchen.wait(40)
    assert kitchen.store.obligation(item["id"])['lifecycle'] == 'cleared'
    kitchen.store.scan_deadlines(utcnow() + timedelta(minutes=31))
    assert kitchen.store.obligation(item["id"])["status"] == "cleared"

def test_unassigned_stable_dish_gets_one_deadline_capture_without_named_blame(kitchen):
    kitchen.store.set_settings({"deadline_minutes": 5})
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    kitchen.wait(360)
    item = kitchen.store.obligation(item["id"])
    deadline = next(s for s in item["evidence_timeline"]["stages"] if s["key"] == "deadline")
    assert deadline["photo"] is not None
    assert parse_stamp(deadline["photo"]["created_at"]) >= parse_stamp(item["due_at"])
    assert not item["overdue_verified"] and item["resident_id"] is None
    kitchen.wait(120)
    captures = [e for e in kitchen.items()[0]["evidence"] if e["label"].startswith("Matching dish still visible after")]
    assert len(captures) == 1


def test_naming_the_owner_of_a_dish_photographed_after_its_deadline_makes_it_late(kitchen):
    kitchen.store.set_settings({"deadline_minutes": 5})
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    kitchen.wait(360)
    assert not kitchen.store.obligation(item["id"])["overdue_verified"]
    named = kitchen.store.action(item["id"], "confirm", resident_id=kitchen.store.add_resident("Owner")["id"], now=kitchen.at)
    assert named["overdue_verified"]
    assert not kitchen.store.confirmed_late_ids()  # Identity confirmation alone is not a dirty-dish verdict.


def test_naming_the_owner_of_a_dish_gone_by_its_deadline_does_not_make_it_late(kitchen):
    kitchen.store.set_settings({"deadline_minutes": 5})
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [item] = kitchen.items()
    named = kitchen.store.action(item["id"], "confirm", resident_id=kitchen.store.add_resident("Owner")["id"], now=kitchen.at + timedelta(minutes=10))
    assert not named["overdue_verified"]

def test_camera_nudge_moves_dishes_without_new_items(kitchen):
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    before = kitchen.inventory.listing()[0]["box"]
    kitchen.inventory.shift(0.01, -0.01)
    assert kitchen.inventory.listing()[0]["box"][0] == pytest.approx(before[0] + 0.01)
    kitchen.wait(40)
    assert len(kitchen.items()) == 1


def test_confirming_one_dish_suggests_the_same_person_for_their_next_one(kitchen):
    looks = {7: [1.0, 0.0, 0.0], 9: [0.0, 1.0, 0.0]}
    kitchen.inventory.describe = lambda frame, box: ({"outfit": looks[7 if box[0] < 0.3 else 9]}, JPEG)
    alex = kitchen.store.add_resident("Alex")["id"]
    kitchen.store.add_resident("Sam")
    kitchen.visit("counter", change=kitchen.put("plate", "plate", "counter"))
    kitchen.wait(40)
    [first] = kitchen.items()
    assert first["suggested_resident_id"] is None
    assert any(e["label"] == "Who handled it (zoomed)" for e in first["evidence"])
    kitchen.store.action(first["id"], "confirm", resident_id=alex)
    kitchen.visit("counter", change=kitchen.put("cup", "cup", "counter", dx=0.18, size=(0.04, 0.06)), track=11)
    kitchen.wait(40)
    second = kitchen.items()[-1]
    assert (second["suggested_resident_id"], second["suggested_name"]) == (alex, "Alex")
