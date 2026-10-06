"""Opt-in single-user identity; not a multi-tenant authorization system."""
from __future__ import annotations

from collections import deque
import hashlib
import hmac
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

COOKIE = "eduagent_local_session"
ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        iterations = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 2_000_000:
            return False
        salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt_bytes) < 16 or len(expected_bytes) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, iterations)
        return hmac.compare_digest(actual, expected_bytes)
    except (ValueError, TypeError):
        return False


class LocalIdentity:
    def __init__(self, password_hash: str, *, ttl_seconds: int = 28800, clock=time.monotonic):
        if not 60 <= ttl_seconds <= 86400:
            raise ValueError("Local auth session expiry must be between 60 and 86400 seconds")
        try:
            algorithm, rounds, salt, digest = password_hash.split("$")
            valid_hash = algorithm == "pbkdf2_sha256" and 100_000 <= int(rounds) <= 2_000_000 and len(bytes.fromhex(salt)) >= 16 and len(bytes.fromhex(digest)) == 32
        except (TypeError, ValueError):
            valid_hash = False
        if not valid_hash:
            raise ValueError("Local authentication requires a PBKDF2 password hash")
        self.password_hash, self.ttl_seconds, self.clock = password_hash, ttl_seconds, clock
        self.sessions: dict[str, float] = {}
        self.attempts: dict[str, deque] = {}
        self.lock = threading.RLock()

    def _prune(self, now):
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        self.attempts = {key: queue for key, queue in self.attempts.items() if queue and queue[-1] > now - 60}

    def login(self, password: str, peer: str) -> str:
        with self.lock:
            now = self.clock()
            self._prune(now)
            # Server-observed peer only; untrusted forwarded headers are ignored.
            queue = self.attempts.setdefault(peer, deque())
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= 5:
                raise HTTPException(429, "Login rate limit exceeded")
            queue.append(now)
            if not verify_password(password, self.password_hash):
                raise HTTPException(401, "Invalid local password")
            token = secrets.token_urlsafe(32)
            if len(self.sessions) >= 128:
                self.sessions.pop(min(self.sessions, key=self.sessions.get))
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + self.ttl_seconds
            return token

    def authenticated(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            self._prune(self.clock())
            return hashlib.sha256(token.encode()).hexdigest() in self.sessions

    def logout(self, token: str | None):
        if token:
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def install_local_identity(app, settings):
    enabled = settings.local_auth_enabled
    identity = LocalIdentity(settings.local_auth_password_hash, ttl_seconds=settings.local_auth_session_hours * 3600) if enabled else None
    app.state.local_identity = identity

    @app.middleware("http")
    async def identity_boundary(request: Request, call_next):
        path = request.url.path
        if enabled:
            if request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith("/api/"):
                origin = request.headers.get("origin")
                cross_site = request.headers.get("sec-fetch-site") == "cross-site"
                supplied = urlsplit(origin) if origin else None
                if cross_site or (supplied and (supplied.netloc != request.url.netloc or supplied.scheme != request.url.scheme)):
                    return JSONResponse({"detail": "Cross-origin mutation blocked"}, status_code=403)
            authenticated = identity.authenticated(request.cookies.get(COOKIE))
            if path.startswith("/api/") and path not in {"/api/auth/login", "/api/auth/status"} and not authenticated:
                return JSONResponse({"detail": "Local login required"}, status_code=401)
            if path in {"/", "/prototype", "/learning"} and not authenticated:
                return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(Path(__file__).resolve().parents[2] / "web" / "login.html")

    @app.get("/api/auth/status")
    def status(request: Request):
        authenticated = bool(identity and identity.authenticated(request.cookies.get(COOKIE)))
        return {"enabled": enabled, "authenticated": authenticated,
                "learner_id": settings.learner_id if authenticated else None,
                "mode": "local-single-user"}

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request):
        if not identity:
            raise HTTPException(409, "Local authentication is disabled")
        token = identity.login(body.password, request.client.host if request.client else "unknown")
        response = JSONResponse({"authenticated": True, "learner_id": settings.learner_id})
        response.set_cookie(COOKIE, token, max_age=identity.ttl_seconds, httponly=True,
                            secure=settings.local_auth_cookie_secure, samesite="strict", path="/")
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        if identity:
            identity.logout(request.cookies.get(COOKIE))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict", secure=settings.local_auth_cookie_secure)
        return response
