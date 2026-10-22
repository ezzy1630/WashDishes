import time

import numpy as np
import pytest

from wash_dishes.visits import Tracer

STOVE = {"id": "stove", "name": "Stovetop", "role": "dirty", "box": [.05, .5, .3, .2]}
SINK = {"id": "sink", "name": "Sink basins", "role": "sink", "box": [.6, .5, .2, .2]}
# Each cook is out of reach of the other's pan.
PAN, OTHER_PAN, IN_SINK = [.07, .55, .1, .1], [.27, .55, .07, .1], [.65, .55, .1, .1]
AT_PAN, AT_OTHER_PAN, AT_SINK = [.02, .2, .15, .5], [.26, .2, .1, .5], [.6, .2, .2, .5]
RED, GREY = (0, 0, 200), (90, 90, 90)


class Kitchen:
    """One frame a second. `people(at)` gives person boxes; `dishes(at)` the boxes holding a red pan."""

    def __init__(self, people, dishes, end=2000, gaps=()):
        self.people, self.dishes, self.end, self.gaps = people, dishes, end, gaps

    def frames(self, t0, t1):
        for at in range(max(0, int(t0)), min(self.end, int(t1)) + 1):
            if not any(a <= at < b for a, b in self.gaps):
                yield {"at": float(at), "detections": [{"kind": "person", "score": .9, "box": b} for b in self.people(at)]}

    def image(self, at):
        image = np.full((200, 400, 3), GREY, np.uint8)
        for x, y, w, h in self.dishes(at):
            image[int(y * 200):int((y + h) * 200), int(x * 400):int((x + w) * 400)] = RED
        return image

    def frame(self, at):
        return at, f"frame {at}".encode()


def embed(crops):
    vectors = np.array([c.reshape(-1, 3).mean(axis=0) + 1 for c in crops], np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def trace(kitchen, logged=1100, until=1900, deadline=None):
    tracer = Tracer(kitchen, lambda: kitchen, embed, lambda: [STOVE, SINK])
    return tracer(IN_SINK, logged, until, deadline=deadline)


def cooked_then_carried(at):
    """Cook alone at the stove 100-400; at 1000-1020 someone else carries the pan to the sink."""
    if 100 <= at < 400 or 1000 <= at < 1010:
        return [AT_PAN]
    return [AT_SINK] if 1010 <= at < 1020 else []


def pan_where(at):
    return [PAN] if 110 <= at < 1005 else [IN_SINK] if at >= 1012 else []


def test_a_carried_pan_is_followed_back_to_whoever_cooked_with_it():
    found = trace(Kitchen(cooked_then_carried, pan_where))
    assert isinstance(found, dict), found
    assert found["use"] == "cooking" and found["stay"] >= 120
    assert len(found["moves"]) == 1 and found["moves"][0]["to"] == IN_SINK
    assert found["box"][0] < .2 and found["box"][1] == pytest.approx(.55, abs=.06)  # on the stove, not in the sink
    assert found["before"]["at"] < 110 and 400 <= found["left"]["at"] < 1000
    # Whoever was with it last, while it cooked: never the person who carried it.
    assert all(280 <= p["at"] < 400 for p in [found["putting"], *found["candidates"]])


def test_a_dish_carried_off_while_still_in_use_is_not_a_move():
    # Served onto at 500-540, carried to the sink at 565 by whoever served it: it never sat left there.
    def people(at):
        return [AT_PAN] if 500 <= at < 540 or 560 <= at < 565 else [AT_SINK] if 565 <= at < 575 else []

    def dishes(at):
        return [PAN] if 505 <= at < 563 else [IN_SINK] if at >= 568 else []

    found = trace(Kitchen(people, dishes))
    assert isinstance(found, dict), found
    assert found["moves"] == [] and found["use"] == "brought" and found["box"] == IN_SINK
    assert 565 <= found["putting"]["at"] < 575


def test_look_alike_pans_carried_together_from_two_cooks_name_nobody():
    def people(at):
        if 100 <= at < 400:
            return [AT_PAN]
        if 500 <= at < 800:
            return [AT_OTHER_PAN]
        if 1000 <= at < 1010:
            return [AT_PAN, AT_OTHER_PAN]
        return [AT_SINK] if 1010 <= at < 1020 else []

    def dishes(at):
        return ([PAN] if 110 <= at < 1005 else []) + ([OTHER_PAN] if 510 <= at < 1005 else []) + ([IN_SINK] if at >= 1012 else [])

    assert trace(Kitchen(people, dishes)) == "look-alike dishes were moved together and lead back to different people"


def test_cooking_together_names_nobody():
    def people(at):
        return [AT_PAN, [.1, .3, .1, .4]] if 100 <= at < 400 else cooked_then_carried(at)
    assert trace(Kitchen(people, pan_where)) == "several people were at the dish together"


def test_an_arrival_the_journal_did_not_see_names_nobody():
    found = trace(Kitchen(cooked_then_carried, pan_where, gaps=[(990, 1080)]))
    assert found == "the dish arrived while the camera history has a gap"


def test_the_trace_stops_at_its_budget():
    assert trace(Kitchen(cooked_then_carried, pan_where), deadline=time.monotonic() - 1) == "the trace reached its time budget"
