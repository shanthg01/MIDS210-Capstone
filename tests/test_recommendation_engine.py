"""Unit tests for the 2-stage recommendation engine.

All fixtures use small, deterministic DataFrames — no 120-player mock.
scheme_fit, gap_match, role_fit, and team_impact_fit (Model 6's delta_adj_em,
normalized — see recommendations.team_impact_fit()) are present as fit columns
(current scope). Program Fit is descoped (2026-07-11) and is not a column here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.recommendations import (
    DEFAULT_FIT_WEIGHTS,
    DELTA_ADJ_EM_CLIP,
    TEAM_IMPACT_FIT_NEUTRAL,
    calculate_overall_fit,
    explain_candidate_ranking,
    fixed_team_impact_preferences,
    generate_top_50_candidates,
    refine_to_top_10,
    team_impact_fit,
)
from scripts.run_recommendations import TEAM_RATING_FRESHNESS_SQL, USERS_SQL

# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def base_df() -> pd.DataFrame:
    """Minimal 3-row candidate pool (pre-filtered available players — no availability_status).

    team_impact_fit is neutral (50.0) for all three rows so existing
    scheme/gap/role-only assertions don't need to account for it; tests that
    specifically exercise the team-rating signal build their own small df.
    """
    return pd.DataFrame(
        {
            "player_name": ["Alice", "Bob", "Carol"],
            "position":    ["PG",    "SG",  "SF"],
            "scheme_fit":  [80.0,    60.0,  40.0],
            "gap_match":   [70.0,    50.0,  30.0],
            "role_fit":    [50.0,    95.0,  20.0],
            "team_impact_fit": [50.0, 50.0, 50.0],
            # future — needed by generate_top_50_candidates when Model 5 ready:
            "player_projection": [8.0,  5.0,  2.0],
            "data_confidence":   [0.9,  0.7,  0.5],
        }
    )


@pytest.fixture()
def large_df() -> pd.DataFrame:
    """60-row pool — enough to verify Top-50 cap."""
    rng = np.random.default_rng(42)
    n = 60
    return pd.DataFrame(
        {
            "player_name": [f"P{i}" for i in range(n)],
            "position":    ["PG"] * n,
            "scheme_fit":  rng.uniform(20, 90, n),
            "gap_match":   rng.uniform(20, 90, n),
            "role_fit":    rng.uniform(20, 90, n),
            "team_impact_fit": rng.uniform(0, 100, n),
            # future — needed by generate_top_50_candidates when Model 5 ready:
            "player_projection": rng.uniform(0, 10, n),
            "data_confidence":   rng.uniform(0.5, 1.0, n),
        }
    )


# ── calculate_overall_fit ────────────────────────────────────────────────────

class TestCalculateOverallFit:
    def test_valid_weights_returns_series(self, base_df):
        result = calculate_overall_fit(base_df)
        assert isinstance(result, pd.Series)
        assert len(result) == len(base_df)

    def test_weights_not_summing_to_one_raises(self, base_df):
        with pytest.raises(ValueError, match="sum to 1"):
            calculate_overall_fit(base_df, weights={"scheme_fit": 0.6, "gap_match": 0.6})

    def test_weights_slightly_off_raises(self, base_df):
        with pytest.raises(ValueError):
            calculate_overall_fit(base_df, weights={"scheme_fit": 0.5, "gap_match": 0.49})

    def test_output_in_range(self, base_df):
        result = calculate_overall_fit(base_df)
        assert (result >= 0).all() and (result <= 100).all()

    def test_correct_weighted_value(self, base_df):
        # Alice: scheme_fit=80, gap_match=70, equal weights → 75.0
        result = calculate_overall_fit(base_df, weights={"scheme_fit": 0.5, "gap_match": 0.5})
        assert result.iloc[0] == pytest.approx(75.0)

    def test_asymmetric_weights(self, base_df):
        # Alice: 0.8*80 + 0.2*70 = 64 + 14 = 78
        result = calculate_overall_fit(
            base_df, weights={"scheme_fit": 0.8, "gap_match": 0.2}
        )
        assert result.iloc[0] == pytest.approx(78.0)

    def test_default_weights_sum_to_one(self):
        total = sum(DEFAULT_FIT_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6

    def test_default_weights_include_team_impact_fit(self):
        assert DEFAULT_FIT_WEIGHTS == {
            "scheme_fit": 0.25,
            "gap_match": 0.30,
            "role_fit": 0.25,
            "team_impact_fit": 0.20,
        }
        assert "program_fit" not in DEFAULT_FIT_WEIGHTS  # descoped, 2026-07-11


# ── team_impact_fit ──────────────────────────────────────────────────────────

class TestTeamImpactFit:
    def test_zero_delta_is_neutral(self):
        result = team_impact_fit(pd.Series([0.0]))
        assert result.iloc[0] == pytest.approx(TEAM_IMPACT_FIT_NEUTRAL)

    def test_positive_clip_bound_maps_to_100(self):
        result = team_impact_fit(pd.Series([DELTA_ADJ_EM_CLIP]))
        assert result.iloc[0] == pytest.approx(100.0)

    def test_negative_clip_bound_maps_to_0(self):
        result = team_impact_fit(pd.Series([-DELTA_ADJ_EM_CLIP]))
        assert result.iloc[0] == pytest.approx(0.0)

    def test_values_beyond_clip_saturate(self):
        result = team_impact_fit(pd.Series([-999.0, 999.0]))
        assert result.iloc[0] == pytest.approx(0.0)
        assert result.iloc[1] == pytest.approx(100.0)

    def test_monotonic_in_delta(self):
        deltas = pd.Series([-4.0, -1.0, 0.0, 1.0, 4.0])
        result = team_impact_fit(deltas).tolist()
        assert result == sorted(result)

    def test_nan_propagates(self):
        """Caller (run_recommendations.py) must fillna(0.0) the raw delta_adj_em
        before calling this — a NaN column would otherwise poison
        calculate_overall_fit's weighted sum for that row."""
        result = team_impact_fit(pd.Series([np.nan]))
        assert pd.isna(result.iloc[0])

    def test_fillna_zero_before_transform_is_neutral(self):
        raw_delta = pd.Series([1.0, np.nan, -1.0]).fillna(0.0)
        result = team_impact_fit(raw_delta)
        assert result.iloc[1] == pytest.approx(TEAM_IMPACT_FIT_NEUTRAL)

    def test_neutral_output_constant_is_not_a_raw_delta(self):
        result = team_impact_fit(pd.Series([TEAM_IMPACT_FIT_NEUTRAL]))
        assert result.iloc[0] == pytest.approx(100.0)
        assert result.iloc[0] != TEAM_IMPACT_FIT_NEUTRAL

    def test_output_always_in_0_100_range(self):
        rng = np.random.default_rng(7)
        deltas = pd.Series(rng.uniform(-50, 50, 200))
        result = team_impact_fit(deltas)
        assert ((result >= 0) & (result <= 100)).all()


