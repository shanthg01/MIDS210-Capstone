import uuid


def _unique_email() -> str:
    """Each test run gets a fresh email — avoids 409 from prior runs against real DB."""
    return f"test-{uuid.uuid4().hex[:8]}@example.com"


def test_signup_returns_201(client):
    r = client.post(
        "/api/auth/signup",
        json={"email": _unique_email(), "password": "password123", "full_name": "New User"},
    )
    assert r.status_code == 201


def test_signup_response_shape(client):
    r = client.post(
        "/api/auth/signup",
        json={"email": _unique_email(), "password": "password123", "full_name": "Shape User"},
    )
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert isinstance(data["user_id"], int)


def test_signup_token_is_valid_jwt(client):
    r = client.post(
        "/api/auth/signup",
        json={"email": _unique_email(), "password": "password123", "full_name": "JWT User"},
    )
    token = r.json()["access_token"]
    assert len(token.split(".")) == 3, "JWT must have 3 dot-separated segments"


def test_signup_duplicate_email_returns_409(client):
    email = _unique_email()
    client.post("/api/auth/signup", json={"email": email, "password": "password123", "full_name": "X"})
    r = client.post("/api/auth/signup", json={"email": email, "password": "password123", "full_name": "X"})
    assert r.status_code == 409


def test_signup_rejects_short_password(client):
    r = client.post(
        "/api/auth/signup",
        json={"email": _unique_email(), "password": "abc", "full_name": "X"},
    )
    assert r.status_code == 422


def test_signup_rejects_invalid_email(client):
    r = client.post(
        "/api/auth/signup",
        json={"email": "not-an-email", "password": "password123", "full_name": "X"},
    )
    assert r.status_code == 422


def test_login_returns_200(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "player@example.com", "password": "testpass123"},
    )
    assert r.status_code == 200


def test_login_wrong_password_returns_401(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "player@example.com", "password": "wrongpassword"},
    )
    assert r.status_code == 401


def test_login_unknown_email_returns_401(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "nobody@nowhere.com", "password": "testpass123"},
    )
    assert r.status_code == 401


def test_login_token_grants_access(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "player@example.com", "password": "testpass123"},
    )
    token = r.json()["access_token"]
    protected = client.get(
        "/api/fit-scores?player_id=101&school_id=301",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert protected.status_code == 200


def test_logout_returns_200(client):
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert "message" in r.json()


def test_missing_token_returns_401(client):
    r = client.get("/api/fit-scores?player_id=101&school_id=301")
    assert r.status_code == 401


def test_invalid_token_returns_401(client):
    r = client.get(
        "/api/fit-scores?player_id=101&school_id=301",
        headers={"Authorization": "Bearer invalid.token.payload"},
    )
    assert r.status_code == 401


def test_malformed_bearer_returns_401_or_403(client):
    r = client.get(
        "/api/fit-scores?player_id=101&school_id=301",
        headers={"Authorization": "NotBearer abc"},
    )
    assert r.status_code in (401, 403)
