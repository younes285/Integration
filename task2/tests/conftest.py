import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ORIGIN = "http://localhost:8000"


@pytest.fixture
def app(tmp_path):
    settings = Settings(_env_file=None, google_client_id="test-client.apps.googleusercontent.com",
                        csrf_secret_key="test-only-secret-key-with-at-least-32-characters",
                        database_url=f"sqlite:///{tmp_path / 'test.db'}", app_origin=ORIGIN)
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


@pytest.fixture
def google_claims(monkeypatch):
    claims = {"sub": "google-subject-123", "email": "student@example.com", "name": "Student",
              "email_verified": True, "iss": "https://accounts.google.com",
              "aud": "test-client.apps.googleusercontent.com", "exp": time.time() + 3600}
    # Only the external Google verification boundary is mocked; real application
    # claim checks, database, cookies, CSRF and authorization run on every request.
    monkeypatch.setattr("app.security.id_token.verify_oauth2_token", lambda *a, **kw: claims.copy())
    return claims


@pytest.fixture
def csrf(client):
    def headers():
        token = client.get("/api/csrf").json()["csrf_token"]
        return {"Origin": ORIGIN, "X-CSRF-Token": token}
    return headers


@pytest.fixture
def login(client, csrf, google_claims):
    def sign_in(**extra):
        return client.post("/auth/google", json={"credential": "mock-google-token", **extra}, headers=csrf())
    return sign_in
