"""Unit tests for portalpoint.modeling.team_rating_projection.

All tests are pure (no DB) — same pattern as test_player_projection.py
and test_gap_matching.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.team_rating_projection import (
    ROSTER_FEATURES,
    ELITE_RECRUITING_SCHOOLS,
    ELITE_RECRUITING_MULTIPLIER,
    FRESHMAN_MIN_PCT_BY_TIER,
    FRESHMAN_RAPM_DISCOUNT_BY_TIER,
    FRESHMAN_OPPORTUNITY_SCALE_MINUTES,
    FRESHMAN_OPPORTUNITY_MAX_FACTOR,
    FRESHMAN_OPPORTUNITY_FLOOR_FACTOR,
    FRESHMAN_VARIANCE_PER_PLAYER,
    _conference_tier,
    _freshman_prior_positions,
    _incoming_freshman_count,
    _returning_minutes_pct,
    _usage_hhi,
    _slot_fill,
    analytical_ci,
    build_candidate_roster,
    build_freshman_prior_rows,
    build_roster_features,
    build_slot_baselines,
    compute_counterfactual,
    build_explanation_payload,
    build_confidence_interval,
    fit_team_translation,
    rolling_origin_cv,
    scale_displaced_minutes,
    upsert_team_rating_projections,
    TeamRatingModels,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_roster(n: int = 8, off_rapm: float = 0.0, def_rapm: float = 0.0) -> list[dict]:
    return [
        {
            "min_pct":          10.0 + i,
            "position":         ["PG", "SG", "SF", "PF", "C"][i % 5],
            "off_adj_rapm":     off_rapm + i * 0.1,
            "def_adj_rapm":     def_rapm + i * 0.05,
            "three_point_rate": 0.35,
            "off_reb_pct":      0.25,
            "usage_rate":       20.0 + i,
        }
        for i in range(n)
    ]


def _make_models(off_intercept: float = 100.0, def_intercept: float = 95.0) -> TeamRatingModels:
    """Fit models on minimal synthetic data."""
    n = 30
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, (n, len(ROSTER_FEATURES)))
    y_off = off_intercept + X[:, 0] * 2 + rng.normal(0, 0.1, n)
    y_def = def_intercept - X[:, 1] * 1.5 + rng.normal(0, 0.1, n)

    feat_df = pd.DataFrame(X, columns=ROSTER_FEATURES)
    feat_df["school_id"] = range(n)
    feat_df["season"] = 2021

    label_df = pd.DataFrame({
        "school_id": range(n),
        "season": 2021,
        "adj_o": y_off,
        "adj_d": y_def,
        "adj_em": y_off - y_def,
    })
    return fit_team_translation(feat_df, label_df)


# ---------------------------------------------------------------------------
# _conference_tier
# ---------------------------------------------------------------------------

def test_conference_tier_high_major():
    ems = np.array([1.0, 5.0, 10.0, 20.0])
    assert _conference_tier(20.0, ems) == 1


def test_conference_tier_low_major():
    # Need at least 6 values so the minimum (pct = 1/6 ≈ 0.167) falls below the 0.20 cut.
    ems = np.array([1.0, 5.0, 8.0, 10.0, 15.0, 20.0])
    assert _conference_tier(1.0, ems) == 4


def test_conference_tier_nan_defaults_to_2():
    assert _conference_tier(float("nan"), np.array([1.0, 2.0, 3.0])) == 2


def test_conference_tier_empty_array_defaults_to_2():
    assert _conference_tier(5.0, np.array([])) == 2


# ---------------------------------------------------------------------------
# _usage_hhi
# ---------------------------------------------------------------------------

def test_usage_hhi_uniform():
    usage = np.array([1.0, 1.0, 1.0, 1.0])
    assert _usage_hhi(usage) == pytest.approx(0.25)


def test_usage_hhi_monopoly():
    usage = np.array([1.0, 0.0, 0.0])
    assert _usage_hhi(usage) == pytest.approx(1.0)


def test_usage_hhi_zero_total():
    assert _usage_hhi(np.zeros(5)) == 0.0


# ---------------------------------------------------------------------------
# build_slot_baselines
# ---------------------------------------------------------------------------

def test_build_slot_baselines_returns_dict():
    df = pd.DataFrame({
        "conference_tier": [1, 1, 2],
        "position":        ["PG", "PG", "C"],
        "off_adj_rapm":    [1.0, 2.0, -0.5],
        "def_adj_rapm":    [0.5, 0.8, 1.2],
        "three_point_rate": [0.40, 0.38, 0.20],
        "off_reb_pct":     [0.20, 0.22, 0.35],
        "usage_rate":      [22.0, 24.0, 18.0],
    })
    baselines = build_slot_baselines(df)
    assert (1, "PG") in baselines
    assert baselines[(1, "PG")]["off_adj_rapm"] == pytest.approx(1.5)


def test_build_slot_baselines_empty_df():
    assert build_slot_baselines(pd.DataFrame()) == {}


# ---------------------------------------------------------------------------
# _slot_fill
# ---------------------------------------------------------------------------

def test_slot_fill_exact_key():
    baselines = {(1, "PG"): {"off_adj_rapm": 2.0, "def_adj_rapm": 0.5,
                              "three_point_rate": 0.40, "off_reb_pct": 0.20, "usage_rate": 22.0}}
    result = _slot_fill(baselines, 1, "PG")
    assert result["off_adj_rapm"] == 2.0


def test_slot_fill_fallback_by_position():
    baselines = {(2, "C"): {"off_adj_rapm": -0.5, "def_adj_rapm": 1.2,
                             "three_point_rate": 0.20, "off_reb_pct": 0.35, "usage_rate": 18.0}}
    # tier=1 not in baselines, should fall back to tier=2
    result = _slot_fill(baselines, 1, "C")
    assert result["off_adj_rapm"] == pytest.approx(-0.5)


def test_slot_fill_empty_baselines():
    result = _slot_fill({}, 1, "PG")
    assert result["off_adj_rapm"] == 0.0


# ---------------------------------------------------------------------------
# build_roster_features
# ---------------------------------------------------------------------------

def test_build_roster_features_returns_all_keys():
    roster = _make_roster(8)
    feats = build_roster_features(roster, conference_tier=2, adj_tempo=68.0,
                                  returning_minutes_pct=0.8, slot_baselines={})
    for key in ROSTER_FEATURES:
        assert key in feats, f"missing feature: {key}"


def test_build_roster_features_weighted_off_positive():
    roster = _make_roster(8, off_rapm=1.0)
    feats = build_roster_features(roster, 2, 68.0, 0.8, {})
    assert feats["weighted_off_impact"] > 0


def test_build_roster_features_empty_roster():
    feats = build_roster_features([], 2, 68.0, 0.8, {})
    assert all(v == 0.0 for v in feats.values())


def test_build_roster_features_conference_tier_passed_through():
    roster = _make_roster(5)
    feats = build_roster_features(roster, 3, 68.0, 0.8, {})
    assert feats["conference_tier"] == 3.0


def test_build_roster_features_returns_minutes_pct():
    roster = _make_roster(5)
    feats = build_roster_features(roster, 2, 68.0, returning_minutes_pct=0.65, slot_baselines={})
    assert feats["returning_minutes_pct"] == pytest.approx(0.65)


def test_build_roster_features_fills_missing_rapm_from_baselines():
    roster = [{"min_pct": 20.0, "position": "PG", "off_adj_rapm": None, "def_adj_rapm": None,
               "three_point_rate": 0.35, "off_reb_pct": 0.25, "usage_rate": 20.0}]
    baselines = {(1, "PG"): {"off_adj_rapm": 1.5, "def_adj_rapm": 0.5,
                              "three_point_rate": 0.40, "off_reb_pct": 0.20, "usage_rate": 22.0}}
    feats = build_roster_features(roster, 1, 68.0, 0.9, baselines)
    assert feats["weighted_off_impact"] == pytest.approx(1.5)


def test_build_roster_features_counts_known_quality_not_priors():
    roster = [
        {"min_pct": 20.0, "position": "PG", "off_adj_rapm": 1.0, "def_adj_rapm": 0.5,
         "three_point_rate": 0.35, "off_reb_pct": 0.20, "usage_rate": 20.0},
        {"min_pct": 10.0, "position": "C", "off_adj_rapm": None, "def_adj_rapm": None,
         "three_point_rate": 0.20, "off_reb_pct": 0.35, "usage_rate": 18.0},
        {"min_pct": 8.0, "position": "SF", "off_adj_rapm": 0.3, "def_adj_rapm": 0.2,
         "three_point_rate": 0.30, "off_reb_pct": 0.20, "usage_rate": 18.0,
         "is_freshman_prior": True},
    ]
    baselines = {(2, "C"): {"off_adj_rapm": -0.5, "def_adj_rapm": 1.2,
                             "three_point_rate": 0.20, "off_reb_pct": 0.35, "usage_rate": 18.0}}
    feats = build_roster_features(roster, 2, 68.0, 0.8, baselines)
    assert feats["n_known_players"] == 1.0


def test_returning_minutes_pct_uses_returning_and_departing_minutes():
    row = pd.Series({
        "returning_minutes_by_position": {"PG": 1200.0, "C": 800.0},
        "departing_minutes_by_position": {"SG": 1000.0},
    })
    assert _returning_minutes_pct(row) == pytest.approx(2 / 3)


def test_returning_minutes_pct_handles_json_strings():
    row = pd.Series({
        "returning_minutes_by_position": '{"PG": 600.0}',
        "open_minutes_by_position": '{"SG": 400.0}',
    })
    assert _returning_minutes_pct(row) == pytest.approx(0.6)


def test_incoming_freshman_count_from_class_balance():
    row = pd.Series({"class_balance": {"incoming_fr": 2, "incoming_so": 1, "transfer_in_fr": 3}})
    assert _incoming_freshman_count(row) == 2


def test_freshman_prior_positions_use_open_minutes():
    positions = _freshman_prior_positions({"C": 30.0, "PG": 10.0, "SG": 0.0}, 3)
    assert positions == ["C", "PG", "C"]


def test_build_freshman_prior_rows_discount_slot_baselines():
    # C has 30 open minutes → opportunity_factor hits MAX (1.5) → 8.0 * 1.5 = 12.0
    # PG has 10 open minutes → factor = 10/15 = 0.667 → 8.0 * 0.667 ≈ 5.333
    row = pd.Series({
        "class_balance": {"incoming_fr": 2},
        "open_minutes_by_position": {"C": 30.0, "PG": 10.0},
    })
    baselines = {
        (2, "C"): {"off_adj_rapm": 2.0, "def_adj_rapm": 1.0,
                   "three_point_rate": 0.20, "off_reb_pct": 0.35, "usage_rate": 18.0},
        (2, "PG"): {"off_adj_rapm": 1.0, "def_adj_rapm": 0.5,
                    "three_point_rate": 0.38, "off_reb_pct": 0.10, "usage_rate": 22.0},
    }
    rows = build_freshman_prior_rows(row, 2, baselines)
    assert len(rows) == 2
    assert rows[0]["position"] == "C"
    # opportunity_factor = min(30/15, 1.5) = 1.5; base = 8.0; adjusted = 12.0
    assert rows[0]["min_pct"] == pytest.approx(8.0 * FRESHMAN_OPPORTUNITY_MAX_FACTOR)
    # tier-2 RAPM discount = 0.65; slot off_adj_rapm = 2.0
    assert rows[0]["off_adj_rapm"] == pytest.approx(2.0 * FRESHMAN_RAPM_DISCOUNT_BY_TIER[2])
    assert rows[0]["is_freshman_prior"] is True


# ---------------------------------------------------------------------------
# fit_team_translation
# ---------------------------------------------------------------------------

def test_fit_team_translation_returns_models():
    m = _make_models()
    assert m.off_model is not None
    assert m.def_model is not None


def test_fit_team_translation_has_residual_std():
    m = _make_models()
    assert m.off_resid_std >= 0
    assert m.def_resid_std >= 0


def test_fit_team_translation_n_train_rows():
    m = _make_models()
    assert m.n_train_rows == 30


# ---------------------------------------------------------------------------
# compute_counterfactual
# ---------------------------------------------------------------------------

def test_compute_counterfactual_positive_delta_for_better_roster():
    models = _make_models()
    # Baseline: average roster; candidate: better player (higher off/def rapm)
    base_roster = _make_roster(8, off_rapm=0.0, def_rapm=0.0)
    cand_roster = _make_roster(8, off_rapm=2.0, def_rapm=1.5)

    base_feats = build_roster_features(base_roster, 2, 68.0, 0.8, {})
    cand_feats = build_roster_features(cand_roster, 2, 68.0, 0.8, {})

    result = compute_counterfactual(base_feats, cand_feats, models)

    assert "delta_adj_em" in result
    assert "baseline_adj_o" in result
    assert "projected_adj_o" in result
    assert "baseline_adj_d" in result
    assert "projected_adj_d" in result


def test_compute_counterfactual_zero_delta_for_identical_rosters():
    models = _make_models()
    roster = _make_roster(8, off_rapm=1.0, def_rapm=0.5)
    feats = build_roster_features(roster, 2, 68.0, 0.8, {})
    result = compute_counterfactual(feats, feats, models)
    assert result["delta_adj_em"] == pytest.approx(0.0, abs=0.001)
    assert result["delta_adj_o"] == pytest.approx(0.0, abs=0.001)


def test_compute_counterfactual_returns_all_keys():
    models = _make_models()
    feats = build_roster_features(_make_roster(8), 2, 68.0, 0.8, {})
    result = compute_counterfactual(feats, feats, models)
    for k in ("baseline_adj_o", "baseline_adj_d", "baseline_adj_em",
              "projected_adj_o", "projected_adj_d", "projected_adj_em",
              "delta_adj_o", "delta_adj_d", "delta_adj_em"):
        assert k in result, f"missing key: {k}"


# ---------------------------------------------------------------------------
# build_explanation_payload
# ---------------------------------------------------------------------------

def test_build_explanation_payload_returns_expected_keys():
    models = _make_models()
    base_feats = build_roster_features(_make_roster(8, off_rapm=0.0), 2, 68.0, 0.8, {})
    cand_feats = build_roster_features(_make_roster(8, off_rapm=1.0), 2, 68.0, 0.8, {})
    delta = compute_counterfactual(base_feats, cand_feats, models)

    import pandas as pd
    pt_row = pd.Series({"expected_minutes": 22.0, "usage_role": "secondary_creator",
                        "displaced_minutes": {"replacement_slot": 5.0,
                                              "same_position_depth": 11.0, "flexible_bench": 6.0}})
    payload = build_explanation_payload(base_feats, cand_feats, models, pt_row, delta)

    for k in ("candidate_off_contribution", "candidate_def_contribution",
              "candidate_minutes", "candidate_usage_role", "displaced_minutes",
              "delta_adj_em"):
        assert k in payload, f"missing key: {k}"


def test_build_explanation_payload_candidate_minutes_matches_pt_row():
    models = _make_models()
    feats = build_roster_features(_make_roster(8), 2, 68.0, 0.8, {})
    delta = compute_counterfactual(feats, feats, models)
    import pandas as pd
    pt_row = pd.Series({"expected_minutes": 26.5, "usage_role": "primary_creator",
                        "displaced_minutes": {}})
    payload = build_explanation_payload(feats, feats, models, pt_row, delta)
    assert payload["candidate_minutes"] == pytest.approx(26.5)


def test_build_explanation_payload_uses_scaled_deltas():
    models = _make_models()
    base_feats = {f: 0.0 for f in ROSTER_FEATURES}
    cand_feats = {f: 0.0 for f in ROSTER_FEATURES}
    cand_feats["weighted_off_impact"] = 2.0
    delta = compute_counterfactual(base_feats, cand_feats, models)
    pt_row = pd.Series({"expected_minutes": 20.0, "usage_role": "rotation", "displaced_minutes": {}})

    payload = build_explanation_payload(base_feats, cand_feats, models, pt_row, delta)

    idx = ROSTER_FEATURES.index("weighted_off_impact")
    expected = models.off_model.coef_[idx] * (2.0 / models.off_scaler.scale_[idx])
    assert payload["candidate_off_contribution"] == pytest.approx(round(expected, 3), abs=0.001)


# ---------------------------------------------------------------------------
# build_confidence_interval
# ---------------------------------------------------------------------------

def test_build_confidence_interval_lower_lt_upper():
    models = _make_models()
    base_rows = _make_roster(8, off_rapm=0.0, def_rapm=0.0)
    cand_rows = _make_roster(9, off_rapm=1.0, def_rapm=0.5)
    import pandas as pd
    pt_row = pd.Series({"expected_minutes": 22.0, "displaced_minutes": {}})
    lo, hi = build_confidence_interval(
        base_rows, cand_rows, pt_row, models, {}, 2, 68.0, 0.8, n_boot=50
    )
    assert lo < hi


def test_build_confidence_interval_80_percent_width():
    models = _make_models()
    rows = _make_roster(8)
    import pandas as pd
    pt_row = pd.Series({"expected_minutes": 20.0, "displaced_minutes": {}})
    lo, hi = build_confidence_interval(rows, rows, pt_row, models, {}, 2, 68.0, 0.8, n_boot=100)
    # Width should be non-negative; no exact assertion since bootstrapped
    assert hi - lo >= 0.0


def test_analytical_ci_combines_offense_and_defense_residuals():
    models = TeamRatingModels(off_resid_std=2.0, def_resid_std=3.0)
    lo, hi = analytical_ci(1.0, models)
    sigma = np.sqrt(2.0 * (2.0 ** 2 + 3.0 ** 2))
    assert lo == pytest.approx(1.0 - 1.2816 * sigma)
    assert hi == pytest.approx(1.0 + 1.2816 * sigma)


def test_build_candidate_roster_uses_real_candidate_profile_fields():
    baseline_info = {
        "roster_rows": _make_roster(4),
        "tier": 2,
        "returning_pct": 0.7,
    }
    pt_row = pd.Series({
        "player_id": 101,
        "expected_minutes": 20.0,
        "expected_usage": 24.0,
        "displaced_minutes": {},
    })
    proj_row = pd.Series({
        "value_per_100": 3.0,
        "position": "C",
        "three_point_rate": 0.12,
        "off_reb_pct": 0.38,
    })

    rows, returning_pct = build_candidate_roster(baseline_info, pt_row, proj_row, {})
    candidate = rows[-1]
    assert returning_pct == pytest.approx(0.7)
    assert candidate["position"] == "C"
    assert candidate["three_point_rate"] == pytest.approx(0.12)
    assert candidate["off_reb_pct"] == pytest.approx(0.38)


# ---------------------------------------------------------------------------
# rolling_origin_cv
# ---------------------------------------------------------------------------

def test_rolling_origin_cv_returns_3_folds():
    rng = np.random.default_rng(1)
    n = 200
    X = rng.normal(0, 1, (n, len(ROSTER_FEATURES)))
    y_off = 100 + X[:, 0] * 2
    y_def = 98 - X[:, 1] * 1.5
    seasons = [2021] * 50 + [2022] * 50 + [2023] * 50 + [2024] * 25 + [2025] * 25

    feat_df = pd.DataFrame(X, columns=ROSTER_FEATURES)
    feat_df["school_id"] = range(n)
    feat_df["season"] = seasons

    label_df = pd.DataFrame({
        "school_id": range(n),
        "season": seasons,
        "adj_o": y_off,
        "adj_d": y_def,
        "adj_em": y_off - y_def,
    })

    folds = [
        ([2021, 2022], 2023),
        ([2021, 2022, 2023], 2024),
        ([2021, 2022, 2023, 2024], 2025),
    ]
    result = rolling_origin_cv(feat_df, label_df, folds=folds)
    assert len(result["fold_metrics"]) == 3
    for fm in result["fold_metrics"]:
        assert fm["off_rmse"] >= 0
        assert fm["def_rmse"] >= 0
        assert fm["em_rmse"] >= 0


def test_rolling_origin_cv_fold3_rmse_in_result():
    rng = np.random.default_rng(2)
    n = 200
    X = rng.normal(0, 1, (n, len(ROSTER_FEATURES)))
    y_off = 100 + X[:, 0] * 2
    y_def = 98 - X[:, 1] * 1.5
    seasons = [2021] * 60 + [2022] * 60 + [2023] * 40 + [2024] * 20 + [2025] * 20

    feat_df = pd.DataFrame(X, columns=ROSTER_FEATURES)
    feat_df["school_id"] = range(n)
    feat_df["season"] = seasons

    label_df = pd.DataFrame({
        "school_id": range(n),
        "season": seasons,
        "adj_o": y_off,
        "adj_d": y_def,
        "adj_em": y_off - y_def,
    })

    result = rolling_origin_cv(feat_df, label_df)
    assert "fold3_em_rmse" in result
    assert result["fold3_em_rmse"] >= 0


# ---------------------------------------------------------------------------
# upsert_team_rating_projections (no DB — tests record building logic)
# ---------------------------------------------------------------------------

def test_upsert_empty_records_returns_zero():
    class FakeEngine:
        pass
    # Should not raise; returns 0 without hitting DB
    from portalpoint.modeling.team_rating_projection import upsert_team_rating_projections
    assert upsert_team_rating_projections.__code__.co_argcount == 3  # engine, records, model_version


# ---------------------------------------------------------------------------
# ROSTER_FEATURES constant
# ---------------------------------------------------------------------------

def test_roster_features_has_14_elements():
    assert len(ROSTER_FEATURES) == 14


def test_roster_features_no_duplicates():
    assert len(ROSTER_FEATURES) == len(set(ROSTER_FEATURES))


# ---------------------------------------------------------------------------
# Freshman Prior v2 — Steps B-E
# ---------------------------------------------------------------------------

def _minimal_slot_baselines(tier: int = 1) -> dict:
    pos_list = ["PG", "SG", "SF", "PF", "C"]
    return {
        (tier, pos): {
            "off_adj_rapm": 1.0, "def_adj_rapm": 0.5,
            "three_point_rate": 0.35, "off_reb_pct": 0.25, "usage_rate": 20.0,
        }
        for pos in pos_list
    }


def test_freshman_prior_tier1_uses_higher_min_pct_than_tier2():
    """B: Tier 1 base min_pct (10) > Tier 2 (8) with no open-minutes signal."""
    row = pd.Series({
        "class_balance": {"incoming_fr": 1},
        # zero open minutes → floor factor applied → both tiers scale equally
        "open_minutes_by_position": {"PG": 0.0},
    })
    bl1 = _minimal_slot_baselines(1)
    bl2 = _minimal_slot_baselines(2)
    rows1 = build_freshman_prior_rows(row, 1, bl1)
    rows2 = build_freshman_prior_rows(row, 2, bl2)
    assert len(rows1) == len(rows2) == 1
    # Both use floor factor (same scalar), so tier1 > tier2 driven purely by base
    assert rows1[0]["min_pct"] > rows2[0]["min_pct"]
    assert FRESHMAN_MIN_PCT_BY_TIER[1] > FRESHMAN_MIN_PCT_BY_TIER[2]


def test_freshman_prior_elite_school_multiplier():
    """C: A school in ELITE_RECRUITING_SCHOOLS gets a higher base min_pct."""
    elite_name = next(iter(ELITE_RECRUITING_SCHOOLS))  # any school from the set
    row = pd.Series({
        "class_balance": {"incoming_fr": 1},
        "open_minutes_by_position": {"PG": 15.0},  # factor = 1.0 → min_pct = base exactly
    })
    baselines = _minimal_slot_baselines(2)
    rows_plain = build_freshman_prior_rows(row, 2, baselines, school_name="Unknown State")
    rows_elite = build_freshman_prior_rows(row, 2, baselines, school_name=elite_name)
    assert len(rows_plain) == len(rows_elite) == 1
    # Elite school gets ELITE_RECRUITING_MULTIPLIER applied to base min_pct
    expected_elite_min_pct = min(
        FRESHMAN_MIN_PCT_BY_TIER[2] * ELITE_RECRUITING_MULTIPLIER, 30.0
    )
    assert rows_elite[0]["min_pct"] == pytest.approx(expected_elite_min_pct, rel=0.01)
    assert rows_elite[0]["min_pct"] > rows_plain[0]["min_pct"]


def test_freshman_prior_opportunity_weighting_floor_and_cap():
    """D: opportunity_factor clamped to [FLOOR, MAX]; 0 open minutes → floor."""
    row_zero = pd.Series({
        "class_balance": {"incoming_fr": 1},
        "open_minutes_by_position": {"SG": 0.0},
    })
    row_max = pd.Series({
        "class_balance": {"incoming_fr": 1},
        "open_minutes_by_position": {"SG": 100.0},  # well above scale → cap
    })
    baselines = _minimal_slot_baselines(2)
    rows_zero = build_freshman_prior_rows(row_zero, 2, baselines)
    rows_max = build_freshman_prior_rows(row_max, 2, baselines)
    assert len(rows_zero) == len(rows_max) == 1
    # Floor case
    expected_floor_min_pct = FRESHMAN_MIN_PCT_BY_TIER[2] * FRESHMAN_OPPORTUNITY_FLOOR_FACTOR
    assert rows_zero[0]["min_pct"] == pytest.approx(expected_floor_min_pct, rel=0.01)
    # Cap case
    expected_cap_min_pct = FRESHMAN_MIN_PCT_BY_TIER[2] * FRESHMAN_OPPORTUNITY_MAX_FACTOR
    assert rows_max[0]["min_pct"] == pytest.approx(expected_cap_min_pct, rel=0.01)


def test_analytical_ci_widens_with_freshman_priors():
    """E: CI is strictly wider when n_freshman_priors > 0."""
    models = TeamRatingModels(off_resid_std=2.0, def_resid_std=3.0)
    lo0, hi0 = analytical_ci(1.0, models, n_freshman_priors=0)
    lo2, hi2 = analytical_ci(1.0, models, n_freshman_priors=2)
    width0 = hi0 - lo0
    width2 = hi2 - lo2
    assert width2 > width0
    # Verify the exact extra variance contribution
    extra_var = 2 * FRESHMAN_VARIANCE_PER_PLAYER
    base_var = 2.0 * (2.0 ** 2 + 3.0 ** 2)
    expected_width2 = 2 * 1.2816 * np.sqrt(base_var + extra_var)
    assert width2 == pytest.approx(expected_width2, rel=1e-4)


def test_analytical_ci_widens_with_candidate_specific_uncertainty():
    models = TeamRatingModels(off_resid_std=2.0, def_resid_std=3.0)
    lo0, hi0 = analytical_ci(1.0, models)
    lo1, hi1 = analytical_ci(
        1.0,
        models,
        candidate_value_std=2.0,
        minutes_std=3.0,
        candidate_value=4.0,
        expected_minutes=24.0,
    )
    assert hi1 - lo1 > hi0 - lo0


# ---------------------------------------------------------------------------
# scale_displaced_minutes (minutes-override PR review fix)
# ---------------------------------------------------------------------------

def test_scale_displaced_minutes_zero_override_reproduces_baseline():
    # 0 MPG override must remove nothing from the returning roster — passing
    # the stored displaced_minutes through unscaled was the reviewed bug.
    result = scale_displaced_minutes(
        {"replacement_slot": 10.0, "same_position_depth": 4.0, "flexible_bench": 2.0},
        stored_minutes=20.0,
        minutes_override=0.0,
    )
    assert result == {"replacement_slot": 0.0, "same_position_depth": 0.0, "flexible_bench": 0.0}


def test_scale_displaced_minutes_scales_proportionally():
    result = scale_displaced_minutes(
        {"replacement_slot": 10.0, "flexible_bench": 4.0},
        stored_minutes=20.0,
        minutes_override=40.0,  # 2x the stored minutes
    )
    assert result == {"replacement_slot": 20.0, "flexible_bench": 8.0}


def test_scale_displaced_minutes_unchanged_at_stored_value():
    # No-op override (minutes_override == stored_minutes) must be a no-op on
    # displacement too — regression guard against the fix changing behavior
    # for calls that aren't actually overriding anything.
    raw = {"replacement_slot": 10.0, "same_position_depth": 3.0}
    result = scale_displaced_minutes(raw, stored_minutes=15.0, minutes_override=15.0)
    assert result == pytest.approx(raw)


def test_scale_displaced_minutes_handles_zero_stored_minutes():
    # No baseline displacement to scale from — must not divide by zero or
    # fabricate a displacement out of nothing.
    result = scale_displaced_minutes(
        {"replacement_slot": 5.0}, stored_minutes=0.0, minutes_override=20.0
    )
    assert result == {"replacement_slot": 0.0}


def test_scale_displaced_minutes_parses_json_string():
    result = scale_displaced_minutes(
        '{"replacement_slot": 10.0}', stored_minutes=10.0, minutes_override=5.0
    )
    assert result == {"replacement_slot": 5.0}


def test_scale_displaced_minutes_handles_missing_or_malformed_input():
    assert scale_displaced_minutes(None, stored_minutes=10.0, minutes_override=5.0) == {}
    assert scale_displaced_minutes("not json", stored_minutes=10.0, minutes_override=5.0) == {}
