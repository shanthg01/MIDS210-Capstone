import json
import pickle

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

    for skill in pp.RAW_RATE_SKILLS:
        col = f"pctile_{skill}"
        assert out[col].between(0, 100).all()


def test_fit_value_model_recovers_known_positive_relationship():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    model, resid_std = pp.fit_value_model(shrunk, "off_adj_rapm")

    # off_adj_rapm's feature set is OFFENSE_SKILLS (2026-06-24 split), not
    # the full SKILLS list -- index against the list actually used to fit.
    skill_idx = pp.OFFENSE_SKILLS.index("shooting_3p")
    coef = model.named_steps["ridge"].coef_
    assert coef[skill_idx] > 0  # ground truth: off_adj_rapm increases with fg3_pct
    assert resid_std > 0


def test_build_design_matrix_zero_pads_a_skill_column_missing_from_the_frame():
    # Real bug (2026-06-24): DEFENSE_SKILLS includes foul_discipline, but
    # the Shrinkage Baseline frames never have skill_foul_discipline at all (no season-grain
    # fouls column). Must zero-pad, not KeyError -- this is exactly what
    # every Shrinkage Baseline def_adj_rapm fit does today.
    df = pd.DataFrame({
        "position": ["WG", "C"],
        "skill_defensive_rebounding": [3.0, 8.0],
        "skill_steal_disruption": [1.5, 0.5],
        "skill_block_rim_protection": [0.2, 2.0],
        # no skill_foul_discipline column at all
    })
    X = pp.build_design_matrix(df, skills=pp.DEFENSE_SKILLS)
    assert "skill_foul_discipline" in X.columns
    assert (X["skill_foul_discipline"] == 0.0).all()
    assert (X["skill_defensive_rebounding"] == df["skill_defensive_rebounding"]).all()


def test_fit_value_model_uses_disjoint_offense_defense_feature_sets():
    # The actual point of the split: off_model literally cannot see
    # defense-only skills, and vice versa -- different coefficient counts,
    # not just different fitted values on the same features.
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    off_model, _ = pp.fit_value_model(shrunk, "off_adj_rapm")
    def_model, _ = pp.fit_value_model(shrunk, "def_adj_rapm")

    n_off_features = len(pp.OFFENSE_SKILLS) + len(pp.POSITIONS)
    n_def_features = len(pp.DEFENSE_SKILLS) + len(pp.POSITIONS)
    assert len(off_model.named_steps["ridge"].coef_) == n_off_features
    assert len(def_model.named_steps["ridge"].coef_) == n_def_features
    assert n_off_features != n_def_features  # genuinely different feature sets, not coincidentally equal-length


def test_fit_value_model_raises_on_too_few_labeled_rows():
    df = _synthetic_training_frame(n=20)
    shrunk = pp.shrink_skills(df)
    with pytest.raises(ValueError, match="Too few labeled rows"):
        pp.fit_value_model(shrunk, "off_adj_rapm")


def test_project_value_combines_offense_minus_raw_defense_with_symmetric_ci():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    off_model, off_resid = pp.fit_value_model(shrunk, "off_adj_rapm")
    def_model, def_resid = pp.fit_value_model(shrunk, "def_adj_rapm")

    out = pp.project_value(shrunk, off_model, def_model, off_resid, def_resid)

    # Hoop Explorer raw def_adj_rapm is lower-is-better, and its published
    # total identity is adj_rapm_margin = off_adj_rapm - def_adj_rapm.
    assert np.allclose(
        out["value_per_100"],
        pp.combine_total_value(out["off_value_per_100"], out["def_value_per_100"]),
    )
    half_width = (out["value_ci_upper"] - out["value_ci_lower"]) / 2.0
    assert np.allclose(out["value_per_100"] - out["value_ci_lower"], half_width)
    assert np.allclose(out["value_ci_upper"] - out["value_per_100"], half_width)
    assert (out["value_ci_upper"] > out["value_ci_lower"]).all()


def test_project_value_ci_width_varies_with_skill_state_uncertainty():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    off_model, off_resid = pp.fit_value_model(shrunk, "off_adj_rapm")
    def_model, def_resid = pp.fit_value_model(shrunk, "def_adj_rapm")

    scored = shrunk.copy()
    for skill in set(pp.OFFENSE_SKILLS + pp.DEFENSE_SKILLS):
        scored[f"skill_var_{skill}"] = np.linspace(0.0, 4.0, len(scored))

    out = pp.project_value(scored, off_model, def_model, off_resid, def_resid)
    widths = out["value_ci_upper"] - out["value_ci_lower"]

    assert widths.nunique() > 1
    assert out["_value_std"].iloc[-1] > out["_value_std"].iloc[0]
    assert out["_skill_state_value_std"].iloc[-1] > out["_skill_state_value_std"].iloc[0]


def test_value_model_extra_features_can_drive_forecast_translation():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    shrunk["source_value_per_100"] = np.linspace(-4.0, 4.0, len(shrunk))
    shrunk["off_adj_rapm"] = shrunk["off_adj_rapm"] + 0.8 * shrunk["source_value_per_100"]

    base_model, base_resid = pp.fit_value_model(shrunk, "off_adj_rapm")
    extra_model, extra_resid = pp.fit_value_model(
        shrunk, "off_adj_rapm", extra_features=["source_value_per_100"],
    )

    base_pred = base_model.predict(pp.build_design_matrix(shrunk, skills=pp.OFFENSE_SKILLS))
    extra_pred = extra_model.predict(
        pp.build_design_matrix(shrunk, skills=pp.OFFENSE_SKILLS, extra_features=["source_value_per_100"])
    )

    assert extra_resid < base_resid
    assert np.corrcoef(extra_pred, shrunk["source_value_per_100"])[0, 1] > np.corrcoef(
        base_pred, shrunk["source_value_per_100"]
    )[0, 1]


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


def test_build_neutral_records_stores_turnover_avoidance_as_higher_is_better():
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    pctiles = pp.skill_percentiles(shrunk)
    off_model, off_resid = pp.fit_value_model(pctiles, "off_adj_rapm")
    def_model, def_resid = pp.fit_value_model(pctiles, "def_adj_rapm")
    projected = pp.project_value(pctiles, off_model, def_model, off_resid, def_resid)

    rec = pp.build_neutral_records(projected.head(1))[0]
    skill_states = json.loads(rec[11])
    explanation = json.loads(rec[14])

    assert skill_states["turnover_avoidance"] <= 0
    assert explanation["skill_state_direction"]["turnover_avoidance"] == "stored_as_negative_rate_so_higher_is_better"


def test_save_artifacts_writes_replayable_bundle(tmp_path):
    df = _synthetic_training_frame(n=80)
    shrunk = pp.shrink_skills(df)
    off_model, off_resid = pp.fit_value_model(shrunk, "off_adj_rapm")
    def_model, def_resid = pp.fit_value_model(shrunk, "def_adj_rapm")

    paths = pp.save_artifacts(tmp_path, off_model, def_model, off_resid, def_resid)

    assert set(paths) == {"off_model", "def_model", "bundle"}
    with open(paths["bundle"], "rb") as f:
        bundle = pickle.load(f)
    assert bundle["metadata"]["model_version"] == pp.MODEL_VERSION
    assert bundle["metadata"]["off_resid_std"] == off_resid
    assert bundle["metadata"]["def_resid_std"] == def_resid
    assert bundle["metadata"]["def_value_target_direction"] == pp.DEF_VALUE_TARGET_DIRECTION
    assert bundle["metadata"]["total_value_formula"] == pp.TOTAL_VALUE_FORMULA
    # Offense/defense split (2026-06-24): two feature-column lists, not one.
    assert bundle["metadata"]["off_feature_columns"] == pp.build_design_matrix(shrunk, skills=pp.OFFENSE_SKILLS).columns.tolist()
    assert bundle["metadata"]["def_feature_columns"] == pp.build_design_matrix(shrunk, skills=pp.DEFENSE_SKILLS).columns.tolist()


