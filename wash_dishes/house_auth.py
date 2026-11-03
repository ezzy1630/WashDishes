"""Supabase JWT verification and admin calls; secrets never enter dashboard payloads."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import jwt
from fastapi import HTTPException


@dataclass(frozen=True)
class HouseConfig:
    supabase_url: str = ''
    secret_key: str = ''
    jwt_secret: str = ''
    site_origin: str = ''
    public_origin: str = ''

    @classmethod
    def load(cls, root: Path) -> HouseConfig:
        path = root / 'house-settings.json'
        saved = json.loads(path.read_text()) if path.exists() else {}
        if path.exists():
            path.chmod(0o600)
        names = {'supabase_url': 'SUPABASE_URL', 'secret_key': 'SUPABASE_SECRET_KEY',
                 'jwt_secret': 'SUPABASE_JWT_SECRET', 'site_origin': 'HOUSE_SITE_ORIGIN',
                 'public_origin': 'HOUSE_PUBLIC_ORIGIN'}
        values = {key: os.getenv(env, saved.get(env, '')) for key, env in names.items()}
        if any(not isinstance(value, str) for value in values.values()):
            raise ValueError('House settings must be strings.')
        for key in ('supabase_url', 'site_origin', 'public_origin'):
            values[key] = values[key].rstrip('/')
            value = values[key]
            parsed = urlsplit(value)
            if value and (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                          or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
                raise ValueError(f'{names[key]} must be an HTTPS origin with no path or credentials.')
        return cls(**values)


def request_json(url: str, *, method: str = 'GET', headers: dict | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=data, method=method, headers={'Content-Type': 'application/json', **(headers or {})})
    with urlopen(request, timeout=8) as response:
        content = response.read(256 * 1024 + 1)
    if len(content) > 256 * 1024:
        raise ValueError('Authentication response is too large.')
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError('Invalid authentication response.')
    return result


class JWTVerifier:
    def __init__(self, config: HouseConfig):
        self.config = config
        self.lock = threading.Lock()
        self.keys: list[dict] = []
        self.fetched_at = 0.0

    def fetch_jwks(self) -> dict:
        return request_json(f'{self.config.supabase_url}/auth/v1/.well-known/jwks.json')

    def subject(self, token: str) -> str:
        if not self.config.supabase_url:
            raise HTTPException(503, 'House sign-in has not been configured yet.')
        if len(token) > 16384:
            raise HTTPException(401, 'Sign in again to continue.')
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get('alg')
            if algorithm == 'HS256' and self.config.jwt_secret:
                key = self.config.jwt_secret
            elif algorithm in ('ES256', 'RS256'):
                kid = header.get('kid')
                if not isinstance(kid, str) or not 1 <= len(kid) <= 200:
                    raise jwt.InvalidTokenError()
                with self.lock:
                    elapsed = time.monotonic() - self.fetched_at
                    match = next((k for k in self.keys if k.get('kid') == kid), None)
                    # Refresh on rotation, but random kids cannot force a network request per attempt.
                    if not self.keys or elapsed >= 600 or (match is None and elapsed >= 30):
                        payload = self.fetch_jwks()
                        if not isinstance(payload.get('keys'), list):
                            raise ValueError('Invalid JWKS.')
                        self.keys = [k for k in payload['keys'] if isinstance(k, dict)]
                        self.fetched_at = time.monotonic()
                        match = next((k for k in self.keys if isinstance(k, dict) and k.get('kid') == kid), None)
                    if not match or match.get('kty') != ('EC' if algorithm == 'ES256' else 'RSA'):
                        raise jwt.InvalidTokenError()
                    if algorithm == 'ES256' and match.get('crv') != 'P-256':
                        raise jwt.InvalidTokenError()
                    if match.get('alg', algorithm) != algorithm or match.get('use', 'sig') != 'sig':
                        raise jwt.InvalidTokenError()
                    key = jwt.PyJWK.from_dict(match, algorithm=algorithm).key
            else:
                raise jwt.InvalidTokenError()
            claims = jwt.decode(token, key, algorithms=[algorithm], audience='authenticated',
                                issuer=f'{self.config.supabase_url}/auth/v1',
                                options={'require': ['sub', 'aud', 'exp', 'iss']})
            if not isinstance(claims['sub'], str) or not claims['sub']:
                raise jwt.InvalidTokenError()
            return claims['sub']
        except (HTTPError, URLError, OSError):
            raise HTTPException(503, 'House sign-in is temporarily unavailable. Try again shortly.') from None
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise HTTPException(401, 'Sign in again to continue.') from None


class SupabaseAdmin:
    def __init__(self, config: HouseConfig):
        self.config = config

    def account(self, resident: dict, slug: str, password: str) -> str:
        config = self.config
        if not config.supabase_url or not config.secret_key:
            raise HTTPException(503, 'House sign-in has not been configured yet.')
        headers = {'apikey': config.secret_key}
        # Legacy service-role JWTs need Bearer; new sb_secret keys are API keys, not JWTs.
        if config.secret_key.startswith('eyJ'):
            headers['Authorization'] = f'Bearer {config.secret_key}'
        auth_id = resident.get('auth_user_id')
        url = f'{config.supabase_url}/auth/v1/admin/users'
        body = {'password': password}
        if auth_id:
            from urllib.parse import quote
            url += '/' + quote(auth_id, safe='')
        else:
            body.update(email=f'{slug}@highest-house.ezzyrappeport.com', email_confirm=True)
        try:
            result = request_json(url, method='PUT' if auth_id else 'POST', headers=headers, body=body)
            if not isinstance(result.get('id'), str) or not result['id']:
                raise ValueError('Missing account id.')
            if auth_id and result['id'] != auth_id:
                raise ValueError('Account mismatch.')
            return result['id']
        except (HTTPError, URLError, OSError, ValueError, KeyError):
            # Do not reflect upstream bodies: they may contain credentials or personal information.
            raise HTTPException(503, 'Could not save your password. Your link still works; try again shortly.') from None
