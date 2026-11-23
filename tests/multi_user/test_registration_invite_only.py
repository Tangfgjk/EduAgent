"""M5 regression — first user becomes admin atomically; concurrent races safe."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


def test_first_save_user_promotes_to_admin(mu_isolated_root):
    from deeptutor.multi_user.identity import list_user_info, save_user

    save_user("alice", "$2b$12$placeholder", role="user")
    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "admin"


def test_second_save_user_keeps_user_role(mu_isolated_root):
    from deeptutor.multi_user.identity import list_user_info, save_user

    save_user("alice", "$2b$12$placeholder", role="user")
    save_user("bob", "$2b$12$placeholder", role="user")
    users = {u["username"]: u for u in list_user_info()}
    assert users["alice"]["role"] == "admin"
    assert users["bob"]["role"] == "user"


def test_concurrent_first_save_only_one_admin(mu_isolated_root):
    """``_USERS_WRITE_LOCK`` must serialise read-modify-write so only one
    concurrent first-time registration can flip the empty-store branch."""
    from deeptutor.multi_user.identity import list_user_info, save_user

    def _save(name):
        try:
            save_user(name, "$2b$12$placeholder", role="user")
            return True
        except Exception:
            return False

    names = [f"u{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_save, names))

    users = list_user_info()
    admins = [u for u in users if u["role"] == "admin"]
    assert len(admins) == 1
    assert len(users) == 8


def test_create_only_registration_never_replaces_existing_credentials(mu_isolated_root):
    from deeptutor.multi_user.identity import load_users, save_user

    save_user("admin", "admin-hash", role="admin")

    def register():
        try:
            save_user("student", "student-hash", create_only=True)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: register(), range(4)))
    assert sum(results) == 1
    assert load_users()["student"]["role"] == "user"


@pytest.mark.asyncio
async def test_self_registration_after_admin_exists(mu_isolated_root, monkeypatch):
    from fastapi import FastAPI
    import httpx

    from deeptutor.api.routers import auth
    from deeptutor.multi_user.identity import load_users, save_user

    save_user("admin", "admin-hash", role="admin")
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth, "PRIVATE_LOGIN_HOSTS", frozenset())
    app = FastAPI()
    app.include_router(auth.router, prefix="/auth")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/auth/register", json={"username": "student", "password": "student123"}
        )
        assert result.status_code == 201
        assert result.json()["role"] == "user"
        original = load_users()["student"]["hash"]
        duplicate = await client.post(
            "/auth/register", json={"username": "student", "password": "changed123"}
        )
        assert duplicate.status_code == 409
        assert load_users()["student"]["hash"] == original
