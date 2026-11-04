import asyncio
import os
import asyncio
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from test_house_api import house_client  # noqa: F401


@pytest.fixture
def recordings(request, monkeypatch):
    client = request.getfixturevalue("house_client")
    footage = client.app.state.house.footage
    end = time.time() - 120
    for i in range(4):
        path = client.app.state.media.buffer / f'1793747900984577979-{i:06}.ts'
        path.write_bytes(b'recorded-video')
        os.utime(path, (end + 10 * i, end + 10 * i))
    monkeypatch.setattr(footage, 'probe', lambda s: 10)
    calls = []

    def remux(command, **kwargs):
        calls.append(command)
        time.sleep(0.02)
        from pathlib import Path
        Path(command[-1]).write_bytes(b'0123456789' * 100)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr('wash_dishes.house_footage.subprocess.run', remux)
    return client, footage, end, calls


def capability(client, end):
    response = client.get('/house/api/footage/chunk', params={'at': end - 2})
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()['url'])
    return url.path + '?' + url.query


def test_household_login_required_but_signed_chunk_works_without_headers(recordings):
    client, footage, end, _ = recordings
    url = capability(client, end)
    client.headers.pop('Authorization')
    assert client.get('/house/api/footage').status_code == 401
    assert client.get('/house/api/footage/chunk', params={'at': end}).status_code == 401
    assert client.get(url.split('?')[0]).status_code == 400
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['accept-ranges'] == 'bytes'
    assert 'Authorization' not in url
    assert footage.cache


def test_signed_chunk_is_exact_expiring_and_member_bound(recordings):
    client, footage, end, _ = recordings
    url = capability(client, end)
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    for change in ({'uid': '999'}, {'sig': '0' * 64}, {'exp': str(int(time.time()) - 1)}):
        assert client.get(parts.path + '?' + urlencode(query | change)).status_code == 403
    expired = int(time.time()) - 1
    chunk = parts.path.rsplit('/', 1)[1][:-4]
    assert client.get(parts.path, params=query | {'exp': expired, 'sig': footage.signature(chunk, int(query['uid']), expired)}).status_code == 403
    assert client.get(url + '&path=/etc/passwd').status_code == 400
    assert client.get(parts.path.replace(chunk, 'a' * 32) + '?' + parts.query).status_code == 403
    client.app.state.store.archive_resident(client.people[0]['id'])
    assert client.get(url).status_code == 403


def test_range_head_suffix_and_unsatisfiable(recordings):
    client, _, end, _ = recordings
    url = capability(client, end)
    for value, content in [('bytes=20-29', b'0123456789'), ('bytes=-5', b'56789'), ('bytes=997-', b'789')]:
        response = client.get(url, headers={'Range': value})
        assert response.status_code == 206
        assert response.content == content
        assert response.headers['content-range'].endswith('/1000')
    assert client.get(url, headers={'Range': 'bytes=1000-'}).status_code == 416
    response = client.head(url, headers={'Range': 'bytes=0-1'})
    assert response.status_code == 206 and response.content == b''
    assert response.headers['content-length'] == '2'


def test_all_retained_footage_without_dish_or_week_visibility(recordings):
    client, _, end, _ = recordings
    # No dishes exist. Any authenticated housemate can navigate all retained segments.
    index = client.get('/house/api/footage').json()
    assert index['ranges'] == [{'start': end - 10, 'end': end + 20}]
    first = client.get('/house/api/footage/chunk', params={'at': end - 2}).json()
    assert first['previous'] is None and first['next'] == pytest.approx(end + 9.9)
    last = client.get('/house/api/footage/chunk', params={'at': end + 19}).json()
    assert last['next'] is None
    assert client.get('/house/api/footage/chunk', params={'at': end - 7200}).status_code == 410
    assert client.get('/house/api/footage/chunk', params={'at': end + 30}).status_code == 410


def test_retention_and_symlinks_rechecked_even_after_remux(recordings, tmp_path):
    client, footage, end, _ = recordings
    url = capability(client, end)
    assert client.get(url).status_code == 200
    source = footage.segments[0].path
    outside = tmp_path / 'outside.ts'
    source.rename(outside)
    source.symlink_to(outside)
    assert client.get(url).status_code == 410
    source.unlink()
    assert client.get(url).status_code == 410
    assert client.get('/house/footage/%2e%2e%2foutside.mp4').status_code in (400, 404)
    assert client.get('/house/api/footage', params={'path': '../outside.ts'}).status_code == 400


def test_ten_simultaneous_viewers_share_remux_without_count_admission(recordings):
    client, _, end, calls = recordings
    url = capability(client, end)
    barrier = threading.Barrier(10)

    def watch(_):
        barrier.wait(timeout=5)
        return client.get(url, headers={'Range': 'bytes=0-199'}).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert list(pool.map(watch, range(10))) == [206] * 10
    assert len(calls) == 1
    assert '-an' in calls[0] and calls[0][calls[0].index('-c:v') + 1] == 'copy'


def test_aborted_transfer_releases_shared_file_pin(tmp_path):
    import anyio

    from wash_dishes.house_footage import Playback

    path = tmp_path / 'chunk.mp4'
    path.write_bytes(b'recorded-video')
    released = []
    response = Playback(path, lambda: released.append(True))

    async def transfer():
        with anyio.CancelScope() as disconnect:
            async def send(message):
                disconnect.cancel()
                await anyio.lowlevel.checkpoint()

            async def receive():
                return {'type': 'http.disconnect'}

            await response({'type': 'http', 'method': 'GET', 'headers': [], 'asgi': {'spec_version': '2.4'}}, receive, send)

    anyio.run(transfer)
    assert released == [True]


def test_full_api_gate_never_refuses_footage():
    """Ten viewers preparing clips must not be turned away by the page-read gate."""
    from wash_dishes.house import HouseBoundary

    async def app(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    async def status(boundary, path):
        sent = []

        async def send(message):
            sent.append(message)

        scope = {'type': 'http', 'method': 'GET', 'path': path, 'headers': [], 'server': ('127.0.0.1', 8788)}
        await boundary(scope, None, send)
        return sent[0]['status']

    async def run():
        boundary = HouseBoundary(app, 'https://site.example', 8788)
        boundary.workers = asyncio.Semaphore(0)
        return [await status(boundary, path) for path in
                ('/house/footage/abc.mp4', '/house/api/footage', '/house/api/footage/chunk', '/house/api/week')]

    assert asyncio.run(run()) == [200, 200, 200, 429]
