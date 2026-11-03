import json
import threading
import time
from datetime import timedelta
from urllib.parse import urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from wash_dishes import house_auth, league
from wash_dishes.app import create_app
from wash_dishes.config import Config
from wash_dishes.store import stamp, utcnow


@pytest.fixture
def house_client(tmp_path, monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://auth.example')
    monkeypatch.setenv('HOUSE_SITE_ORIGIN', 'https://highest-house.example')
    monkeypatch.setenv('HOUSE_PUBLIC_ORIGIN', 'https://dishwatch.example')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'sb_secret_fake')
    monkeypatch.setenv('SUPABASE_JWT_SECRET', 'h' * 40)
    # Every auth request in this file either uses this fake JWKS or an explicit fake admin.
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key())) | {'kid': 'test-ec', 'alg': 'ES256'}
    app = create_app(Config(tmp_path / 'house', password='operator', workers=False))
    monkeypatch.setattr(app.state.house.verifier, 'fetch_jwks', lambda: {'keys': [jwk]})
    store = app.state.store
    people = [store.add_resident(name) for name in ('Alex', 'Bo', 'Casey')]
    with store.lock, store.conn:
        for i, person in enumerate(people):
            store.conn.execute('UPDATE residents SET auth_user_id=? WHERE id=?', (f'user-{i}', person['id']))

    def token(index=0, **claims):
        payload = {'sub': f'user-{index}', 'iss': 'https://auth.example/auth/v1', 'aud': 'authenticated', 'exp': int(time.time()) + 3600}
        return jwt.encode(payload | claims, key, algorithm='ES256', headers={'kid': 'test-ec'})

    with TestClient(app) as client:
        client.headers['Authorization'] = f'Bearer {token()}'
        client.people = people
        client.token = token
        yield client


def late(client, *, owner=None, quantity=1, kind='pan', verified=True):
    store = client.app.state.store
    item = store.create_obligation(owner, [{'kind': kind, 'quantity': quantity}], at=utcnow() - timedelta(hours=3))
    with store.lock, store.conn:
        store.conn.execute('UPDATE obligations SET due_at=?,overdue_verified=? WHERE id=?',
                           (stamp(utcnow() - timedelta(minutes=1)), int(verified), item['id']))
    return store.obligation(item['id'])


def as_person(client, index):
    client.headers['Authorization'] = 'Bearer ' + client.token(index)


def operator(client):
    result = client.post('/api/login', json={'password': 'operator'})
    assert result.status_code == 200
    client.headers['X-CSRF-Token'] = result.json()['csrf_token']


def test_auth_valid_ec_and_subject_mapping(house_client):
    client = house_client
    assert client.get('/house/api/me').json() == {'resident': {'id': client.people[0]['id'], 'name': 'Alex'}, 'house': 'Highest House'}
    assert client.get('/api/dashboard').status_code == 401
    client.headers.pop('Authorization')
    assert client.get('/house/api/me').status_code == 401
    as_person(client, 9)
    assert client.get('/house/api/me').status_code == 403
    as_person(client, 0)
    client.app.state.store.archive_resident(client.people[0]['id'])
    assert client.get('/house/api/me').status_code == 403


@pytest.mark.parametrize('claims', [
    {'exp': int(time.time()) - 10}, {'aud': 'anon'}, {'iss': 'https://wrong.example/auth/v1'}, {'sub': ''},
])
def test_jwt_rejects_expired_wrong_aud_issuer_and_subject(house_client, claims):
    client = house_client
    client.headers['Authorization'] = 'Bearer ' + client.token(**claims)
    assert client.get('/house/api/me').status_code == 401


def test_rsa_hs_key_confusion_signature_and_jwks_cache(house_client, monkeypatch):
    client, verifier = house_client, house_client.app.state.house.verifier
    assert client.get('/house/api/me').status_code == 200
    # A cached EC key is enough: a network outage cannot break valid cached tokens.
    monkeypatch.setattr(verifier, 'fetch_jwks', lambda: pytest.fail('Unnecessary JWKS request'))
    assert client.get('/house/api/me').status_code == 200
    payload = jwt.decode(client.token(), options={'verify_signature': False})
    client.headers['Authorization'] = 'Bearer ' + jwt.encode(payload, 'wrong-key' * 8, algorithm='HS256')
    assert client.get('/house/api/me').status_code == 401
    client.headers['Authorization'] = 'Bearer ' + jwt.encode(payload, 'h' * 40, algorithm='HS256')
    assert client.get('/house/api/me').status_code == 200
    client.headers['Authorization'] = 'Bearer ' + jwt.encode(payload, '', algorithm='none')
    assert client.get('/house/api/me').status_code == 401
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {'kid': 'rsa', 'alg': 'RS256'}
    verifier.keys = []
    monkeypatch.setattr(verifier, 'fetch_jwks', lambda: {'keys': [jwk]})
    client.headers['Authorization'] = 'Bearer ' + jwt.encode(payload, key, algorithm='RS256', headers={'kid': 'rsa'})
    assert client.get('/house/api/me').status_code == 200
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client.headers['Authorization'] = 'Bearer ' + jwt.encode(payload, wrong, algorithm='RS256', headers={'kid': 'rsa'})
    assert client.get('/house/api/me').status_code == 401