# === Merged from tests/test_player_projection_kalman.py (Intra-Season Kalman Smoothing) ===

def test_build_player_sequences_uses_external_prior_when_player_present():
    # Gap D (Issue #37 reconciliation): a player present in external_priors
    # should get their the Shrinkage Baseline shrinkage estimate as prior_mean, not the flat
    # population mean of the observed game-level data.
    df = pd.DataFrame({
        "player_id": [1, 1, 2, 2],
        "y_shooting_3p": [0.50, 0.52, 0.10, 0.12],
        "weight_shooting_3p": [5.0, 6.0, 5.0, 6.0],
    })
    external_priors = pd.DataFrame({
        "player_id": [1],
        "skill_shooting_3p": [0.80],  # deliberately far from player 1's observed ~0.51
        "_weight": [100.0],  # high the Shrinkage Baseline sample weight -> low prior_var
    })
    sequences = pp.build_player_sequences(df, "shooting_3p", external_priors=external_priors)

    _, _, _, prior_mean_p1, prior_var_p1 = sequences[1]
    assert prior_mean_p1 == pytest.approx(0.80)  # used the external prior, not the population mean

    _, _, _, prior_mean_p2, _ = sequences[2]
    assert prior_mean_p2 != pytest.approx(0.80)  # player 2 absent from external_priors -> population fallback


def test_build_player_sequences_external_prior_var_shrinks_with_weight():
    # Higher the Shrinkage Baseline sample weight should mean more confidence in the prior
    # (lower prior_var), same weight/(weight+k) shape the Shrinkage Baseline itself uses.
    df = pd.DataFrame({
        "player_id": [1, 1, 2, 2],
        "y_shooting_3p": [0.40, 0.42, 0.40, 0.42],
        "weight_shooting_3p": [5.0, 6.0, 5.0, 6.0],
    })
    external_priors = pd.DataFrame({
        "player_id": [1, 2],
        "skill_shooting_3p": [0.40, 0.40],
        "_weight": [1.0, 200.0],  # player 2 has far more the Shrinkage Baseline sample weight
    })
    sequences = pp.build_player_sequences(df, "shooting_3p", external_priors=external_priors)
    _, _, _, _, prior_var_low_weight = sequences[1]
    _, _, _, _, prior_var_high_weight = sequences[2]
    assert prior_var_high_weight < prior_var_low_weight


def test_smooth_all_skills_external_priors_df_is_optional_and_backward_compatible():
    # None must reproduce the original flat-population-prior behavior exactly
    # (regression guard for Gap D's signature change).
    rng = np.random.default_rng(0)
    n = 30
    df = pd.DataFrame({
        "player_id": np.repeat(np.arange(5), 6),
        "game_date": pd.to_datetime(["2026-01-01"] * 6 * 5),
        "minutes": rng.uniform(10, 35, size=n),
        "field_goals_made": rng.integers(0, 8, size=n),
        "field_goals_attempted": rng.integers(5, 15, size=n),
        "three_point_field_goals_made": rng.integers(0, 4, size=n),
        "three_point_field_goals_attempted": rng.integers(0, 8, size=n),
        "free_throws_made": rng.integers(0, 5, size=n),
        "free_throws_attempted": rng.integers(0, 6, size=n),
        "offensive_rebounds": rng.integers(0, 4, size=n),
        "defensive_rebounds": rng.integers(0, 6, size=n),
        "assists": rng.integers(0, 6, size=n),
        "steals": rng.integers(0, 3, size=n),
        "blocks": rng.integers(0, 3, size=n),
        "turnovers": rng.integers(0, 4, size=n),
        "fouls": rng.integers(0, 5, size=n),
    })
    obs_df = pp.build_game_observations(df)
    fitted_q_a, merged_a = pp.smooth_all_skills(obs_df)
    fitted_q_b, merged_b = pp.smooth_all_skills(obs_df, external_priors_df=None)
    assert fitted_q_a == fitted_q_b
    pd.testing.assert_frame_equal(merged_a, merged_b)


def test_foul_discipline_is_a_count_rate_skill_built_from_raw_fouls():
    # foul_discipline (2026-06-24): hoopr_player_game_logs.fouls exists at
    # game grain and was never wired in. Same per-40 count-rate shape as
    # turnover_avoidance -- not a special case in build_game_observations.
    assert "foul_discipline" in pp.SKILLS
    df = pd.DataFrame({
        "player_id": [1, 1],
        "game_date": pd.to_datetime(["2026-01-01", "2026-01-03"]),
        "minutes": [30.0, 20.0],
        "field_goals_made": [5, 3], "field_goals_attempted": [10, 8],
        "three_point_field_goals_made": [1, 1], "three_point_field_goals_attempted": [3, 3],
        "free_throws_made": [2, 1], "free_throws_attempted": [3, 2],
        "offensive_rebounds": [1, 0], "defensive_rebounds": [3, 2],
        "assists": [2, 1], "steals": [1, 0], "blocks": [0, 1], "turnovers": [2, 1],
        "fouls": [3, 2],
    })
    obs_df = pp.build_game_observations(df)
    assert obs_df["y_foul_discipline"].tolist() == pytest.approx([3.0 / 30.0 * 40.0, 2.0 / 20.0 * 40.0])
    assert obs_df["weight_foul_discipline"].tolist() == pytest.approx([30.0, 20.0])


def test_r_numerator_shooting_is_bernoulli_variance():
    # p(1-p) at p=0.4 is 0.24 — bounded, roughly constant across realistic
    # shooting percentages, unlike the count-rate case below.
    assert pp._r_numerator("shooting_3p", 0.4) == pytest.approx(0.4 * 0.6)
    assert pp._r_numerator("free_throw_touch", 0.8) == pytest.approx(0.8 * 0.2)


def test_r_numerator_clips_extreme_probabilities():
    # p must stay in (0, 1) for p(1-p) to be a sane variance — guard the
    # edges so a degenerate 0%/100% shooter doesn't zero out the numerator.
    lo = pp._r_numerator("shooting_3p", 0.0)
    hi = pp._r_numerator("shooting_3p", 1.0)
    assert lo > 0
    assert hi > 0


def test_r_numerator_count_rate_scales_with_the_rate_itself():
    # This is the actual bug: a flat numerator of 1.0 was used for every
    # skill regardless of its typical magnitude. Count-rate skills (e.g.
    # turnover_avoidance, mean per-40 rate ~15) need a numerator an order of
    # magnitude larger than shooting skills' ~0.2-0.25 — verify the formula
    # produces that, not a flat constant.
    low_rate_numerator = pp._r_numerator("steal_disruption", 1.0)   # rare event, low per-40 mean
    high_rate_numerator = pp._r_numerator("turnover_avoidance", 15.0)  # common event, high per-40 mean
    assert high_rate_numerator > low_rate_numerator
    assert high_rate_numerator == pytest.approx(15.0 * 40.0)
    assert low_rate_numerator == pytest.approx(1.0 * 40.0)


