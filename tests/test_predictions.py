VALID_TIERS = {"Very Low", "Low", "Moderate", "High", "Very High"}


def test_requires_auth(client):
    assert client.get("/api/predictions?player_id=101&school_id=9900301").status_code == 401


def test_returns_200(client, H):
    assert client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).status_code == 200


def test_requires_both_params(client, H):
    assert client.get("/api/predictions?player_id=101", headers=H).status_code == 422


def test_schema(client, H):
    data = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()
    for field in (
        "player_id", "school_id", "success_probability", "success_tier",
        "cell_n", "shrinkage_w", "explanation", "similar_transfers", "model_version",
    ):
        assert field in data, f"missing field: {field}"


def test_success_probability_in_range(client, H):
    prob = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["success_probability"]
    assert 0 <= prob <= 1


def test_tier_is_valid_enum(client, H):
    tier = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["success_tier"]
    assert tier in VALID_TIERS


def test_similar_transfers_is_list(client, H):
    # No transfer_success_scores seed data exists for this pair (see
    # scripts/seed_test_data.py) — stub_prediction() falls back to an empty
    # list rather than fabricating comps, so this only asserts shape, not
    # nonemptiness (unlike the old hardcoded-stub-era version of this test).
    transfers = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["similar_transfers"]
    assert isinstance(transfers, list)


def test_similar_transfers_shape(client, H):
    transfers = client.get("/api/predictions?player_id=101&school_id=9900301", headers=H).json()["similar_transfers"]
    for t in transfers:
        for field in (
            "player_name", "season", "value_vs_projection", "success_label",
            "minutes_drift", "usage_drift", "actual_value_per_100", "projected_value_per_100",
        ):
            assert field in t, f"missing field {field} in similar transfer"


def test_deterministic(client, H):
    r1 = client.get("/api/predictions?player_id=55&school_id=305", headers=H).json()
    r2 = client.get("/api/predictions?player_id=55&school_id=305", headers=H).json()
    assert r1["success_probability"] == r2["success_probability"]
    assert r1["success_tier"] == r2["success_tier"]
