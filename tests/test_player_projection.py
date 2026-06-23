import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling import player_projection as pp


def _toy_skill_frame() -> pd.DataFrame:
    """Eight "filler" players anchoring the WG/2026 prior near fg3_pct=0.35,
    plus two test players (indices 0 and 1) whose raw fg3_pct is set far from
    that prior in the test itself, with very different sample-size weight
    (games_played x min_pct share)."""
    n_filler = 8
    base = {
        "player_id": list(range(100, 100 + n_filler)),
        "season": [2026] * n_filler,
        "position": ["WG"] * n_filler,
        "games_played": [30] * n_filler,
        "min_pct": [80.0] * n_filler,
        "fg3_pct": [0.35] * n_filler,
        "rim_pct": [0.60] * n_filler,
        "ft_pct": [0.80] * n_filler,
        "usage_rate": [25.0] * n_filler,
        "assist_rate": [15.0] * n_filler,
        "tov_pct": [12.0] * n_filler,
        "off_reb_pct": [5.0] * n_filler,
        "def_reb_pct": [10.0] * n_filler,
        "steal_pct": [2.0] * n_filler,
        "block_pct": [3.0] * n_filler,
    }
    filler = pd.DataFrame(base)
    test_players = pd.DataFrame({
        "player_id": [1, 2],
        "season": [2026, 2026],
        "position": ["WG", "WG"],
        "games_played": [30, 30],
        "min_pct": [90.0, 5.0],  # player 1: heavy minutes share; player 2: barely played
        "fg3_pct": [0.50, 0.50],
        "rim_pct": [0.60, 0.60],
        "ft_pct": [0.80, 0.80],
        "usage_rate": [25.0, 25.0],
        "assist_rate": [15.0, 15.0],
        "tov_pct": [12.0, 12.0],
        "off_reb_pct": [5.0, 5.0],
        "def_reb_pct": [10.0, 10.0],
        "steal_pct": [2.0, 2.0],
        "block_pct": [3.0, 3.0],
    })
    return pd.concat([test_players, filler], ignore_index=True)


def _synthetic_training_frame(n: int = 60, seed: int = 0) -> pd.DataFrame:
    """Synthetic player-seasons with a known linear relationship between
    shooting_3p (fg3_pct) and off_adj_rapm, so fit_value_model's recovered
    direction can be checked against ground truth rather than just "runs"."""
    rng = np.random.default_rng(seed)
    n_half = n // 2
    seasons = np.repeat([2025, 2026], n_half)
    positions = rng.choice(["WG", "C", "PG"], size=n)
    fg3_pct = rng.uniform(0.25, 0.45, size=n)
    noise = rng.normal(0, 0.5, size=n)
    off_adj_rapm = 10.0 * (fg3_pct - 0.35) + noise
    def_adj_rapm = rng.normal(0, 1.0, size=n)

    df = pd.DataFrame({
        "player_id": np.arange(1, n + 1),
        "season": seasons,
        "position": positions,
        "games_played": rng.integers(10, 32, size=n),
        "min_pct": rng.uniform(20.0, 90.0, size=n),
        "fg3_pct": fg3_pct,
        "rim_pct": rng.uniform(0.45, 0.65, size=n),
        "ft_pct": rng.uniform(0.6, 0.9, size=n),
        "usage_rate": rng.uniform(15.0, 30.0, size=n),
        "assist_rate": rng.uniform(5.0, 25.0, size=n),
        "tov_pct": rng.uniform(8.0, 20.0, size=n),
        "off_reb_pct": rng.uniform(1.0, 12.0, size=n),
        "def_reb_pct": rng.uniform(5.0, 20.0, size=n),
        "steal_pct": rng.uniform(0.5, 3.5, size=n),
        "block_pct": rng.uniform(0.0, 6.0, size=n),
        "off_adj_rapm": off_adj_rapm,
        "def_adj_rapm": def_adj_rapm,
    })
    return df


