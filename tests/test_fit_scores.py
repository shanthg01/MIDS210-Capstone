import pytest

# Ids deliberately out of range of real ingested data (~27k players, ~365
# schools) so these tests exercise the stub-fallback path predictably instead
# of colliding with real (and possibly data-incomplete) player_team_fit_scores
# rows — see PLAYER_ID/SCHOOL_ID in conftest.py for the same rationale.
PLAYER_A, PLAYER_B = 9_900_001, 9_900_002
SCHOOL_A, SCHOOL_B = 9_900_101, 9_900_102


def test_requires_auth(client):
    assert (
        client.get(f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}").status_code == 401
    )


def test_returns_200(client, H):
    assert (
        client.get(
            f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
        ).status_code
        == 200
    )


def test_requires_both_params(client, H):
    assert client.get(f"/api/fit-scores?player_id={PLAYER_A}", headers=H).status_code == 422
    assert client.get(f"/api/fit-scores?school_id={SCHOOL_A}", headers=H).status_code == 422


def test_top_level_schema(client, H):
    data = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()
    for field in (
        "player_id",
        "school_id",
        "overall_fit",
        "gap_match",
        "scheme_fit",
        "role_fit",
        "team_impact_fit",
        "breakdown",
        "weights_used",
        "computed_at",
        "model_version",
        "raw_components",
        "component_confidences",
        "overall_confidence",
        "data_quality_flags",
    ):
        assert field in data, f"missing top-level field: {field}"


def test_component_scores_in_range(client, H):
    data = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()
    for field in ("overall_fit", "gap_match", "scheme_fit", "role_fit", "team_impact_fit"):
        assert 0 <= data[field] <= 100, f"{field}={data[field]} out of [0, 100]"


def test_overall_matches_weighted_sum(client, H):
    data = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()
    w = data["weights_used"]
    expected = (
        data["gap_match"] * w["gap"]
        + data["scheme_fit"] * w["scheme"]
        + data["role_fit"] * w["role_fit"]
        + data["team_impact_fit"] * w["team_impact_fit"]
    )
    assert data["overall_fit"] == pytest.approx(expected, abs=0.2)


def test_default_weights_sum_to_one(client, H):
    w = client.get(f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H).json()[
        "weights_used"
    ]
    assert sum(w.values()) == pytest.approx(1.0, abs=0.01)


def test_confidence_contract(client, H):
    data = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()
    assert 0 <= data["overall_confidence"] <= 1
    for component in ("gap_match", "scheme_fit", "role_fit", "team_impact_fit"):
        assert 0 <= data["component_confidences"][component] <= 1


def test_scheme_breakdown_fields_in_range(client, H):
    scheme = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()["breakdown"]["scheme"]
    for field in ("three_point_match", "pace_match", "rim_attack_match", "mid_range_match"):
        assert 0 <= scheme[field] <= 100, f"scheme.{field} out of range"


def test_role_fit_breakdown(client, H):
    role = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()["breakdown"]["role_fit"]
    assert role["projected_minutes"] > 0
    ci_lo, ci_hi = role["confidence_interval"]
    assert ci_lo < role["projected_minutes"] < ci_hi, "projected minutes must be inside CI"
    assert 0 <= role["starter_probability"] <= 1
    assert role["depth_chart_position"] >= 1


def test_team_impact_breakdown_present(client, H):
    impact = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()["breakdown"]["team_impact"]
    assert isinstance(impact["available"], bool)


def test_gap_breakdown_present(client, H):
    gap = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()["breakdown"]["gap"]
    assert isinstance(gap["archetype_needed"], bool)
    assert 0 <= gap["position_depth_score"] <= 100
    assert 0.0 <= gap["gap_reliability"] <= 1.0
    assert isinstance(gap["top_gap_features"], list)
    for f in gap["top_gap_features"]:
        assert "feature" in f and "gap" in f


def test_deterministic(client, H):
    r1 = client.get(f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H).json()
    r2 = client.get(f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H).json()
    assert r1["overall_fit"] == r2["overall_fit"]
    assert (
        r1["breakdown"]["role_fit"]["projected_minutes"]
        == r2["breakdown"]["role_fit"]["projected_minutes"]
    )


def test_different_schools_produce_different_scores(client, H):
    r1 = client.get(f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H).json()[
        "overall_fit"
    ]
    r2 = client.get(f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_B}", headers=H).json()[
        "overall_fit"
    ]
    assert r1 != r2


def test_different_players_produce_different_scores(client, H):
    r1 = client.get(f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H).json()[
        "overall_fit"
    ]
    r2 = client.get(f"/api/fit-scores?player_id={PLAYER_B}&school_id={SCHOOL_A}", headers=H).json()[
        "overall_fit"
    ]
    assert r1 != r2


def test_portal_candidate_and_current_school_flags_present(client, H):
    # Stub-fallback pair (ids out of range of real data) — both flags should
    # be present and False: no real row to check availability against, and
    # the player isn't really on that school's roster.
    data = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()
    assert data["is_portal_candidate"] is False
    assert data["is_current_school"] is False


def test_scheme_fit_stale_fields_present(client, H):
    # No real team_system_profiles row for out-of-range school — stale flag
    # should default to False, reason to None.
    data = client.get(
        f"/api/fit-scores?player_id={PLAYER_A}&school_id={SCHOOL_A}", headers=H
    ).json()
    assert "scheme_fit_stale" in data
    assert "scheme_fit_stale_reason" in data
    assert data["scheme_fit_stale"] is False
    assert data["scheme_fit_stale_reason"] is None
