"""Player Projection — Phase 1 (single-season game-level state-space).

Validates per-skill Kalman filtering/smoothing on individual game logs
before attempting the full cross-season, block-covariance model (Phase 2 —
see docs/models/player_projection_state_space_plan.md §15). Only season
2026 has game-level data (`hoopr_player_game_logs`) today, so this is a
single-season local-level model per skill, not a cross-season persistence
model — `rho` and the class-year development curve are not fittable until
the 2020-2025 backfill lands.

Model per (player, skill) — scalar local-level state-space:

    alpha_t = alpha_(t-1) + w_t,      w_t ~ N(0, Q)
    y_t     = alpha_t + v_t,          v_t ~ N(0, R_t)

`R_t` is sample-size-weighted (`R_t = numerator / weight_t`, see `_r_numerator`)
per §7 of the plan doc: shooting skills weight by attempts that game (Bernoulli
variance, numerator ~ p(1-p)), rate skills (assists, rebounds, etc., expressed
per-40-minutes) weight by minutes played that game (Poisson-consistent
numerator ~ mean_rate * 40 — fixed 2026-06-23, see notebook §13/BRANCH_TODO.md
P1; a flat numerator of 1.0 had been underestimating count-rate noise by
orders of magnitude). `Q` is fit once per skill via pooled MLE across every
player's game sequence for that skill — fitting a separate Q per player would
be unstable on panels this short (many players have under 15 games this
season).

This module deliberately does not re-derive the value-translation step —
it produces `skill_*` columns in the same shape Phase 0
(`player_projection.py`) expects, so the existing `fit_value_model` /
`build_design_matrix` / `project_value` functions can be reused as-is.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

# Game-level skill definition: (numerator expr, denominator/weight expr).
# Shooting skills are make/attempt rates weighted by attempts; everything
# else is a per-40-minutes rate weighted by minutes played that game.
RATE_PER_40_SKILLS = {
    "shot_creation_usage": lambda d: d["field_goals_attempted"] + 0.44 * d["free_throws_attempted"],
    "passing_creation": lambda d: d["assists"],
    "turnover_avoidance": lambda d: d["turnovers"],  # inverted at use time, see player_projection.INVERTED_SKILLS
    "offensive_rebounding": lambda d: d["offensive_rebounds"],
    "defensive_rebounding": lambda d: d["defensive_rebounds"],
    "steal_disruption": lambda d: d["steals"],
    "block_rim_protection": lambda d: d["blocks"],
}
SHOOTING_SKILLS = {
    "shooting_3p": ("three_point_field_goals_made", "three_point_field_goals_attempted"),
    "shooting_2p_finishing": ("_fg2_made", "_fg2_attempted"),
    "free_throw_touch": ("free_throws_made", "free_throws_attempted"),
}
SKILLS = list(SHOOTING_SKILLS) + list(RATE_PER_40_SKILLS)


def build_game_observations(game_logs: pd.DataFrame) -> pd.DataFrame:
    """One row per (player_id, game) with y_<skill>/weight_<skill> columns,
    sorted by game_date within player. weight=0 marks a missing observation
    for that skill/game (e.g. zero attempts), not a true zero rate."""
    df = game_logs.sort_values(["player_id", "game_date"]).reset_index(drop=True)
    df["_fg2_made"] = df["field_goals_made"] - df["three_point_field_goals_made"]
    df["_fg2_attempted"] = df["field_goals_attempted"] - df["three_point_field_goals_attempted"]

    for skill, (made_col, att_col) in SHOOTING_SKILLS.items():
        weight = df[att_col].fillna(0).clip(lower=0)
        df[f"y_{skill}"] = np.where(weight > 0, df[made_col] / weight.replace(0, np.nan), np.nan)
        df[f"weight_{skill}"] = weight

    minutes = df["minutes"].fillna(0).clip(lower=0)
    for skill, numer_fn in RATE_PER_40_SKILLS.items():
        numer = numer_fn(df).fillna(0)
        df[f"y_{skill}"] = np.where(minutes > 0, numer / minutes.replace(0, np.nan) * 40.0, np.nan)
        df[f"weight_{skill}"] = minutes

    return df


def kalman_filter_series(
    y: np.ndarray, R: np.ndarray, Q: float, mask: np.ndarray, prior_mean: float, prior_var: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Scalar local-level Kalman filter over one player's game sequence.

    Missing observations (mask=False) propagate the state with no update —
    the natural way to handle a ragged panel (§5 of the plan doc) without
    imputing a fake game-level rate.

    Returns (filtered_mean, filtered_var, pred_mean, pred_var) — pred_* are
    the one-step-ahead prediction moments, needed for the pooled MLE
    log-likelihood in fit_q_mle.
    """
    n = len(y)
    a = np.empty(n)
    P = np.empty(n)
    pred_mean = np.empty(n)
    pred_var = np.empty(n)
    a_prev, P_prev = prior_mean, prior_var
    for t in range(n):
        a_pred = a_prev
        P_pred = P_prev + Q
        pred_mean[t] = a_pred
        pred_var[t] = P_pred + R[t]
        if mask[t]:
            k_gain = P_pred / (P_pred + R[t])
            a_t = a_pred + k_gain * (y[t] - a_pred)
            P_t = (1.0 - k_gain) * P_pred
        else:
            a_t, P_t = a_pred, P_pred
        a[t], P[t] = a_t, P_t
        a_prev, P_prev = a_t, P_t
    return a, P, pred_mean, pred_var


