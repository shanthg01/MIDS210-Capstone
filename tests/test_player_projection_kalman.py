import numpy as np
import pandas as pd
import pytest

from portalpoint.modeling import player_projection_kalman as ppk


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
