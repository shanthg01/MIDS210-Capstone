import pytest

_P1_URL = "/api/projections/team-rating?player_id=101&school_id=9900301"
_P2_URL = "/api/projections/team-rating?player_id=101&school_id=9900302"
_P1_2026_URL = "/api/projections/team-rating?player_id=101&school_id=9900301&season=2026"


def test_requires_auth(client):
    assert client.get(_P1_URL).status_code == 401


def test_returns_200(client, H):
    assert client.get(_P1_URL, headers=H).status_code == 200


def test_requires_both_params(client, H):
    assert client.get("/api/projections/team-rating?player_id=101", headers=H).status_code == 422


def test_unknown_pair_returns_404(client, H):
    assert client.get(
        "/api/projections/team-rating?player_id=99999&school_id=99999", headers=H
    ).status_code == 404


def test_honors_requested_season(client, H):
    data = client.get(_P1_2026_URL, headers=H).json()
    assert data["season"] == 2026
    assert data["delta_adjEM"] == pytest.approx(0.4)


def test_expired_projection_returns_404(client, H):
    assert client.get(
        "/api/projections/team-rating?player_id=42&school_id=9900301&season=2027",
        headers=H,
    ).status_code == 404


def test_schema(client, H):
    data = client.get(_P1_URL, headers=H).json()
    for field in (
        "player_id", "school_id", "season", "current_adjEM", "projected_adjEM", "delta_adjEM",
        "confidence_interval", "national_percentile", "conference_rank",
        "context", "expected_minutes_input", "model_version",
    ):
        assert field in data, f"missing field: {field}"


def test_projected_equals_current_plus_delta(client, H):
    data = client.get(_P1_URL, headers=H).json()
    assert data["projected_adjEM"] == pytest.approx(data["current_adjEM"] + data["delta_adjEM"], abs=0.01)


def test_confidence_interval_ordered(client, H):
    ci = client.get(_P1_URL, headers=H).json()["confidence_interval"]
    assert ci[0] < ci[1], "CI lower bound must be less than upper bound"


def test_national_percentile_in_range(client, H):
    p = client.get(_P1_URL, headers=H).json()["national_percentile"]
    assert 1 <= p <= 100


def test_conference_rank_positive(client, H):
    rank = client.get(_P1_URL, headers=H).json()["conference_rank"]
    assert rank >= 1


def test_expected_minutes_positive(client, H):
    mins = client.get(_P1_URL, headers=H).json()["expected_minutes_input"]
    assert mins > 0


def test_context_nonempty(client, H):
    ctx = client.get(_P1_URL, headers=H).json()["context"]
    assert len(ctx) > 10


def test_deterministic(client, H):
    r1 = client.get(_P1_URL, headers=H).json()
    r2 = client.get(_P1_URL, headers=H).json()
    assert r1["delta_adjEM"] == r2["delta_adjEM"]
    assert r1["national_percentile"] == r2["national_percentile"]


def test_different_pairings_differ(client, H):
    d1 = client.get(_P1_URL, headers=H).json()["delta_adjEM"]
    d2 = client.get(_P2_URL, headers=H).json()["delta_adjEM"]
    assert d1 != d2


def test_compare_team_rating_scenarios_returns_ordered_live_results(client, H):
    response = client.post(
        "/api/projections/team-rating/compare",
        json={"player_ids": [101, 2], "school_id": 9900301, "season": 2027},
        headers=H,
    )

    assert response.status_code == 200
    data = response.json()
    assert [scenario["player_id"] for scenario in data["scenarios"]] == ["101", "2"]
    assert data["preferred_player_id"] == "101"
    assert data["delta_margin"] == pytest.approx(1.1)
    assert data["confidence_intervals_overlap"] is True
    assert data["reasoning"]
