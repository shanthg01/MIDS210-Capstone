import pytest
from fastapi.testclient import TestClient

from portalpoint.main import app

# Deliberately out of range of real ingested ids (~27k players, ~365 schools)
# so tests exercise the stub-fallback path predictably instead of colliding
# with real (and possibly data-incomplete) rows in player_team_fit_scores.
PLAYER_ID = 9_900_001
SCHOOL_ID = 9_900_101

_TEST_EMAIL = "player@example.com"
_TEST_PASS  = "testpass123"
_TEST_NAME  = "Test Player"


@pytest.fixture(scope="session")
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        # Seed test user — idempotent: 201 on first run, 409 if already exists
        c.post(
            "/api/auth/signup",
            json={"email": _TEST_EMAIL, "password": _TEST_PASS, "full_name": _TEST_NAME},
        )
        yield c


@pytest.fixture(scope="session")
def auth_token(client):
    r = client.post(
        "/api/auth/login",
        json={"email": _TEST_EMAIL, "password": _TEST_PASS},
    )
    assert r.status_code == 200, f"login failed: {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def user_id(client) -> int:
    """Actual DB ID of the test user — varies across environments."""
    r = client.post(
        "/api/auth/login",
        json={"email": _TEST_EMAIL, "password": _TEST_PASS},
    )
    assert r.status_code == 200
    return r.json()["user_id"]


@pytest.fixture(scope="session")
def H(auth_token):
    """Auth headers shorthand — import in every test module that hits protected routes."""
    return {"Authorization": f"Bearer {auth_token}"}