def test_build_player_sequences_uses_poisson_scaled_r_for_count_skills():
    # End-to-end check that the fix actually reaches R, not just the helper
    # in isolation. A count-rate skill with a high mean rate should get a
    # much larger R (less trusted observations) than the old flat-1
    # numerator would have given for the same minutes played.
    df = pd.DataFrame({
        "player_id": [1, 1, 1],
        "game_date": pd.to_datetime(["2026-01-01", "2026-01-03", "2026-01-05"]),
        "y_turnover_avoidance": [15.0, 16.0, 14.0],
        "weight_turnover_avoidance": [30.0, 30.0, 30.0],
    })
    sequences = pp.build_player_sequences(df, "turnover_avoidance")
    _, R, _, _, _ = sequences[1]
    # Old flat-numerator behavior: R = 1/30 ≈ 0.033. Fixed behavior: R should
    # be roughly (mean_rate * 40) / 30 ≈ (15 * 40) / 30 = 20 — orders of
    # magnitude larger, reflecting that count-rate observations are noisier
    # than the old formula assumed.
    assert R[0] > 1.0  # nowhere near the old ~0.033 scale
    assert R[0] == pytest.approx(15.0 * 40.0 / 30.0, rel=0.2)


def test_q_bounds_widened_to_match_corrected_r_scale():
    # The original (1e-6, 2.0) bound was implicitly tuned against the old,
    # badly undersized R — it would clip a correctly-scaled count-rate
    # skill's true Q. Confirm the shared bound is wide enough not to do that.
    assert pp.Q_BOUNDS[1] >= 50.0


def _synthetic_q_sequences(n_players, true_q, seed=0):
    rng = np.random.default_rng(seed)
    sequences = []
    for _ in range(n_players):
        n_games = rng.integers(10, 20)
        alpha = 0.4
        y, r_arr = [], []
        for _ in range(n_games):
            alpha = alpha + rng.normal(0, np.sqrt(true_q))
            r_t = 0.02
            y.append(alpha + rng.normal(0, np.sqrt(r_t)))
            r_arr.append(r_t)
        sequences.append((np.array(y), np.array(r_arr), np.ones(n_games, dtype=bool), 0.4, 0.1))
    return sequences


def test_fit_q_mle_subsampling_is_deterministic_given_fixed_random_state():
    # Performance change (2026-06-24): same random_state must always pick the
    # same subsample -- a non-deterministic Q fit between reruns of the same
    # data would be a real regression (different production runs giving
    # different Q for no data reason).
    sequences = _synthetic_q_sequences(n_players=2000, true_q=0.01)
    q1, _ = pp.fit_q_mle(sequences, max_sequences_for_search=200, random_state=42)
    q2, _ = pp.fit_q_mle(sequences, max_sequences_for_search=200, random_state=42)
    assert q1 == q2


def test_fit_q_mle_subsample_recovers_similar_q_to_full_population():
    # The actual performance claim: Q is a population-level nuisance
    # parameter, so a few-hundred-player subsample should land close to the
    # full-population fit, not meaningfully different.
    sequences = _synthetic_q_sequences(n_players=2000, true_q=0.01)
    q_full, _ = pp.fit_q_mle(sequences, max_sequences_for_search=None)
    q_subsampled, _ = pp.fit_q_mle(sequences, max_sequences_for_search=300, random_state=1)
    assert q_subsampled == pytest.approx(q_full, rel=0.35)


def test_fit_q_mle_no_subsampling_below_threshold():
    # Population smaller than max_sequences_for_search must use every
    # sequence -- no silent subsampling of an already-small population.
    sequences = _synthetic_q_sequences(n_players=50, true_q=0.01)
    q_a, nll_a = pp.fit_q_mle(sequences, max_sequences_for_search=1000)
    q_b, nll_b = pp.fit_q_mle(sequences, max_sequences_for_search=None)
    assert q_a == pytest.approx(q_b)
    assert nll_a == pytest.approx(nll_b)


# === Merged from tests/test_player_projection_phase2.py (Cross-Season State-Space Model) ===

def test_kalman_filter_with_drift_reduces_to_random_walk_when_rho_one_mu_zero():
    # rho=1, mu=0 should behave identically to a pure random-walk local-level
    # filter — same shape as Phase 1's kalman_filter_series.
    y = np.array([0.30, 0.32, 0.31, 0.29])
    r_arr = np.array([0.02, 0.02, 0.02, 0.02])
    mu = np.zeros(4)
    mask = np.ones(4, dtype=bool)
    a, p_var, pred_mean, pred_var = pp.kalman_filter_with_drift(
        y, r_arr, rho=1.0, mu=mu, q_value=0.01, mask=mask, prior_mean=0.30, prior_var=0.05,
    )
    # filtered estimates should stay close to the (tightly clustered) observations
    assert np.all(np.abs(a - y) < 0.05)
    assert np.all(p_var > 0)


def test_kalman_filter_with_drift_mean_reverts_when_rho_below_one():
    # A player who starts far from the long-run mean (mu/(1-rho)) should drift
    # toward it when rho < 1, even with no observations (mask all False) —
    # this is what makes rho<1 a real mean-reversion mechanism, not just a
    # rescaled random walk.
    y = np.zeros(5)
    r_arr = np.ones(5)
    mu = np.full(5, 0.5)  # long-run mean = mu/(1-rho) = 0.5/0.5 = 1.0
    mask = np.zeros(5, dtype=bool)  # no real observations — pure drift
    a, _, _, _ = pp.kalman_filter_with_drift(
        y, r_arr, rho=0.5, mu=mu, q_value=0.01, mask=mask, prior_mean=10.0, prior_var=1.0,
    )
    # should monotonically approach 1.0 from a far-away starting point of 10.0
    assert a[0] < 10.0
    assert a[-1] < a[0]
    assert a[-1] > 0.9  # converging toward the long-run mean


def test_build_season_sequences_orders_by_season_and_aligns_covariates():
    skill_df = pd.DataFrame({
        "player_id": [1, 1, 2],
        "season": [2022, 2021, 2021],  # deliberately out of order for player 1
        "skill_shooting_3p": [0.40, 0.35, 0.30],
        "skill_var_shooting_3p": [0.01, 0.02, 0.015],
    })
    covariates = pd.DataFrame({
        "player_id": [1, 1, 2],
        "season": [2021, 2022, 2021],
        "career_season_index": [1, 2, 1],
        "transfer_flag": [0.0, 1.0, 0.0],
        "level_change": [0.0, 1.0, 0.0],
    })
    sequences = pp.build_season_sequences(skill_df, covariates, "shooting_3p")
    assert set(sequences.keys()) == {1, 2}

    y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, seasons_arr = sequences[1]
    assert len(y) == 2
    assert y[0] == pytest.approx(0.35)  # 2021 comes first despite skill_df's row order
    assert y[1] == pytest.approx(0.40)  # 2022 second
    assert csi.tolist() == [1, 2]
    assert transfer_flag.tolist() == [0.0, 1.0]
    # the real season, not just positional order -- 2026-06-25 fix's whole point
    assert seasons_arr.tolist() == [2021, 2022]


