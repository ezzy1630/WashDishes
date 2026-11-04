"""Additive SQLite upgrades shared by the league and house sign-in API."""
from __future__ import annotations

import re
import unicodedata


def migrate(conn) -> None:
    from .learning import migrate as learning_migrate
    learning_migrate(conn)
    for table, additions in {
        'residents': [('auth_user_id', 'TEXT'), ('auth_slug', 'TEXT')],
        'obligations': [
            ('named_by', 'INTEGER REFERENCES residents(id)'), ('named_at', 'TEXT'),
            ('dispute_state', 'TEXT'), ('dispute_by', 'INTEGER REFERENCES residents(id)'),
            ('dispute_note', "TEXT NOT NULL DEFAULT ''"), ('dispute_at', 'TEXT'),
            ('dispute_settled_by', 'INTEGER REFERENCES residents(id)'), ('dispute_settled_at', 'TEXT'),
            ('dispute_previous', 'TEXT'),
        ],
    }.items():
        columns = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, definition in additions:
            if name not in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    conn.executescript('''
        CREATE UNIQUE INDEX IF NOT EXISTS residents_auth ON residents(auth_user_id) WHERE auth_user_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS residents_slug ON residents(auth_slug) WHERE auth_slug IS NOT NULL;
        CREATE INDEX IF NOT EXISTS evidence_arrival ON evidence(kind,label,created_at);
        CREATE INDEX IF NOT EXISTS evidence_obligation_kind ON evidence(obligation_id,kind,label,created_at);
        CREATE TABLE IF NOT EXISTS house_join_tokens(
            token_hash TEXT PRIMARY KEY, resident_id INTEGER NOT NULL REFERENCES residents(id),
            expires_at TEXT NOT NULL, used_at TEXT);
        CREATE TABLE IF NOT EXISTS league_rules(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS league_start(id INTEGER PRIMARY KEY CHECK(id=1), start_week TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS league_weeks(
            start TEXT PRIMARY KEY, end TEXT NOT NULL, closes_at TEXT NOT NULL,
            closed_at TEXT NOT NULL, snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS league_duty(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT);
        CREATE TABLE IF NOT EXISTS house_verdicts(
            obligation_id INTEGER PRIMARY KEY REFERENCES obligations(id), verdict TEXT NOT NULL,
            by_id INTEGER REFERENCES residents(id), at TEXT NOT NULL, of_id INTEGER REFERENCES obligations(id));
        CREATE TABLE IF NOT EXISTS house_visit_verdicts(
            visit_id TEXT PRIMARY KEY, verdict TEXT NOT NULL, by_id INTEGER REFERENCES residents(id),
            at TEXT NOT NULL, of_visit_id TEXT, obligation_ids TEXT NOT NULL, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS house_hidden_visits(
            resident_id INTEGER NOT NULL, visit_id TEXT NOT NULL, PRIMARY KEY(resident_id,visit_id));
        CREATE TABLE IF NOT EXISTS house_verdict_before(visit_id TEXT PRIMARY KEY, data TEXT NOT NULL);
    ''')


def slug_for(store, resident: dict) -> str:
    """Persist a unique login once; duplicate/Unicode names never share an auth account."""
    row = store.conn.execute('SELECT auth_slug FROM residents WHERE id=?', (resident['id'],)).fetchone()
    if row['auth_slug']:
        return row['auth_slug']
    ascii_name = unicodedata.normalize('NFKD', resident['name']).encode('ascii', 'ignore').decode()
    base = re.sub('[^a-z0-9]+', '-', ascii_name.lower()).strip('-')[:40] or 'housemate'
    slug = base
    suffix = 0
    while store.conn.execute('SELECT 1 FROM residents WHERE auth_slug=?', (slug,)).fetchone():
        suffix += 1
        slug = f"{base}-{resident['id']}" + (f'-{suffix}' if suffix > 1 else '')
    store.conn.execute('UPDATE residents SET auth_slug=? WHERE id=?', (slug, resident['id']))
    return slug