def kalman_smoother_series(a: np.ndarray, P: np.ndarray, Q: float) -> tuple[np.ndarray, np.ndarray]:
    """Rauch-Tung-Striebel smoother given filtered means/variances."""
    n = len(a)
    a_s, P_s = a.copy(), P.copy()
    for t in range(n - 2, -1, -1):
        p_pred = P[t] + Q
        if p_pred <= 0:
            continue
        j_gain = P[t] / p_pred
        a_s[t] = a[t] + j_gain * (a_s[t + 1] - a[t])
        P_s[t] = P[t] + j_gain * j_gain * (P_s[t + 1] - p_pred)
    return a_s, P_s


Sequence = tuple[np.ndarray, np.ndarray, np.ndarray, float, float]  # y, R, mask, prior_mean, prior_var

# Q search bounds. Widened from the original (1e-6, 2.0) — that range was
# implicitly tuned against the old, badly-undersized R_t (see _r_numerator):
# now that count-rate skills' R_t is correctly scaled to their y-units (which
# can run into the tens, e.g. assists/turnovers per 40 minutes, vs. shooting
# skills' [0, 1] rate scale), their plausible Q range is larger too. One wide
# shared bound is simpler than a per-skill-type bound and costs nothing extra
# — minimize_scalar's bounded Brent search is cheap regardless of width.
Q_BOUNDS = (1e-6, 100.0)


def _pooled_neg_log_likelihood(q_value: float, sequences: list[Sequence]) -> float:
    if q_value <= 0:
        return np.inf
    total = 0.0
    for y, R, mask, prior_mean, prior_var in sequences:
        _, _, pred_mean, pred_var = kalman_filter_series(y, R, q_value, mask, prior_mean, prior_var)
        if not mask.any():
            continue
        var = pred_var[mask]
        err = y[mask] - pred_mean[mask]
        total += 0.5 * float(np.sum(np.log(2 * np.pi * var) + err**2 / var))
    return total


def pooled_neg_log_likelihood(q_value: float, sequences: list[Sequence]) -> float:
    """Public alias of _pooled_neg_log_likelihood — exposed for notebook
    diagnostics (e.g. plotting the likelihood curve around a fitted Q)."""
    return _pooled_neg_log_likelihood(q_value, sequences)


def fit_q_mle(sequences: list[Sequence], bounds: tuple[float, float] = Q_BOUNDS) -> tuple[float, float]:
    """Pooled MLE fit of one global process variance Q for a skill, across
    every player's game sequence. Returns (Q, neg_log_likelihood_at_Q)."""
    result = minimize_scalar(_pooled_neg_log_likelihood, bounds=bounds, args=(sequences,), method="bounded")
    return float(result.x), float(result.fun)