def test_forecast_next_season_states_advances_target_season_and_transition():
    states = pd.DataFrame({
        "player_id": [1],
        "season": [2024],
        "position": ["WG"],
    })
    for skill in pp.SKILLS:
        states[f"skill_{skill}"] = 10.0
        states[f"skill_var_{skill}"] = 4.0

    covariates = pd.DataFrame({
        "player_id": [1, 1],
        "season": [2024, 2025],
        "career_season_index": [2, 3],
        "transfer_flag": [0.0, 1.0],
        "level_change": [0.0, -1.0],
    })
    fitted_params = {
        skill: {
            "rho": 0.5,
            "beta_0": 1.0,
            "beta_1": 0.1,
            "beta_2": 0.0,
            "beta_3": 2.0,
            "beta_4": -0.5,
            "Q": 0.25,
        }
        for skill in pp.SKILLS
    }

    out = pp.forecast_next_season_states(states, covariates, fitted_params)

    assert out.loc[0, "season"] == 2025
    assert out.loc[0, "source_observed_season"] == 2024
    # 0.5*10 + (1 + 0.1*3 + 2*1 + -0.5*-1)
    assert out.loc[0, "skill_shooting_3p"] == pytest.approx(8.8)
    assert out.loc[0, "skill_var_shooting_3p"] == pytest.approx(1.25)


def _synthetic_sequence(rng, n, true_rho, true_beta1):
    csi = np.arange(1, n + 1, dtype=np.float64)
    alpha = 0.3
    ys = []
    for i in range(n):
        mu = true_beta1 * csi[i]
        alpha = true_rho * alpha + mu + rng.normal(0, 0.005)
        ys.append(alpha + rng.normal(0, 0.01))
    y = np.array(ys)
    r_arr = np.full(n, 0.01**2)
    mask = np.ones(n, dtype=bool)
    seasons_arr = (2020 + csi).astype(np.int64)  # placeholder real seasons, value unused by these fits
    return (y, r_arr, mask, csi, np.zeros(n), np.zeros(n), 0.3, 0.05, seasons_arr)


def test_joint_rho_and_drift_fit_is_not_identifiable_on_short_sequences_alone():
    # Real finding (2026-06-23): jointly fitting rho + drift on short
    # sequences (n=2-4, the real-data median) lands on a different optimum
    # than the true generating params — not a local-minimum artifact, the
    # *true* params score a worse (higher) negative-log-likelihood than the
    # fitted ones. This is why fit_season_model's recommended path always
    # passes a pre-estimated fixed_rho (see estimate_rho_autocorrelation)
    # instead of estimating rho jointly. This test intentionally does NOT
    # assert correct recovery — it characterizes the problem the rest of the
    # module's design works around.
    rng = np.random.default_rng(0)
    true_rho, true_beta1 = 0.7, 0.05
    short_sequences = [_synthetic_sequence(rng, rng.integers(2, 5), true_rho, true_beta1) for _ in range(300)]

    true_params = np.array([true_rho, 0.0, true_beta1, 0.0, 0.0, 0.0, np.log(0.005**2)])
    fitted = pp.fit_season_model(short_sequences)  # joint, no fixed_rho — the naive path
    fitted_params = np.array([
        fitted["rho"], fitted["beta_0"], fitted["beta_1"], fitted["beta_2"],
        fitted["beta_3"], fitted["beta_4"], np.log(fitted["Q"]),
    ])
    nll_at_truth = pp._season_pooled_neg_log_likelihood(true_params, short_sequences)
    nll_at_fit = pp._season_pooled_neg_log_likelihood(fitted_params, short_sequences)
    # the naive joint fit's optimum genuinely beats the true params' likelihood
    # on short sequences — this is the identifiability problem, reproduced.
    assert nll_at_fit <= nll_at_truth


def test_fit_season_model_subsampling_is_deterministic_given_fixed_random_state():
    # Performance change (2026-06-24): a py-spy dump of an actual stuck real
    # run showed fit_season_model's Nelder-Mead search (not the intra-season
    # Q-search) was the true dominant cost. Same fix as fit_q_mle's
    # subsampling: same random_state must always pick the same subsample.
    rng = np.random.default_rng(1)
    sequences = [_synthetic_sequence(rng, rng.integers(2, 6), 0.7, 0.05) for _ in range(2000)]
    fit_a = pp.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=200, random_state=42)
    fit_b = pp.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=200, random_state=42)
    assert fit_a["beta_1"] == fit_b["beta_1"]
    assert fit_a["Q"] == fit_b["Q"]


def test_fit_season_model_subsample_recovers_similar_drift_to_full_population():
    # The actual performance claim: beta_0..4/Q are pooled population-level
    # estimates -- a few-hundred-sequence subsample should land close to the
    # full-population fit's drift direction/magnitude, not meaningfully off.
    rng = np.random.default_rng(2)
    sequences = [_synthetic_sequence(rng, rng.integers(2, 6), 0.7, 0.05) for _ in range(2000)]
    fit_full = pp.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=None)
    fit_sub = pp.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=300, random_state=3)
    assert fit_sub["beta_1"] == pytest.approx(fit_full["beta_1"], abs=0.02)


def test_fit_season_model_no_subsampling_below_threshold():
    # Population smaller than max_sequences_for_search must use every
    # sequence -- no silent subsampling of an already-small population.
    rng = np.random.default_rng(3)
    sequences = [_synthetic_sequence(rng, rng.integers(2, 6), 0.7, 0.05) for _ in range(50)]
    fit_a = pp.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=1000)
    fit_b = pp.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=None)
    assert fit_a["beta_1"] == pytest.approx(fit_b["beta_1"])
    assert fit_a["neg_log_likelihood"] == pytest.approx(fit_b["neg_log_likelihood"])


def _synthetic_skill_and_covariates_frames(rng, n_players, true_rho, true_beta1, skill="shooting_3p"):
    """Builds skill_df/covariates frames (the real shape smooth_season_skill
    consumes) from the same generating process as _synthetic_sequence, with
    a realistic short-career-dominated season-count mix (n=2-4)."""
    rows, cov_rows = [], []
    for player_id in range(n_players):
        n = int(rng.integers(2, 5))
        seq = _synthetic_sequence(rng, n, true_rho, true_beta1)
        y, _, _, csi, transfer_flag, level_change, _, _, _ = seq
        for i in range(n):
            season = 2020 + i
            rows.append({
                "player_id": player_id, "season": season,
                f"skill_{skill}": y[i], f"skill_var_{skill}": 0.01**2,
            })
            cov_rows.append({
                "player_id": player_id, "season": season,
                "career_season_index": csi[i],
                "transfer_flag": transfer_flag[i], "level_change": level_change[i],
            })
    return pd.DataFrame(rows), pd.DataFrame(cov_rows)


def test_estimate_rho_autocorrelation_and_fixed_rho_fit_recovers_drift_direction():
    # Real-shaped data: every player short-career (n=2-4, matching the real
    # population's median), the regime where the naive joint fit (previous
    # test) fails outright. The autocorrelation-based rho estimate plus a
    # fixed-rho drift fit should still recover beta_1's sign, since it never
    # asks the optimizer to trade rho against the trend term.
    rng = np.random.default_rng(1)
    true_rho, true_beta1 = 0.7, 0.05
    skill_df, covariates = _synthetic_skill_and_covariates_frames(rng, 700, true_rho, true_beta1)

    rho = pp.estimate_rho_autocorrelation(skill_df, covariates, "shooting_3p")
    assert rho is not None
    assert 0.2 <= rho <= 0.95  # within the documented clip range, not degenerate

    sequences = pp.build_season_sequences(skill_df, covariates, "shooting_3p")
    fitted = pp.fit_season_model(list(sequences.values()), fixed_rho=rho)
    assert fitted["rho"] == rho
    assert fitted["rho_fixed"] is True
    assert fitted["beta_1"] > 0  # right direction, where the naive joint fit got the wrong sign