def test_tunnel_cannot_reach_private_paths_even_with_operator_session(house_client):
    client = house_client
    operator(client)
    for path in ('/', '/static/app.js', '/api/health', '/api/dashboard', '/api/live/video', '/me', '/house', '/household/api/me'):
        assert client.get(path, headers={'Cf-Connecting-IP': '192.0.2.1'}).status_code == 403
    assert client.get('/house/api/me', headers={'Cf-Connecting-IP': '192.0.2.1'}).status_code == 200
    assert client.get('/api/dashboard').status_code == 200


def test_cors_allowed_only_house_origin_and_operator_csrf_stays_intact(house_client):
    client = house_client
    headers = {'Origin': 'https://highest-house.example', 'Sec-Fetch-Site': 'cross-site'}
    response = client.get('/house/api/week', headers=headers)
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == headers['Origin']
    assert 'access-control-allow-credentials' not in response.headers
    preflight = client.options('/house/api/dishes/1/name', headers=headers | {
        'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization, content-type'})
    assert preflight.status_code == 204
    assert client.get('/house/api/week', headers={'Origin': 'https://evil.example'}).status_code == 403
    item = late(client)
    assert client.post(f"/house/api/dishes/{item['id']}/name", json={'resident_id': client.people[0]['id']}, headers=headers).status_code == 200
    operator(client)
    assert client.post('/api/residents', json={'name': 'New'}, headers=headers).status_code == 403
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/league/duty', json={'resident_id': None}).status_code == 403


def test_naming_disputing_settling_permissions_and_history(house_client):
    client = house_client
    item = late(client, quantity=2)
    path = f"/house/api/dishes/{item['id']}"
    board = client.get('/house/api/week').json()
    assert board['unnamed_count'] == 1 and board['dishes'][0]['can_name']
    assert [p['points'] for p in board['standings']] == [0, 0, 0]
    named = client.post(path + '/name', json={'resident_id': client.people[1]['id']}).json()
    assert named['resident']['name'] == 'Bo' and named['named_by']['name'] == 'Alex' and named['named_at']
    assert not named['can_name'] and not named['can_dispute']
    assert client.post(path + '/name', json={'resident_id': client.people[2]['id']}).status_code == 409
    assert client.post(path + '/dispute', json={}).status_code == 403
    as_person(client, 1)
    assert client.get('/house/api/week').json()['dishes'][0]['can_dispute']
    disputed = client.post(path + '/dispute', json={'note': 'Not my pan'}).json()
    assert disputed['points'] == 0 and disputed['dispute']['state'] == 'open'
    assert sum(p['points'] for p in client.get('/house/api/week').json()['standings']) == 0
    assert client.post(path + '/settle', json={'decision': 'them'}).status_code == 403
    as_person(client, 0)
    assert not client.get('/house/api/week').json()['dishes'][0]['can_settle']
    assert client.post(path + '/settle', json={'decision': 'them'}).status_code == 403
    as_person(client, 2)
    assert client.get('/house/api/week').json()['dishes'][0]['can_settle']
    upheld = client.post(path + '/settle', json={'decision': 'them'}).json()
    assert upheld['resident']['name'] == 'Bo' and upheld['points'] == 4
    assert upheld['dispute']['settled_by']['name'] == 'Casey'
    as_person(client, 1)
    assert client.post(path + '/dispute', json={}).status_code == 200
    as_person(client, 2)
    overturned = client.post(path + '/settle', json={'decision': 'not_them'}).json()
    assert overturned['resident'] is None and overturned['can_name'] and overturned['named_by'] is None
    assert overturned['dispute']['state'] == 'overturned'
    assert client.post(path + '/name', json={'resident_id': client.people[2]['id']}).status_code == 200
    events = client.app.state.store.obligation(item['id'])['events']
    assert sum(e['event'] == 'house_dispute_settled' for e in events) == 2


