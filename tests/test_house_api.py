import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
    verifier.attempted_at = None
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
    assert client.get('/house/api/login-names').status_code == 404
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
    refused = client.post('/house/api/visits/trip-one/verdict', json={'verdict': verdict})
    assert refused.status_code == 409
    assert sum(r['points'] for r in client.get('/house/api/week').json()['standings']) == 3
    operator(client)
    result = client.post('/api/league/visits/trip-one/verdict', json={'verdict': verdict})
    assert result.status_code == 200, result.text
    assert result.json()['verdict'] == {'kind': verdict, 'by': None, 'at': result.json()['verdict']['at'], 'of_visit_id': None}
    as_person(client, 1)
    board = client.get('/house/api/week').json()
    assert board['visits'] == [] and board['dishes'] == [] and board['unreviewed_count'] == 0
    assert sum(r['points'] for r in board['standings']) == 0
    assert board['verdicts'][0]['kind'] == verdict and board['verdicts'][0]['by'] is None
    operator(client)
    assert client.post('/api/league/visits/trip-one/undo-verdict', json={}).status_code == 200
    assert len(client.get('/house/api/week').json()['dishes']) == 2
    assert client.get('/house/api/week').json()['standings'][0]['points'] == 3
    assert store.conn.execute("SELECT count(*) FROM events WHERE event='house_visit_verdict'").fetchone()[0] == 1


def test_cant_tell_is_private_hide_without_label_or_audit_verdict(house_client):
    client, store = house_client, house_client.app.state.store
    item = late(client)
    sample = store.add_dish_sample([1.] + [0.] * 511, .9)
    store.link_dish_sample(sample, item['id'])
    path = f"/house/api/visits/dish-{item['id']}/verdict"
    assert client.get('/house/api/week').json()['unreviewed_count'] == 1
    assert client.post(path, json={'verdict': 'cant_tell'}).json()['hidden']
    assert client.get('/house/api/week').json()['visits'] == []
    as_person(client, 1)
    board = client.get('/house/api/week').json()
    assert len(board['visits']) == 1 and board['unreviewed_count'] == 1 and board['verdicts'] == []
    assert store.conn.execute('SELECT count(*) FROM house_verdicts').fetchone()[0] == 0
    assert store.conn.execute("SELECT count(*) FROM events WHERE event='house_visit_verdict'").fetchone()[0] == 0

    assert store.remembered_objects() == [] and store.labeled_dish_samples() == []


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
    assert all(a['verdict']['kind'] == 'clean' for a in response.json()['aliases'])
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
        store.mark_duplicate(alias['id'], original['id'], f"resident:{client.people[0]['id']}")
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
        store.mark_duplicate(second['id'], first['id'], 'operator')
        store.mark_duplicate(third['id'], second['id'], 'operator')
        with pytest.raises(ValueError, match='itself'):
            store.mark_duplicate(first['id'], third['id'], 'operator')
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


def test_media_sizes_follow_actual_jpegs_and_cache_header_reads(house_client, monkeypatch):
    from io import BytesIO

    from PIL import Image

    from wash_dishes import house

    client = house_client
    item = late(client)
    store = client.app.state.store
    for evidence_id, kind, size in [('short-strip', 'filmstrip', (1200, 1177)), ('first', 'snapshot', (640, 360)), ('later', 'snapshot', (960, 540))]:
        output = BytesIO()
        Image.new('RGB', size, '#333333').save(output, 'JPEG')
        (store.root / 'media' / f'{evidence_id}.jpg').write_bytes(output.getvalue())
        capture = {'visit_id': 'short', 'obligation_ids': [item['id']], 'window': {'arrival_recorded': False}}
        store.add_evidence(item['id'], evidence_id, f'{evidence_id}.jpg', kind, 'arrival' if kind == 'filmstrip' else 'Matching dish still visible after the deadline' if evidence_id == 'later' else 'Dish left unattended', capture=capture)
    house._jpeg_size.cache_clear()
    opened = []
    original = Image.open
    def track(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Image, 'open', track)
    for _ in range(2):
        result = client.get('/house/api/week').json()
        for record in [result['dishes'][0], result['visits'][0]]:
            assert (record['filmstrip_width'], record['filmstrip_height']) == (1200, 1177)
            assert (record['photo_width'], record['photo_height']) == (960, 540)
            assert '/house/media/later?' in record['photo_url']
        assert result['visits'][0]['arrival_seen'] is False
    assert len(opened) == 2  # Both URLs share a header cache across dish/visit and repeated reads.
    output = BytesIO()
    Image.new('RGB', (640, 480)).save(output, 'JPEG')
    (store.root / 'media' / 'later.jpg').write_bytes(output.getvalue())
    updated = client.get('/house/api/week').json()['visits'][0]
    assert (updated['photo_width'], updated['photo_height']) == (640, 480)
    assert len(opened) == 3
    with store.conn:
        store.conn.execute("UPDATE evidence SET expired=1 WHERE id='short-strip'")
    expired = client.get('/house/api/week').json()['visits'][0]
    assert expired['filmstrip_url'] is None
    assert expired['filmstrip_width'] is None and expired['filmstrip_height'] is None


