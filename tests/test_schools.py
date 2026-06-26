def test_roster_gap_requires_auth(client):
    assert client.get("/api/schools/roster-gap").status_code == 401


def test_roster_gap_404_when_no_school_or_snapshot(client, H):
    # Test user has no school_id set and/or no roster_state_features row —
    # the real "don't fabricate" 404, not a 500.
    r = client.get("/api/schools/roster-gap", headers=H)
    assert r.status_code == 404