def test_compute_block_correlations_detects_perfect_correlation():
    n = 50
    base = np.linspace(-1, 1, n)
    residual_df = pd.DataFrame({
        "std_resid_shooting_3p": base,
        "std_resid_shooting_2p_finishing": base * 2.0,  # perfectly correlated, different scale
        "std_resid_free_throw_touch": -base,             # perfectly anti-correlated
    })
    block_corrs = pp.compute_block_correlations(residual_df)
    assert "shooting_touch" in block_corrs
    corr = block_corrs["shooting_touch"]
    assert corr.loc["std_resid_shooting_3p", "std_resid_shooting_2p_finishing"] == pytest.approx(1.0)
    assert corr.loc["std_resid_shooting_3p", "std_resid_free_throw_touch"] == pytest.approx(-1.0)


def test_compute_block_correlations_skips_blocks_with_insufficient_columns():
    residual_df = pd.DataFrame({"std_resid_offensive_rebounding": [0.1, 0.2, 0.3]})
    block_corrs = pp.compute_block_correlations(residual_df)
    # rebounding block needs 2 columns (offensive + defensive); only 1 present
    assert "rebounding" not in block_corrs


def _rebounding_residual_frame():
    n = 30
    rng = np.random.default_rng(0)
    off_resid = rng.normal(0, 1, n)
    def_resid = 0.6 * off_resid + rng.normal(0, 0.5, n)  # genuinely correlated
    return pd.DataFrame({
        "phase2_skill_offensive_rebounding": np.full(n, 5.0),
        "phase2_skill_var_offensive_rebounding": np.full(n, 1.0),
        "std_resid_offensive_rebounding": off_resid,
        "phase2_skill_defensive_rebounding": np.full(n, 8.0),
        "phase2_skill_var_defensive_rebounding": np.full(n, 1.0),
        "std_resid_defensive_rebounding": def_resid,
    })


def test_blend_block_priors_only_touches_validated_blocks():
    residual_df = _rebounding_residual_frame()
    # shooting_touch is not a validated block — add columns for it too, and
    # confirm it gets no _blended column even though it's structurally present.
    residual_df["phase2_skill_shooting_3p"] = 0.35
    residual_df["phase2_skill_var_shooting_3p"] = 0.01
    residual_df["std_resid_shooting_3p"] = 0.5
    residual_df["phase2_skill_2p_finishing"] = 0.5  # deliberately not matching SKILL_COLUMNS naming
    block_corrs = pp.compute_block_correlations(residual_df)

    blended = pp.blend_block_priors(residual_df, block_corrs)
    assert "phase2_skill_offensive_rebounding_blended" in blended.columns
    assert "phase2_skill_defensive_rebounding_blended" in blended.columns
    assert "phase2_skill_shooting_3p_blended" not in blended.columns  # not a validated block


def test_blend_block_priors_adjustment_direction_matches_correlation_sign():
    residual_df = _rebounding_residual_frame()
    block_corrs = pp.compute_block_correlations(residual_df)
    blended = pp.blend_block_priors(residual_df, block_corrs)

    # offensive_rebounding's blended estimate should move in the same
    # direction as defensive_rebounding's residual sign (positive correlation)
    high_def_resid_rows = blended[blended["std_resid_defensive_rebounding"] > 1.0]
    low_def_resid_rows = blended[blended["std_resid_defensive_rebounding"] < -1.0]
    assert len(high_def_resid_rows) > 0 and len(low_def_resid_rows) > 0
    assert (
        high_def_resid_rows["phase2_skill_offensive_rebounding_blended"].mean()
        > low_def_resid_rows["phase2_skill_offensive_rebounding_blended"].mean()
    )


def test_blend_block_priors_handles_missing_block_correlation_gracefully():
    residual_df = _rebounding_residual_frame()
    blended = pp.blend_block_priors(residual_df, block_correlations={})  # no correlations computed at all
    assert "phase2_skill_offensive_rebounding_blended" not in blended.columns
    # should not raise, should just pass through unchanged
    assert len(blended) == len(residual_df)


def test_attach_game_context_joins_own_and_opponent_correctly():
    game_logs = pd.DataFrame({
        "player_id": [1, 2],
        "school_id": [101, 102],
        "opponent_school_id": [102, 101],
        "home_away": ["home", "away"],
    })
    team_context = pd.DataFrame({
        "school_id": [101, 102],
        "season": [2026, 2026],
        "adj_d": [95.0, 100.0],  # lower adj_d = better defense, by barttorvik convention
        "adj_tempo": [68.0, 72.0],
        "tier": [4, 2],
    })
    result = pp.attach_game_context(game_logs, team_context)

    # player 1 is on school 101, facing opponent 102 -> own pace/tier from 101, opponent_adj_d from 102
    row1 = result[result["player_id"] == 1].iloc[0]
    assert row1["team_pace"] == pytest.approx(68.0)
    assert row1["tier"] == 4
    assert row1["opponent_adj_d"] == pytest.approx(100.0)
    assert row1["home_flag"] == pytest.approx(1.0)

    row2 = result[result["player_id"] == 2].iloc[0]
    assert row2["team_pace"] == pytest.approx(72.0)
    assert row2["opponent_adj_d"] == pytest.approx(95.0)
    assert row2["home_flag"] == pytest.approx(0.0)


def test_fit_context_adjustment_returns_none_on_too_little_data():
    obs_df = pd.DataFrame({
        "y_shooting_3p": [0.3, 0.4],
        "weight_shooting_3p": [5.0, 6.0],
        "opponent_adj_d": [95.0, 100.0],
        "team_pace": [68.0, 70.0],
        "tier": [3, 2],
        "home_flag": [1.0, 0.0],
    })
    model = pp.fit_context_adjustment(obs_df, "shooting_3p")
    assert model is None  # below the 30-row floor


def test_apply_context_adjustment_recovers_known_opponent_effect():
    # Synthetic: true rate = baseline + 0.01 * (opponent_adj_d - 100), no other effects.
    rng = np.random.default_rng(0)
    n = 200
    opponent_adj_d = rng.uniform(85.0, 115.0, n)
    baseline = 0.35
    true_effect = 0.01 * (opponent_adj_d - 100.0)
    y = baseline + true_effect + rng.normal(0, 0.01, n)
    obs_df = pd.DataFrame({
        "y_shooting_3p": y,
        "weight_shooting_3p": np.full(n, 10.0),
        "opponent_adj_d": opponent_adj_d,
        "team_pace": np.full(n, 68.0),  # no variation -> no confound
        "tier": np.full(n, 3),
        "home_flag": rng.integers(0, 2, n).astype(float),
    })
    model = pp.fit_context_adjustment(obs_df, "shooting_3p")
    assert model is not None
    adjusted = pp.apply_context_adjustment(obs_df, "shooting_3p", model)

    # adjusted series should have much less correlation with opponent_adj_d
    # than the raw series did, since the opponent effect has been removed.
    raw_corr = np.corrcoef(obs_df["y_shooting_3p"], opponent_adj_d)[0, 1]
    adjusted_corr = np.corrcoef(adjusted, opponent_adj_d)[0, 1]
    assert abs(adjusted_corr) < abs(raw_corr)
    # re-centered on the same population mean, not shifted wholesale
    assert adjusted.mean() == pytest.approx(obs_df["y_shooting_3p"].mean(), abs=0.02)