class TestFixedTeamImpactPreferences:
    @pytest.mark.parametrize(
        "raw_weights",
        [
            (0.25, 0.30, 0.25),
            (0.30, 0.35, 0.35),
            (0.90, 0.05, 0.05),
            (0.0, 0.0, 0.0),
        ],
    )
    def test_reserves_exactly_twenty_percent(self, raw_weights):
        weights = fixed_team_impact_preferences(*raw_weights)
        assert sum(weights.values()) == pytest.approx(1.0)
        assert weights["team_impact_fit_weight"] == pytest.approx(0.20)
        assert sum(
            value for key, value in weights.items() if key != "team_impact_fit_weight"
        ) == pytest.approx(0.80)

    def test_default_split_matches_stage1_weights(self):
        weights = fixed_team_impact_preferences(0.25, 0.30, 0.25)
        assert weights == {
            "scheme_fit_weight": pytest.approx(DEFAULT_FIT_WEIGHTS["scheme_fit"]),
            "gap_match_weight": pytest.approx(DEFAULT_FIT_WEIGHTS["gap_match"]),
            "role_fit_weight": pytest.approx(DEFAULT_FIT_WEIGHTS["role_fit"]),
            "team_impact_fit_weight": pytest.approx(DEFAULT_FIT_WEIGHTS["team_impact_fit"]),
        }

    def test_all_zero_weights_fall_back_to_stage1_defaults(self):
        assert fixed_team_impact_preferences(0.0, 0.0, 0.0) == (
            fixed_team_impact_preferences(0.25, 0.30, 0.25)
        )

    def test_negative_weight_is_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            fixed_team_impact_preferences(-0.1, 0.5, 0.5)


