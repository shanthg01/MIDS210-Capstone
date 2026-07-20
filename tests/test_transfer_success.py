"""Unit tests for portalpoint.modeling.transfer_success.

Pure (no DB) — same pattern as test_team_rating_projection.py /
test_gap_matching.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.transfer_success import (
    SHRINKAGE_K,
    attach_similar_transfers,
    build_explanation,
    compute_drift,
    compute_success_probability,
    label_transfer_success,
    run_transfer_success_pipeline,
    upsert_transfer_success_scores,
)


def _base_row(**overrides) -> dict:
    row = {
        "transfer_id": 1,
        "player_id": 100,
        "player_name": "Test Player",
        "season": 2024,
        "actual_value_per_100": 5.0,
        "projected_value_per_100": 4.0,
        "post_per": 15.0,
        "pre_per": 14.0,
        "post_minutes_per_game": 22.0,
        "projected_minutes": 20.0,
        "post_usage_rate": 21.0,
        "projected_usage": 19.0,
        "player_cluster": 1,
        "team_offense_cluster_id": 2,
        "team_defense_cluster_id": 3,
        "team_cluster_label": "Motion / Pack Line",
    }
    row.update(overrides)
    return row


def _make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# label_transfer_success
# ---------------------------------------------------------------------------

def test_label_uses_rapm_when_available():
    df = _make_df([_base_row(actual_value_per_100=5.0, projected_value_per_100=4.0)])
    out = label_transfer_success(df)
    assert out.loc[0, "success"] == 1.0
    assert out.loc[0, "success_label"] == True  # noqa: E712
    assert out.loc[0, "label_source"] == "rapm_vs_projection"


def test_label_falls_back_to_per_when_rapm_missing():
    df = _make_df([
        _base_row(actual_value_per_100=np.nan, projected_value_per_100=np.nan, post_per=15.0, pre_per=16.0)
    ])
    out = label_transfer_success(df)
    assert out.loc[0, "success"] == 0.0
    assert out.loc[0, "label_source"] == "per_improvement"


def test_label_unlabeled_when_both_missing():
    df = _make_df([
        _base_row(
            actual_value_per_100=np.nan, projected_value_per_100=np.nan,
            post_per=np.nan, pre_per=np.nan,
        )
    ])
    out = label_transfer_success(df)
    assert pd.isna(out.loc[0, "success"])
    assert out.loc[0, "label_source"] == "missing"


# ---------------------------------------------------------------------------
# compute_drift
# ---------------------------------------------------------------------------

def test_compute_drift_actual_minus_projected():
    df = _make_df([_base_row(post_minutes_per_game=22.0, projected_minutes=20.0,
                              post_usage_rate=21.0, projected_usage=19.0)])
    out = compute_drift(df)
    assert out.loc[0, "minutes_drift"] == pytest.approx(2.0)
    assert out.loc[0, "usage_drift"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# compute_success_probability
# ---------------------------------------------------------------------------

def test_success_probability_first_season_is_uninformative():
    df = label_transfer_success(_make_df([_base_row(season=2021)]))
    out = compute_success_probability(df)
    assert out.loc[0, "cluster_success_rate"] == 0.5
    assert out.loc[0, "cell_success_rate"] == 0.5
    assert out.loc[0, "has_prior_history"] == False  # noqa: E712


def test_success_probability_shrinks_toward_cluster_with_sparse_cell():
    # Two prior seasons of successes for the same player_cluster, but a
    # different (offense, defense) cell than the row being scored — cell_n
    # should be 0 for the scored row, so shrinkage_w must be 0 and the
    # scored probability must equal the cluster rate exactly.
    rows = [
        _base_row(season=2021, player_id=1, team_offense_cluster_id=2, team_defense_cluster_id=3,
                  actual_value_per_100=5.0, projected_value_per_100=4.0),
        _base_row(season=2021, player_id=2, team_offense_cluster_id=2, team_defense_cluster_id=3,
                  actual_value_per_100=5.0, projected_value_per_100=4.0),
        # Scored row: same player_cluster, different team cell -> cell_n=0 for it.
        _base_row(season=2022, player_id=3, team_offense_cluster_id=9, team_defense_cluster_id=9),
    ]
    df = label_transfer_success(_make_df(rows))
    out = compute_success_probability(df)
    scored = out[out["season"] == 2022].iloc[0]
    assert scored["cell_n"] == 0.0
    assert scored["shrinkage_w"] == 0.0
    assert scored["success_probability"] == pytest.approx(scored["cluster_success_rate"])


def test_success_probability_full_shrinkage_matches_cell_rate():
    # cell_n large relative to SHRINKAGE_K -> shrinkage_w approaches 1,
    # success_probability approaches cell_success_rate.
    rows = [
        _base_row(season=2021, player_id=i, actual_value_per_100=5.0, projected_value_per_100=4.0)
        for i in range(SHRINKAGE_K * 20)
    ]
    rows.append(_base_row(season=2022, player_id=9999))
    df = label_transfer_success(_make_df(rows))
    out = compute_success_probability(df)
    scored = out[out["season"] == 2022].iloc[0]
    # decay_lambda < 1 keeps effective cell_n slightly below the raw row count,
    # so shrinkage_w approaches but never reaches 1.0 exactly.
    assert scored["shrinkage_w"] > 0.9
    assert scored["success_probability"] == pytest.approx(scored["cell_success_rate"], abs=0.02)


def test_success_tier_bins_match_probability():
    rows = [
        _base_row(season=2021, player_id=i, actual_value_per_100=5.0, projected_value_per_100=4.0)
        for i in range(SHRINKAGE_K * 20)
    ]
    rows.append(_base_row(season=2022, player_id=9999))
    df = label_transfer_success(_make_df(rows))
    out = compute_success_probability(df)
    scored = out[out["season"] == 2022].iloc[0]
    assert scored["success_tier"] in ["Very Low", "Low", "Moderate", "High", "Very High"]
    # All prior rows succeeded -> probability should land in the top tier.
    assert scored["success_tier"] == "Very High"


# ---------------------------------------------------------------------------
# attach_similar_transfers / build_explanation
# ---------------------------------------------------------------------------

def test_attach_similar_transfers_no_leakage_from_future_season():
    rows = [
        _base_row(season=2023, player_id=1, player_name="Prior Player",
                  actual_value_per_100=5.0, projected_value_per_100=4.0),
        _base_row(season=2024, player_id=2, player_name="Scored Player"),
    ]
    df = label_transfer_success(_make_df(rows))
    df = compute_drift(df)
    out = attach_similar_transfers(df)
    scored_comps = out[out["season"] == 2024].iloc[0]["similar_transfers"]
    assert len(scored_comps) == 1
    assert scored_comps[0]["player_name"] == "Prior Player"

    prior_comps = out[out["season"] == 2023].iloc[0]["similar_transfers"]
    assert prior_comps == []  # nothing strictly before 2023 in this frame


def test_build_explanation_handles_missing_cluster():
    row = pd.Series({"player_cluster": np.nan, "success_probability": 0.3})
    text = build_explanation(row)
    assert "No player archetype assigned" in text
    assert "30%" in text


def test_build_explanation_with_comps():
    row = pd.Series({
        "player_cluster": 1,
        "success_tier": "High",
        "success_probability": 0.72,
        "cell_n": 20.0,
        "team_cluster_label": "Motion",
        "similar_transfers": [{
            "player_name": "Comp Player", "season": 2023, "success_label": True,
            "actual_value_per_100": 5.0, "projected_value_per_100": 4.0,
            "value_vs_projection": 1.0,
        }],
    })
    text = build_explanation(row)
    assert "Comp Player" in text
    assert "backed by real precedent" in text


def test_build_explanation_low_n_flags_broad_average():
    row = pd.Series({
        "player_cluster": 1,
        "success_tier": "Moderate",
        "success_probability": 0.55,
        "cell_n": 1.0,
        "team_cluster_label": "Motion",
        "similar_transfers": [{
            "player_name": "Comp Player", "season": 2023, "success_label": False,
            "actual_value_per_100": 3.0, "projected_value_per_100": 4.0,
            "value_vs_projection": -1.0,
        }],
    })
    text = build_explanation(row)
    assert "broader archetype average" in text


# ---------------------------------------------------------------------------
# run_transfer_success_pipeline (end-to-end, in-memory)
# ---------------------------------------------------------------------------

def test_pipeline_end_to_end_produces_all_columns():
    rows = [
        _base_row(season=2021, player_id=i, actual_value_per_100=5.0, projected_value_per_100=4.0)
        for i in range(5)
    ]
    rows.append(_base_row(season=2022, player_id=100))
    out = run_transfer_success_pipeline(_make_df(rows))
    for col in ["success", "success_label", "minutes_drift", "usage_drift",
                "success_probability", "success_tier", "similar_transfers", "explanation"]:
        assert col in out.columns
    assert out["explanation"].notna().all()


# ---------------------------------------------------------------------------
# upsert_transfer_success_scores
# ---------------------------------------------------------------------------

def test_upsert_empty_records_is_noop():
    assert upsert_transfer_success_scores(engine=None, records=[]) == 0