def test_apply_context_adjustment_passes_through_unchanged_when_model_is_none():
    obs_df = pd.DataFrame({"y_shooting_3p": [0.3, 0.4, 0.5]})
    result = pp.apply_context_adjustment(obs_df, "shooting_3p", None)
    pd.testing.assert_series_equal(result, obs_df["y_shooting_3p"])


def _synthetic_stage2a_states(n=300, seed=0):
    rng = np.random.default_rng(seed)
    skill_shot_creation_usage = rng.uniform(5.0, 25.0, n)
    skill_shooting_3p = rng.uniform(0.25, 0.45, n)
    skill_shooting_2p_finishing = rng.uniform(0.40, 0.65, n)
    skill_free_throw_touch = rng.uniform(0.6, 0.9, n)
    # known generating relationship: higher shooting_3p skill -> more of the
    # total volume goes toward 3PA, not 2PA -- this is exactly the "attempt
    # share" signal Stage 2A is supposed to recover.
    rate_3pa_attempted = 2.0 + 0.6 * skill_shot_creation_usage * (skill_shooting_3p - 0.25) / 0.20
    rate_2pa_attempted = (skill_shot_creation_usage - rate_3pa_attempted).clip(min=0.5)
    rate_ft_trip = 0.3 * skill_shot_creation_usage + rng.normal(0, 0.2, n)
    return pd.DataFrame({
        "player_id": np.arange(n),
        "season": 2026,
        "position": rng.choice(["PG", "WG", "C"], size=n),
        "total_minutes": rng.uniform(200, 900, n),
        "skill_shot_creation_usage": skill_shot_creation_usage,
        "skill_shooting_3p": skill_shooting_3p,
        "skill_shooting_2p_finishing": skill_shooting_2p_finishing,
        "skill_free_throw_touch": skill_free_throw_touch,
        "skill_passing_creation": rng.uniform(0.5, 6.0, n),
        "skill_offensive_rebounding": rng.uniform(0.2, 4.0, n),
        "skill_defensive_rebounding": rng.uniform(1.0, 8.0, n),
        "skill_steal_disruption": rng.uniform(0.2, 2.5, n),
        "skill_block_rim_protection": rng.uniform(0.0, 3.0, n),
        "skill_turnover_avoidance": rng.uniform(5.0, 18.0, n),
        "rate_3pa_attempted": rate_3pa_attempted,
        "rate_2pa_attempted": rate_2pa_attempted,
        "rate_ft_trip": rate_ft_trip.clip(min=0.0),
    })


def test_fit_attempt_rate_models_recovers_the_attempt_share_signal():
    states = _synthetic_stage2a_states()
    models = pp.fit_attempt_rate_models(states)
    assert set(models.keys()) == set(pp.STAGE_2A_TARGETS)

    X = pp._stage_2a_design_matrix(states)
    pred_3pa = models["rate_3pa_attempted"][0].predict(X)
    # higher shooting_3p skill at the same usage level should predict a
    # higher 3PA attempt rate -- the actual signal Stage 2A exists to find.
    corr = np.corrcoef(pred_3pa, states["skill_shooting_3p"])[0, 1]
    assert corr > 0.3


def test_fit_attempt_rate_models_skips_targets_with_too_few_rows():
    tiny = _synthetic_stage2a_states(n=5)
    models = pp.fit_attempt_rate_models(tiny)
    assert models == {}


def test_project_rates_make_miss_split_uses_shooting_percentage_skill():
    states = _synthetic_stage2a_states(n=200)
    models = pp.fit_attempt_rate_models(states)
    out = pp.project_rates(states, models)

    # the make share of total 2PA rate should track skill_shooting_2p_finishing
    implied_pct = out["rate_2pa_make"] / out["rate_2pa_attempted"].replace(0, np.nan)
    valid = implied_pct.dropna()
    assert valid.corr(states.loc[valid.index, "skill_shooting_2p_finishing"]) > 0.9
    # makes + misses must reconstruct the attempted rate exactly
    assert np.allclose(out["rate_2pa_make"] + out["rate_2pa_miss"], out["rate_2pa_attempted"])


def test_project_rates_stage_2b_is_a_direct_skill_readoff_not_a_model():
    states = _synthetic_stage2a_states(n=50)
    out = pp.project_rates(states, attempt_models={})
    for rate_col, skill in pp.STAGE_2B_RATE_SKILLS.items():
        np.testing.assert_allclose(out[rate_col].to_numpy(), states[f"skill_{skill}"].clip(lower=0.0).to_numpy())


def test_project_rates_per100_uses_given_pace_else_default():
    states = _synthetic_stage2a_states(n=10)
    out_default = pp.project_rates(states, attempt_models={})
    pace = pd.Series([100.0] * len(states))  # 100 poss/40min -> per_100 == per_40
    out_paced = pp.project_rates(states, attempt_models={}, pace=pace)

    assert np.allclose(out_paced["rate_assist_per100"], out_paced["rate_assist"])
    # default pace (68) is lower than 100 -> per_100 should scale UP from per_40
    assert (out_default["rate_assist_per100"] >= out_default["rate_assist"]).all()


def test_compute_attempt_rates_drops_near_zero_minutes_instead_of_blowing_up():
    # Real bug, first real-data run (2026-06-24): clip(lower=1e-6) on minutes
    # let a garbage-time player (seconds of total_minutes, 1 attempt) produce
    # an astronomical per-40 rate that dominated the Ridge fit's residual
    # variance (resid_std ~227,000 on real data). The fix drops low-minutes
    # rows outright instead of computing a garbage rate for them.
    totals = pd.DataFrame({
        "player_id": [1, 2, 3],
        "season": [2026, 2026, 2026],
        "total_minutes": [500.0, 0.0001, 30.0],  # player 2: near-zero minutes
        "fg2a": [100, 1, 20],
        "fg3a": [50, 0, 10],
        "fta": [40, 0, 8],
    })
    out = pp._compute_attempt_rates(totals)
    assert set(out["player_id"]) == {1}  # player 2 (near-zero) and 3 (below 40min floor) both dropped
    assert (out["rate_2pa_attempted"] < 100).all()  # sane per-40 scale, nothing astronomical


def _synthetic_projected_df(n=20, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "player_id": np.arange(n),
        "season": 2026,
        "value_per_100": rng.normal(0, 3, n),
        "value_ci_lower": rng.normal(-5, 1, n),
        "value_ci_upper": rng.normal(5, 1, n),
        "_resid_std": 1.5,
    })
    # build_cross_season_records iterates pp.SKILLS (the Cross-Season model's 11-skill list,
    # includes foul_discipline), not pp.SKILLS (the Shrinkage Baseline's 10, no fouls
    # column at season grain) -- this fixture must match what it actually
    # reads from a real the Cross-Season model frame.
    for s in pp.SKILLS:
        df[f"skill_{s}"] = rng.uniform(0.1, 5.0, n)
        df[f"skill_var_{s}"] = rng.uniform(0.01, 1.0, n)
        df[f"pctile_{s}"] = rng.uniform(0, 100, n)
    return df


