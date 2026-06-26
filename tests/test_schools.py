def test_list_schools_is_public(client):
    assert client.get("/api/schools").status_code == 200


def test_list_schools_response_shape(client):
    data = client.get("/api/schools").json()
    assert len(data["schools"]) > 0
    item = data["schools"][0]
    assert "school_id" in item
    assert "name" in item


def test_roster_gap_requires_auth(client):
    assert client.get("/api/schools/roster-gap").status_code == 401


def test_roster_gap_404_when_no_school_or_snapshot(client, H):
    # Test user has school_id=301 (seed_test_data) but no roster_state_features
    # row for it — the real "don't fabricate" 404, not a 500.
    r = client.get("/api/schools/roster-gap", headers=H)
    assert r.status_code == 404


def test_system_profile_requires_auth(client):
    assert client.get("/api/schools/system-profile").status_code == 401


def test_system_profile_response_shape(client, H):
    # Test user's school_id=301 has a seeded team_system_profiles row (seed_test_data.py).
    data = client.get("/api/schools/system-profile", headers=H).json()
    assert data["school_id"] == 301
    assert "system_label" in data
    assert "offense_label" in data
    assert "defense_label" in data