def test_shrink_skills_shrinks_low_sample_player_more_than_high_sample_player():
    df = _toy_skill_frame()
    # Player 1 plays heavy minutes at a divergent raw rate (less shrinkage
    # expected); player 2 barely plays at the same divergent raw rate (more
    # shrinkage toward the position x season prior expected).
    df.loc[0, "fg3_pct"] = 0.70
    df.loc[0, "min_pct"] = 90.0
    df.loc[1, "fg3_pct"] = 0.70
    df.loc[1, "min_pct"] = 5.0

    out = pp.shrink_skills(df)
    prior = out.loc[0, "prior_shooting_3p"]

    dist_heavy_minutes = abs(out.loc[0, "skill_shooting_3p"] - prior)
    dist_light_minutes = abs(out.loc[1, "skill_shooting_3p"] - prior)
    assert dist_heavy_minutes > dist_light_minutes  # heavy-minutes player stays closer to raw, further from prior


def test_skill_percentiles_flips_direction_for_inverted_skill():
    df = _synthetic_training_frame(n=40)
    shrunk = pp.shrink_skills(df)
    out = pp.skill_percentiles(shrunk)

    # turnover_avoidance is inverted: a player with a *lower* raw tov_pct
    # (better ball security) should get a *higher* percentile.
    low_tov_idx = df["tov_pct"].idxmin()
    high_tov_idx = df["tov_pct"].idxmax()
    assert out.loc[low_tov_idx, "pctile_turnover_avoidance"] > out.loc[high_tov_idx, "pctile_turnover_avoidance"]

    for skill in pp.SKILLS:
        col = f"pctile_{skill}"
        assert out[col].between(0, 100).all()


def test_fit_value_model_recovers_known_positive_relationship():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    model, resid_std = pp.fit_value_model(shrunk, "off_adj_rapm")

    skill_idx = pp.SKILLS.index("shooting_3p")
    assert model.coef_[skill_idx] > 0  # ground truth: off_adj_rapm increases with fg3_pct
    assert resid_std > 0


def test_fit_value_model_raises_on_too_few_labeled_rows():
    df = _synthetic_training_frame(n=20)
    shrunk = pp.shrink_skills(df)
    with pytest.raises(ValueError, match="Too few labeled rows"):
        pp.fit_value_model(shrunk, "off_adj_rapm")


def test_project_value_combines_offense_and_defense_with_symmetric_ci():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    off_model, off_resid = pp.fit_value_model(shrunk, "off_adj_rapm")
    def_model, def_resid = pp.fit_value_model(shrunk, "def_adj_rapm")

    out = pp.project_value(shrunk, off_model, def_model, off_resid, def_resid)

    assert np.allclose(out["value_per_100"], out["off_value_per_100"] + out["def_value_per_100"])
    half_width = (out["value_ci_upper"] - out["value_ci_lower"]) / 2.0
    assert np.allclose(out["value_per_100"] - out["value_ci_lower"], half_width)
    assert np.allclose(out["value_ci_upper"] - out["value_per_100"], half_width)
    assert (out["value_ci_upper"] > out["value_ci_lower"]).all()


def test_build_neutral_records_shape_and_neutral_mode():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    pctiles = pp.skill_percentiles(shrunk)
    off_model, off_resid = pp.fit_value_model(pctiles, "off_adj_rapm")
    def_model, def_resid = pp.fit_value_model(pctiles, "def_adj_rapm")
    projected = pp.project_value(pctiles, off_model, def_model, off_resid, def_resid)

    records = pp.build_neutral_records(projected.head(5))
    assert len(records) == 5
    for rec in records:
        player_id, school_id, season, mode = rec[0], rec[1], rec[2], rec[3]
        model_version = rec[-3]
        assert isinstance(player_id, int)
        assert school_id is None
        assert season == 2025 or season == 2026
        assert mode == "neutral"
        assert model_version == pp.MODEL_VERSION