def test_build_cross_season_records_inverts_turnover_avoidance_sign():
    df = _synthetic_projected_df(n=5)
    records = pp.build_cross_season_records(df)
    skill_states = json.loads(records[0][11])
    assert skill_states["turnover_avoidance"] == pytest.approx(-df.iloc[0]["skill_turnover_avoidance"], abs=1e-3)
    assert skill_states["shooting_3p"] == pytest.approx(df.iloc[0]["skill_shooting_3p"], abs=1e-3)


def test_build_cross_season_records_uses_distinct_model_version_and_neutral_school_id():
    df = _synthetic_projected_df(n=3)
    df["_residual_std"] = 1.2
    df["_value_std"] = [1.2, 1.5, 1.8]
    df["_skill_state_value_std"] = [0.0, 0.9, 1.34]
    records = pp.build_cross_season_records(df)
    for r in records:
        assert r[1] is None  # school_id -- neutral mode
        assert r[15] == pp.MODEL_VERSION_CROSS_SEASON
    uncertainty = json.loads(records[2][13])
    assert uncertainty["residual_std"] == pytest.approx(1.2)
    assert uncertainty["value_std"] == pytest.approx(1.8)
    assert uncertainty["skill_state_value_std"] == pytest.approx(1.34)


def test_build_cross_season_records_marks_next_season_forecast_explanation():
    df = _synthetic_projected_df(n=1)
    df["source_observed_season"] = 2025
    df["season"] = 2026
    df["source_value_per_100"] = 4.2
    df["source_off_value_per_100"] = 3.1
    df["source_def_value_per_100"] = -1.1
    df["off_value_per_100"] = 3.5
    df["def_value_per_100"] = -0.7
    df["_value_drivers"] = [{
        "top_positive": [{"feature": "source_value_per_100", "component": "offense", "total_value_contribution": 2.1}],
        "top_negative": [{"feature": "skill_turnover_avoidance", "component": "offense", "total_value_contribution": -0.4}],
    }]
    records = pp.build_cross_season_records(df, model_version=pp.MODEL_VERSION_CROSS_SEASON_FORECAST)

    explanation = json.loads(records[0][14])
    assert records[0][2] == 2026
    assert records[0][15] == pp.MODEL_VERSION_CROSS_SEASON_FORECAST
    assert explanation["source"] == "phase2a_next_season_forecast"
    assert explanation["source_observed_season"] == 2025
    assert explanation["target_projected_season"] == 2026
    assert explanation["forecast_horizon_seasons"] == 1
    assert explanation["source_internal_value_prior"]["source_value_per_100"] == pytest.approx(4.2)
    assert explanation["source_internal_value_prior"]["source_off_value_per_100"] == pytest.approx(3.1)
    assert explanation["source_internal_value_prior"]["source_def_value_per_100"] == pytest.approx(-1.1)
    assert explanation["value_components"]["off_value_per_100"] == pytest.approx(3.5)
    assert explanation["value_components"]["raw_def_value_per_100"] == pytest.approx(-0.7)
    assert explanation["value_drivers"]["top_positive"][0]["feature"] == "source_value_per_100"


def test_build_cross_season_records_populates_rates_when_given_else_empty():
    df = _synthetic_projected_df(n=3)
    rates_df = pd.DataFrame({
        "player_id": [0, 1],  # player 2 deliberately missing -> {} for that player
        "season": [2026, 2026],
        "rate_2pa_make": [3.0, 4.0],
        "rate_3pa_make": [1.0, 2.0],
        "rate_ft_trip": [2.0, 3.0],
        "rate_oreb": [1.0, 1.0],
        "rate_dreb": [3.0, 3.0],
        "rate_assist": [2.0, 2.0],
        "rate_stl": [1.0, 1.0],
        "rate_blk": [0.5, 0.5],
        "rate_tov": [2.0, 2.0],
    })
    df["skill_free_throw_touch"] = 0.8
    records = pp.build_cross_season_records(df, projected_rates_df=rates_df)

    rates_player0 = json.loads(records[0][10])
    box_player0 = json.loads(records[0][9])
    assert rates_player0["rate_2pa_make"] == 3.0
    assert box_player0["pts_per_40"] == pytest.approx(2 * 3.0 + 3 * 1.0 + 2.0 * 0.8)

    box_player2 = json.loads(records[2][9])
    rates_player2 = json.loads(records[2][10])
    assert box_player2 == {}
    assert rates_player2 == {}


def test_build_cross_season_records_adds_archetype_metadata_to_explanation_only():
    # Gap E (Issue #37 reconciliation): archetype is evaluation/explanation
    # metadata only -- must land in `explanation`, never touch skill_states,
    # uncertainty, or value_per_100. Missing-archetype players get no extra
    # keys, not a crash.
    df = _synthetic_projected_df(n=3)
    archetypes_df = pd.DataFrame({
        "player_id": [0, 1],  # player 2 deliberately missing
        "season": [2026, 2026],
        "archetype_label": ["3&D Wing", "Post Scoring Big"],
        "confidence": [0.91, 0.74],
    })
    records = pp.build_cross_season_records(df, archetypes_df=archetypes_df)

    explanation0 = json.loads(records[0][14])
    assert explanation0["archetype_label"] == "3&D Wing"
    assert explanation0["archetype_confidence"] == pytest.approx(0.91)
    assert "skill_state_direction" in explanation0  # existing content preserved, not replaced

    skill_states0 = json.loads(records[0][11])
    assert "archetype_label" not in skill_states0  # never leaks into skill_states

    explanation2 = json.loads(records[2][14])
    assert "archetype_label" not in explanation2  # player 2 has no archetype row -- no crash, no extra keys


# === Merged from tests/test_player_projection_eval.py (Evaluation & Calibration) ===

def test_make_rolling_origin_folds_splits_by_season():
    df = pd.DataFrame({"season": [2021, 2022, 2023, 2024, 2025, 2026], "value": range(6)})
    folds = pp.make_rolling_origin_folds(df)
    assert len(folds) == 3

    fold3 = folds[2]
    assert fold3["train"]["season"].tolist() == [2021, 2022, 2023, 2024]
    assert fold3["val"]["season"].tolist() == [2025]
    assert fold3["test"]["season"].tolist() == [2026]
    # folds don't overlap within themselves
    assert set(fold3["train"]["season"]) & set(fold3["val"]["season"]) == set()
    assert set(fold3["val"]["season"]) & set(fold3["test"]["season"]) == set()


def test_compute_regression_metrics_perfect_prediction():
    y_true = [1.0, 2.0, 3.0, 4.0, 5.0]
    metrics = pp.compute_regression_metrics(y_true, y_true)
    assert metrics["rmse"] == pytest.approx(0.0)
    assert metrics["r2"] == pytest.approx(1.0)
    assert metrics["spearman"] == pytest.approx(1.0)
    assert metrics["n"] == 5


def test_compute_regression_metrics_handles_constant_predictions():
    # constant y_pred makes spearman undefined (zero variance) — should
    # degrade to nan, not raise.
    y_true = [1.0, 2.0, 3.0]
    y_pred = [2.0, 2.0, 2.0]
    metrics = pp.compute_regression_metrics(y_true, y_pred)
    assert np.isnan(metrics["spearman"])
    assert np.isfinite(metrics["rmse"])