def _r_numerator(skill: str, prior_mean: float) -> float:
    """Per-skill observation-noise numerator for R_t = numerator / weight_t.

    Shooting skills: y is a make/attempt Bernoulli rate, weight is attempts.
    Var(y) = p(1-p)/attempts, so numerator = p(1-p) — roughly constant
    (~0.2-0.25) across realistic shooting percentages, which is why a flat
    numerator of 1.0 happened to work tolerably for these (just a constant
    rescaling Q absorbs).

    Rate-per-40 skills: y = K/minutes*40 where K ~ Poisson(lambda*minutes)
    is the raw game count and lambda is the true per-minute rate.
    Var(K) = lambda*minutes (Poisson variance = mean), so
    Var(y) = Var(K)*(40/minutes)**2 = lambda*1600/minutes.
    lambda = E[y]/40, so Var(y) = (E[y]/40)*1600/minutes = E[y]*40/minutes
    -> numerator = E[y]*40 (approximated here by prior_mean*40).

    This was the actual bug (plan doc §15, notebook §13): a flat numerator of
    1.0 underestimated count-rate observation noise by orders of magnitude
    (e.g. turnover_avoidance's true numerator is ~prior_mean*40, often
    100-500+, not 1) — Q was saturating at its upper bound trying to explain
    that mismatch as real game-to-game state movement instead of noise.
    """
    if skill in SHOOTING_SKILLS:
        p = min(max(prior_mean, 0.01), 0.99)
        return p * (1.0 - p)
    return max(prior_mean, 1e-3) * 40.0


def build_player_sequences(obs_df: pd.DataFrame, skill: str) -> dict[int, Sequence]:
    """One Sequence per player_id for the given skill. Observation noise
    R_t = numerator / weight_t — see _r_numerator for the Bernoulli vs.
    Poisson-rate derivation. This is still an approximation (population-mean
    numerator, not per-player), but fixes the order-of-magnitude scale error
    that previously pinned count-rate skills' Q at its upper bound."""
    y_col, w_col = f"y_{skill}", f"weight_{skill}"
    valid_mask_all = obs_df[w_col] > 0
    prior_mean = float(obs_df.loc[valid_mask_all, y_col].mean())
    prior_var = float(obs_df.loc[valid_mask_all, y_col].var(ddof=1)) * 4.0
    if not np.isfinite(prior_var) or prior_var <= 0:
        prior_var = 1.0
    r_numerator = _r_numerator(skill, prior_mean)

    sequences: dict[int, Sequence] = {}
    for player_id, g in obs_df.groupby("player_id"):
        mask = (g[w_col] > 0).to_numpy()
        y = g[y_col].fillna(prior_mean).to_numpy(dtype=np.float64)
        weight = g[w_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
        R = r_numerator / weight
        sequences[int(player_id)] = (y, R, mask, prior_mean, prior_var)
    return sequences


def smooth_skill(obs_df: pd.DataFrame, skill: str) -> tuple[float, pd.DataFrame]:
    """Fit Q for one skill, then filter+smooth every player's sequence.
    Returns (fitted_Q, frame with one row per player: end-of-season smoothed
    mean/var = the last smoothed state, used as the Phase 1 skill estimate)."""
    sequences = build_player_sequences(obs_df, skill)
    q_value, _ = fit_q_mle(list(sequences.values()))

    rows = []
    for player_id, (y, R, mask, prior_mean, prior_var) in sequences.items():
        a, P, _, _ = kalman_filter_series(y, R, q_value, mask, prior_mean, prior_var)
        a_s, P_s = kalman_smoother_series(a, P, q_value)
        rows.append({
            "player_id": player_id,
            f"skill_{skill}": float(a_s[-1]),
            f"skill_var_{skill}": float(P_s[-1]),
            "_n_games_observed": int(mask.sum()),
        })
    return q_value, pd.DataFrame(rows)


def smooth_all_skills(obs_df: pd.DataFrame) -> tuple[dict[str, float], pd.DataFrame]:
    """Runs smooth_skill for every skill in SKILLS, merges into one
    per-player frame. Returns (fitted_Q_per_skill, merged frame)."""
    fitted_q: dict[str, float] = {}
    merged: pd.DataFrame | None = None
    for skill in SKILLS:
        q_value, skill_df = smooth_skill(obs_df, skill)
        fitted_q[skill] = q_value
        skill_df = skill_df.drop(columns=["_n_games_observed"])
        merged = skill_df if merged is None else merged.merge(skill_df, on="player_id", how="outer")
    return fitted_q, merged
