import json
import os
import sqlite3
import subprocess
import sys
from datetime import timedelta

import pytest

from wash_dishes import league
from wash_dishes.store import stamp, utcnow


@pytest.mark.parametrize('recorded_start', [False, True])
@pytest.mark.parametrize('assignment,should_clear', [
    ('old_pick', True), ('start_day_pick', True), ('vote', False), ('later_pick', False),
])
def test_reset_cli_dry_run_real_and_idempotent(store, monkeypatch, recorded_start, assignment, should_clear):
    monkeypatch.setenv('WASHDISHES_TIMEZONE', 'America/Los_Angeles')
    now = utcnow()
    week = league.week_bounds(now=now)
    start, _ = league.week_times(week)
    prior = league.week_bounds(now=start - timedelta(weeks=2))
    resident = store.add_resident('Alex')['id']
    since = start - timedelta(weeks=1)
    if assignment == 'start_day_pick':
        since = start + timedelta(hours=12)
    elif assignment == 'later_pick':
        since = start + timedelta(days=1)
    current = {'resident_id': resident, 'name': 'Alex', 'since': stamp(since),
               'why': 'Set by the operator after a house vote.' if assignment == 'vote' else 'Weekly pick: 4 points · 4 ahead.'}
    with store.conn:
        if recorded_start:
            store.conn.execute('INSERT INTO league_start VALUES(1,?)', (prior['start'],))
        else:
            # The dry run must also work on the schema before this upgrade.
            store.conn.execute('DROP TABLE league_start')
        for bounds in (prior, week):
            store.conn.execute('INSERT INTO league_weeks VALUES(?,?,?,?,?)',
                               (bounds['start'], bounds['end'], bounds['closes_at'], stamp(now), '{}'))
        store.conn.execute('INSERT INTO league_duty VALUES(1,?)', (json.dumps(current),))
    root = store.root
    store.close()
    database = root / 'history.db'
    # SQLite may create WAL read-lock sidecars even in mode=ro. Neither the main
    # database nor application files/directories may change.
    def application_files():
        return {p.relative_to(root): p.read_bytes() for p in root.rglob('*')
                if p.is_file() and p.name not in ('history.db-wal', 'history.db-shm')}

    before_files = application_files()
    before_directories = {p.relative_to(root) for p in root.rglob('*') if p.is_dir()}
    env = {**os.environ, 'WASHDISHES_DATA_DIR': str(root.parent), 'PYTHONDONTWRITEBYTECODE': '1'}

    def run(*args):
        return json.loads(subprocess.run([sys.executable, '-B', '-m', 'wash_dishes', 'league-reset-start', *args],
                                         env=env, capture_output=True, text=True, check=True).stdout)

    dry = run('--dry-run')
    assert dry == {'dry_run': True, 'previous_start_week': prior['start'] if recorded_start else None,
                   'start_week': week['start'], 'deleted_weeks': [prior['start']],
                   'cleared_duty': current if should_clear else None, 'changed': True}
    assert application_files() == before_files
    assert {p.relative_to(root) for p in root.rglob('*') if p.is_dir()} == before_directories
    applied = run()
    assert applied == dry | {'dry_run': False}
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT start_week FROM league_start').fetchone()[0] == week['start']
        assert conn.execute('SELECT start FROM league_weeks').fetchall() == [(week['start'],)]
        saved = json.loads(conn.execute('SELECT data FROM league_duty').fetchone()[0])
        assert saved == (None if should_clear else current)
        event = conn.execute("SELECT data FROM events WHERE event='league_start_reset'").fetchone()
        assert json.loads(event[0]) == applied
        assert conn.execute('SELECT count(*) FROM residents').fetchone()[0] == 1
    again = run()
    assert again == {'dry_run': False, 'previous_start_week': week['start'], 'start_week': week['start'],
                     'deleted_weeks': [], 'cleared_duty': None, 'changed': False}
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE event='league_start_reset'").fetchone()[0] == 1


def test_reset_dry_run_refuses_missing_database_without_creating_directories(tmp_path):
    target = tmp_path / 'missing'
    result = subprocess.run([sys.executable, '-B', '-m', 'wash_dishes', 'league-reset-start', '--dry-run'],
                            env={**os.environ, 'WASHDISHES_DATA_DIR': str(target), 'PYTHONDONTWRITEBYTECODE': '1'},
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert not target.exists()
