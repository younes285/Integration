import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.main import create_app
from app.models import Session, User, utcnow


def test_unsafe_configuration_rejected():
    base = {"google_client_id": "client", "csrf_secret_key": "x" * 48, "_env_file": None}
    for change in ({"csrf_secret_key": "replace-with-strong-random-secret"},
                   {"app_origin": "http://example.com"},
                   {"app_origin": "https://example.com"},
                   {"app_origin": "http://localhost:8000/path"}):
        with pytest.raises(ValidationError):
            Settings(**(base | change))


def test_https_cookie_flags(tmp_path, google_claims):
    settings = Settings(_env_file=None, google_client_id=google_claims["aud"],
                        csrf_secret_key="x" * 48, app_origin="https://example.com",
                        session_cookie_secure=True, database_url=f"sqlite:///{tmp_path / 'secure.db'}")
    with TestClient(create_app(settings), base_url="https://example.com") as client:
        csrf = client.get("/api/csrf").json()["csrf_token"]
        response = client.post("/auth/google", json={"credential": "mock"},
                               headers={"Origin": "https://example.com", "X-CSRF-Token": csrf})
        assert response.status_code == 200
        assert all("Secure" in cookie for cookie in response.headers.get_list("set-cookie"))


def test_database_constraints(client, app):
    with app.state.session_factory() as db:
        db.add(User(google_sub="test", email="test@example.com", name="Test", role="owner"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(Session(session_token_hash="0" * 64, user_id=999, expires_at=utcnow()))
        with pytest.raises(IntegrityError):
            db.commit()


def test_cli_promotes_existing_user(client, login, app):
    user_id = login().json()["id"]
    env = os.environ | {"GOOGLE_CLIENT_ID": app.state.settings.google_client_id,
                        "CSRF_SECRET_KEY": app.state.settings.csrf_secret_key,
                        "APP_ORIGIN": app.state.settings.app_origin,
                        "DATABASE_URL": app.state.settings.database_url,
                        "SESSION_COOKIE_SECURE": "false"}
    result = subprocess.run([sys.executable, "-m", "app.cli", "list-users"], env=env,
                            capture_output=True, text=True, check=True)
    assert "student@example.com" in result.stdout
    subprocess.run([sys.executable, "-m", "app.cli", "promote", "--id", str(user_id)],
                   env=env, capture_output=True, text=True, check=True)
    assert client.get("/api/admin/users").status_code == 200
    with app.state.session_factory() as db:
        assert db.scalar(select(User)).role == "admin"
