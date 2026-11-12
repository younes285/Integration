import pytest
from sqlalchemy import select

from app.models import User


def test_public_endpoints(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert set(client.get("/config").json()) == {"google_client_id"}
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_verifier_rejects_invalid_signature(client, csrf, monkeypatch):
    def reject(*args, **kwargs):
        raise ValueError("invalid cryptographic signature")
    monkeypatch.setattr("app.security.id_token.verify_oauth2_token", reject)
    assert client.post("/auth/google", json={"credential": "bad"}, headers=csrf()).status_code == 401


@pytest.mark.parametrize("change", [
    {"exp": 0}, {"aud": "attacker-client"}, {"iss": "https://attacker.example"},
    {"email_verified": False}, {"email_verified": "true"}, {"sub": ""},
    {"sub": None}, {"email": None},
])
def test_claim_requirements(login, google_claims, change):
    google_claims.update(change)
    assert login().status_code == 401


@pytest.mark.parametrize("reason", ["Token expired", "Wrong audience"])
def test_google_library_errors(client, csrf, monkeypatch, reason):
    def reject(*args, **kwargs):
        raise ValueError(reason)
    monkeypatch.setattr("app.security.id_token.verify_oauth2_token", reject)
    assert client.post("/auth/google", json={"credential": "bad"}, headers=csrf()).status_code == 401


def test_verification_receives_configured_audience(client, csrf, google_claims, monkeypatch):
    calls = []
    def verify(credential, request, audience):
        calls.append((credential, audience))
        return google_claims.copy()
    monkeypatch.setattr("app.security.id_token.verify_oauth2_token", verify)
    assert client.post("/auth/google", json={"credential": "signed-token"}, headers=csrf()).status_code == 200
    assert calls == [("signed-token", "test-client.apps.googleusercontent.com")]


def test_new_user_and_returning_admin(login, app, google_claims):
    response = login()
    assert response.status_code == 200
    assert response.json()["role"] == "user"
    with app.state.session_factory() as db:
        user = db.scalar(select(User))
        assert user.google_sub == google_claims["sub"]
        user.role = "admin"
        db.commit()
    google_claims["email"] = "updated@example.com"
    assert login().json()["role"] == "admin"
    with app.state.session_factory() as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 1
        assert users[0].email == "updated@example.com"


def test_equal_email_does_not_merge_distinct_subjects(login, google_claims, app):
    assert login().status_code == 200
    google_claims["sub"] = "a-second-google-subject"
    assert login().status_code == 200
    with app.state.session_factory() as db:
        assert len(db.scalars(select(User)).all()) == 2


def test_client_cannot_assign_role(login, client):
    assert login(role="admin").status_code == 422
    assert client.get("/api/me").status_code == 401
    assert login().json()["role"] == "user"