def test_operator_settlement_closed_week_errors_rate_and_invalid_payloads(house_client):
    client = house_client
    item = late(client, owner=client.people[0]['id'])
    path = f"/house/api/dishes/{item['id']}"
    assert client.post(path + '/dispute', json={}).status_code == 200
    operator(client)
    assert client.post(f"/api/league/dishes/{item['id']}/settle", json={'decision': 'not_them'}).status_code == 200
    assert client.post(path + '/name', json={'resident_id': 'bad'}).status_code == 400
    assert client.post(path + '/name', json={'resident_id': 99999}).status_code == 400
    assert client.post('/house/api/dishes/99999/name', json={'resident_id': 1}).status_code == 404
    assert client.get('/house/api/week?start=2025-11-04').status_code == 400
    assert client.post(path + '/settle', json={'decision': 'bogus'}).status_code == 400
    store = client.app.state.store
    with store.lock, store.conn:
        old = utcnow() - timedelta(weeks=2)
        store.conn.execute('UPDATE obligations SET due_at=?,created_at=? WHERE id=?', (stamp(old), stamp(old - timedelta(hours=2)), item['id']))
    response = client.post(path + '/name', json={'resident_id': 1})
    assert response.status_code == 409 and 'closed' in response.json()['detail']
    # Rate limiting applies per account, even for rejected writes.
    codes = [client.post('/house/api/dishes/99999/name', json={'resident_id': 1}).status_code for _ in range(25)]
    assert 429 in codes


def test_signed_media_is_scoped_expiring_private_and_not_video(house_client):
    client, store = house_client, house_client.app.state.store
    item = late(client)
    media = client.app.state.media
    one = media.save_snapshot(item['id'], b'\xff\xd8synthetic\xff\xd9', 'Dish left unattended')
    two = media.save_snapshot(item['id'], b'\xff\xd8synthetic2\xff\xd9', 'Another photo')
    url = client.get('/house/api/week').json()['dishes'][0]['photo_url']
    assert url.startswith('https://dishwatch.example/house/media/')
    path = urlsplit(url).path + '?' + urlsplit(url).query
    client.headers.pop('Authorization')
    response = client.get(path, headers={'Cf-Connecting-IP': '192.0.2.1'})
    assert response.status_code == 200 and response.content.startswith(b'\xff\xd8')
    assert response.headers['cache-control'] == 'private, no-store'
    assert client.get(path.replace(one, two)).status_code == 403
    assert client.get(path.replace('sig=', 'sig=x')).status_code == 403
    from urllib.parse import parse_qs
    expiry = int(parse_qs(urlsplit(url).query)['exp'][0])
    assert client.get(path.replace(str(expiry), str(int(time.time()) - 1))).status_code == 403
    store.action(item['id'], 'dismiss', note='Not a dish')
    assert client.get(path).status_code == 404
    other = store.create_obligation(None, [{'kind': 'cup', 'quantity': 1}])
    unrelated = media.save_snapshot(other['id'], b'\xff\xd8synthetic\xff\xd9', 'Not late')
    signed = client.app.state.house.signed_url(store.evidence(unrelated))
    assert client.get(signed).status_code == 404
    store.add_evidence(other['id'], 'video', 'video.mp4', 'clip', 'Private clip')
    signed = client.app.state.house.signed_url(store.evidence('video'))
    assert client.get(signed).status_code == 404


