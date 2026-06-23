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

    y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var = sequences[1]
    assert len(y) == 2
    assert y[0] == pytest.approx(0.35)  # 2021 comes first despite skill_df's row order
    assert y[1] == pytest.approx(0.40)  # 2022 second
    assert csi.tolist() == [1, 2]
    assert transfer_flag.tolist() == [0.0, 1.0]


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
    return (y, r_arr, mask, csi, np.zeros(n), np.zeros(n), 0.3, 0.05)


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


def _synthetic_skill_and_covariates_frames(rng, n_players, true_rho, true_beta1, skill="shooting_3p"):
    """Builds skill_df/covariates frames (the real shape smooth_season_skill
    consumes) from the same generating process as _synthetic_sequence, with
    a realistic short-career-dominated season-count mix (n=2-4)."""
    rows, cov_rows = [], []
    for player_id in range(n_players):
        n = int(rng.integers(2, 5))
        seq = _synthetic_sequence(rng, n, true_rho, true_beta1)
        y, _, _, csi, transfer_flag, level_change, _, _ = seq
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
