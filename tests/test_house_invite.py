"""A shared invite grants first-time enrollment, never password-reset authority."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import HTTPException
from test_house_api import house_client as house_client
from test_house_api import operator

from wash_dishes.house import HouseJoin
from wash_dishes.store import stamp, utcnow


def invite(client):
    operator(client)
    result = client.post('/api/league/house-invite', json={})
    assert result.status_code == 200, result.text
    return result.json()['url'].split('#')[1]


def test_one_invite_enrolls_multiple_people_without_exposing_or_consuming_token(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    people = [store.add_resident(name) for name in ('New One', 'New Two')]
    calls = []
    def account(resident, slug, password):
        assert resident['auth_user_id'] is None
        calls.append((resident['id'], slug))
        return 'joined-' + slug
    monkeypatch.setattr(client.app.state.house.admin, 'account', account)
    token = invite(client)
    client.headers.pop('Authorization')
    client.cookies.clear()
    assert client.post('/api/league/house-invite', json={}).status_code == 401
    assert client.post('/house/api/join-options', json={'token': 'invalid' * 5}).status_code == 410
    data = client.post('/house/api/join-options', json={'token': token}).json()
    assert len(data['residents']) == 5
    assert sum(r['has_account'] for r in data['residents']) == 3
    for person, slug in zip(people, ('new-one', 'new-two')):
        body = {'token': token, 'resident_id': person['id'], 'password': 'new-password'}
        result = client.post('/house/api/join-house', json=body)
        assert result.status_code == 200 and result.json() == {'login': slug}
        assert client.post('/house/api/join-house', json=body).status_code == 409
    assert len(calls) == 2
    assert all(r['has_account'] for r in client.post('/house/api/join-options', json={'token': token}).json()['residents'])
    assert token not in json.dumps(store.activity())
    assert token not in json.dumps([dict(r) for r in store.conn.execute('SELECT * FROM house_invite')])


def test_shared_invite_refuses_existing_unknown_archived_and_stale_personal_links(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    person = store.add_resident('Available')
    archived = store.add_resident('Archived')
    store.archive_resident(archived['id'])
    token = invite(client)
    personal = client.post(f"/api/league/residents/{person['id']}/join-link", json={}).json()['url'].split('#')[1]
    calls = []
    monkeypatch.setattr(client.app.state.house.admin, 'account', lambda *args: calls.append(args) or 'new-user')
    for resident_id, status in [(client.people[0]['id'], 409), (9999, 400), (archived['id'], 400)]:
        result = client.post('/house/api/join-house', json={'token': token, 'resident_id': resident_id, 'password': 'new-password'})
        assert result.status_code == status
    assert not calls
    assert client.post('/house/api/join-house', json={'token': token, 'resident_id': person['id'], 'password': 'new-password'}).status_code == 200
    assert client.post('/house/api/join', json={'token': personal, 'password': 'reset-password'}).status_code == 400
    assert len(calls) == 1
    # The separate operator-generated reset still works after enrollment.
    personal = client.post(f"/api/league/residents/{person['id']}/join-link", json={}).json()['url'].split('#')[1]
    assert client.post('/house/api/join', json={'token': personal, 'password': 'reset-password'}).status_code == 200
    assert calls[-1][0]['auth_user_id'] == 'new-user'


def test_invite_rotation_expiry_and_upstream_failure_leave_accounts_unchanged(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    person = store.add_resident('Retry Invite')
    old, token = invite(client), invite(client)
    body = {'token': token, 'resident_id': person['id'], 'password': 'new-password'}
    assert client.post('/house/api/join-options', json={'token': old}).status_code == 410
    assert client.post('/house/api/join-house', json=body | {'token': old}).status_code == 410
    def fail(*args):
        raise HTTPException(503, 'Try again')
    monkeypatch.setattr(client.app.state.house.admin, 'account', fail)
    assert client.post('/house/api/join-house', json=body).status_code == 503
    assert store._resident(person['id'])['auth_user_id'] is None
    monkeypatch.setattr(client.app.state.house.admin, 'account', lambda *args: 'retry-user')
    with store.lock, store.conn:
        store.conn.execute('UPDATE house_invite SET expires_at=?', (stamp(utcnow() - timedelta(seconds=1)),))
    assert client.post('/house/api/join-options', json={'token': token}).status_code == 410
    assert client.post('/house/api/join-house', json=body).status_code == 410
    token = invite(client)
    assert client.post('/house/api/join-house', json=body | {'token': token}).status_code == 200


def test_shared_signup_serializes_claims_without_blocking_camera(house_client, monkeypatch):
    client, store, house = house_client, house_client.app.state.store, house_client.app.state.house
    person = store.add_resident('Concurrent')
    token = invite(client)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def account(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)
        return 'concurrent-user'
    monkeypatch.setattr(house.admin, 'account', account)
    body = HouseJoin(token=token, resident_id=person['id'], password='new-password')
    def join():
        try:
            return house.join_house(body)
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(join)
        assert entered.wait(2)
        second = pool.submit(join)
        assert store.residents()  # Network IO must leave the camera store available.
        release.set()
        assert first.result() == {'login': 'concurrent'}
        assert second.result() == 409
    assert len(calls) == 1


def test_whole_house_can_join_from_the_same_network(house_client, monkeypatch):
    client, store = house_client, house_client.app.state.store
    people = [store.add_resident(f'Housemate {i}') for i in range(10)]
    token = invite(client)
    monkeypatch.setattr(client.app.state.house.admin, 'account', lambda resident, *args: f"new-{resident['id']}")
    for person in people:
        assert client.post('/house/api/join-house', json={'token': token, 'resident_id': person['id'], 'password': 'new-password'}).status_code == 200


@pytest.mark.parametrize('mode', ['demo', 'snapshot'])
def test_group_enrollment_unavailable_in_nonlive_modes(house_client, mode):
    client = house_client
    token = invite(client)
    client.app.state.config = replace(client.app.state.config, **{mode: True})
    assert client.post('/house/api/join-options', json={'token': token}).status_code == 403
    assert client.post('/house/api/join-house', json={'token': token, 'resident_id': 1, 'password': 'new-password'}).status_code == 403
