from copy import deepcopy

import pytest
from test_proof_checks import BOX, FOUND, HIDDEN, LATE, LEFT, PUTTING, Verifier

from wash_dishes.proof_checks import ProofChecker
from wash_dishes.proof_visits import single_visitor


def frames():
    return [{'at': at, 'detections': ([{'kind': 'person', 'box': HIDDEN, 'score': .9, 'track_id': 7}]
                                     if 11 <= at <= 29 else [])} for at in range(10, 31)]


def test_hidden_release_has_one_continuous_visitor():
    result = single_visitor(frames(), BOX)
    assert result['start'] == 11 and result['end'] == 29
    assert len(result['observations']) == 19


@pytest.mark.parametrize('change', ['gap', 'handoff', 'two_people', 'missed_person', 'jump', 'untracked'])
def test_ambiguity_never_becomes_a_visitor(change):
    data = frames()
    if change == 'gap':
        del data[6:10]
    elif change == 'handoff':
        data[10]['detections'][0]['track_id'] = 8
    elif change == 'two_people':
        data[10]['detections'].append(deepcopy(data[10]['detections'][0]))
    elif change == 'missed_person':
        data[10]['detections'] = []
    elif change == 'jump':
        data[10]['detections'][0]['box'] = [.05, .3, .3, .6]
    else:
        data[10]['detections'][0].pop('track_id')
    assert isinstance(single_visitor(data, BOX), str)


def visit_verifier():
    verifier = Verifier()
    verifier.sure = True
    return verifier


def test_checker_allows_hidden_body_only_with_whole_visit_and_exact_person():
    detections = {PUTTING: [{'kind': 'person', 'box': HIDDEN}]}
    checker = ProofChecker(lambda raw, _: detections.get(raw, []), visit_verifier(), lambda _: {'label': 'can', 'margin': -.1},
                           drying_areas=lambda: [{'id': 'counter', 'role': 'dirty', 'box': BOX}])
    assert isinstance(checker(1, FOUND, LATE, BOX, None), str)
    found = deepcopy(FOUND) | {'method': 'visit', 'visit_frames': frames()}
    assert checker(1, found, LATE, BOX, None)['putting']['person_box'] == HIDDEN
    found['visit_frames'][8]['detections'] = []
    assert isinstance(checker(1, found, LATE, BOX, None), str)


def test_dirty_source_vetoes_even_a_single_person_visit():
    source = [.3, .65, .10, .12]
    detections = {FOUND['before']['jpeg']: [{'kind': 'bowl', 'box': source}],
                  PUTTING: [{'kind': 'person', 'box': HIDDEN}]}
    checker = ProofChecker(lambda raw, _: detections.get(raw, []), visit_verifier(), lambda _: {'label': 'can', 'margin': -.1},
                           drying_areas=lambda: [{'id': 'sink', 'role': 'sink', 'box': source},
                                                 {'id': 'counter', 'role': 'dirty', 'box': BOX}])
    found = deepcopy(FOUND) | {'method': 'visit', 'visit_frames': frames()}
    assert 'another dirty spot' in checker(1, found, LATE, BOX, None)
    # An existing dish in Before remains a rejection even with perfect person continuity.
    found['before']['jpeg'] = LEFT
    assert checker(1, found, LATE, BOX, None) == 'dish already there in the before picture'


def test_visit_cannot_bypass_unavailable_kitchen_probe_or_unconfigured_surface():
    found = deepcopy(FOUND) | {'method': 'visit', 'visit_frames': frames()}
    checker = ProofChecker(lambda raw, _: [{'kind': 'person', 'box': HIDDEN}] if raw == PUTTING else [],
                           Verifier(), lambda _: {'label': 'can', 'margin': -.1})
    result = checker.evaluate(1, found, LATE, BOX, None)
    assert result['status'] == 'rejected'
    assert 'the visit needs the kitchen dish checker to be sure it is a dish' in result['checks']['failures']
    assert 'the visit is outside the configured dirty or sink surfaces' in result['checks']['failures']


def test_visits_are_discovered_without_dish_detections_and_gaps_do_not_invent_endpoints():
    from wash_dishes.proof_visits import arrival_visits

    assert list(arrival_visits(frames(), BOX)) == [(10, 11, 30)]
    data = frames()
    del data[6:10]
    assert list(arrival_visits(data, BOX)) == []


def test_a_later_journal_gap_invalidates_older_visit_candidates(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from wash_dishes.putdown import _find_visit_journal

    data = frames() + [{'at': at, 'detections': []} for at in range(100, 104)]
    journal = SimpleNamespace(frames=lambda *_: iter(data))
    assert _find_visit_journal(journal, tmp_path, BOX, 100, 103, lambda _: [],
                               earlier_than=None, deadline=float('inf')) is None