def test_compute_calibration_full_and_zero_coverage():
    y_true = np.array([1.0, 2.0, 3.0])
    wide_lower, wide_upper = np.array([0.0, 0.0, 0.0]), np.array([10.0, 10.0, 10.0])
    assert pp.compute_calibration(y_true, wide_lower, wide_upper) == pytest.approx(1.0)

    narrow_lower, narrow_upper = np.array([5.0, 5.0, 5.0]), np.array([6.0, 6.0, 6.0])
    assert pp.compute_calibration(y_true, narrow_lower, narrow_upper) == pytest.approx(0.0)


def _synthetic_baseline_frame(n=400, seed=0):
    rng = np.random.default_rng(seed)
    seasons = rng.choice([2021, 2022, 2023, 2024, 2025, 2026], size=n)
    positions = rng.choice(["WG", "C", "PG"], size=n)
    fg3_pct = rng.uniform(0.25, 0.45, size=n)
    off_adj_rapm = 10.0 * (fg3_pct - 0.35) + rng.normal(0, 0.5, size=n)
    def_adj_rapm = rng.normal(0, 1.0, size=n)
    return pd.DataFrame({
        "player_id": np.arange(n),
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


def test_tune_hyperparameters_selects_from_grid_and_falls_back_when_too_few_rows():
    df = _synthetic_baseline_frame(n=400)
    train_df = df[df["season"].isin([2021, 2022, 2023, 2024])]
    val_df = df[df["season"] == 2025]

    k, alpha, grid_df = pp.tune_hyperparameters(train_df, val_df, k_candidates=[4.0, 8.0], alpha_candidates=[1.0, 10.0])
    assert k in [4.0, 8.0]
    assert alpha in [1.0, 10.0]
    assert not grid_df.empty
    assert set(grid_df.columns) >= {"k", "alpha", "val_rmse"}

    # fallback path: empty train/val should fall back to production defaults, not crash
    empty = df.iloc[0:0]
    k_fb, alpha_fb, grid_fb = pp.tune_hyperparameters(empty, empty, k_candidates=[4.0], alpha_candidates=[1.0])
    assert k_fb == pp.SHRINKAGE_K
    assert alpha_fb == pp.RIDGE_ALPHA
    assert grid_fb.empty


def test_tune_hyperparameters_skip_shrinkage_works_on_a_presmoothed_frame_with_no_games_played():
    # Gap G (Issue #37 reconciliation, 2026-06-24): the Cross-Season model's state frame has
    # skill_<x> columns (already-smoothed Kalman states) but no
    # games_played/min_pct -- shrink_skills() would KeyError on it. This is
    # the bug a real notebook run actually hit; regression-guard it.
    rng = np.random.default_rng(0)
    n = 300
    df = pd.DataFrame({
        "player_id": np.arange(n),
        "season": rng.choice([2024, 2025, 2026], size=n),
        "position": rng.choice(["WG", "C", "PG"], size=n),
        "off_adj_rapm": rng.normal(0, 1.0, size=n),
        "def_adj_rapm": rng.normal(0, 1.0, size=n),
    })
    for skill in pp.SKILLS:
        df[f"skill_{skill}"] = rng.normal(0, 1.0, size=n)
    train_df = df[df["season"].isin([2024, 2025])]
    val_df = df[df["season"] == 2026]

    assert "games_played" not in df.columns  # the actual shape that broke shrink_skills()
    k, alpha, grid_df = pp.tune_hyperparameters(train_df, val_df, alpha_candidates=[1.0, 10.0], skip_shrinkage=True)
    assert k is None
    assert alpha in [1.0, 10.0]
    assert not grid_df.empty


def test_compare_to_baselines_position_mean_beats_or_ties_global_mean_when_positions_differ():
    train_df = pd.DataFrame({
        "position": ["C"] * 50 + ["PG"] * 50,
        "off_adj_rapm": [5.0] * 50 + [-5.0] * 50,  # positions have very different means
    })
    eval_df = pd.DataFrame({
        "position": ["C"] * 10 + ["PG"] * 10,
        "off_adj_rapm": [5.0] * 10 + [-5.0] * 10,
    })
    result = pp.compare_to_baselines(train_df, eval_df, "off_adj_rapm")
    assert "predict_train_mean" in result
    assert "predict_position_mean" in result
    # position-aware baseline should have near-zero error; global mean baseline should not
    assert result["predict_position_mean"]["rmse"] < result["predict_train_mean"]["rmse"]


def test_evaluate_cohort_slices_skips_small_slices_and_reports_real_ones():
    df = pd.DataFrame({
        "target": list(range(20)),
        "pred": list(range(20)),
        "is_big": [True] * 8 + [False] * 12,
        "is_tiny_slice": [True] * 2 + [False] * 18,
    })
    slice_defs = {
        "bigs": df["is_big"],
        "tiny": df["is_tiny_slice"],
    }
    result = pp.evaluate_cohort_slices(df, "target", "pred", slice_defs, min_n=5)
    assert "bigs" in result["slice"].tolist()
    assert "tiny" not in result["slice"].tolist()  # below min_n, correctly skipped


def test_join_archetype_metadata_left_join_does_not_drop_missing_rows():
    # Issue #37: missing archetype labels must not block evaluation for
    # players with sufficient statistical history.
    df = pd.DataFrame({"player_id": [1, 2, 3], "season": [2026, 2026, 2026], "value": [1.0, 2.0, 3.0]})
    archetypes_df = pd.DataFrame({
        "player_id": [1, 3], "season": [2026, 2026],
        "archetype_id": [0, 2], "archetype_label": ["3&D Wing", "Post Scoring Big"], "confidence": [0.9, 0.7],
    })
    joined = pp.join_archetype_metadata(df, archetypes_df)
    assert len(joined) == 3  # player 2 (no archetype row) is not dropped
    assert joined.loc[joined["player_id"] == 2, "archetype_label"].isna().all()
    assert joined.loc[joined["player_id"] == 1, "archetype_label"].iloc[0] == "3&D Wing"


def test_join_archetype_metadata_raises_on_missing_columns():
    df = pd.DataFrame({"player_id": [1], "season": [2026]})
    bad_archetypes_df = pd.DataFrame({"player_id": [1], "season": [2026]})  # missing archetype_id etc.
    with pytest.raises(ValueError, match="missing expected columns"):
        pp.join_archetype_metadata(df, bad_archetypes_df)


def test_find_comparable_players_returns_nearest_by_skill_distance_not_archetype():
    df = pd.DataFrame({
        "player_id": [1, 2, 3, 4],
        "season": [2026, 2026, 2026, 2026],
        "skill_a": [0.0, 0.1, 5.0, 0.05],
        "skill_b": [0.0, 0.1, 5.0, 0.05],
        "archetype_label": ["Wing", "Big", "Big", "Wing"],
    })
    result = pp.find_comparable_players(df, player_id=1, season=2026, skill_cols=["skill_a", "skill_b"], n=2)
    # nearest by skill distance should be players 2 and 4 (close in skill space),
    # not player 3 (far in skill space despite no archetype filter applied)
    assert set(result["player_id"].tolist()) == {2, 4}
    assert 3 not in result["player_id"].tolist()


def test_find_comparable_players_raises_for_unknown_player_season():
    df = pd.DataFrame({"player_id": [1], "season": [2026], "skill_a": [0.0]})
    with pytest.raises(ValueError, match="No row for"):
        pp.find_comparable_players(df, player_id=99, season=2026, skill_cols=["skill_a"])
