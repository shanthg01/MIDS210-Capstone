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