class TestRunnerContracts:
    def test_no_preference_fallback_matches_stage1_component_weights(self):
        assert "COALESCE(up.weight_scheme, 0.25)" in USERS_SQL
        assert "COALESCE(up.weight_gap,    0.30)" in USERS_SQL
        assert "COALESCE(up.weight_role,   0.25)" in USERS_SQL

    def test_team_rating_freshness_is_school_scoped(self):
        assert "school_id = :school_id" in TEAM_RATING_FRESHNESS_SQL
        assert "season = :season" in TEAM_RATING_FRESHNESS_SQL


class TestRecommendationExplanation:
    def test_reports_player_outside_eligible_pool(self, base_df):
        scored = base_df.copy()
        scored["player_id"] = range(1, len(scored) + 1)
        result = explain_candidate_ranking(scored, player_id=999)

        assert result["eligible"] is False
        assert result["selection_stage"] == "not_in_eligible_pool"

    def test_reports_top_50_cutoff_margin(self, large_df):
        scored = large_df.copy()
        scored["player_id"] = range(1, len(scored) + 1)
        selected_ids = set(generate_top_50_candidates(scored)["player_id"].astype(int))
        excluded_id = next(int(pid) for pid in scored["player_id"] if int(pid) not in selected_ids)
        result = explain_candidate_ranking(scored, player_id=excluded_id)

        assert result["selection_stage"] == "top_50_excluded"
        assert result["stage1_rank"] > 50
        assert result["stage1_margin"] < 0
        assert result["weakest_component"] in DEFAULT_FIT_WEIGHTS

    def test_selected_player_has_rank_margin(self, large_df):
        scored = large_df.copy()
        scored["player_id"] = range(1, len(scored) + 1)
        selected_id = int(generate_top_50_candidates(scored).iloc[0]["player_id"])
        result = explain_candidate_ranking(scored, player_id=selected_id)

        assert result["selected"] is True
        assert result["final_rank"] <= 10
        assert result["margin_to_next_rank"] is not None

    def test_explanations_match_canonical_stage_2_ranking(self, large_df):
        scored = large_df.copy()
        scored["player_id"] = range(1, len(scored) + 1)
        preferences = {
            "scheme_fit_weight": 0.55,
            "gap_match_weight": 0.15,
            "role_fit_weight": 0.10,
            "team_impact_fit_weight": 0.20,
        }
        top50 = generate_top_50_candidates(scored)
        top10 = refine_to_top_10(
            top50,
            user_preferences=preferences,
            risk_tolerance="low",
        )
        selected_by_id = {
            int(row.player_id): row for row in top10.itertuples(index=False)
        }

        for player_id in top50["player_id"].astype(int):
            explanation = explain_candidate_ranking(
                scored,
                player_id=player_id,
                user_preferences=preferences,
                risk_tolerance="low",
            )
            assert explanation["selected"] is (player_id in selected_by_id)
            assert explanation["risk_tolerance"] == "low"
            if player_id in selected_by_id:
                ranked = selected_by_id[player_id]
                assert explanation["final_rank"] == ranked.final_rank
                assert explanation["final_score"] == pytest.approx(ranked.final_rec_score)
                assert explanation["confidence_penalty"] == pytest.approx(
                    ranked.confidence_penalty
                )

    def test_invalid_risk_tolerance_is_rejected_for_eligible_player(self, base_df):
        scored = base_df.copy()
        scored["player_id"] = range(1, len(scored) + 1)

        with pytest.raises(ValueError, match="risk_tolerance"):
            explain_candidate_ranking(scored, player_id=1, risk_tolerance="extreme")


# ── generate_top_50_candidates ───────────────────────────────────────────────

