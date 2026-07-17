from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling.scheme_fit import (
    HE_FEATS,
    PLAYER_SHOT_FEATS,
    compute_scheme_fit_ondemand,
    score_all_seasons,
)


def test_ondemand_scheme_explanation_reconstructs_score() -> None:
    result = compute_scheme_fit_ondemand(
        player_three=0.42,
        player_rim=0.39,
        player_mid=0.19,
        team_three=0.38,
        team_rim=0.44,
        team_mid=0.18,
        feat_ranges={feature: 0.5 for feature in PLAYER_SHOT_FEATS},
        tempo_range=10.0,
    )

    breakdown = result["breakdown"]
    assert [item["feature"] for item in breakdown["cosine_contributions"]] == PLAYER_SHOT_FEATS
    reconstructed = (
        sum(item["contribution"] for item in breakdown["cosine_contributions"])
        + breakdown["cosine_score_adjustment"]
    )
    assert reconstructed == pytest.approx(result["scheme_fit"], abs=2e-6)


def test_ondemand_scheme_explanation_accounts_for_zero_norm_fallback() -> None:
    result = compute_scheme_fit_ondemand(
        player_three=0.0,
        player_rim=0.0,
        player_mid=0.0,
        team_three=0.4,
        team_rim=0.4,
        team_mid=0.2,
        feat_ranges={feature: 0.5 for feature in PLAYER_SHOT_FEATS},
        tempo_range=10.0,
    )

    breakdown = result["breakdown"]
    assert sum(item["contribution"] for item in breakdown["cosine_contributions"]) == 0.0
    assert breakdown["cosine_score_adjustment"] == 50.0


def test_batch_scheme_explanation_reconstructs_persisted_score() -> None:
    season = 2026
    player = {
        "player_id": 1,
        "season": season,
        "three_point_rate": 0.42,
        "rim_rate": 0.39,
        "mid_range_rate": 0.19,
        "current_tempo": 69.0,
        **{feature: np.nan for feature in HE_FEATS},
    }
    team = {
        "school_id": 10,
        "season": season,
        "team_three_rate": 0.38,
        "team_rim_rate": 0.44,
        "team_mid_rate": 0.18,
        "adj_tempo": 70.0,
        "_he_covered": False,
        **{feature: np.nan for feature in HE_FEATS},
    }

    records, _, _ = score_all_seasons(
        pd.DataFrame([player]),
        pd.DataFrame([team]),
        [season],
        tempo_default=68.0,
    )

    assert len(records) == 1
    scheme_score = records[0][5]
    breakdown = json.loads(records[0][12])["scheme"]
    reconstructed = (
        sum(item["contribution"] for item in breakdown["cosine_contributions"])
        + breakdown["cosine_score_adjustment"]
    )
    assert reconstructed == pytest.approx(scheme_score, abs=2e-6)
