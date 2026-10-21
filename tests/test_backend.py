from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as sentinel
from storage import Storage


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # This is deliberately an isolated test database, not DATABASE_URL and never
    # any uploaded/legacy sentinel.db file.
    test_store = Storage(f"sqlite:///{tmp_path / 'isolated-test.db'}")
    test_store.init_schema()
    monkeypatch.setattr(sentinel, "db", test_store)
    monkeypatch.setattr(sentinel, "DB_ERROR", "")
    sentinel.runtime.clear()

    async def fake_exe(method, path, payload=None, token=None):
        if path == "/auth/login":
            return {"access_token": "mock-access-token", "refresh_token": "mock-refresh-token"}
        if path == "/auth/forgot-password":
            return {"ok": True, "email": payload["email"]}
        if path == "/auth/logout":
            return {"ok": True}
        raise AssertionError(f"unexpected EXE request: {method} {path}")

    async def fake_verify(token):
        assert token == "mock-access-token"
        return {"id": "exe-user-1", "name": "Sentinel Tester", "email": "tester@example.com", "avatar_url": ""}

    monkeypatch.setattr(sentinel, "exe", fake_exe)
    monkeypatch.setattr(sentinel, "verify_access", fake_verify)
    with TestClient(sentinel.app) as test_client:
        yield test_client, test_store


def test_auth_session_and_dashboard_gate(client):
    browser, _ = client
    assert browser.get("/sentinel/api/health").status_code == 200
    assert browser.get("/sentinel/Dashboard", follow_redirects=False).status_code == 303

    denied = browser.post("/sentinel/api/auth/login", json={"email": "tester@example.com", "password": "password"})
    assert denied.status_code == 403

    logged_in = browser.post(
        "/sentinel/api/auth/login",
        headers={"X-Sentinel-Request": "1"},
        json={"email": "tester@example.com", "password": "password"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json() == {"ok": True, "user": {"id": "exe-user-1", "name": "Sentinel Tester", "email": "tester@example.com"}}
    assert "httponly" in logged_in.headers["set-cookie"].lower()
    assert "mock-access-token" not in logged_in.text
    assert browser.get("/sentinel/api/auth/me").json()["user"]["id"] == "exe-user-1"


def test_owner_scope_and_csrf_protection(client):
    browser, store = client
    browser.post(
        "/sentinel/api/auth/login", headers={"X-Sentinel-Request": "1"},
        json={"email": "tester@example.com", "password": "password"},
    )
    missing_header = browser.post("/sentinel/api/groups", json={"id": "123"})
    assert missing_header.status_code == 403

    cross_account = browser.post(
        "/sentinel/api/groups", headers={"X-Sentinel-Request": "1"},
        json={"id": "123", "profile_id": "someone-else"},
    )
    assert cross_account.status_code == 403

    created = browser.post(
        "/sentinel/api/groups", headers={"X-Sentinel-Request": "1"},
        json={"id": "123", "name": "Owned Group", "profile_id": "exe-user-1"},
    )
    assert created.status_code == 200
    assert browser.get("/sentinel/api/groups?profile_id=exe-user-1").json()[0]["id"] == "123"
    assert store.scalar("SELECT COUNT(*) FROM sentinel_groups WHERE user_id=?", ("someone-else",)) == 0


def test_auth_proxy_forgot_password_requires_csrf(client):
    browser, _ = client
    assert browser.post("/sentinel/api/auth/forgot-password", json={"email": "tester@example.com"}).status_code == 403
    forged = browser.post(
        "/sentinel/api/auth/forgot-password",
        headers={"X-Sentinel-Request": "1", "Origin": "https://evil.example"},
        json={"email": "tester@example.com"},
    )
    assert forged.status_code == 403
    response = browser.post("/sentinel/api/auth/forgot-password", headers={"X-Sentinel-Request": "1"}, json={"email": "tester@example.com"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
