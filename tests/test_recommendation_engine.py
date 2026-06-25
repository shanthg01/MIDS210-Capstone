"""Unit tests for the 2-stage recommendation engine.

All fixtures use small, deterministic DataFrames — no 120-player mock.
Only scheme_fit and gap_match are present as fit columns (current scope).
"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from portalpoint.modeling.recommendations import (
    calculate_overall_fit,
    generate_top_50_candidates,
    refine_to_top_10,
    DEFAULT_FIT_WEIGHTS,
)


# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def base_df() -> pd.DataFrame:
    """Minimal 3-row candidate pool (pre-filtered available players — no availability_status)."""
    return pd.DataFrame(
        {
            "player_name": ["Alice", "Bob", "Carol"],
            "position":    ["PG",    "SG",  "SF"],
            "scheme_fit":  [80.0,    60.0,  40.0],
            "gap_match":   [70.0,    50.0,  30.0],
            # future — needed by generate_top_50_candidates when Models 5–6 ready:
            "player_projection": [8.0,  5.0,  2.0],
            "data_confidence":   [0.9,  0.7,  0.5],
            "team_rating_delta": [1.0,  0.5, -1.0],
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
            # future — needed by generate_top_50_candidates when Models 5–6 ready:
            "player_projection": rng.uniform(0, 10, n),
            "data_confidence":   rng.uniform(0.5, 1.0, n),
            "team_rating_delta": rng.uniform(-2, 3, n),
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

    def test_confidence_penalty_is_zero(self, top50):
        """confidence_penalty is always 0.0 for all risk levels until predictions table is ready."""
        for risk in ("low", "medium", "high"):
            result = refine_to_top_10(top50, risk_tolerance=risk)
            assert (result["confidence_penalty"] == 0.0).all(), f"penalty non-zero for risk={risk}"

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