def test_operator_and_house_share_the_structured_pick_contract(house_client):
    client = house_client
    for owner, quantity in ((0, 6), (1, 2)):
        item = late(client, kind='plate', quantity=quantity)
        response = client.post(f"/house/api/dishes/{item['id']}/name", json={'resident_id': client.people[owner]['id']})
        assert response.status_code == 200
    chosen = client.get('/house/api/week').json()['pick']
    assert chosen == {'resident_id': client.people[0]['id'], 'name': 'Alex', 'state': 'pick',
                      'points': 6, 'lead': 4, 'needed_points': 0, 'needed_lead': 0,
                      'reason': '6 points · 4 ahead'}
    operator(client)
    assert client.get('/api/league/week').json()['pick'] == chosen


@pytest.mark.parametrize('verdict', ['not_dish', 'clean', 'duplicate'])
@pytest.mark.parametrize('state', ['named', 'disputed', 'settled', 'mixed'])
def test_resident_global_verdict_requires_every_member_nameable(house_client, verdict, state):
    client, store = house_client, house_client.app.state.store
    first, second, original = late(client), late(client), late(client)
    visit(client, [first, second])
    if state == 'mixed':
        store.action(second['id'], 'confirm', resident_id=client.people[1]['id'])
    else:
        assert client.post('/house/api/visits/trip-one/name', json={'resident_id': client.people[1]['id']}).status_code == 200
        if state != 'named':
            as_person(client, 1)
            assert client.post('/house/api/visits/trip-one/dispute', json={'note': 'Not mine'}).status_code == 200
        if state == 'settled':
            as_person(client, 2)
            assert client.post('/house/api/visits/trip-one/settle', json={'decision': 'them'}).status_code == 200
    as_person(client, 1)
    league.close_weeks(store)
    before = [dict(r) for r in store.conn.execute('SELECT * FROM obligations')]
    events = store.conn.execute('SELECT count(*) FROM events').fetchone()[0]
    result = client.post('/house/api/visits/trip-one/verdict', json={'verdict': verdict, **({'of_visit_id': f"dish-{original['id']}"} if verdict == 'duplicate' else {})})
    assert result.status_code == 409
    assert result.json()['detail'] == "Already named — use That wasn't me to dispute it."
    assert before == [dict(r) for r in store.conn.execute('SELECT * FROM obligations')]
    assert store.conn.execute('SELECT count(*) FROM events').fetchone()[0] == events
    # Personal hiding still works even on a named/disputed visit.
    assert client.post('/house/api/visits/trip-one/verdict', json={'verdict': 'cant_tell'}).status_code == 200


def test_resident_can_exclude_unnamed_visit_and_history_contract(house_client):
    client = house_client
    first, second = late(client), late(client)
    visit(client, [first, second])
    result = client.post('/house/api/visits/trip-one/verdict', json={'verdict': 'not_dish'})
    assert result.status_code == 200
    assert result.json()['verdict']['kind'] == 'not_dish'
    assert result.json()['verdict']['by']['name'] == 'Alex'
    as_person(client, 1)
    data = client.get('/house/api/week').json()
    assert data['visits'] == [] and data['dishes'] == []
    assert data['verdicts'][0]['kind'] == 'not_dish'
    assert data['verdicts'][0]['by']['name'] == 'Alex'


