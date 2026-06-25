import json

import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling import player_projection_phase2 as pp2


def test_kalman_filter_with_drift_reduces_to_random_walk_when_rho_one_mu_zero():
    # rho=1, mu=0 should behave identically to a pure random-walk local-level
    # filter — same shape as Phase 1's kalman_filter_series.
    y = np.array([0.30, 0.32, 0.31, 0.29])
    r_arr = np.array([0.02, 0.02, 0.02, 0.02])
    mu = np.zeros(4)
    mask = np.ones(4, dtype=bool)
    a, p_var, pred_mean, pred_var = pp2.kalman_filter_with_drift(
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
    a, _, _, _ = pp2.kalman_filter_with_drift(
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
    sequences = pp2.build_season_sequences(skill_df, covariates, "shooting_3p")
    assert set(sequences.keys()) == {1, 2}

    y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, seasons_arr = sequences[1]
    assert len(y) == 2
    assert y[0] == pytest.approx(0.35)  # 2021 comes first despite skill_df's row order
    assert y[1] == pytest.approx(0.40)  # 2022 second
    assert csi.tolist() == [1, 2]
    assert transfer_flag.tolist() == [0.0, 1.0]
    # the real season, not just positional order -- 2026-06-25 fix's whole point
    assert seasons_arr.tolist() == [2021, 2022]


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
    fitted = pp2.fit_season_model(short_sequences)  # joint, no fixed_rho — the naive path
    fitted_params = np.array([
        fitted["rho"], fitted["beta_0"], fitted["beta_1"], fitted["beta_2"],
        fitted["beta_3"], fitted["beta_4"], np.log(fitted["Q"]),
    ])
    nll_at_truth = pp2._pooled_neg_log_likelihood(true_params, short_sequences)
    nll_at_fit = pp2._pooled_neg_log_likelihood(fitted_params, short_sequences)
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
    fit_a = pp2.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=200, random_state=42)
    fit_b = pp2.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=200, random_state=42)
    assert fit_a["beta_1"] == fit_b["beta_1"]
    assert fit_a["Q"] == fit_b["Q"]


def test_fit_season_model_subsample_recovers_similar_drift_to_full_population():
    # The actual performance claim: beta_0..4/Q are pooled population-level
    # estimates -- a few-hundred-sequence subsample should land close to the
    # full-population fit's drift direction/magnitude, not meaningfully off.
    rng = np.random.default_rng(2)
    sequences = [_synthetic_sequence(rng, rng.integers(2, 6), 0.7, 0.05) for _ in range(2000)]
    fit_full = pp2.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=None)
    fit_sub = pp2.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=300, random_state=3)
    assert fit_sub["beta_1"] == pytest.approx(fit_full["beta_1"], abs=0.02)


def test_fit_season_model_no_subsampling_below_threshold():
    # Population smaller than max_sequences_for_search must use every
    # sequence -- no silent subsampling of an already-small population.
    rng = np.random.default_rng(3)
    sequences = [_synthetic_sequence(rng, rng.integers(2, 6), 0.7, 0.05) for _ in range(50)]
    fit_a = pp2.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=1000)
    fit_b = pp2.fit_season_model(sequences, fixed_rho=0.7, max_sequences_for_search=None)
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

    rho = pp2.estimate_rho_autocorrelation(skill_df, covariates, "shooting_3p")
    assert rho is not None
    assert 0.2 <= rho <= 0.95  # within the documented clip range, not degenerate

    sequences = pp2.build_season_sequences(skill_df, covariates, "shooting_3p")
    fitted = pp2.fit_season_model(list(sequences.values()), fixed_rho=rho)
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
    block_corrs = pp2.compute_block_correlations(residual_df)
    assert "shooting_touch" in block_corrs
    corr = block_corrs["shooting_touch"]
    assert corr.loc["std_resid_shooting_3p", "std_resid_shooting_2p_finishing"] == pytest.approx(1.0)
    assert corr.loc["std_resid_shooting_3p", "std_resid_free_throw_touch"] == pytest.approx(-1.0)


def test_compute_block_correlations_skips_blocks_with_insufficient_columns():
    residual_df = pd.DataFrame({"std_resid_offensive_rebounding": [0.1, 0.2, 0.3]})
    block_corrs = pp2.compute_block_correlations(residual_df)
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
    block_corrs = pp2.compute_block_correlations(residual_df)

    blended = pp2.blend_block_priors(residual_df, block_corrs)
    assert "phase2_skill_offensive_rebounding_blended" in blended.columns
    assert "phase2_skill_defensive_rebounding_blended" in blended.columns
    assert "phase2_skill_shooting_3p_blended" not in blended.columns  # not a validated block


