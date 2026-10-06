import hashlib

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.gateway.routes import create_app
from app.gateway.security import COOKIE, LocalIdentity, hash_password, verify_password
from app.storage.db import Store


@pytest.fixture(scope="module")
def password_hash():
    return hash_password("test-only-secret")


def test_pbkdf2_salted_hash_and_malformed_rejection(password_hash):
    assert verify_password("test-only-secret", password_hash)
    assert not verify_password("wrong", password_hash)
    assert hash_password("test-only-secret") != password_hash
    for malformed in ["plaintext", "pbkdf2_sha256$999999999$00$00", "pbkdf2_sha256$600000$xx$xx"]:
        assert not verify_password("anything", malformed)
        with pytest.raises(ValueError):
            LocalIdentity(malformed)


def test_session_expiry_logout_restart_and_token_not_stored_plain(password_hash):
    now = [0]
    identity = LocalIdentity(password_hash, ttl_seconds=60, clock=lambda: now[0])
    token = identity.login("test-only-secret", "local")
    assert identity.authenticated(token) and token not in identity.sessions
    assert hashlib.sha256(token.encode()).hexdigest() in identity.sessions
    assert not LocalIdentity(password_hash).authenticated(token)
    identity.logout(token)
    assert not identity.authenticated(token)
    token = identity.login("test-only-secret", "local")
    now[0] = 60
    assert not identity.authenticated(token)


def test_login_rate_limit_is_bounded_and_recovers(password_hash):
    now = [0]
    identity = LocalIdentity(password_hash, clock=lambda: now[0])
    for _ in range(5):
        with pytest.raises(HTTPException) as error:
            identity.login("wrong", "peer")
        assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        identity.login("test-only-secret", "peer")
    assert error.value.status_code == 429
    now[0] = 61
    assert identity.authenticated(identity.login("test-only-secret", "peer"))


def test_api_identity_cookie_csrf_and_independent_consent(password_hash):
    store = Store()
    settings = Settings(local_auth_enabled=True, local_auth_password_hash=password_hash)
    with TestClient(create_app(settings, store=store)) as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/login"
        assert client.get("/login").status_code == 200
        assert client.get("/api/events").status_code == 401
        assert client.get("/api/governance/hard-rules").status_code == 401
        assert client.get("/api/auth/status").json()["learner_id"] is None
        result = client.post("/api/auth/login", json={"password": "test-only-secret"})
        assert result.status_code == 200
        cookie = result.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert client.get("/api/auth/status").json()["authenticated"]
        # Identity does not silently grant teaching consent.
        assert client.get("/api/events").status_code == 403
        assert client.get("/api/governance/hard-rules").status_code == 200
        assert client.post("/api/auth/logout", headers={"Origin": "https://attacker.example"}).status_code == 403
        assert client.post("/api/auth/logout", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert client.post("/api/auth/logout", headers={"Origin": "http://testserver"}).status_code == 200
        assert client.get("/api/governance/hard-rules").status_code == 401
    store.close()


def test_disabled_mode_and_secure_cookie(password_hash):
    store = Store()
    with TestClient(create_app(Settings(), store=store)) as client:
        assert client.get("/api/governance/hard-rules").status_code == 200
        assert not client.get("/api/auth/status").json()["enabled"]
        assert client.post("/api/auth/login", json={"password": "test"}).status_code == 409
    settings = Settings(local_auth_enabled=True, local_auth_password_hash=password_hash, local_auth_cookie_secure=True)
    with TestClient(create_app(settings, store=store), base_url="https://testserver") as client:
        response = client.post("/api/auth/login", json={"password": "test-only-secret"})
        assert "Secure" in response.headers["set-cookie"]
        assert client.cookies.get(COOKIE)
    store.close()


def test_auth_enabled_without_hash_fails_closed():
    store = Store()
    with pytest.raises(ValueError, match="PBKDF2"):
        create_app(Settings(local_auth_enabled=True), store=store)
    store.close()