def test_read_budgets_are_separate_and_global(house_client):
    client, house = house_client, house_client.app.state.house
    house.week_budget.per_identity = 1
    assert client.get('/house/api/week').status_code == 200
    assert client.get('/house/api/week').status_code == 429
    as_person(client, 1)
    assert client.get('/house/api/week').status_code == 200
    # Authenticated writes do not spend the read or join budgets.
    assert client.post('/house/api/visits/missing/name', json={'resident_id': client.people[0]['id']}).status_code == 404
    house.week_budget.total = 2
    as_person(client, 2)
    assert client.get('/house/api/week').status_code == 429
    assert client.get('/house/api/me').status_code == 200


def test_read_concurrency_and_formatting_release_camera_lock(house_client, monkeypatch):
    client, house = house_client, house_client.app.state.house
    late(client)
    entered, release = threading.Event(), threading.Event()
    original = house.image_size
    def slow_header(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(house, 'image_size', slow_header)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(client.get, '/house/api/week')
        assert entered.wait(5)
        try:
            assert house.store.lock.acquire(timeout=.5), 'JPEG I/O held the camera lock'
            house.store.lock.release()
            assert client.get('/house/api/week').status_code == 429
        finally:
            release.set()
        assert pending.result().status_code == 200


@pytest.mark.parametrize('empty', [False, True])
def test_failed_and_empty_jwks_have_attempt_cooldown(house_client, monkeypatch, empty):
    from urllib.error import URLError
    client, verifier = house_client, house_client.app.state.house.verifier
    calls, entered, release = [], threading.Event(), threading.Event()
    def fetch():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        if empty:
            return {'keys': []}
        raise URLError('offline')
    monkeypatch.setattr(verifier, 'fetch_jwks', fetch)
    with ThreadPoolExecutor() as pool:
        first = pool.submit(client.get, '/house/api/me')
        assert entered.wait(5)
        try:
            # A waiter must return before the refresh's network request completes.
            assert pool.submit(client.get, '/house/api/me').result(timeout=1).status_code == 503
        finally:
            release.set()
        assert first.result().status_code == (401 if empty else 503)
    for _ in range(6):
        assert client.get('/house/api/me').status_code == (401 if empty else 503)
    assert len(calls) == 1
    verifier.attempted_at -= 31
    assert client.get('/house/api/me').status_code == (401 if empty else 503)
    assert len(calls) == 2


def test_two_real_uvicorn_listeners_separate_trust_and_limiter_pools(tmp_path, monkeypatch):
    import socket

    import httpx
    import uvicorn
    monkeypatch.setenv('SUPABASE_URL', 'https://auth.example')
    monkeypatch.setenv('SUPABASE_JWT_SECRET', 'h' * 40)
    with socket.socket() as private, socket.socket() as tunnel:
        for sock in (private, tunnel):
            sock.bind(('127.0.0.1', 0))
            sock.listen()
        private_port, tunnel_port = private.getsockname()[1], tunnel.getsockname()[1]
        app = create_app(Config(tmp_path / 'listeners', password='operator', workers=False), house_tunnel_port=tunnel_port)
        person = app.state.store.add_resident('Alex')
        with app.state.store.conn:
            app.state.store.conn.execute('UPDATE residents SET auth_user_id=? WHERE id=?', ('user', person['id']))
        token = jwt.encode({'sub': 'user', 'iss': 'https://auth.example/auth/v1', 'aud': 'authenticated', 'exp': int(time.time()) + 3600}, 'h' * 40, algorithm='HS256')
        server = uvicorn.Server(uvicorn.Config(app, proxy_headers=False, log_level='critical'))
        with ThreadPoolExecutor() as pool:
            running = pool.submit(server.run, sockets=[private, tunnel])
            try:
                deadline = time.monotonic() + 5
                while not server.started and time.monotonic() < deadline:
                    time.sleep(.01)
                assert server.started
                with httpx.Client(base_url=f'http://127.0.0.1:{private_port}', trust_env=False) as tail, httpx.Client(base_url=f'http://127.0.0.1:{tunnel_port}', trust_env=False) as edge:
                    assert tail.get('/api/health').status_code == 200
                    assert edge.get('/api/health').status_code == 404
                    assert edge.post('/api/login', json={'password': 'operator'}).status_code == 404
                    assert tail.get('/api/health', headers={'Cf-Connecting-IP': '192.0.2.1'}).status_code == 403
                    assert edge.get('/house/api/me', headers={'Authorization': 'Bearer ' + token}).status_code == 200
                    body = {'token': 'x' * 43, 'password': 'synthetic-password'}
                    for i in range(5):
                        assert tail.post('/house/api/join', json=body, headers={'Cf-Connecting-IP': f'fake-{i}', 'X-Forwarded-For': f'192.0.2.{i}'}).status_code == 400
                    assert tail.post('/house/api/join', json=body, headers={'Cf-Connecting-IP': '192.0.2.99'}).status_code == 429
                    assert edge.post('/house/api/join', json=body, headers={'Cf-Connecting-IP': 'not-an-IP'}).status_code == 400
                    for i in range(5):
                        assert edge.post('/house/api/join', json=body, headers={'Cf-Connecting-IP': '192.0.2.1'}).status_code == 400
                    assert edge.post('/house/api/join', json=body, headers={'Cf-Connecting-IP': '192.0.2.1'}).status_code == 429
                    assert edge.post('/house/api/join', json=body, headers={'Cf-Connecting-IP': '2001:db8::1'}).status_code == 400
                    # Fill the isolated public capacity; a fresh writer is still admitted.
                    app.state.house.join_limiter.entries = {f'join:{i}': [time.monotonic()] for i in range(2048)}
                    assert edge.post('/house/api/join', json=body, headers={'Cf-Connecting-IP': '192.0.2.2'}).status_code == 429
                    assert tail.post('/house/api/visits/missing/name', json={'resident_id': person['id']}, headers={'Authorization': 'Bearer ' + token}).status_code == 404
                    assert edge.get('/house/api/login-names').status_code == 404
            finally:
                server.should_exit = True
                running.result(timeout=10)


def test_warm_jwks_key_does_not_wait_for_another_refresh(house_client):
    client, verifier = house_client, house_client.app.state.house.verifier
    assert client.get('/house/api/me').status_code == 200
    with verifier.lock:
        assert client.get('/house/api/me').status_code == 200


def test_media_predicate_cache_budget_and_live_expiry(house_client, monkeypatch):
    client, house = house_client, house_client.app.state.house
    item = late(client)
    eid = visit(client, [item])
    url = client.get('/house/api/week').json()['visits'][0]['filmstrip_url']
    assert client.get(url).status_code == 200
    # A replay uses the small predicate cache, not a league/catalog recomputation.
    from wash_dishes import house_visits
    catalog = house_visits.catalog
    monkeypatch.setattr(house_visits, 'catalog', lambda *a, **k: pytest.fail('Unnecessary media catalog read'))
    assert client.get(url).status_code == 200
    house.media_budget.per_identity = 2
    assert client.get(url).status_code == 429
    house.media_budget.rate.entries.clear()
    with house.store.conn:
        house.store.conn.execute('UPDATE evidence SET expires_at=? WHERE id=?', (stamp(utcnow() - timedelta(seconds=1)), eid))
    monkeypatch.setattr(house_visits, 'catalog', catalog)
    assert client.get(url).status_code == 404


def test_visit_correction_undo_rolls_back_all_members_after_later_decision(house_client):
    client, store = house_client, house_client.app.state.store
    items = [late(client), late(client, kind='bowl')]
    visit(client, items)
    assert client.post('/house/api/visits/trip-one/verdict', json={'verdict': 'not_dish'}).status_code == 200
    store.action(items[1]['id'], 'reopen')
    operator(client)
    assert client.post('/api/league/visits/trip-one/undo-verdict', json={}).status_code == 409
    assert store.obligation(items[0]['id'])['lifecycle'] == 'dismissed'
    assert store.conn.execute('SELECT count(*) FROM house_visit_verdicts').fetchone()[0] == 1
    assert store.conn.execute('SELECT count(*) FROM dish_reviews').fetchone()[0] == 1


def test_duplicate_visit_correction_rolls_back_if_later_member_has_no_target(house_client):
    client, store = house_client, house_client.app.state.store
    items = [late(client), late(client, kind='cup')]
    targets = [late(client), late(client, kind='bowl')]
    visit(client, items)
    visit(client, targets, 'target-trip')
    response = client.post('/house/api/visits/trip-one/verdict', json={'verdict': 'duplicate', 'of_visit_id': 'target-trip'})
    assert response.status_code == 400
    assert all(store.obligation(i['id'])['lifecycle'] == 'open' for i in items)
    assert store.conn.execute('SELECT count(*) FROM dish_reviews').fetchone()[0] == 0
    assert store.conn.execute('SELECT count(*) FROM house_verdicts').fetchone()[0] == 0
