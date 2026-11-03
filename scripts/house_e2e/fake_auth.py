"""Loopback-only GoTrue subset. Never use these credentials outside this harness."""
from __future__ import annotations

import json
import secrets
import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response


class FakeAuth:
    def __init__(self, origin: str, site: str):
        self.origin = origin
        self.admin_key = secrets.token_urlsafe(32)
        self.anon_key = "sb_publishable_house_e2e_local_only"
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "house-e2e-local-p256"
        self.users: dict[str, dict] = {}
        self.passwords: dict[str, str] = {}
        self.refreshes: dict[str, str] = {}
        self.revoked: set[str] = set()
        self.calls: list[dict] = []
        self.app = FastAPI()
        self.app.add_middleware(CORSMiddleware, allow_origins=[site], allow_methods=["*"], allow_headers=["*"])
        self.app.add_api_route("/auth/v1/{path:path}", self.handle, methods=["GET", "POST", "PUT", "DELETE"])

    def token(self, user: dict, *, seconds: int = 3600, audience: str = "authenticated") -> str:
        now = int(time.time())
        return jwt.encode({"aud": audience, "iss": self.origin + "/auth/v1", "sub": user["id"],
                           "email": user["email"], "role": "authenticated", "iat": now,
                           "exp": now + seconds, "session_id": str(uuid.uuid4()),
                           "app_metadata": user["app_metadata"], "user_metadata": user["user_metadata"]},
                          self.key, algorithm="ES256", headers={"kid": self.kid})

    def session(self, user: dict) -> dict:
        refresh = secrets.token_urlsafe(32)
        self.refreshes[refresh] = user["id"]
        return {"access_token": self.token(user), "token_type": "bearer", "expires_in": 3600,
                "expires_at": int(time.time()) + 3600, "refresh_token": refresh, "user": user}

    def current(self, request: Request) -> dict:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(token, self.key.public_key(), algorithms=["ES256"],
                                audience="authenticated", issuer=self.origin + "/auth/v1")
            if token in self.revoked:
                raise ValueError("signed out")
            return self.users[claims["sub"]]
        except (jwt.PyJWTError, KeyError, ValueError):
            raise HTTPException(401, "Invalid or expired access token") from None

    async def handle(self, request: Request, path: str):
        self.calls.append({"method": request.method, "path": path})
        if path == ".well-known/jwks.json" and request.method == "GET":
            jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.key.public_key()))
            return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "ES256"}]}
        if path == "settings" and request.method == "GET":
            return {"external": {"email": True}, "disable_signup": True, "mailer_autoconfirm": True,
                    "phone_autoconfirm": False, "sms_provider": ""}
        body = await request.json() if request.method in ("POST", "PUT") and await request.body() else {}
        if path.startswith("admin/users"):
            if request.headers.get("apikey") != self.admin_key:
                raise HTTPException(403, "Local service role required")
            if path == "admin/users" and request.method == "POST":
                if any(u["email"] == body["email"] for u in self.users.values()):
                    raise HTTPException(422, "User already exists")
                uid = str(uuid.uuid5(uuid.NAMESPACE_URL, "house-e2e:" + body["email"]))
                user = {"id": uid, "aud": "authenticated", "role": "authenticated", "email": body["email"],
                        "email_confirmed_at": "2026-01-01T00:00:00Z", "confirmed_at": "2026-01-01T00:00:00Z",
                        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
                        "app_metadata": {"provider": "email", "providers": ["email"], **body.get("app_metadata", {})},
                        "user_metadata": body.get("user_metadata", {}), "identities": []}
                self.users[uid] = user
                self.passwords[uid] = body.get("password", "")
                return user
            uid = path.removeprefix("admin/users/")
            if request.method == "PUT" and uid in self.users:
                if "password" in body:
                    self.passwords[uid] = body["password"]
                return self.users[uid]
            raise HTTPException(404, "Unknown local user")
        if path == "token" and request.method == "POST":
            if request.query_params.get("grant_type") == "password":
                user = next((u for u in self.users.values() if u["email"] == body.get("email")), None)
                if not user or not secrets.compare_digest(self.passwords[user["id"]], body.get("password", "")):
                    raise HTTPException(400, "Invalid login credentials")
            elif request.query_params.get("grant_type") == "refresh_token":
                uid = self.refreshes.pop(body.get("refresh_token", ""), None)
                if not uid:
                    raise HTTPException(400, "Invalid refresh token")
                user = self.users[uid]
            else:
                raise HTTPException(400, "Unsupported grant type")
            return self.session(user)
        if path == "user" and request.method == "GET":
            return self.current(request)
        if path == "logout" and request.method == "POST":
            user = self.current(request)
            self.revoked.add(request.headers["authorization"].removeprefix("Bearer "))
            self.refreshes = {t: uid for t, uid in self.refreshes.items() if uid != user["id"]}
            return Response(status_code=204)
        raise HTTPException(404, "Unimplemented fake Auth endpoint: " + path)
