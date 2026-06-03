import pytest
from fastapi.testclient import TestClient

from portalpoint.main import app

PLAYER_ID = 101
SCHOOL_ID = 301
USER_ID = 1001


@pytest.fixture(scope="session")
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(scope="session")
def auth_token(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "player@example.com", "password": "testpass123"},
    )
    assert r.status_code == 200, f"login failed: {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def H(auth_token):
    """Auth headers shorthand — import in every test module that hits protected routes."""
    return {"Authorization": f"Bearer {auth_token}"}
