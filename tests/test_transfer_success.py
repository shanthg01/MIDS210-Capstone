"""Unit tests for portalpoint.modeling.transfer_success.

Pure (no DB) — same pattern as test_team_rating_projection.py /
test_gap_matching.py.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.transfer_success import (
    SHRINKAGE_K,
    attach_similar_transfers,
    build_explanation,
    build_upsert_rows,
    compute_drift,
    compute_success_probability,
    label_transfer_success,
    run_transfer_success_pipeline,
    score_active_candidates,
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
# score_active_candidates
# ---------------------------------------------------------------------------

def test_score_active_candidates_handles_already_scored_historical_frame():
    # Real bug (found running against production data): df_historical here is
    # already-scored output of run_transfer_success_pipeline (carries its own
    # cluster_success_rate/cell_success_rate/cell_n/shrinkage_w/success_tier/
    # has_prior_history columns). Concatenating it with the active frame and
    # re-running compute_success_probability used to collide on those same-named
    # columns (pandas merge suffixes duplicates _x/_y), raising
    # KeyError: 'cluster_success_rate' on the very next .fillna() call.
    historical_rows = [
        _base_row(
            season=2021, player_id=i, actual_value_per_100=5.0, projected_value_per_100=4.0,
            to_school_id=42, player_cluster_label="Test Archetype",
        )
        for i in range(SHRINKAGE_K * 5)
    ]
    df_historical = run_transfer_success_pipeline(_make_df(historical_rows))
    assert "cluster_success_rate" in df_historical.columns  # precondition for the bug

    df_active = pd.DataFrame([{
        "player_id": 999,
        "player_name": "Active Candidate",
        "to_school_id": 42,
        "season": 2022,
        "player_cluster": 1,
        "player_cluster_label": "Test Archetype",
        "team_cluster_label": "Motion / Pack Line",
        "team_offense_cluster_id": 2,
        "team_defense_cluster_id": 3,
    }])

    scored = score_active_candidates(df_active, df_historical, target_season=2022)

    assert len(scored) == 1
    row = scored.iloc[0]
    assert row["season"] == 2022
    assert 0.0 <= row["success_probability"] <= 1.0
    assert row["cell_n"] > 0.0  # matched the sparse historical cell built above

    # Real bug #2 (also found against production, same run): score_active_candidates
    # never called attach_similar_transfers/build_explanation on the forward-scored
    # rows at all — every real row written to transfer_success_scores had NaN
    # explanation and an empty similar_transfers list despite the matching
    # historical cell built above. explanation is a required (non-Optional) field
    # on PredictionResponse, so a NaN here breaks API serialization, not just display.
    assert isinstance(row["explanation"], str) and row["explanation"]
    assert isinstance(row["similar_transfers"], list) and len(row["similar_transfers"]) > 0


# ---------------------------------------------------------------------------
# upsert_transfer_success_scores / build_upsert_rows
# ---------------------------------------------------------------------------

def test_upsert_empty_records_is_noop():
    assert upsert_transfer_success_scores(engine=None, records=[]) == 0


def test_build_upsert_rows_sanitizes_nan_in_similar_transfers():
    # Real bug (found on the first run that actually populated similar_transfers,
    # after the score_active_candidates fix above): a comp dict's minutes_drift/
    # usage_drift can be NaN when either side is missing (compute_drift's
    # subtraction). json.dumps serializes Python float('nan') as the bare
    # token NaN — valid Python JSON, rejected by Postgres's JSON parser with
    # "invalid input syntax for type json ... Token 'NaN' is invalid".
    record = {
        "player_id": 1, "to_school_id": 2, "season": 2027,
        "player_cluster": 1, "team_offense_cluster_id": 2, "team_defense_cluster_id": 3,
        "team_cluster_label": "Motion / Pack Line",
        "success_probability": 0.6, "success_tier": "Moderate",
        "cell_n": 5.0, "shrinkage_w": 0.5,
        "cluster_success_rate": 0.55, "cell_success_rate": 0.6,
        "explanation": "test",
        "similar_transfers": [
            {"player_name": "X", "minutes_drift": float("nan"), "usage_drift": 1.0},
        ],
    }
    rows = build_upsert_rows([record])
    assert len(rows) == 1
    sim_json = rows[0][14]  # similar_transfers column position in the tuple
    parsed = json.loads(sim_json)  # must not raise, and must round-trip through real JSON
    assert parsed[0]["minutes_drift"] is None
    assert parsed[0]["usage_drift"] == 1.0
