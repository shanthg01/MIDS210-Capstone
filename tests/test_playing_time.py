from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from portalpoint.modeling import playing_time as pt
from portalpoint.modeling.io import get_sync_engine

TEST_PLAYER_ID = 42
TEST_SCHOOL_ID = 9900302
TEST_SEASON = 2026


def _cleanup_playing_time_rows() -> None:
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM playing_time_projections
                WHERE (player_id = :player_id AND school_id = :school_id AND season = :season)
                   OR (player_id = 101 AND school_id = 9900301 AND season = 2031)
                """
            ),
            {"player_id": TEST_PLAYER_ID, "school_id": TEST_SCHOOL_ID, "season": TEST_SEASON},
        )
        conn.execute(
            text(
                """
                DELETE FROM player_team_fit_scores
                WHERE (player_id = :player_id AND school_id = :school_id AND season = :season)
                   OR (player_id = 101 AND school_id = 9900301 AND season = 2031)
                """
            ),
            {"player_id": TEST_PLAYER_ID, "school_id": TEST_SCHOOL_ID, "season": TEST_SEASON},
        )


@pytest.fixture(autouse=True)
def clean_playing_time_rows():
    _cleanup_playing_time_rows()
    yield
    _cleanup_playing_time_rows()


class _ConstantModel:
    def __init__(self, value: float):
        self.value = value

    def predict(self, x):
        return [self.value] * len(x)


def test_label_construction_uses_min_pct():
    frame = pt.prepare_playing_time_frame(
        pd.DataFrame(
            [
                {
                    "actual_min_pct": 62.5,
                    "actual_usage_rate": 21.0,
                    "season": 2026,
                    "prior_min_pct": 50.0,
                    "prior_games_played": 20,
                    "prior_college_seasons": 0,
                    "first_observed_season": 2026,
                    "position": "PG",
                }
            ]
        ),
        include_labels=True,
    )
    assert frame.loc[0, "actual_minutes_share"] == pytest.approx(0.625)
    assert frame.loc[0, "actual_minutes"] == pytest.approx(25.0)
    assert frame.loc[0, "prior_minutes"] == pytest.approx(20.0)
    assert frame.loc[0, "is_no_prior_college_season"] == 1.0
    assert frame.loc[0, "career_season_index"] == 1.0
    assert frame.loc[0, "years_since_first_observed"] == 0.0


def test_neutral_projection_fallback_rate_flags_non_forecast_rows():
    frame = pd.DataFrame(
        [
            {"neutral_projection_model_version": pt.PLAYER_PROJECTION_MODEL_VERSION},
            {"neutral_projection_model_version": pt.PLAYER_PROJECTION_MODEL_VERSION},
            {"neutral_projection_model_version": "player-projection-shrinkage-v2"},
            {"neutral_projection_model_version": None},
        ]
    )
    rate = pt.neutral_projection_fallback_rate(frame)
    assert rate == pytest.approx(0.25)


def test_neutral_projection_fallback_rate_empty_frame():
    assert pt.neutral_projection_fallback_rate(pd.DataFrame()) == 0.0


def test_null_leaking_neutral_projection_features_blanks_fallback_rows_only():
    frame = pd.DataFrame(
        [
            {
                "neutral_projection_model_version": pt.PLAYER_PROJECTION_MODEL_VERSION,
                "value_per_100": 5.0,
                "value_ci_lower": 4.0,
                "value_ci_upper": 6.0,
                "skill_percentiles": {"shooting_3p": 70},
                "uncertainty": 0.2,
            },
            {
                "neutral_projection_model_version": "player-projection-shrinkage-v2",
                "value_per_100": 9.0,
                "value_ci_lower": 8.0,
                "value_ci_upper": 10.0,
                "skill_percentiles": {"shooting_3p": 80},
                "uncertainty": 0.3,
            },
            {
                "neutral_projection_model_version": None,
                "value_per_100": 1.0,
                "value_ci_lower": 0.5,
                "value_ci_upper": 1.5,
                "skill_percentiles": {},
                "uncertainty": 0.1,
            },
        ]
    )
    out = pt.null_leaking_neutral_projection_features(frame)
    # forecast row (row 0) untouched
    assert out.loc[0, "value_per_100"] == 5.0
    # same-season fallback row (row 1) nulled
    for col in pt.NEUTRAL_PROJECTION_FEATURE_COLUMNS:
        assert pd.isna(out.loc[1, col])
    # no-projection-joined row (row 2, model_version is None) untouched — not a leakage risk
    assert out.loc[2, "value_per_100"] == 1.0


def test_compress_usage_to_roster_budget_pulls_excess_toward_open_usage_position():
    frame = pd.DataFrame(
        [
            {
                # exceeds open_usage_position (15.0), high confidence -> pulled less
                "expected_usage": 30.0,
                "open_usage_position": 15.0,
                "roster_reliability": 0.9,
                "rotation_probability_model": 0.95,
                "expected_minutes_share": 0.90,
            },
            {
                # same excess, low confidence -> pulled more than the row above
                "expected_usage": 30.0,
                "open_usage_position": 15.0,
                "roster_reliability": 0.9,
                "rotation_probability_model": 0.10,
                "expected_minutes_share": 0.15,
            },
        ]
    )
    out = pt.compress_usage_to_roster_budget(frame)
    assert out["expected_usage_raw"].tolist() == [30.0, 30.0]
    assert out.loc[0, "expected_usage"] > out.loc[1, "expected_usage"]
    # both still pulled down from the raw 30.0, neither crushed to (or below) the cap
    assert 15.0 < out.loc[1, "expected_usage"] < out.loc[0, "expected_usage"] < 30.0


def test_compress_usage_to_roster_budget_leaves_under_cap_rows_unchanged():
    frame = pd.DataFrame(
        [
            {
                "expected_usage": 12.0,
                "open_usage_position": 20.0,
                "roster_reliability": 0.9,
                "rotation_probability_model": 0.5,
                "expected_minutes_share": 0.5,
            }
        ]
    )
    out = pt.compress_usage_to_roster_budget(frame)
    assert out.loc[0, "expected_usage"] == pytest.approx(12.0)


def test_compress_usage_to_roster_budget_skips_unreliable_roster_context():
    frame = pd.DataFrame(
        [
            {
                "expected_usage": 30.0,
                "open_usage_position": 5.0,
                "roster_reliability": 0.45,
                "rotation_probability_model": 0.5,
                "expected_minutes_share": 0.5,
            }
        ]
    )
    out = pt.compress_usage_to_roster_budget(frame)
    # missing/unreliable roster context -> leave raw usage alone rather than crush toward
    # a cap that isn't really known
    assert out.loc[0, "expected_usage"] == pytest.approx(30.0)


def test_freshman_position_group_priors_use_freshman_rows_only():
    train_df = pd.DataFrame(
        {
            "position": ["PG", "PG", "PG", "C"],
            "is_no_prior_college_season": [1.0, 1.0, 0.0, 1.0],
        }
    )
    y_minutes = np.array([0.20, 0.30, 0.90, 0.40])
    y_usage = np.array([15.0, 17.0, 28.0, 18.0])
    minutes_share_by_group, usage_by_group = pt.freshman_position_group_priors(
        train_df, y_minutes, y_usage
    )
    # guard group's prior averages only the two freshman PG rows (0.20, 0.30), excludes
    # the veteran PG row (0.90)
    assert minutes_share_by_group["guard"] == pytest.approx(0.25)
    assert usage_by_group["guard"] == pytest.approx(16.0)
    assert minutes_share_by_group["big"] == pytest.approx(0.40)


def test_apply_freshman_prior_shrinkage_only_affects_freshman_rows():
    df = pd.DataFrame(
        [
            {"position": "PG", "is_no_prior_college_season": 1.0, "team_adj_em": 0.0},
            {"position": "PG", "is_no_prior_college_season": 0.0, "team_adj_em": 0.0},
        ]
    )
    raw_share = np.array([0.80, 0.80])
    raw_usage = np.array([28.0, 28.0])
    minutes_share_by_group = {grp: 0.20 for grp in pt.POSITION_GROUPS}
    usage_by_group = {grp: 14.0 for grp in pt.POSITION_GROUPS}
    blended_share, blended_usage = pt.apply_freshman_prior_shrinkage(
        df, raw_share, raw_usage, minutes_share_by_group, usage_by_group
    )
    # freshman row (0) pulled toward the lower prior; non-freshman row (1) untouched
    assert blended_share[0] < raw_share[0]
    assert blended_share[1] == pytest.approx(0.80)
    assert blended_usage[0] < raw_usage[0]
    assert blended_usage[1] == pytest.approx(28.0)


def test_apply_freshman_prior_shrinkage_noop_without_group_priors():
    df = pd.DataFrame([{"position": "PG", "is_no_prior_college_season": 1.0}])
    share, usage = pt.apply_freshman_prior_shrinkage(
        df, np.array([0.80]), np.array([28.0]), {}, {}
    )
    assert share[0] == pytest.approx(0.80)
    assert usage[0] == pytest.approx(28.0)


def test_low_sample_player_gets_wider_interval():
    models = pt.PlayingTimeModels(
        minutes_model=_ConstantModel(0.50),
        usage_model=_ConstantModel(20.0),
        lower_model=_ConstantModel(0.45),
        upper_model=_ConstantModel(0.55),
        feature_medians={col: 0.0 for col in pt.NUMERIC_FEATURES},
        train_metrics={},
    )
    base = {
        "player_id": 1,
        "school_id": TEST_SCHOOL_ID,
        "season": 2026,
        "position": "PG",
        "prior_min_pct": 60.0,
        "prior_usage_rate": 20.0,
        "value_ci_lower": 1.0,
        "value_ci_upper": 3.0,
        "gap_match": 70.0,
        "scheme_fit": 70.0,
        "roster_player_count": 12,
        "pos_confidence_pg": 0.95,
        "transfer_match_confidence": 1.0,
    }
    high_sample = {**base, "prior_games_played": 30}
    low_sample = {**base, "player_id": 2, "prior_games_played": 2, "prior_min_pct": 5.0}
    frame = pt.prepare_playing_time_frame(
        pd.DataFrame([high_sample, low_sample]), include_labels=False
    )
    scored = pt.predict_minutes_usage(models, frame)
    widths = scored["minutes_ci_upper"] - scored["minutes_ci_lower"]
    assert widths.iloc[1] > widths.iloc[0]
    for col in (
        "rotation_probability_model",
        "starter_probability_model",
        "heavy_minutes_probability",
        "high_usage_probability",
    ):
        assert scored[col].between(0.0, 1.0).all()


def test_role_fit_score_bounds_and_uncertainty_penalty():
    confident = pd.Series(
        {
            "expected_minutes": 24.0,
            "expected_usage": 20.0,
            "minutes_ci_lower": 21.0,
            "minutes_ci_upper": 27.0,
            "gap_match": 80.0,
            "scheme_fit": 80.0,
            "roster_player_count": 12,
        }
    )
    uncertain = confident.copy()
    uncertain["minutes_ci_lower"] = 8.0
    uncertain["minutes_ci_upper"] = 36.0
    assert 0 <= pt.compute_role_fit_score(confident) <= 100
    assert pt.compute_role_fit_score(confident) > pt.compute_role_fit_score(uncertain)


def test_role_fit_does_not_depend_on_scheme_or_gap_fit():
    base = pd.Series(
        {
            "expected_minutes": 24.0,
            "expected_usage": 20.0,
            "minutes_ci_lower": 18.0,
            "minutes_ci_upper": 30.0,
            "roster_player_count": 12,
            "roster_open_minutes": 40.0,
            "rotation_probability_model": 0.9,
            "starter_probability_model": 0.45,
            "gap_match": 5.0,
            "scheme_fit": 5.0,
        }
    )
    high_fit = base.copy()
    high_fit["gap_match"] = 95.0
    high_fit["scheme_fit"] = 95.0
    assert pt.compute_role_fit_score(base) == pt.compute_role_fit_score(high_fit)


def test_compute_role_fit_scores_matches_per_row_scalar_on_multi_row_frame():
    df = pd.DataFrame(
        [
            {
                "expected_minutes": 24.0,
                "expected_usage": 20.0,
                "minutes_ci_lower": 21.0,
                "minutes_ci_upper": 27.0,
                "roster_player_count": 12,
            },
            {
                "expected_minutes": 8.0,
                "expected_usage": 10.0,
                "minutes_ci_lower": 2.0,
                "minutes_ci_upper": 14.0,
                "roster_player_count": 18,
            },
        ]
    )
    vectorized = pt.compute_role_fit_scores(df)
    scalar = [pt.compute_role_fit_score(row) for _, row in df.iterrows()]
    assert vectorized.tolist() == pytest.approx(scalar)


def test_compute_role_fit_scores_handles_entirely_missing_optional_columns():
    # Regression: df.get(name, default) returns the bare scalar default when the column
    # doesn't exist at all (not a broadcast Series) — chaining .fillna()/.to_numpy() on
    # that crashed with AttributeError before _numeric_column_or_default fixed it. Only
    # the columns build_playing_time_records always has present are included here.
    df = pd.DataFrame([{"expected_minutes": 20.0, "expected_usage": 18.0}])
    score = pt.compute_role_fit_scores(df)
    assert len(score) == 1
    assert 0.0 <= score[0] <= 100.0


def test_derive_usage_roles_matches_per_row_scalar_on_multi_row_frame():
    df = pd.DataFrame(
        [
            {
                "expected_usage": 27.0,
                "expected_minutes": 28.0,
                "archetype_label": "Lead Scoring Playmaker",
                "sample_reliability": 0.9,
                "skill_percentiles": {"passing_creation": 85},
            },
            {
                "expected_usage": 8.0,
                "expected_minutes": 6.0,
                "archetype_label": "",
                "sample_reliability": 0.2,
                "skill_percentiles": {},
            },
        ]
    )
    roles, confidences = pt.derive_usage_roles(df)
    scalar_results = [pt.derive_usage_role(row) for _, row in df.iterrows()]
    assert list(roles) == [r[0] for r in scalar_results]
    assert confidences.tolist() == pytest.approx([r[1] for r in scalar_results])


def test_derive_usage_roles_handles_entirely_missing_optional_columns():
    df = pd.DataFrame([{"expected_usage": 5.0, "expected_minutes": 4.0}])
    roles, confidences = pt.derive_usage_roles(df)
    assert roles[0] == "depth"
    assert 0.25 <= confidences[0] <= 0.95


def test_data_quality_flags_batch_handles_entirely_missing_optional_columns():
    df = pd.DataFrame([{"expected_minutes": 20.0}])
    flags = pt.data_quality_flags_batch(df)
    assert len(flags) == 1
    assert flags[0]["missing_feature_count"] == 0
    assert 0.60 <= flags[0]["uncertainty_multiplier"] <= 2.10


def test_allocate_displaced_minutes_batch_handles_entirely_missing_optional_columns():
    df = pd.DataFrame([{"expected_minutes": 20.0}])
    displaced = pt.allocate_displaced_minutes_batch(df)
    assert len(displaced) == 1
    total = sum(displaced[0].values())
    assert total == pytest.approx(20.0, abs=0.01)


def test_uncertainty_multipliers_matches_scalar_on_multi_row_frame():
    df = pd.DataFrame(
        [
            {"sample_reliability": 0.9, "projection_reliability": 0.9, "roster_reliability": 0.9},
            {"sample_reliability": 0.1, "projection_reliability": 0.2, "roster_reliability": 0.3},
        ]
    )
    vectorized = pt.uncertainty_multipliers(df)
    scalar = [pt.uncertainty_multiplier(row) for _, row in df.iterrows()]
    assert vectorized.tolist() == pytest.approx(scalar)


def test_validation_metrics_include_calibration_and_tail_checks():
    scored = pd.DataFrame(
        [
            {
                "actual_minutes": 34.0,
                "expected_minutes": 31.0,
                "minutes_ci_lower": 25.0,
                "minutes_ci_upper": 37.0,
                "actual_usage_rate": 28.0,
                "expected_usage": 27.0,
                "rotation_probability_model": 0.95,
                "starter_probability_model": 0.82,
                "heavy_minutes_probability": 0.55,
                "high_usage_probability": 0.62,
            },
            {
                "actual_minutes": 10.0,
                "expected_minutes": 12.0,
                "minutes_ci_lower": 8.0,
                "minutes_ci_upper": 18.0,
                "actual_usage_rate": 15.0,
                "expected_usage": 17.0,
                "rotation_probability_model": 0.45,
                "starter_probability_model": 0.15,
                "heavy_minutes_probability": 0.05,
                "high_usage_probability": 0.10,
            },
        ]
    )
    metrics = pt.validate_predictions(scored)
    assert metrics["interval_coverage"] == 1.0
    assert metrics["interval_coverage_error_80pct"] == pytest.approx(0.2)
    assert metrics["minutes_32_plus_actual_rate"] == 0.5
    assert metrics["minutes_32_plus_recall"] == 0.0
    assert 0.0 <= metrics["minutes_distribution_tvd"] <= 1.0
    assert 0.0 <= metrics["starter_brier_24mpg"] <= 1.0
    assert 0.0 <= metrics["high_usage_brier_26"] <= 1.0


def test_usage_role_derivation_is_archetype_aware():
    role, confidence = pt.derive_usage_role(
        pd.Series(
            {
                "expected_minutes": 28.0,
                "expected_usage": 27.0,
                "archetype_label": "Lead Scoring Playmaker",
                "sample_reliability": 0.9,
                "skill_percentiles": {"passing_creation": 85},
            }
        )
    )
    assert role in {"primary_creator", "secondary_creator"}
    assert 0.0 <= confidence <= 1.0


def test_build_records_reuses_precomputed_role_and_role_fit():
    # Deliberately mismatched vs. what derive_usage_role/compute_role_fit_score would
    # produce from these inputs, so a pass means the precomputed values were reused
    # (not silently recomputed and overwritten).
    scored = pd.DataFrame(
        [
            {
                "player_id": TEST_PLAYER_ID,
                "school_id": TEST_SCHOOL_ID,
                "season": 2027,
                "roster_snapshot_id": None,
                "expected_minutes": 22.0,
                "expected_minutes_share": 0.55,
                "minutes_ci_lower": 17.0,
                "minutes_ci_upper": 28.0,
                "expected_usage": 19.5,
                "usage_role": "sentinel_role",
                "usage_role_confidence": 0.111,
                "role_fit": 1.23,
                "rotation_probability_model": 0.90,
                "starter_probability_model": 0.41,
                "heavy_minutes_probability": 0.12,
                "high_usage_probability": 0.10,
                "roster_open_minutes": 40.0,
                "gap_match": 75.0,
                "scheme_fit": 70.0,
                "program_fit": 50.0,
                "weight_gap": 0.20,
                "weight_scheme": 0.30,
                "weight_role": 0.25,
                "weight_program": 0.25,
                "fit_breakdown": {},
            }
        ]
    )
    projection_records, sync_records = pt.build_playing_time_records(scored)
    assert projection_records[0][9] == "sentinel_role"
    assert projection_records[0][10] == pytest.approx(0.111)
    assert projection_records[0][17] == pytest.approx(1.23)
    assert sync_records[0][5] == pytest.approx(1.23)


def test_build_records_include_destination_projection_context():
    scored = pd.DataFrame(
        [
            {
                "player_id": TEST_PLAYER_ID,
                "school_id": TEST_SCHOOL_ID,
                "season": 2027,
                "roster_snapshot_id": None,
                "expected_minutes": 22.0,
                "expected_minutes_share": 0.55,
                "minutes_ci_lower": 17.0,
                "minutes_ci_upper": 28.0,
                "expected_usage": 19.5,
                "expected_usage_raw": 24.0,
                "usage_role": "connector",
                "usage_role_confidence": 0.72,
                "rotation_probability_model": 0.90,
                "starter_probability_model": 0.41,
                "heavy_minutes_probability": 0.12,
                "high_usage_probability": 0.10,
                "roster_open_minutes": 40.0,
                "returning_minutes_position": 30.0,
                "same_position_prior_minutes": 75.0,
                "position_crowding_ratio": 0.75,
                "opportunity_to_prior_minutes_ratio": 1.2,
                "prior_team_rotation_hhi": 0.12,
                "prior_team_rotation_players": 8,
                "prior_minutes": 20.0,
                "sample_reliability": 0.8,
                "projection_reliability": 0.8,
                "roster_reliability": 0.9,
                "position_confidence": 0.9,
                "transfer_match_confidence": 1.0,
                "missing_feature_count": 0,
                "source_stat_season": 2026,
                "roster_context_season": 2026,
                "fit_context_season": 2026,
                "team_context_season": 2026,
                "neutral_projection_model_version": "player-proj-phase2a-fcast-v1",
                "candidate_changes_school": 1,
                "is_portal_candidate": 1,
                "gap_match": 75.0,
                "scheme_fit": 70.0,
                "program_fit": 50.0,
                "weight_gap": 0.20,
                "weight_scheme": 0.30,
                "weight_role": 0.25,
                "weight_program": 0.25,
                "fit_breakdown": {"gap_match": {}, "scheme_fit": {}},
            }
        ]
    )

    projection_records, sync_records = pt.build_playing_time_records(scored)
    opportunity = json.loads(projection_records[0][14])
    role_breakdown = json.loads(sync_records[0][12])

    assert opportunity["target_season"] == 2027
    assert opportunity["source_stat_season"] == 2026
    assert opportunity["roster_context_season"] == 2026
    assert opportunity["neutral_projection_model_version"] == "player-proj-phase2a-fcast-v1"
    assert opportunity["candidate_changes_school"] is True
    assert opportunity["is_portal_candidate"] is True
    assert opportunity["expected_usage_raw"] == pytest.approx(24.0)
    assert role_breakdown["opportunity_drivers"]["target_season"] == 2027


def _seed_playing_time_row() -> None:
    engine = get_sync_engine()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)
    role_breakdown = {
        "projected_minutes": 22.0,
        "confidence_interval": [17.0, 28.0],
        "minutes_ci_lower": 17.0,
        "minutes_ci_upper": 28.0,
        "expected_usage": 19.5,
        "usage_role": "connector",
        "usage_role_confidence": 0.72,
        "starter_probability": 0.41,
        "rotation_probability": 0.9,
        "depth_chart_position": 2,
        "displaced_minutes": {
            "replacement_slot": 8.0,
            "same_position_depth": 9.1,
            "flexible_bench": 4.9,
        },
        "data_quality_flags": {"low_sample": False},
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO playing_time_projections
                    (player_id, school_id, season, expected_minutes, expected_minutes_share,
                     minutes_ci_lower, minutes_ci_upper, expected_usage, usage_role,
                     usage_role_confidence, starter_probability, rotation_probability,
                     displaced_minutes, opportunity_drivers, data_quality_flags,
                     role_fit, model_version, computed_at, expires_at)
                VALUES
                    (:player_id, :school_id, :season, 22.0, 0.55, 17.0, 28.0, 19.5, 'connector',
                     0.72, 0.41, 0.90, '{"replacement_slot": 8.0}'::jsonb,
                     '{"gap_match": 75.0}'::jsonb, '{"low_sample": false}'::jsonb,
                     76.5, :model_version, :computed_at, :expires_at)
                ON CONFLICT ON CONSTRAINT uq_playing_time_projection DO UPDATE SET
                    expected_minutes = EXCLUDED.expected_minutes,
                    expected_minutes_share = EXCLUDED.expected_minutes_share,
                    minutes_ci_lower = EXCLUDED.minutes_ci_lower,
                    minutes_ci_upper = EXCLUDED.minutes_ci_upper,
                    expected_usage = EXCLUDED.expected_usage,
                    usage_role = EXCLUDED.usage_role,
                    usage_role_confidence = EXCLUDED.usage_role_confidence,
                    starter_probability = EXCLUDED.starter_probability,
                    rotation_probability = EXCLUDED.rotation_probability,
                    displaced_minutes = EXCLUDED.displaced_minutes,
                    opportunity_drivers = EXCLUDED.opportunity_drivers,
                    data_quality_flags = EXCLUDED.data_quality_flags,
                    role_fit = EXCLUDED.role_fit,
                    computed_at = EXCLUDED.computed_at,
                    expires_at = EXCLUDED.expires_at
                """
            ),
            {
                "player_id": TEST_PLAYER_ID,
                "school_id": TEST_SCHOOL_ID,
                "season": TEST_SEASON,
                "model_version": pt.MODEL_VERSION,
                "computed_at": now,
                "expires_at": expires,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO player_team_fit_scores
                    (player_id, school_id, season, overall_fit, gap_match, scheme_fit,
                     role_fit, program_fit, weight_gap, weight_scheme, weight_role,
                     weight_program, breakdown, model_version, computed_at, expires_at)
                VALUES
                    (:player_id, :school_id, :season, 69.62, 75.0, 70.0, 76.5, 50.0,
                     0.20, 0.30, 0.25, 0.25,
                     jsonb_build_object('role_fit', cast(:role_breakdown AS jsonb)),
                     :model_version, :computed_at, :expires_at)
                ON CONFLICT ON CONSTRAINT uq_fit_score DO UPDATE SET
                    role_fit = EXCLUDED.role_fit,
                    overall_fit = EXCLUDED.overall_fit,
                    breakdown = EXCLUDED.breakdown,
                    model_version = EXCLUDED.model_version,
                    computed_at = EXCLUDED.computed_at,
                    expires_at = EXCLUDED.expires_at
                """
            ),
            {
                "player_id": TEST_PLAYER_ID,
                "school_id": TEST_SCHOOL_ID,
                "role_breakdown": json.dumps(role_breakdown),
                "season": TEST_SEASON,
                "model_version": pt.MODEL_VERSION,
                "computed_at": now,
                "expires_at": expires,
            },
        )


def test_playing_time_endpoint_requires_auth(client):
    _seed_playing_time_row()
    r = client.get(
        f"/api/players/{TEST_PLAYER_ID}/playing-time?school_id={TEST_SCHOOL_ID}&season={TEST_SEASON}"
    )
    assert r.status_code == 401


def test_playing_time_endpoint_returns_real_row(client, H):
    _seed_playing_time_row()
    r = client.get(
        f"/api/players/{TEST_PLAYER_ID}/playing-time?school_id={TEST_SCHOOL_ID}&season={TEST_SEASON}",
        headers=H,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["player_id"] == str(TEST_PLAYER_ID)
    assert data["school_id"] == TEST_SCHOOL_ID
    assert data["expected_minutes"] == pytest.approx(22.0)
    assert data["minutes_ci_lower"] < data["expected_minutes"] < data["minutes_ci_upper"]
    assert data["usage_role"] == "connector"
    assert data["role_fit"] == pytest.approx(76.5)


def test_fit_score_uses_real_role_breakdown(client, H):
    _seed_playing_time_row()
    r = client.get(
        f"/api/fit-scores?player_id={TEST_PLAYER_ID}&school_id={TEST_SCHOOL_ID}&season={TEST_SEASON}",
        headers=H,
    )
    assert r.status_code == 200
    role = r.json()["breakdown"]["role_fit"]
    assert role["projected_minutes"] == pytest.approx(22.0)
    assert role["usage_role"] == "connector"
    assert role["displaced_minutes"]["replacement_slot"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Promotion gate metric (regression: real false-promotion found 2026-07-14)
# ---------------------------------------------------------------------------

def test_resolve_promotion_gate_metric_returns_real_minutes_rmse():
    metrics = {"minutes_rmse": 5.623, "usage_rmse": 4.86}
    assert pt.resolve_promotion_gate_metric(metrics) == 5.623


def test_resolve_promotion_gate_metric_none_when_missing():
    # Regression: a single-season/population-restricted run has no rolling-origin
    # CV folds, so minutes_rmse is never computed. Previously this silently fell
    # back to train_minutes_share_rmse (a different, incompatible metric) inside
    # scripts/run_playing_time.py's log_mlflow, comparing 0.0754 against a real
    # champion's minutes_rmse=5.623 and producing a false Δ=+98.6% promotion
    # (confirmed real, manually reverted). The fix must return None here, not a
    # substitute value, so the caller skips maybe_promote entirely.
    metrics = {"train_minutes_share_rmse": 0.0754, "usage_rmse": 3.496}
    assert pt.resolve_promotion_gate_metric(metrics) is None


def test_resolve_promotion_gate_metric_none_on_empty_metrics():
    assert pt.resolve_promotion_gate_metric({}) is None


# ---------------------------------------------------------------------------
# build_playing_time_records vectorization equivalence
# (regression: real production bottleneck found 2026-07-22 — the itertuples()
# loop called _as_float() ~20x/row to build two JSON dicts, ~10M Python-level
# calls for one 75-school chunk; this proves the vectorized rewrite is
# bit-for-bit identical to the original per-row implementation, kept as
# _build_playing_time_records_scalar_reference for exactly this comparison)
# ---------------------------------------------------------------------------

def _assert_records_equivalent(vec_row: tuple, ref_row: tuple) -> None:
    """Field-by-field comparison, robust to float rounding noise, JSON key-order
    differences, and the trailing (model_version, now, expires) fields — the last
    two are independently computed via datetime.now(timezone.utc) inside each
    function, so they legitimately differ by microseconds between the two calls."""
    assert len(vec_row) == len(ref_row)
    for vec_val, ref_val in zip(vec_row[:-2], ref_row[:-2]):
        if isinstance(vec_val, float) or isinstance(ref_val, float):
            assert vec_val == pytest.approx(ref_val, abs=1e-9)
        elif isinstance(vec_val, str) and vec_val.startswith(("{", "[")):
            # JSON-string fields — compare parsed, not the raw string (key
            # order isn't guaranteed identical between the two code paths).
            assert json.loads(vec_val) == json.loads(ref_val)
        else:
            assert vec_val == ref_val
    assert isinstance(vec_row[-2], datetime) and isinstance(ref_row[-2], datetime)
    assert isinstance(vec_row[-1], datetime) and isinstance(ref_row[-1], datetime)


def _make_varied_scored_frame(n: int = 24, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        expected_minutes = float(rng.uniform(0.0, 38.0))
        ci_lower = max(expected_minutes - rng.uniform(2.0, 8.0), 0.0)
        ci_upper = expected_minutes + rng.uniform(2.0, 8.0)
        row: dict = {
            "player_id": TEST_PLAYER_ID + i,
            "school_id": TEST_SCHOOL_ID,
            "season": 2027,
            "roster_snapshot_id": None if i % 3 == 0 else 100 + i,
            "expected_minutes": expected_minutes,
            "expected_minutes_share": expected_minutes / 40.0,
            "minutes_ci_lower": ci_lower,
            "minutes_ci_upper": ci_upper,
            "expected_usage": float(rng.uniform(5.0, 35.0)),
            "roster_open_minutes": float(rng.uniform(0.0, 40.0)),
            "returning_minutes_position": float(rng.uniform(0.0, 40.0)),
            "same_position_prior_minutes": float(rng.uniform(0.0, 100.0)),
            "position_crowding_ratio": float(rng.uniform(0.0, 2.0)),
            "opportunity_to_prior_minutes_ratio": float(rng.uniform(0.0, 3.0)),
            "prior_team_rotation_hhi": float(rng.uniform(0.0, 1.0)),
            "prior_team_rotation_players": float(rng.integers(0, 12)),
            "prior_minutes": float(rng.uniform(0.0, 30.0)),
            "sample_reliability": float(rng.uniform(0.0, 1.0)),
            "projection_reliability": float(rng.uniform(0.0, 1.0)),
            "heavy_minutes_probability": float(rng.uniform(0.0, 1.0)),
            "high_usage_probability": float(rng.uniform(0.0, 1.0)),
            "candidate_changes_school": float(rng.integers(0, 2)),
            "is_portal_candidate": float(rng.integers(0, 2)),
            "source_stat_season": 2026,
            "roster_context_season": 2026,
            "fit_context_season": 2026,
            "team_context_season": 2026,
            "neutral_projection_model_version": "player-proj-phase2a-fcast-v1",
            "gap_match": float(rng.uniform(0.0, 100.0)),
            "scheme_fit": float(rng.uniform(0.0, 100.0)),
            "program_fit": 50.0,
            "weight_gap": 0.20,
            "weight_scheme": 0.30,
            "weight_role": 0.25,
            "weight_program": 0.25,
            "fit_breakdown": {"scheme_fit": {"pace_match": 50.0}} if i % 4 == 0 else {},
            "explanation": {"value_drivers": {"top_positive": []}} if i % 5 == 0 else None,
        }
        # Deliberately vary which rows carry model-provided values vs need the
        # scalar-fallback branches (usage_role/confidence, starter/rotation
        # model, role_fit) — the exact branches build_playing_time_records and
        # its scalar reference must agree on.
        if i % 2 == 0:
            row["usage_role"] = ["primary_creator", "connector", "depth", "spacing_specialist"][i % 4]
            row["usage_role_confidence"] = float(rng.uniform(0.3, 0.95))
        if i % 3 != 0:
            row["starter_probability_model"] = float(rng.uniform(0.0, 1.0))
            row["rotation_probability_model"] = float(rng.uniform(0.0, 1.0))
        if i % 4 != 0:
            row["role_fit"] = float(rng.uniform(0.0, 100.0))
        rows.append(row)
    return pd.DataFrame(rows)


def test_build_playing_time_records_matches_scalar_reference_on_varied_frame():
    scored = _make_varied_scored_frame()
    vec_projection, vec_sync = pt.build_playing_time_records(scored)
    ref_projection, ref_sync = pt._build_playing_time_records_scalar_reference(scored)

    assert len(vec_projection) == len(ref_projection) == len(scored)
    for vec_row, ref_row in zip(vec_projection, ref_projection):
        _assert_records_equivalent(vec_row, ref_row)
    for vec_row, ref_row in zip(vec_sync, ref_sync):
        _assert_records_equivalent(vec_row, ref_row)


def test_build_playing_time_records_matches_scalar_reference_on_minimal_frame():
    # Entirely-missing-optional-columns case — same convention as
    # test_compute_role_fit_scores_handles_entirely_missing_optional_columns etc.
    scored = pd.DataFrame(
        [
            {
                "player_id": TEST_PLAYER_ID,
                "school_id": TEST_SCHOOL_ID,
                "season": 2027,
                "expected_minutes": 18.0,
                "expected_minutes_share": 0.45,
                "expected_usage": 15.0,
                "minutes_ci_lower": 12.0,
                "minutes_ci_upper": 24.0,
            }
        ]
    )
    vec_projection, vec_sync = pt.build_playing_time_records(scored)
    ref_projection, ref_sync = pt._build_playing_time_records_scalar_reference(scored)

    assert len(vec_projection) == len(ref_projection) == 1
    _assert_records_equivalent(vec_projection[0], ref_projection[0])
    _assert_records_equivalent(vec_sync[0], ref_sync[0])


def test_build_playing_time_records_empty_frame_returns_empty():
    empty = pd.DataFrame(columns=["player_id", "school_id", "season", "expected_minutes"])
    projection_records, sync_records = pt.build_playing_time_records(empty)
    assert projection_records == []
    assert sync_records == []


# ---------------------------------------------------------------------------
# sync_role_fit_scores: COPY + bulk UPDATE...FROM rewrite (real DB)
# (regression: player_team_fit_scores is a 17GB/~11.15M-row table with 5
# indexes — the old execute_values ON CONFLICT DO UPDATE paid an
# index-probe-and-update cost per ~1000-row batch, ~492 round-trips for one
# 75-school chunk. This proves the COPY-into-temp-table + single bulk
# UPDATE...FROM (plus rare fallback INSERT) rewrite lands the exact same
# resulting row as the original _sync_role_fit_scores_execute_values_reference,
# for both the update-existing-row and insert-missing-row paths.)
# ---------------------------------------------------------------------------

def _one_sync_record(
    player_id: int = TEST_PLAYER_ID,
    school_id: int = TEST_SCHOOL_ID,
    season: int = TEST_SEASON,
    gap_match: float = 61.5,
    scheme_fit: float = 72.25,
    role_fit: float = 44.0,
    program_fit: float = 50.0,
) -> tuple:
    scored = _make_varied_scored_frame(n=1)
    scored.loc[0, "player_id"] = player_id
    scored.loc[0, "school_id"] = school_id
    scored.loc[0, "season"] = season
    scored.loc[0, "gap_match"] = gap_match
    scored.loc[0, "scheme_fit"] = scheme_fit
    scored.loc[0, "role_fit"] = role_fit
    scored.loc[0, "program_fit"] = program_fit
    _, sync_records = pt.build_playing_time_records(scored)
    return sync_records[0]


def _fetch_fit_score_row(player_id: int, school_id: int, season: int) -> dict | None:
    engine = get_sync_engine()
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT overall_fit, gap_match, scheme_fit, role_fit, program_fit,
                           weight_gap, weight_scheme, weight_role, weight_program,
                           breakdown, is_portal_candidate, model_version
                    FROM player_team_fit_scores
                    WHERE player_id = :player_id AND school_id = :school_id AND season = :season
                    """
                ),
                {"player_id": player_id, "school_id": school_id, "season": season},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def test_sync_role_fit_scores_updates_existing_row():
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO player_team_fit_scores
                    (player_id, school_id, season, overall_fit, gap_match, scheme_fit,
                     role_fit, program_fit, weight_gap, weight_scheme, weight_role,
                     weight_program, breakdown, is_portal_candidate, model_version,
                     expires_at)
                VALUES
                    (:player_id, :school_id, :season, 10.0, 5.0, 5.0, 0.0, 50.0,
                     0.20, 0.30, 0.25, 0.25, '{}'::jsonb, false, 'seed-test',
                     now() + interval '1 day')
                """
            ),
            {"player_id": TEST_PLAYER_ID, "school_id": TEST_SCHOOL_ID, "season": TEST_SEASON},
        )

    record = _one_sync_record(role_fit=63.5, gap_match=71.0, scheme_fit=82.0)
    written = pt.sync_role_fit_scores(engine, [record])
    assert written == 1

    row = _fetch_fit_score_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert row is not None
    assert row["role_fit"] == pytest.approx(63.5)
    assert row["gap_match"] == pytest.approx(71.0)
    assert row["scheme_fit"] == pytest.approx(82.0)
    assert row["model_version"] != "seed-test"
    assert "role_fit" in row["breakdown"]


def test_sync_role_fit_scores_inserts_missing_row():
    engine = get_sync_engine()
    assert _fetch_fit_score_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON) is None

    record = _one_sync_record(role_fit=48.0)
    written = pt.sync_role_fit_scores(engine, [record])
    assert written == 1

    row = _fetch_fit_score_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert row is not None
    assert row["role_fit"] == pytest.approx(48.0)


def test_sync_role_fit_scores_matches_execute_values_reference():
    engine = get_sync_engine()
    record = _one_sync_record(role_fit=55.5, gap_match=66.0, scheme_fit=77.0)

    pt._sync_role_fit_scores_execute_values_reference(engine, [record])
    ref_row = _fetch_fit_score_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert ref_row is not None
    _cleanup_playing_time_rows()

    pt.sync_role_fit_scores(engine, [record])
    new_row = _fetch_fit_score_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert new_row is not None

    for key in (
        "overall_fit", "gap_match", "scheme_fit", "role_fit", "program_fit",
        "weight_gap", "weight_scheme", "weight_role", "weight_program",
    ):
        assert new_row[key] == pytest.approx(ref_row[key])
    assert new_row["breakdown"] == ref_row["breakdown"]
    assert new_row["is_portal_candidate"] == ref_row["is_portal_candidate"]


def test_sync_role_fit_scores_empty_records_returns_zero():
    engine = get_sync_engine()
    assert pt.sync_role_fit_scores(engine, []) == 0


# ---------------------------------------------------------------------------
# upsert_playing_time_projections: COPY + bulk UPDATE...FROM rewrite (real DB)
# (regression: same anti-pattern as sync_role_fit_scores, on the sibling write —
# ~492 execute_values round-trips per 491K-row chunk against playing_time_projections
# over an SSM-tunneled connection was the dominant cost in a chunk's score+write+sync
# phase, more than the read step itself, 2026-07-22)
# ---------------------------------------------------------------------------

def _one_projection_record(
    player_id: int = TEST_PLAYER_ID,
    school_id: int = TEST_SCHOOL_ID,
    season: int = TEST_SEASON,
    role_fit: float = 44.0,
) -> tuple:
    scored = _make_varied_scored_frame(n=1)
    scored.loc[0, "player_id"] = player_id
    scored.loc[0, "school_id"] = school_id
    scored.loc[0, "season"] = season
    scored.loc[0, "role_fit"] = role_fit
    projection_records, _ = pt.build_playing_time_records(scored)
    return projection_records[0]


def _fetch_projection_row(player_id: int, school_id: int, season: int) -> dict | None:
    engine = get_sync_engine()
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT expected_minutes, expected_minutes_share, minutes_ci_lower,
                           minutes_ci_upper, expected_usage, usage_role, usage_role_confidence,
                           role_fit, displaced_minutes, opportunity_drivers, data_quality_flags,
                           explanation, model_version
                    FROM playing_time_projections
                    WHERE player_id = :player_id AND school_id = :school_id AND season = :season
                    """
                ),
                {"player_id": player_id, "school_id": school_id, "season": season},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def test_upsert_playing_time_projections_updates_existing_row():
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO playing_time_projections
                    (player_id, school_id, season, expected_minutes, expected_minutes_share,
                     minutes_ci_lower, minutes_ci_upper, expected_usage, usage_role,
                     usage_role_confidence, role_fit, model_version, expires_at)
                VALUES
                    (:player_id, :school_id, :season, 10.0, 0.25, 5.0, 15.0, 8.0, 'depth',
                     0.5, 0.0, :model_version, now() + interval '1 day')
                """
            ),
            {
                "player_id": TEST_PLAYER_ID,
                "school_id": TEST_SCHOOL_ID,
                "season": TEST_SEASON,
                "model_version": pt.MODEL_VERSION,
            },
        )

    record = _one_projection_record(role_fit=63.5)
    written = pt.upsert_playing_time_projections(engine, [record])
    assert written == 1

    row = _fetch_projection_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert row is not None
    assert row["role_fit"] == pytest.approx(63.5)
    assert row["model_version"] == pt.MODEL_VERSION


def test_upsert_playing_time_projections_inserts_missing_row():
    engine = get_sync_engine()
    assert _fetch_projection_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON) is None

    record = _one_projection_record(role_fit=48.0)
    written = pt.upsert_playing_time_projections(engine, [record])
    assert written == 1

    row = _fetch_projection_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert row is not None
    assert row["role_fit"] == pytest.approx(48.0)


def test_upsert_playing_time_projections_matches_execute_values_reference():
    engine = get_sync_engine()
    record = _one_projection_record(role_fit=55.5)

    pt._upsert_playing_time_projections_execute_values_reference(engine, [record])
    ref_row = _fetch_projection_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert ref_row is not None
    _cleanup_playing_time_rows()

    pt.upsert_playing_time_projections(engine, [record])
    new_row = _fetch_projection_row(TEST_PLAYER_ID, TEST_SCHOOL_ID, TEST_SEASON)
    assert new_row is not None

    for key in (
        "expected_minutes", "expected_minutes_share", "minutes_ci_lower", "minutes_ci_upper",
        "expected_usage", "usage_role", "usage_role_confidence", "role_fit",
    ):
        if isinstance(ref_row[key], float):
            assert new_row[key] == pytest.approx(ref_row[key])
        else:
            assert new_row[key] == ref_row[key]
    assert new_row["displaced_minutes"] == ref_row["displaced_minutes"]
    assert new_row["opportunity_drivers"] == ref_row["opportunity_drivers"]
    assert new_row["data_quality_flags"] == ref_row["data_quality_flags"]
    assert new_row["explanation"] == ref_row["explanation"]


def test_upsert_playing_time_projections_empty_records_returns_zero():
    engine = get_sync_engine()
    assert pt.upsert_playing_time_projections(engine, []) == 0


# ---------------------------------------------------------------------------
# _RecordsCSVStream (pure unit, no DB)
# (real fix, not a bandaid, 2026-07-23: sync_role_fit_scores/
# upsert_playing_time_projections building the whole CSV payload as one
# io.StringIO string OOM-killed a real Fargate task at 1GB, 8GB, then 16GB for
# a single ~491,625-row chunk with real JSON-heavy explanation/breakdown
# fields — not a network problem, an in-VPC ECS task hit it. _RecordsCSVStream
# streams the CSV to copy_expert in fixed-size row batches instead. These
# tests prove it reproduces the exact same CSV bytes as the original
# io.StringIO/writerows approach, across read() sizes and batch boundaries,
# so the DB write's content is unaffected by the memory fix.)
# ---------------------------------------------------------------------------

def _reference_csv_text(records: list[tuple]) -> str:
    import csv as csv_module
    import io as io_module

    buf = io_module.StringIO()
    csv_module.writer(buf).writerows(records)
    return buf.getvalue()


def _drain(stream: "pt._RecordsCSVStream", read_size: int) -> str:
    parts = []
    while True:
        chunk = stream.read(read_size)
        if not chunk:
            break
        parts.append(chunk)
    return "".join(parts)


def _varied_csv_records(n: int, seed: int = 11) -> list[tuple]:
    rng = np.random.default_rng(seed)
    records = []
    for i in range(n):
        breakdown = json.dumps({"scheme_fit": {"pace_match": float(rng.uniform(0, 100))}, "note": 'has "quotes", commas, and\nnewlines'})
        records.append((
            1000 + i,
            9900301,
            2026,
            float(rng.uniform(0, 100)),
            float(rng.uniform(0, 100)),
            None if i % 5 == 0 else float(rng.uniform(0, 100)),
            "some,text,with,commas" if i % 3 == 0 else "plain",
            breakdown,
        ))
    return records


def test_records_csv_stream_matches_reference_at_various_read_sizes():
    records = _varied_csv_records(37)
    expected = _reference_csv_text(records)
    for read_size in (1, 7, 64, 1024, -1):
        stream = pt._RecordsCSVStream(records)
        assert _drain(stream, read_size) == expected


def test_records_csv_stream_matches_reference_across_batch_boundary():
    # _BATCH_ROWS is 5000 — exercise a batch boundary with a small override
    # rather than generating 10000+ real rows in a unit test.
    original_batch_rows = pt._RecordsCSVStream._BATCH_ROWS
    pt._RecordsCSVStream._BATCH_ROWS = 3
    try:
        records = _varied_csv_records(10)
        expected = _reference_csv_text(records)
        stream = pt._RecordsCSVStream(records)
        assert _drain(stream, -1) == expected
        # Also verify with a read size smaller than one row's worth of bytes,
        # forcing read() to pull multiple internal batches to satisfy a request.
        stream = pt._RecordsCSVStream(records)
        assert _drain(stream, 5) == expected
    finally:
        pt._RecordsCSVStream._BATCH_ROWS = original_batch_rows


def test_records_csv_stream_empty_records_reads_empty():
    stream = pt._RecordsCSVStream([])
    assert stream.read(-1) == ""
    assert stream.read(100) == ""


def test_records_csv_stream_single_record():
    records = [(1, "a", None, 3.5)]
    expected = _reference_csv_text(records)
    stream = pt._RecordsCSVStream(records)
    assert _drain(stream, -1) == expected
