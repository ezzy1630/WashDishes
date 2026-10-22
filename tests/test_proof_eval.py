"""Evaluation must not reward missing labels or incorrect arrival windows."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('proof_eval', Path(__file__).parents[1] / 'scripts/proof_eval.py')
eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval)


def truth(**changes):
    return dict(obligation_id=1, is_dish=True, left_out_dirty=True, still_there_at_late=True,
                arrival_known='seen', arrival_at=100, **changes)


@pytest.mark.parametrize(('arrival', 'passes'), [(54.999, False), (55, True), (155, True), (155.001, False)])
def test_arrival_tolerance_is_window_not_logging_time(arrival, passes):
    row = truth()
    row['arrival_at'] = arrival
    result = dict(status='ready', before_at=100, putting_at=105, left_at=110)
    assert (eval.disagreement(row, result) is None) == passes


def test_unknowns_unlabeled_and_missing_results_do_not_inflate_score():
    unknown = truth()
    unknown['still_there_at_late'] = None
    rows = {1: truth(), 2: unknown, 3: truth()}
    results = {1: dict(status='ready', putting_at=100), 2: dict(status='ready', putting_at=100),
               4: dict(status='ready', putting_at=100)}
    score = eval.score_rows(rows, results)
    assert score['precision'] == score['recall'] == 1 / 3
    assert score['ready_unlabeled'] == 1
    assert {d['obligation_id'] for d in score['disagreements']} == {2, 3, 4}


def test_truth_corrections_are_last_line_wins(tmp_path):
    path = tmp_path / 'truth.jsonl'
    path.write_text('{"obligation_id":1,"is_dish":true}\n{"obligation_id":1,"is_dish":false}\n')
    assert eval.jsonl(path)[1]['is_dish'] is False


@pytest.mark.parametrize('window', [(110, 100), (float('nan'), 110), (100, float('inf'))])
def test_invalid_windows_cannot_pass(window):
    assert eval.disagreement(truth(), dict(before_at=window[0], left_at=window[1]))


@pytest.mark.parametrize('before_command', [True, False])
def test_shared_copy_honors_repo_on_either_side_of_command(tmp_path, before_command):
    import json
    import shutil
    import subprocess
    import sys

    repo = Path(__file__).parents[1]
    shared = tmp_path / 'proof_eval.py'
    shutil.copyfile(repo / 'scripts/proof_eval.py', shared)
    rows = tmp_path / 'rows.jsonl'
    rows.write_text('{"obligation_id":1,"status":"untraced"}\n')
    options = ['--repo', str(repo)]
    action = ['score', '--truth', str(rows), '--results', str(rows)]
    args = options + action if before_command else action + options
    result = subprocess.run([sys.executable, str(shared), *args], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['truth_labeled'] == 1