class TestGenerateTop50Candidates:
    def test_returns_at_most_50_rows(self, large_df):
        result = generate_top_50_candidates(large_df)
        assert len(result) <= 50

    def test_required_columns_present(self, base_df):
        result = generate_top_50_candidates(base_df)
        for col in ("overall_fit", "stage1_rank_score"):
            assert col in result.columns
        # future — uncomment when predictions table ready:
        # assert "adjusted_projection" in result.columns

    def test_sorted_descending_by_rank_score(self, base_df):
        result = generate_top_50_candidates(base_df)
        scores = result["stage1_rank_score"].tolist()
        assert scores == sorted(scores, reverse=True)

    def test_stage1_rank_score_formula(self, base_df):
        """Verify formula: stage1_rank_score = overall_fit / 100 (current scope).

        future — extend when predictions + team_rating_projections tables ready:
        # assert alice["adjusted_projection"] == pytest.approx(7.2)  # 8.0 * 0.9
        # assert alice["stage1_rank_score"]   == pytest.approx(8.95) # 7.2 + 1.0 + 0.75
        """
        result = generate_top_50_candidates(
            base_df,
            weights={"scheme_fit": 0.5, "gap_match": 0.5},
        )
        alice = result[result["player_name"] == "Alice"].iloc[0]
        assert alice["overall_fit"] == pytest.approx(75.0)
        assert alice["stage1_rank_score"] == pytest.approx(75.0 / 100)

    def test_default_role_fit_can_change_stage1_order(self, base_df):
        """Bob's stronger role fit should beat Alice under default weights
        (team_impact_fit is neutral/50.0 for all three rows in base_df, so it
        doesn't affect this ordering: 0.25*60 + 0.30*50 + 0.25*95 + 0.20*50 = 63.75)."""
        result = generate_top_50_candidates(base_df)
        assert result.loc[0, "player_name"] == "Bob"
        assert result.loc[0, "overall_fit"] == pytest.approx(63.75)

    def test_requires_team_impact_fit_column_when_using_default_weights(self, base_df):
        """Stage 1 does not degrade gracefully like Stage 2 — DEFAULT_FIT_WEIGHTS
        names team_impact_fit, so the caller (run_recommendations.py) must add
        it before calling this, same as any other required fit column."""
        with pytest.raises(KeyError):
            generate_top_50_candidates(base_df.drop(columns=["team_impact_fit"]))

    def test_index_is_reset(self, base_df):
        result = generate_top_50_candidates(base_df)
        assert list(result.index) == list(range(len(result)))


# ── refine_to_top_10 ─────────────────────────────────────────────────────────