def test_blend_block_priors_adjustment_direction_matches_correlation_sign():
    residual_df = _rebounding_residual_frame()
    block_corrs = pp2.compute_block_correlations(residual_df)
    blended = pp2.blend_block_priors(residual_df, block_corrs)

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
    blended = pp2.blend_block_priors(residual_df, block_correlations={})  # no correlations computed at all
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
    result = pp2.attach_game_context(game_logs, team_context)

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
    model = pp2.fit_context_adjustment(obs_df, "shooting_3p")
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
    model = pp2.fit_context_adjustment(obs_df, "shooting_3p")
    assert model is not None
    adjusted = pp2.apply_context_adjustment(obs_df, "shooting_3p", model)

    # adjusted series should have much less correlation with opponent_adj_d
    # than the raw series did, since the opponent effect has been removed.
    raw_corr = np.corrcoef(obs_df["y_shooting_3p"], opponent_adj_d)[0, 1]
    adjusted_corr = np.corrcoef(adjusted, opponent_adj_d)[0, 1]
    assert abs(adjusted_corr) < abs(raw_corr)
    # re-centered on the same population mean, not shifted wholesale
    assert adjusted.mean() == pytest.approx(obs_df["y_shooting_3p"].mean(), abs=0.02)


def test_apply_context_adjustment_passes_through_unchanged_when_model_is_none():
    obs_df = pd.DataFrame({"y_shooting_3p": [0.3, 0.4, 0.5]})
    result = pp2.apply_context_adjustment(obs_df, "shooting_3p", None)
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
    models = pp2.fit_attempt_rate_models(states)
    assert set(models.keys()) == set(pp2.STAGE_2A_TARGETS)

    X = pp2._stage_2a_design_matrix(states)
    pred_3pa = models["rate_3pa_attempted"][0].predict(X)
    # higher shooting_3p skill at the same usage level should predict a
    # higher 3PA attempt rate -- the actual signal Stage 2A exists to find.
    corr = np.corrcoef(pred_3pa, states["skill_shooting_3p"])[0, 1]
    assert corr > 0.3


def test_fit_attempt_rate_models_skips_targets_with_too_few_rows():
    tiny = _synthetic_stage2a_states(n=5)
    models = pp2.fit_attempt_rate_models(tiny)
    assert models == {}


def test_project_rates_make_miss_split_uses_shooting_percentage_skill():
    states = _synthetic_stage2a_states(n=200)
    models = pp2.fit_attempt_rate_models(states)
    out = pp2.project_rates(states, models)

    # the make share of total 2PA rate should track skill_shooting_2p_finishing
    implied_pct = out["rate_2pa_make"] / out["rate_2pa_attempted"].replace(0, np.nan)
    valid = implied_pct.dropna()
    assert valid.corr(states.loc[valid.index, "skill_shooting_2p_finishing"]) > 0.9
    # makes + misses must reconstruct the attempted rate exactly
    assert np.allclose(out["rate_2pa_make"] + out["rate_2pa_miss"], out["rate_2pa_attempted"])


def test_project_rates_stage_2b_is_a_direct_skill_readoff_not_a_model():
    states = _synthetic_stage2a_states(n=50)
    out = pp2.project_rates(states, attempt_models={})
    for rate_col, skill in pp2.STAGE_2B_RATE_SKILLS.items():
        np.testing.assert_allclose(out[rate_col].to_numpy(), states[f"skill_{skill}"].clip(lower=0.0).to_numpy())


def test_project_rates_per100_uses_given_pace_else_default():
    states = _synthetic_stage2a_states(n=10)
    out_default = pp2.project_rates(states, attempt_models={})
    pace = pd.Series([100.0] * len(states))  # 100 poss/40min -> per_100 == per_40
    out_paced = pp2.project_rates(states, attempt_models={}, pace=pace)

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
    out = pp2._compute_attempt_rates(totals)
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
    # build_phase2_records iterates ppk.SKILLS (Phase 2a's 11-skill list,
    # includes foul_discipline), not pp.SKILLS (Phase 0's 10, no fouls
    # column at season grain) -- this fixture must match what it actually
    # reads from a real Phase 2a frame.
    for s in pp2.SKILLS:
        df[f"skill_{s}"] = rng.uniform(0.1, 5.0, n)
        df[f"skill_var_{s}"] = rng.uniform(0.01, 1.0, n)
        df[f"pctile_{s}"] = rng.uniform(0, 100, n)
    return df


def test_build_phase2_records_inverts_turnover_avoidance_sign():
    import portalpoint.modeling.player_projection as pp
    df = _synthetic_projected_df(n=5)
    records = pp2.build_phase2_records(df)
    skill_states = json.loads(records[0][11])
    assert skill_states["turnover_avoidance"] == pytest.approx(-df.iloc[0]["skill_turnover_avoidance"], abs=1e-3)
    assert skill_states["shooting_3p"] == pytest.approx(df.iloc[0]["skill_shooting_3p"], abs=1e-3)


def test_build_phase2_records_uses_distinct_model_version_and_neutral_school_id():
    df = _synthetic_projected_df(n=3)
    records = pp2.build_phase2_records(df)
    for r in records:
        assert r[1] is None  # school_id -- neutral mode
        assert r[15] == pp2.MODEL_VERSION_PHASE2A


def test_build_phase2_records_populates_rates_when_given_else_empty():
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
    records = pp2.build_phase2_records(df, projected_rates_df=rates_df)

    rates_player0 = json.loads(records[0][10])
    box_player0 = json.loads(records[0][9])
    assert rates_player0["rate_2pa_make"] == 3.0
    assert box_player0["pts_per_40"] == pytest.approx(2 * 3.0 + 3 * 1.0 + 2.0 * 0.8)

    box_player2 = json.loads(records[2][9])
    rates_player2 = json.loads(records[2][10])
    assert box_player2 == {}
    assert rates_player2 == {}
