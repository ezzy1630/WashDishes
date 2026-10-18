from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path


class Access:
    """Random, expiring sessions; the household key never travels in a cookie."""

    def __init__(self, root: Path, password: str | None = None, demo: bool = False):
        self.root = root
        self.sessions: dict[str, tuple[float, str]] = {}
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.RLock()
        path = root / "access.json"
        if demo or password:
            self.salt = secrets.token_bytes(16)
            self.digest = self._hash("demo" if demo else password)
        elif path.exists():
            saved = json.loads(path.read_text())
            self.salt = bytes.fromhex(saved["salt"])
            self.digest = bytes.fromhex(saved["digest"])
        else:
            key = secrets.token_urlsafe(24)
            self.salt = secrets.token_bytes(16)
            self.digest = self._hash(key)
            path.write_text(json.dumps({"salt": self.salt.hex(), "digest": self.digest.hex()}))
            path.chmod(0o600)
            key_path = root / "access-key"
            key_path.write_text(key + "\n")
            key_path.chmod(0o600)

    def _hash(self, value: str) -> bytes:
        return hashlib.scrypt(value.encode(), salt=self.salt, n=16384, r=8, p=1)

    def login(self, password: str, client: str) -> tuple[str, str]:
        now = time.time()
        with self.lock:
            recent = [t for t in self.failures.get(client, []) if t > now - 300]
            if len(recent) >= 10:
                raise ValueError("Too many attempts. Wait five minutes before trying again.")
            if len(password) > 256 or not hmac.compare_digest(self._hash(password), self.digest):
                self.failures[client] = [*recent, now]
                raise PermissionError("The access key is incorrect.")
            self.failures.pop(client, None)
            self.sessions = {key: value for key, value in self.sessions.items() if value[0] > now}
            if len(self.sessions) >= 100:
                oldest = min(self.sessions, key=lambda key: self.sessions[key][0])
                self.sessions.pop(oldest)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self.sessions[token] = (now + 12 * 3600, csrf)
            return token, csrf

    def session(self, token: str | None) -> str | None:
        with self.lock:
            saved = self.sessions.get(token or "")
            if saved and saved[0] > time.time():
                return saved[1]
            self.sessions.pop(token or "", None)
            return None

    def logout(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)