def test_join_one_time_reset_expiry_hashed_tokens_and_admin_payload(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    operator(client)
    new = store.add_resident('New Person')
    calls = []
    def admin(url, **kwargs):
        calls.append((url, kwargs))
        return {'id': 'new-auth-id'}
    monkeypatch.setattr(house_auth, 'request_json', admin)
    link = client.post(f"/api/league/residents/{new['id']}/join-link", json={}).json()
    token = link['url'].split('#')[1]
    assert token not in json.dumps([dict(r) for r in store.conn.execute('SELECT * FROM house_join_tokens')])
    assert client.post('/house/api/join', json={'token': token, 'password': 'short'}).status_code == 400
    response = client.post('/house/api/join', json={'token': token, 'password': 'test-password'})
    assert response.status_code == 200 and response.json() == {'login': 'new-person'}
    url, request = calls[-1]
    assert url == 'https://auth.example/auth/v1/admin/users' and request['method'] == 'POST'
    assert request['headers'] == {'apikey': 'sb_secret_fake'}
    assert request['body'] == {'email': 'new-person@highest-house.ezzyrappeport.com', 'email_confirm': True, 'password': 'test-password'}
    assert client.post('/house/api/join', json={'token': token, 'password': 'test-password'}).status_code == 400
    assert {'slug': 'new-person', 'name': 'New Person'} in client.get('/house/api/login-names').json()
    reset = client.post(f"/api/league/residents/{new['id']}/join-link", json={}).json()['url'].split('#')[1]
    assert client.post('/house/api/join', json={'token': reset, 'password': 'new-password'}).status_code == 200
    assert calls[-1][0].endswith('/admin/users/new-auth-id') and calls[-1][1]['method'] == 'PUT'
    assert calls[-1][1]['body'] == {'password': 'new-password'}
    event_text = json.dumps(store.activity())
    assert 'test-password' not in event_text and token not in event_text and reset not in event_text
    assert 'sb_secret_fake' not in client.get('/api/dashboard').text


def test_join_expired_revoked_and_failed_upstream_retry(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    operator(client)
    new = store.add_resident('Retry')
    def link():
        return client.post(f"/api/league/residents/{new['id']}/join-link", json={}).json()['url'].split('#')[1]
    original, token = link(), link()
    assert client.post('/house/api/join', json={'token': original, 'password': 'a-password'}).status_code == 400
    def fail(*args, **kwargs):
        raise OSError('private upstream error')
    monkeypatch.setattr(house_auth, 'request_json', fail)
    result = client.post('/house/api/join', json={'token': token, 'password': 'a-password'})
    assert result.status_code == 503 and 'private upstream' not in result.text
    assert store.conn.execute('SELECT used_at FROM house_join_tokens WHERE resident_id=?', (new['id'],)).fetchone()[0] is None
    with store.lock, store.conn:
        store.conn.execute('UPDATE house_join_tokens SET expires_at=? WHERE resident_id=?', (stamp(utcnow() - timedelta(seconds=1)), new['id']))
    assert client.post('/house/api/join', json={'token': token, 'password': 'a-password'}).status_code == 400


def visit(client, items, visit_id='trip-one'):
    media, store = client.app.state.media, client.app.state.store
    eid = media.save_snapshot(items[0]['id'], b'\xff\xd8synthetic filmstrip\xff\xd9', 'arrival',
        capture={'visit_id': visit_id, 'obligation_ids': [d['id'] for d in items], 'frames': []})
    with store.lock, store.conn:
        store.conn.execute("UPDATE evidence SET kind='filmstrip' WHERE id=?", (eid,))
    return eid


def test_visit_grouping_guess_atomic_naming_disputing_and_settling(house_client):
    client, store = house_client, house_client.app.state.store
    pan, bowls, single = late(client), late(client, quantity=2, kind='bowl'), late(client, kind='cup')
    eid = visit(client, [pan, bowls])
    for item in (pan, bowls):
        store.set_suggestion(item['id'], client.people[1]['id'], 0.8, {'basis': 'same outfit as the earlier visit'})
    board = client.get('/house/api/week').json()
    grouped = next(v for v in board['visits'] if v['id'] == 'trip-one')
    assert grouped['title'] == 'Pan and 2 bowls'
    assert grouped['dish_ids'] == [pan['id'], bowls['id']] and grouped['points'] == 4
    assert grouped['guess'] == {'resident_id': client.people[1]['id'], 'name': 'Bo', 'basis': 'same outfit as the earlier visit'}
    assert grouped['resident'] is None and sum(r['points'] for r in board['standings']) == 0
    assert eid in grouped['filmstrip_url']
    assert any(v['id'] == f"dish-{single['id']}" for v in board['visits'])
    path = '/house/api/visits/trip-one'
    response = client.post(path + '/name', json={'resident_id': client.people[1]['id']})
    assert response.status_code == 200, response.text
    named = response.json()
    assert named['resident']['name'] == 'Bo' and named['named_by']['name'] == 'Alex' and named['named_at']
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 4
    as_person(client, 1)
    disputed = client.post(path + '/dispute', json={'note': 'Not my trip'})
    assert disputed.status_code == 200, disputed.text
    assert disputed.json()['points'] == 0
    as_person(client, 0)
    assert client.post(path + '/settle', json={'decision': 'not_them'}).status_code == 403
    as_person(client, 2)
    overturned = client.post(path + '/settle', json={'decision': 'not_them'})
    assert overturned.status_code == 200 and overturned.json()['resident'] is None
    assert all(store.obligation(i['id'])['resident_id'] is None for i in (pan, bowls))
    # The original dish endpoint retains its response shape and follows the visit unit too.
    result = client.post(f"/house/api/dishes/{pan['id']}/name", json={'resident_id': client.people[2]['id']})
    assert result.status_code == 200 and result.json()['id'] == pan['id']
    assert all(store.obligation(i['id'])['resident_id'] == client.people[2]['id'] for i in (pan, bowls))
    assert client.post(f"/house/api/visits/dish-{pan['id']}/name", json={'resident_id': 1}).status_code == 409


def test_visit_failure_rolls_back_every_member_and_audit_events(house_client):
    client, store = house_client, house_client.app.state.store
    first, second = late(client), late(client, owner=client.people[1]['id'])
    visit(client, [first, second])
    league.close_weeks(store)
    event = store.latest_event_id()
    response = client.post('/house/api/visits/trip-one/name', json={'resident_id': client.people[0]['id']})
    assert response.status_code == 409
    assert store.obligation(first['id'])['resident_id'] is None
    assert store.obligation(second['id'])['resident_id'] == client.people[1]['id']
    assert store.latest_event_id() == event


def test_visit_shared_filmstrip_visible_only_while_a_member_is_visible(house_client):
    client, store = house_client, house_client.app.state.store
    first, second = late(client), late(client)
    eid = visit(client, [first, second])
    store.action(first['id'], 'dismiss', note='Not a dish')
    board = client.get('/house/api/week').json()
    group = board['visits'][0]
    assert group['dish_ids'] == [second['id']] and not group['can_name']
    assert client.get(group['filmstrip_url']).status_code == 200
    store.action(second['id'], 'dismiss', note='Not a dish')
    assert client.get(client.app.state.house.signed_url(store.evidence(eid))).status_code == 404


@pytest.mark.parametrize('verdict', ['not_dish', 'clean'])
def test_visit_verdict_excludes_everyone_is_attributed_and_operator_can_undo(house_client, verdict):
    client, store = house_client, house_client.app.state.store
    first, second = late(client, owner=client.people[0]['id']), late(client, owner=client.people[0]['id'], kind='bowl')
    visit(client, [first, second])
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 3
    result = client.post('/house/api/visits/trip-one/verdict', json={'verdict': verdict})
    assert result.status_code == 200, result.text
    assert result.json()['verdict']['by']['name'] == 'Alex'
    as_person(client, 1)
    board = client.get('/house/api/week').json()
    assert board['visits'] == [] and board['dishes'] == [] and board['unreviewed_count'] == 0
    assert sum(r['points'] for r in board['standings']) == 0
    assert board['verdicts'][0]['verdict'] == verdict and board['verdicts'][0]['by']['name'] == 'Alex'
    assert client.post('/api/league/visits/trip-one/undo-verdict', json={}).status_code == 401
    operator(client)
    assert client.post('/api/league/visits/trip-one/undo-verdict', json={}).status_code == 200
    assert len(client.get('/house/api/week').json()['dishes']) == 2
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 3
    assert store.conn.execute("SELECT count(*) FROM events WHERE event='house_visit_verdict'").fetchone()[0] == 1


def test_cant_tell_is_private_hide_without_label_or_audit_verdict(house_client):
    client, store = house_client, house_client.app.state.store
    item = late(client)
    path = f"/house/api/visits/dish-{item['id']}/verdict"
    assert client.get('/house/api/week').json()['unreviewed_count'] == 1
    assert client.post(path, json={'verdict': 'cant_tell'}).json()['hidden']
    assert client.get('/house/api/week').json()['visits'] == []
    as_person(client, 1)
    board = client.get('/house/api/week').json()
    assert len(board['visits']) == 1 and board['unreviewed_count'] == 1 and board['verdicts'] == []
    assert store.conn.execute('SELECT count(*) FROM house_verdicts').fetchone()[0] == 0
    assert store.conn.execute("SELECT count(*) FROM events WHERE event='house_visit_verdict'").fetchone()[0] == 0


def test_duplicate_requires_another_original_and_allows_detector_kind_corrections(house_client):
    client, store = house_client, house_client.app.state.store
    canonical, duplicate, wrong = late(client), late(client), late(client, kind='cup')
    path = f"/house/api/visits/dish-{duplicate['id']}/verdict"
    assert client.post(path, json={'verdict': 'duplicate'}).status_code == 400
    assert client.post(path, json={'verdict': 'duplicate', 'of_visit_id': f"dish-{duplicate['id']}"}).status_code == 400
    assert client.post(path, json={'verdict': 'duplicate', 'of_visit_id': 'dish-999999'}).status_code == 404
    result = client.post(path, json={'verdict': 'duplicate', 'of_visit_id': f"dish-{canonical['id']}"})
    assert result.status_code == 200, result.text
    assert store.conn.execute('SELECT of_id FROM house_verdicts WHERE obligation_id=?', (duplicate['id'],)).fetchone()[0] == canonical['id']
    ids = {d['id'] for d in client.get('/house/api/week').json()['dishes']}
    assert duplicate['id'] not in ids and canonical['id'] in ids
    assert client.post(f"/house/api/visits/dish-{wrong['id']}/verdict", json={'verdict': 'duplicate', 'of_visit_id': f"dish-{canonical['id']}"}).status_code == 200


def test_camera_automatic_name_is_reviewable_and_only_human_confirmation_scores(house_client):
    client, store = house_client, house_client.app.state.store
    item = late(client, owner=client.people[0]['id'], quantity=3)
    with store.lock, store.conn:
        store.conn.execute("UPDATE obligations SET attribution='camera',source='vision',suggested_resident_id=? WHERE id=?", (client.people[0]['id'], item['id']))
    board = client.get('/house/api/week').json()
    assert sum(r['points'] for r in board['standings']) == 0
    assert board['dishes'][0]['resident'] is None and board['dishes'][0]['can_name']
    result = client.post(f"/house/api/visits/dish-{item['id']}/name", json={'resident_id': client.people[0]['id']})
    assert result.status_code == 200, result.text
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 6


def test_closed_week_refuses_verdict_and_operator_undo(house_client):
    client, store = house_client, house_client.app.state.store
    item = late(client)
    path = f"/house/api/visits/dish-{item['id']}/verdict"
    assert client.post(path, json={'verdict': 'clean'}).status_code == 200
    with store.lock, store.conn:
        store.conn.execute('UPDATE obligations SET due_at=? WHERE id=?', (stamp(utcnow() - timedelta(weeks=2)), item['id']))
    assert client.post(path, json={'verdict': 'cant_tell'}).status_code == 409
    operator(client)
    assert client.post(f"/api/league/visits/dish-{item['id']}/undo-verdict", json={}).status_code == 409


def test_sign_up_does_not_block_camera_store_work(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    operator(client)
    person = store.add_resident('Network wait')
    token = client.post(f"/api/league/residents/{person['id']}/join-link", json={}).json()['url'].split('#')[1]
    completed = threading.Event()
    def account(*args):
        def camera_store_work():
            store.residents()
            completed.set()
        worker = threading.Thread(target=camera_store_work, daemon=True)
        worker.start()
        assert completed.wait(1), 'Sign-up held the shared camera/history store lock during network IO.'
        return 'network-wait-account'
    monkeypatch.setattr(client.app.state.house.admin, 'account', account)
    assert client.post('/house/api/join', json={'token': token, 'password': 'test-password'}).status_code == 200


def stored_visit(client, items, *, aliases=None, arrival_seen=False, evidence=True, visit_id='arrival-trip'):
    store = client.app.state.store
    capture = {'visit_id': visit_id, 'obligation_ids': [d['id'] for d in items],
               'canonical_obligation_ids': [d['id'] for d in items if d['id'] not in (aliases or {})],
               'duplicate_of': {str(k): v for k, v in (aliases or {}).items()},
               'window': {'arrival_recorded': arrival_seen}, 'frames': []}
    eid = visit(client, items, visit_id) if evidence else None
    with store.conn:
        if eid:
            store.conn.execute('UPDATE evidence SET capture=? WHERE id=?', (json.dumps(capture), eid))
        store.conn.execute('INSERT INTO arrival_visits VALUES(?,?,?,?)', (visit_id, eid, stamp(), json.dumps(capture)))
        store.conn.executemany('INSERT OR REPLACE INTO arrival_visit_members VALUES(?,?)', [(d['id'], visit_id) for d in items])
    return eid


def test_arrival_aliases_count_once_and_inherit_visit_review(house_client):
    client = house_client
    items = [late(client) for _ in range(4)]
    canonical = items[0]['id']
    aliases = {d['id']: canonical for d in items[1:]}
    stored_visit(client, items, aliases=aliases)
    board = client.get('/house/api/week').json()
    assert len(board['dishes']) == 1 and board['unnamed_count'] == board['unreviewed_count'] == 1
    visit_row = board['visits'][0]
    assert visit_row['dish_ids'] == [canonical] and visit_row['alias_ids'] == list(aliases)
    assert visit_row['title'] == 'Pan' and visit_row['points'] == 2
    # Legacy dish routes for aliases must reach the canonical naming unit too.
    result = client.post(f"/house/api/dishes/{items[-1]['id']}/name", json={'resident_id': client.people[1]['id']})
    assert result.status_code == 200, result.text
    assert result.json()['points'] == 0 and result.json()['resident']['name'] == 'Bo'
    board = client.get('/house/api/week').json()
    assert board['standings'][0]['points'] == 2 and board['standings'][0]['dishes'] == 1
    assert board['unreviewed_count'] == 0
    assert all(a['points'] == 0 and a['resident']['name'] == 'Bo' for a in board['visits'][0]['aliases'])
    operator(client)
    assert client.get('/api/league/week').json()['standings'] == board['standings']
    response = client.post('/api/league/visits/arrival-trip/verdict', json={'verdict': 'clean'})
    assert response.status_code == 200, response.text
    assert all(a['verdict']['verdict'] == 'clean' for a in response.json()['aliases'])
    assert client.get('/api/league/week').json()['visits'] == []


def test_untimed_named_observation_scores_but_pickup_without_late_photo_does_not(house_client):
    client, store = house_client, house_client.app.state.store
    observed, pickup = late(client, verified=False), late(client, verified=False)
    with store.conn:
        store.conn.execute('UPDATE obligations SET timing_known=0 WHERE id IN (?,?)', (observed['id'], pickup['id']))
        store.conn.execute("UPDATE obligations SET lifecycle='resolved',resolved_at=? WHERE id=?", (stamp(), pickup['id']))
    store.add_evidence(observed['id'], 'late-observed', 'late.jpg', 'snapshot',
                       'Matching dish still visible after the deadline', at=utcnow())
    board = client.get('/house/api/week').json()
    assert [d['id'] for d in board['dishes']] == [observed['id']]
    assert board['visits'][0]['arrival_seen'] is False
    result = client.post(f"/house/api/visits/dish-{observed['id']}/name", json={'resident_id': client.people[0]['id']})
    assert result.status_code == 200, result.text
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 2
    # Unknown original arrival must still obey the frozen deadline week.
    with store.conn:
        store.conn.execute('UPDATE obligations SET due_at=? WHERE id=?', (stamp(utcnow() - timedelta(weeks=2)), observed['id']))
    assert client.post(f"/house/api/visits/dish-{observed['id']}/name", json={'resident_id': client.people[1]['id']}).status_code == 409


def test_table_visit_without_filmstrip_and_expired_media_keeps_membership(house_client):
    client = house_client
    items = [late(client), late(client, kind='bowl')]
    stored_visit(client, items, evidence=False)
    board = client.get('/house/api/week').json()
    assert len(board['visits']) == 1 and board['visits'][0]['id'] == 'arrival-trip'
    assert board['visits'][0]['filmstrip_url'] is None
    assert client.post('/house/api/visits/arrival-trip/name', json={'resident_id': client.people[0]['id']}).status_code == 200
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 3


def test_regenerated_visit_supersedes_old_write_routes_and_old_media(house_client):
    client, store = house_client, house_client.app.state.store
    items = [late(client), late(client, kind='bowl')]
    old = visit(client, items, 'old-trip')
    new = stored_visit(client, items, arrival_seen=True)
    with store.conn:
        store.conn.execute('UPDATE evidence SET created_at=? WHERE id=?', (stamp(utcnow() - timedelta(minutes=1)), old))
        store.conn.execute('UPDATE evidence SET expired=1 WHERE id=?', (new,))
    board = client.get('/house/api/week').json()
    assert [v['id'] for v in board['visits']] == ['arrival-trip']
    assert board['visits'][0]['arrival_seen'] is True
    assert board['visits'][0]['filmstrip_url'] is None
    assert client.post('/house/api/visits/old-trip/name', json={'resident_id': client.people[0]['id']}).status_code == 409
    assert client.get('/house/api/week').json()['unreviewed_count'] == 1


def test_human_duplicate_inside_a_visit_does_not_block_naming(house_client):
    client, store = house_client, house_client.app.state.store
    original, alias = late(client), late(client)
    stored_visit(client, [original, alias])
    with store.conn:
        store.mark_duplicate(alias['id'], original['id'], client.people[0]['id'])
    board = client.get('/house/api/week').json()
    assert len(board['visits']) == 1 and board['visits'][0]['can_name']
    assert board['visits'][0]['dish_ids'] == [original['id']]
    assert board['visits'][0]['points'] == 2
    assert client.post('/house/api/visits/arrival-trip/name', json={'resident_id': client.people[0]['id']}).status_code == 200
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 2


def test_closed_week_keeps_canonical_membership_but_expires_shared_media(house_client):
    client, store = house_client, house_client.app.state.store
    items = [late(client) for _ in range(2)]
    eid = stored_visit(client, items, aliases={items[1]['id']: items[0]['id']})
    store.action(items[0]['id'], 'confirm', resident_id=client.people[0]['id'])
    for item in items:
        store.set_suggestion(item['id'], client.people[1]['id'], .9)
    old = utcnow() - timedelta(weeks=2)
    with store.conn:
        store.conn.execute('UPDATE obligations SET due_at=?,created_at=?', (stamp(old), stamp(old - timedelta(hours=3))))
    start = league.week_bounds(now=old)['start']
    board = client.get('/house/api/week', params={'start': start}).json()
    assert board['week']['closed'] and len(board['dishes']) == 1
    assert board['visits'][0]['alias_ids'] == [items[1]['id']]
    assert board['visits'][0]['filmstrip_url']
    assert board['standings'][0]['points'] == 2
    assert board['visits'][0]['guess']['name'] == 'Bo'
    with store.conn:
        store.conn.execute('UPDATE evidence SET expired=1 WHERE id=?', (eid,))
        store.conn.execute('DELETE FROM arrival_visit_members')
    store.set_suggestion(items[1]['id'], client.people[2]['id'], .9)
    frozen = client.get('/house/api/week', params={'start': start}).json()
    assert frozen['visits'][0]['alias_ids'] == [items[1]['id']]
    assert frozen['visits'][0]['filmstrip_url'] is None
    assert frozen['standings'] == board['standings']
    assert frozen['visits'][0]['guess'] == board['visits'][0]['guess']


def test_alias_deadline_cannot_move_a_physical_dish_into_another_week(house_client):
    client, store = house_client, house_client.app.state.store
    original, alias = late(client), late(client)
    stored_visit(client, [original, alias], aliases={alias['id']: original['id']})
    old = utcnow() - timedelta(weeks=2)
    with store.conn:
        store.conn.execute('UPDATE obligations SET due_at=?,created_at=? WHERE id=?',
                           (stamp(old), stamp(old - timedelta(hours=3)), original['id']))
    assert client.get('/house/api/week').json()['visits'] == []
    board = client.get('/house/api/week', params={'start': league.week_bounds(now=old)['start']}).json()
    assert len(board['dishes']) == len(board['visits']) == 1
    assert board['visits'][0]['alias_ids'] == [alias['id']]


def test_duplicate_chains_cannot_create_cycles(house_client):
    client, store = house_client, house_client.app.state.store
    first, second, third = [late(client) for _ in range(3)]
    with store.conn:
        store.mark_duplicate(second['id'], first['id'], None)
        store.mark_duplicate(third['id'], second['id'], None)
        with pytest.raises(ValueError, match='itself'):
            store.mark_duplicate(first['id'], third['id'], None)
    board = client.get('/house/api/week').json()
    assert len(board['dishes']) == 1 and board['visits'][0]['alias_ids'] == [second['id'], third['id']]
    assert client.post(f"/house/api/dishes/{third['id']}/name", json={'resident_id': client.people[0]['id']}).status_code == 200
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 2


def test_shared_media_uses_table_membership_even_when_anchor_is_an_alias(house_client):
    client, store = house_client, house_client.app.state.store
    original, alias = late(client), late(client)
    eid = stored_visit(client, [original, alias], aliases={alias['id']: original['id']})
    with store.conn:
        store.conn.execute("UPDATE evidence SET obligation_id=?,capture='{}' WHERE id=?", (alias['id'], eid))
    board = client.get('/house/api/week').json()
    url = board['visits'][0]['filmstrip_url']
    assert url and eid in url
    assert client.get(url).status_code == 200
    response = client.post(f"/house/api/dishes/{alias['id']}/name", json={'resident_id': client.people[0]['id']})
    assert response.status_code == 200, response.text
    assert response.json()['points'] == 0 and response.json()['filmstrip_url']
