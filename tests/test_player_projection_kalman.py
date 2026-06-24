import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling import player_projection_kalman as ppk


def test_build_player_sequences_uses_external_prior_when_player_present():
    # Gap D (Issue #37 reconciliation): a player present in external_priors
    # should get their Phase 0 shrinkage estimate as prior_mean, not the flat
    # population mean of the observed game-level data.
    df = pd.DataFrame({
        "player_id": [1, 1, 2, 2],
        "y_shooting_3p": [0.50, 0.52, 0.10, 0.12],
        "weight_shooting_3p": [5.0, 6.0, 5.0, 6.0],
    })
    external_priors = pd.DataFrame({
        "player_id": [1],
        "skill_shooting_3p": [0.80],  # deliberately far from player 1's observed ~0.51
        "_weight": [100.0],  # high Phase 0 sample weight -> low prior_var
    })
    sequences = ppk.build_player_sequences(df, "shooting_3p", external_priors=external_priors)

    _, _, _, prior_mean_p1, prior_var_p1 = sequences[1]
    assert prior_mean_p1 == pytest.approx(0.80)  # used the external prior, not the population mean

    _, _, _, prior_mean_p2, _ = sequences[2]
    assert prior_mean_p2 != pytest.approx(0.80)  # player 2 absent from external_priors -> population fallback


def test_build_player_sequences_external_prior_var_shrinks_with_weight():
    # Higher Phase 0 sample weight should mean more confidence in the prior
    # (lower prior_var), same weight/(weight+k) shape Phase 0 itself uses.
    df = pd.DataFrame({
        "player_id": [1, 1, 2, 2],
        "y_shooting_3p": [0.40, 0.42, 0.40, 0.42],
        "weight_shooting_3p": [5.0, 6.0, 5.0, 6.0],
    })
    external_priors = pd.DataFrame({
        "player_id": [1, 2],
        "skill_shooting_3p": [0.40, 0.40],
        "_weight": [1.0, 200.0],  # player 2 has far more Phase 0 sample weight
    })
    sequences = ppk.build_player_sequences(df, "shooting_3p", external_priors=external_priors)
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
    })
    obs_df = ppk.build_game_observations(df)
    fitted_q_a, merged_a = ppk.smooth_all_skills(obs_df)
    fitted_q_b, merged_b = ppk.smooth_all_skills(obs_df, external_priors_df=None)
    assert fitted_q_a == fitted_q_b
    pd.testing.assert_frame_equal(merged_a, merged_b)


def test_r_numerator_shooting_is_bernoulli_variance():
    # p(1-p) at p=0.4 is 0.24 — bounded, roughly constant across realistic
    # shooting percentages, unlike the count-rate case below.
    assert ppk._r_numerator("shooting_3p", 0.4) == pytest.approx(0.4 * 0.6)
    assert ppk._r_numerator("free_throw_touch", 0.8) == pytest.approx(0.8 * 0.2)


def test_r_numerator_clips_extreme_probabilities():
    # p must stay in (0, 1) for p(1-p) to be a sane variance — guard the
    # edges so a degenerate 0%/100% shooter doesn't zero out the numerator.
    lo = ppk._r_numerator("shooting_3p", 0.0)
    hi = ppk._r_numerator("shooting_3p", 1.0)
    assert lo > 0
    assert hi > 0


def test_r_numerator_count_rate_scales_with_the_rate_itself():
    # This is the actual bug: a flat numerator of 1.0 was used for every
    # skill regardless of its typical magnitude. Count-rate skills (e.g.
    # turnover_avoidance, mean per-40 rate ~15) need a numerator an order of
    # magnitude larger than shooting skills' ~0.2-0.25 — verify the formula
    # produces that, not a flat constant.
    low_rate_numerator = ppk._r_numerator("steal_disruption", 1.0)   # rare event, low per-40 mean
    high_rate_numerator = ppk._r_numerator("turnover_avoidance", 15.0)  # common event, high per-40 mean
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
    sequences = ppk.build_player_sequences(df, "turnover_avoidance")
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
    assert ppk.Q_BOUNDS[1] >= 50.0


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
    q1, _ = ppk.fit_q_mle(sequences, max_sequences_for_search=200, random_state=42)
    q2, _ = ppk.fit_q_mle(sequences, max_sequences_for_search=200, random_state=42)
    assert q1 == q2


def test_fit_q_mle_subsample_recovers_similar_q_to_full_population():
    # The actual performance claim: Q is a population-level nuisance
    # parameter, so a few-hundred-player subsample should land close to the
    # full-population fit, not meaningfully different.
    sequences = _synthetic_q_sequences(n_players=2000, true_q=0.01)
    q_full, _ = ppk.fit_q_mle(sequences, max_sequences_for_search=None)
    q_subsampled, _ = ppk.fit_q_mle(sequences, max_sequences_for_search=300, random_state=1)
    assert q_subsampled == pytest.approx(q_full, rel=0.35)


def test_fit_q_mle_no_subsampling_below_threshold():
    # Population smaller than max_sequences_for_search must use every
    # sequence -- no silent subsampling of an already-small population.
    sequences = _synthetic_q_sequences(n_players=50, true_q=0.01)
    q_a, nll_a = ppk.fit_q_mle(sequences, max_sequences_for_search=1000)
    q_b, nll_b = ppk.fit_q_mle(sequences, max_sequences_for_search=None)
    assert q_a == pytest.approx(q_b)
    assert nll_a == pytest.approx(nll_b)
