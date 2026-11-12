import pytest
from sqlalchemy import select

from app.models import User


@pytest.mark.parametrize("path", ["/api/me", "/api/dashboard", "/api/admin/dashboard", "/api/admin/users"])
def test_missing_session(client, path):
    assert client.get(path).status_code == 401


def test_user_and_admin_permissions(client, login, app):
    assert login().status_code == 200
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    for path in ("/api/admin/dashboard", "/api/admin/users"):
        assert client.get(path).status_code == 403
    with app.state.session_factory() as db:
        user = db.scalar(select(User))
        user.role = "admin"
        db.commit()
    assert client.get("/api/admin/dashboard").status_code == 200
    users = client.get("/api/admin/users")
    assert users.status_code == 200
    assert set(users.json()[0]) == {"id", "name", "email", "role", "picture_url"}
    # Permissions are also removed immediately on a database role change.
    with app.state.session_factory() as db:
        user = db.scalar(select(User))
        user.role = "user"
        db.commit()
    assert client.get("/api/admin/users").status_code == 403