class TestRefineToTop10:
    @pytest.fixture()
    def top50(self, large_df):
        return generate_top_50_candidates(large_df)

    def test_returns_at_most_10_rows(self, top50):
        result = refine_to_top_10(top50)
        assert len(result) <= 10

    def test_final_rank_starts_at_one(self, top50):
        result = refine_to_top_10(top50)
        assert result["final_rank"].iloc[0] == 1

    def test_final_rank_sequential(self, top50):
        result = refine_to_top_10(top50)
        assert list(result["final_rank"]) == list(range(1, len(result) + 1))

    def test_final_rank_is_first_column(self, top50):
        result = refine_to_top_10(top50)
        assert result.columns[0] == "final_rank"

    def test_unknown_risk_tolerance_raises(self, top50):
        with pytest.raises(ValueError, match="risk_tolerance"):
            refine_to_top_10(top50, risk_tolerance="extreme")

    def test_required_columns_present(self, top50):
        result = refine_to_top_10(top50)
        for col in ("personalized_fit", "confidence_penalty", "final_rec_score"):
            assert col in result.columns

    def test_sorted_descending_by_final_score(self, top50):
        result = refine_to_top_10(top50)
        scores = result["final_rec_score"].tolist()
        assert scores == sorted(scores, reverse=True)

    def test_user_weights_are_normalized(self, top50):
        """scheme=2, gap=1 must produce identical personalized_fit as scheme=0.667, gap=0.333."""
        result_raw = refine_to_top_10(
            top50,
            user_preferences={"scheme_fit_weight": 2, "gap_match_weight": 1},
        )
        result_norm = refine_to_top_10(
            top50,
            user_preferences={
                "scheme_fit_weight": 2 / 3,
                "gap_match_weight": 1 / 3,
            },
        )
        pd.testing.assert_series_equal(
            result_raw["personalized_fit"].reset_index(drop=True),
            result_norm["personalized_fit"].reset_index(drop=True),
            check_names=False,
        )

    def test_user_role_weight_is_normalized(self, base_df):
        top50 = generate_top_50_candidates(
            base_df,
            weights={"scheme_fit": 0.5, "gap_match": 0.5},
        )
        result = refine_to_top_10(
            top50,
            user_preferences={
                "scheme_fit_weight": 1,
                "gap_match_weight": 1,
                "role_fit_weight": 2,
            },
        )
        bob = result[result["player_name"] == "Bob"].iloc[0]
        expected = (0.25 * 60.0) + (0.25 * 50.0) + (0.50 * 95.0)
        assert bob["personalized_fit"] == pytest.approx(expected)

    def test_missing_future_preference_columns_are_ignored(self, top50):
        result = refine_to_top_10(
            top50,
            user_preferences={
                "scheme_fit_weight": 1,
                "program_fit_weight": 100,
            },
        )
        pd.testing.assert_series_equal(
            result["personalized_fit"].reset_index(drop=True),
            result["scheme_fit"].reset_index(drop=True),
            check_names=False,
        )

    def test_confidence_penalty_is_zero(self, top50):
        """confidence_penalty is always 0.0 for all risk levels until predictions table is ready."""
        for risk in ("low", "medium", "high"):
            result = refine_to_top_10(top50, risk_tolerance=risk)
            assert (result["confidence_penalty"] == 0.0).all(), f"penalty non-zero for risk={risk}"

    def test_accepts_team_impact_fit_weight(self, base_df):
        """Player with the best score on every single component must rank first
        once team_impact_fit_weight is included alongside the other three."""
        df = base_df.copy()
        df["team_impact_fit"] = [100.0, 20.0, 0.0]  # Alice best, Carol worst
        top50 = generate_top_50_candidates(
            df, weights={"scheme_fit": 0.25, "gap_match": 0.25, "role_fit": 0.25, "team_impact_fit": 0.25}
        )
        result = refine_to_top_10(
            top50,
            user_preferences={
                "scheme_fit_weight": 0.25,
                "gap_match_weight": 0.25,
                "role_fit_weight": 0.25,
                "team_impact_fit_weight": 0.25,
            },
        )
        alice = result[result["player_name"] == "Alice"].iloc[0]
        expected = (0.25 * 80.0) + (0.25 * 70.0) + (0.25 * 50.0) + (0.25 * 100.0)
        assert alice["personalized_fit"] == pytest.approx(expected)
        assert result.iloc[0]["player_name"] == "Alice"

    def test_ignores_team_impact_fit_weight_when_column_absent(self, base_df):
        """A school with zero team_rating_projections coverage still ranks —
        the weight is dropped and the remaining 3 columns re-normalize,
        matching the existing missing-column precedent (program_fit_weight)."""
        df = base_df.drop(columns=["team_impact_fit"])
        top50 = generate_top_50_candidates(
            df, weights={"scheme_fit": 1 / 3, "gap_match": 1 / 3, "role_fit": 1 / 3}
        )
        result = refine_to_top_10(
            top50,
            user_preferences={
                "scheme_fit_weight": 1,
                "gap_match_weight": 1,
                "role_fit_weight": 1,
                "team_impact_fit_weight": 1,
            },
        )
        assert len(result) == 3
        bob = result[result["player_name"] == "Bob"].iloc[0]
        expected = (60.0 + 50.0 + 95.0) / 3
        assert bob["personalized_fit"] == pytest.approx(expected)

    # future — uncomment when predictions table ready:
    # def test_confidence_penalty_nonzero_for_low_risk(self, top50):
    #     """'low' risk has floor=0.70; players with data_confidence < 0.70 incur penalty."""
    #     result = refine_to_top_10(top50, risk_tolerance="low")
    #     low_conf = result[result["data_confidence"] < 0.70]
    #     if not low_conf.empty:
    #         assert (low_conf["confidence_penalty"] > 0).all()
    #
    # def test_low_confidence_penalty_formula(self, available_df):
    #     """Manually verify penalty = max(0, floor - conf) * rate."""
    #     top50 = generate_top_50_candidates(available_df, filter_available=False)
    #     result = refine_to_top_10(top50, risk_tolerance="low")
    #     for _, row in result.iterrows():
    #         expected_penalty = max(0.0, 0.70 - row["data_confidence"]) * 2.0
    #         assert row["confidence_penalty"] == pytest.approx(expected_penalty, abs=1e-9)
