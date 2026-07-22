import pytest

_P1_URL = "/api/predictions?player_id=101&school_id=9900301"
_P2_URL = "/api/predictions?player_id=101&school_id=9900302"
_P1_2027_URL = "/api/predictions?player_id=101&school_id=9900301&season=2027"


def test_requires_auth(client):
    assert client.get(_P1_URL).status_code == 401


def test_returns_200(client, H):
    assert client.get(_P1_URL, headers=H).status_code == 200


def test_requires_both_params(client, H):
    assert client.get("/api/predictions?player_id=101", headers=H).status_code == 422


def test_unknown_pair_returns_404(client, H):
    assert client.get(
        "/api/predictions?player_id=99999&school_id=99999", headers=H
    ).status_code == 404


def test_honors_requested_season(client, H):
    data = client.get(_P1_2027_URL, headers=H).json()
    assert data["season"] == 2027
    assert data["success_probability"] == pytest.approx(0.68)


def test_expired_score_returns_404(client, H):
    assert client.get(
        "/api/predictions?player_id=42&school_id=9900301&season=2027",
        headers=H,
    ).status_code == 404


def test_schema(client, H):
    data = client.get(_P1_URL, headers=H).json()
    for field in (
        "player_id", "school_id", "season", "success_probability", "success_tier",
        "explanation", "similar_transfers", "model_version",
    ):
        assert field in data, f"missing field: {field}"


def test_success_probability_in_range(client, H):
    prob = client.get(_P1_URL, headers=H).json()["success_probability"]
    assert 0 <= prob <= 1


def test_success_tier_nonempty(client, H):
    tier = client.get(_P1_URL, headers=H).json()["success_tier"]
    assert isinstance(tier, str) and tier


def test_explanation_nonempty(client, H):
    explanation = client.get(_P1_URL, headers=H).json()["explanation"]
    assert isinstance(explanation, str) and len(explanation) > 10


def test_model_version_is_transfer_success_v2(client, H):
    assert client.get(_P1_URL, headers=H).json()["model_version"] == "transfer-success-eb-v2"


def test_similar_transfers_shape(client, H):
    transfers = client.get(_P1_URL, headers=H).json()["similar_transfers"]
    assert len(transfers) > 0
    for t in transfers:
        for field in (
            "player_name", "season", "success_label",
            "actual_value_per_100", "projected_value_per_100", "value_vs_projection",
        ):
            assert field in t, f"missing field {field} in similar transfer"
        assert t["value_vs_projection"] == pytest.approx(
            t["actual_value_per_100"] - t["projected_value_per_100"], abs=0.01
        )


def test_deterministic(client, H):
    r1 = client.get(_P1_URL, headers=H).json()
    r2 = client.get(_P1_URL, headers=H).json()
    assert r1["success_probability"] == r2["success_probability"]
    assert r1["success_tier"] == r2["success_tier"]
    assert r1["model_version"] == r2["model_version"]


def test_different_pairings_differ(client, H):
    p1 = client.get(_P1_URL, headers=H).json()["success_probability"]
    p2 = client.get(_P2_URL, headers=H).json()["success_probability"]
    assert p1 != p2
