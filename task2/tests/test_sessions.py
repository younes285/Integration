from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Session, utcnow
from app.security import CSRF_COOKIE, SESSION_COOKIE, token_hash
from tests.conftest import ORIGIN


def test_cookie_flags_and_hash_storage(client, login, app):
    response = login()
    cookie_headers = response.headers.get_list("set-cookie")
    assert all("HttpOnly" in value and "SameSite=lax" in value and "Path=/" in value for value in cookie_headers)
    assert all("Domain=" not in value for value in cookie_headers)
    raw = client.cookies.get(SESSION_COOKIE)
    with app.state.session_factory() as db:
        session = db.scalar(select(Session))
        assert session.session_token_hash == token_hash(raw)
        assert session.session_token_hash != raw
    # A fresh page/client with the same cookie remains authenticated.
    with TestClient(app, base_url=ORIGIN) as refreshed:
        refreshed.cookies.set(SESSION_COOKIE, raw)
        assert refreshed.get("/api/me").status_code == 200


def test_expired_session(client, login, app):
    login()
    with app.state.session_factory() as db:
        session = db.scalar(select(Session))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/me").status_code == 401


def test_logout_revokes_and_prevents_replay(client, login, csrf, app):
    login()
    raw = client.cookies.get(SESSION_COOKIE)
    assert client.post("/auth/logout", headers=csrf()).status_code == 200
    assert client.cookies.get(SESSION_COOKIE) is None
    assert client.cookies.get(CSRF_COOKIE) is None
    client.cookies.set(SESSION_COOKIE, raw)
    assert client.get("/api/dashboard").status_code == 401
    with app.state.session_factory() as db:
        assert db.scalar(select(Session)).revoked_at is not None


def test_login_rotates_existing_session(client, login, app):
    login()
    old = client.cookies.get(SESSION_COOKIE)
    old_csrf = client.cookies.get(CSRF_COOKIE)
    login()
    assert old != client.cookies.get(SESSION_COOKIE)
    assert old_csrf != client.cookies.get(CSRF_COOKIE)
    with app.state.session_factory() as db:
        assert db.scalar(select(Session).where(Session.session_token_hash == token_hash(old))).revoked_at is not None
    with TestClient(app, base_url=ORIGIN) as attacker:
        attacker.cookies.set(SESSION_COOKIE, old)
        assert attacker.get("/api/me").status_code == 401


@pytest.mark.parametrize("mode", ["missing", "wrong-token", "untrusted-origin", "missing-origin", "forged-cookie"])
@pytest.mark.parametrize("path", ["/auth/google", "/auth/logout"])
def test_csrf_rejection(client, login, csrf, mode, path):
    login()
    headers = csrf()
    if mode == "missing":
        headers.pop("X-CSRF-Token")
    elif mode == "wrong-token":
        headers["X-CSRF-Token"] = "wrong"
    elif mode == "untrusted-origin":
        headers["Origin"] = "https://evil.example"
    elif mode == "missing-origin":
        headers.pop("Origin")
    else:
        client.cookies.clear()
        client.cookies.set(CSRF_COOKIE, "12345.forged.signature")
        headers["X-CSRF-Token"] = "12345.forged.signature"
    assert client.post(path, headers=headers, json={"credential": "mock"}).status_code == 403


def test_prelogin_csrf_cannot_be_reused_after_login(client, login, csrf):
    old = csrf()["X-CSRF-Token"]
    login()
    # Even an injected matching cookie cannot restore an anonymous token's validity.
    session = client.cookies.get(SESSION_COOKIE)
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, session)
    client.cookies.set(CSRF_COOKIE, old)
    assert client.post("/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": old}).status_code == 403


def test_expired_signed_csrf_rejected(client, csrf, monkeypatch):
    headers = csrf()
    import time
    now = time.time()
    monkeypatch.setattr("app.security.time.time", lambda: now + 3601)
    assert client.post("/auth/google", headers=headers, json={"credential": "mock"}).status_code == 403


def test_invalid_session_cookie(client):
    client.cookies.set(SESSION_COOKIE, "invented-session")
    assert client.get("/api/me").status_code == 401
