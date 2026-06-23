"""Player Projection — Phase 2a (cross-season, block-aware state-space).

Builds on Phase 1 (`player_projection_kalman.py`) rather than replacing it:
Phase 1's intra-season scalar Kalman filter/smoother is reused as-is, once per
season (2020-2026, all now backfilled), to produce one end-of-season smoothed
skill estimate per player per season per skill. This module adds a second,
season-grain Kalman layer on top of those season-ending estimates — the
actual cross-season persistence (`rho`) and development-curve/transfer/
level-change drift from the plan doc's §7 state evolution equation:

    alpha[p,t+1] = rho * alpha[p,t] + mu[p,t] + epsilon[p,t]
    mu[p,t] = beta_0 + beta_1*x + beta_2*x^2 + beta_3*transfer_flag + beta_4*level_change

Two deliberate deviations from the plan doc's literal text, both because the
data doesn't actually support the literal version:

1. **`x` is `career_season_index` (1, 2, 3, ... — rank among a player's own
   observed game-log seasons), not literal `class_year`.** `players.class_year`
   is a single column updated on every barttorvik ingest re-run — it holds
   only the player's *most recently ingested* class year, not a per-season
   history. Re-deriving historical class year from it would require assuming
   no redshirts/grad years, which is exactly the population (transfers) this
   model cares about. `career_season_index` is directly computable from the
   data we actually have and is arguably a better-motivated development-curve
   input anyway (exposure-based, not eligibility-based).

2. **Block covariance is an empirical post-hoc residual-correlation estimate,
   not a joint multivariate Kalman update.** Fitting a single state vector
   per block (correlated process noise *during* filtering) is a much bigger
   numerical-stability lift than the per-skill univariate fits below. This
   module fits each skill's season-grain model independently, then estimates
   the within-block correlation of standardized one-step-ahead residuals as
   a diagnostic and a cross-skill prior-blending input — the "shared priors
   informed by correlated skills" version from plan doc §6's table, not the
   full joint-covariance version. Documented as a scope decision, not silently
   downgraded.

The season-level "observation" fed into this layer is Phase 1's smoothed
end-of-season estimate, with its own smoothed variance used as that
observation's noise `R` — a standard hierarchical-Kalman composition: each
season's intra-season filter answers "what do this season's games tell us",
and this layer answers "how does that estimate evolve season to season."
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sqlalchemy import Engine, text

from portalpoint.modeling import player_projection_kalman as ppk
from portalpoint.modeling.io import find_repo_root

log = logging.getLogger(__name__)

# The intra-season filtering pass (build_season_skill_states) takes ~2h on
# the full 2020-2026 dataset — 2b/2c/2d all need its output as direct input
# (not just the fitted-param summary), so cache it instead of re-deriving on
# every call. Gitignored — same convention as data/features/*.parquet.
DEFAULT_CACHE_DIR = find_repo_root() / "data" / "features" / "player_projection_phase2"

SKILLS = ppk.SKILLS

# §6's recommended blocks. foul_discipline is absent from SKILLS entirely
# (no season- or game-grain foul-rate data — see player_projection.py and
# player_projection_kalman.py docstrings), so defensive_playmaking only has 2
# of its originally-recommended 3 members.
SKILL_BLOCKS: dict[str, list[str]] = {
    "shooting_touch": ["shooting_3p", "shooting_2p_finishing", "free_throw_touch"],
    "creation": ["shot_creation_usage", "passing_creation", "turnover_avoidance"],
    "rebounding": ["offensive_rebounding", "defensive_rebounding"],
    "defensive_playmaking": ["steal_disruption", "block_rim_protection"],
}

LEVEL_TIERS = 4

GAME_LOG_SQL = """
SELECT
    player_id, game_date, minutes,
    field_goals_made, field_goals_attempted,
    three_point_field_goals_made, three_point_field_goals_attempted,
    free_throws_made, free_throws_attempted,
    offensive_rebounds, defensive_rebounds,
    assists, steals, blocks, turnovers
FROM hoopr_player_game_logs
WHERE season = :season AND player_id IS NOT NULL
"""


def load_game_logs(engine: Engine, season: int) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(GAME_LOG_SQL), conn, params={"season": season})


def compute_level_tier(engine: Engine, seasons: list[int]) -> pd.DataFrame:
    """level_TP (plan doc §5): 4 quantile buckets of team_season_stats.adj_em,
    recomputed independently per season — conference strength drifts year to
    year, so this is never cached across seasons. tier=4 is the strongest
    quartile that season, tier=1 the weakest."""
    sql = "SELECT school_id, season, adj_em FROM team_season_stats WHERE season = ANY(:seasons) AND adj_em IS NOT NULL"
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn, params={"seasons": seasons})
    df["tier"] = (
        df.groupby("season")["adj_em"].transform(
            lambda x: pd.qcut(x, LEVEL_TIERS, labels=False, duplicates="drop")
        )
        + 1
    )
    return df[["school_id", "season", "tier"]]


def build_season_skill_states(
    engine: Engine, seasons: list[int],
) -> tuple[dict[int, dict[str, float]], pd.DataFrame]:
    """Runs Phase 1's intra-season filter+smoother once per season. Returns
    (fitted_Q per season per skill, one merged frame with skill_<s>/
    skill_var_<s> per player per season — the season-grain layer's raw
    "observations")."""
    fitted_q_by_season: dict[int, dict[str, float]] = {}
    frames: list[pd.DataFrame] = []
    for season in seasons:
        game_logs = load_game_logs(engine, season)
        if game_logs.empty:
            log.warning("No game logs for season %d, skipping", season)
            continue
        obs_df = ppk.build_game_observations(game_logs)
        fitted_q, kalman_df = ppk.smooth_all_skills(obs_df)
        kalman_df = kalman_df.copy()
        kalman_df["season"] = season
        fitted_q_by_season[season] = fitted_q
        frames.append(kalman_df)
        log.info("Season %d: %d players, intra-season filter complete", season, len(kalman_df))
    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return fitted_q_by_season, merged


def load_or_build_season_skill_states(
    engine: Engine, seasons: list[int], cache_dir: Path | None = None, force_rebuild: bool = False,
) -> tuple[dict[int, dict[str, float]], pd.DataFrame]:
    """Cached wrapper around build_season_skill_states. The ~2h intra-season
    filtering pass is identical every time it's run against the same seasons
    (deterministic given the same data) — there's no reason to pay that cost
    once per caller (2b, 2c, 2d, and re-running 2a's own diagnostics all need
    this exact output). Set force_rebuild=True after a real upstream data
    change (e.g. another game-log backfill)."""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    states_path = cache_dir / "season_states.parquet"
    q_path = cache_dir / "fitted_q_by_season.json"

    if not force_rebuild and states_path.exists() and q_path.exists():
        log.info("Loading cached season skill states from %s", states_path)
        season_states = pd.read_parquet(states_path)
        fitted_q_by_season = {int(k): v for k, v in json.loads(q_path.read_text()).items()}
        return fitted_q_by_season, season_states

    fitted_q_by_season, season_states = build_season_skill_states(engine, seasons)
    cache_dir.mkdir(parents=True, exist_ok=True)
    season_states.to_parquet(states_path)
    q_path.write_text(json.dumps(fitted_q_by_season))
    log.info("Cached season skill states to %s", states_path)
    return fitted_q_by_season, season_states


def build_season_covariates(engine: Engine, season_states: pd.DataFrame) -> pd.DataFrame:
    """career_season_index (rank within each player's own observed seasons),
    transfer_flag, and level_change for every (player_id, season) row in
    season_states. See module docstring for why career_season_index replaces
    literal class_year."""
    df = season_states[["player_id", "season"]].drop_duplicates().sort_values(["player_id", "season"]).copy()
    df["career_season_index"] = df.groupby("player_id").cumcount() + 1

    with engine.connect() as conn:
        transfers = pd.read_sql(
            text("SELECT player_id, season, from_school_id, to_school_id FROM transfers"), conn,
        )
    transfers["transfer_flag"] = 1.0

    seasons = sorted(df["season"].unique().tolist())
    tier_df = compute_level_tier(engine, seasons + [min(seasons) - 1])

    to_tier = tier_df.rename(columns={"school_id": "to_school_id", "tier": "to_tier"})
    transfers = transfers.merge(to_tier, on=["to_school_id", "season"], how="left")

    transfers["prev_season"] = transfers["season"] - 1
    from_tier = tier_df.rename(columns={"school_id": "from_school_id", "season": "prev_season", "tier": "from_tier"})
    transfers = transfers.merge(from_tier, on=["from_school_id", "prev_season"], how="left")
    transfers["level_change"] = (transfers["to_tier"] - transfers["from_tier"]).fillna(0.0)

    df = df.merge(
        transfers[["player_id", "season", "transfer_flag", "level_change"]],
        on=["player_id", "season"], how="left",
    )
    df["transfer_flag"] = df["transfer_flag"].fillna(0.0)
    df["level_change"] = df["level_change"].fillna(0.0)
    return df


def load_or_build_season_covariates(
    engine: Engine, season_states: pd.DataFrame, cache_dir: Path | None = None, force_rebuild: bool = False,
) -> pd.DataFrame:
    """Cached wrapper around build_season_covariates — same rationale as
    load_or_build_season_skill_states, though this step is cheap on its own
    (a few DB queries, not 2h of filtering); cached mainly so a full
    load_or_build_season_skill_states + load_or_build_season_covariates pair
    is a single fast no-op call once both are warm."""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    covariates_path = cache_dir / "covariates.parquet"

    if not force_rebuild and covariates_path.exists():
        log.info("Loading cached season covariates from %s", covariates_path)
        return pd.read_parquet(covariates_path)

    covariates = build_season_covariates(engine, season_states)
    cache_dir.mkdir(parents=True, exist_ok=True)
    covariates.to_parquet(covariates_path)
    log.info("Cached season covariates to %s", covariates_path)
    return covariates


def kalman_filter_with_drift(
    y: np.ndarray, R: np.ndarray, rho: float, mu: np.ndarray, q_value: float,
    mask: np.ndarray, prior_mean: float, prior_var: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Season-grain filter: alpha_t = rho*alpha_(t-1) + mu_t + eps_t. mu_t is
    precomputed per timestep from that player-season's covariates and the
    candidate beta params — this function just runs the filter recursion
    given mu already evaluated, same interface shape as
    player_projection_kalman.kalman_filter_series but with an AR coefficient
    and a time-varying (not zero) drift term."""
    n = len(y)
    a = np.empty(n)
    p_var = np.empty(n)
    pred_mean = np.empty(n)
    pred_var = np.empty(n)
    a_prev, p_prev = prior_mean, prior_var
    for t in range(n):
        a_pred = rho * a_prev + mu[t]
        p_pred = rho * rho * p_prev + q_value
        pred_mean[t] = a_pred
        pred_var[t] = p_pred + R[t]
        if mask[t]:
            k_gain = p_pred / (p_pred + R[t])
            a_t = a_pred + k_gain * (y[t] - a_pred)
            p_t = (1.0 - k_gain) * p_pred
        else:
            a_t, p_t = a_pred, p_pred
        a[t], p_var[t] = a_t, p_t
        a_prev, p_prev = a_t, p_t
    return a, p_var, pred_mean, pred_var


SeasonSequence = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float]
# y, R, mask, career_season_index, transfer_flag, level_change, prior_mean, prior_var

PARAM_NAMES = ["rho", "beta_0", "beta_1", "beta_2", "beta_3", "beta_4", "log_Q"]


def build_season_sequences(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, skill: str,
) -> dict[int, SeasonSequence]:
    """One SeasonSequence per player_id, ordered by season, for one skill."""
    y_col, var_col = f"skill_{skill}", f"skill_var_{skill}"
    merged = skill_df[["player_id", "season", y_col, var_col]].merge(
        covariates, on=["player_id", "season"], how="inner",
    ).sort_values(["player_id", "season"])
    merged = merged.dropna(subset=[y_col, var_col])

    valid_mask_all = merged[y_col].notna()
    prior_mean = float(merged.loc[valid_mask_all, y_col].mean())
    prior_var = float(merged.loc[valid_mask_all, y_col].var(ddof=1)) * 4.0
    if not np.isfinite(prior_var) or prior_var <= 0:
        prior_var = 1.0

    sequences: dict[int, SeasonSequence] = {}
    for player_id, g in merged.groupby("player_id"):
        n = len(g)
        mask = np.ones(n, dtype=bool)
        y = g[y_col].to_numpy(dtype=np.float64)
        r_arr = g[var_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
        csi = g["career_season_index"].to_numpy(dtype=np.float64)
        transfer_flag = g["transfer_flag"].to_numpy(dtype=np.float64)
        level_change = g["level_change"].to_numpy(dtype=np.float64)
        sequences[int(player_id)] = (y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var)
    return sequences


def _pooled_neg_log_likelihood(params: np.ndarray, sequences: list[SeasonSequence]) -> float:
    rho, beta_0, beta_1, beta_2, beta_3, beta_4, log_q = params
    q_value = float(np.exp(log_q))
    if not (0.0 <= rho <= 1.2) or q_value <= 0:
        return np.inf
    total = 0.0
    for y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var in sequences:
        mu = beta_0 + beta_1 * csi + beta_2 * csi**2 + beta_3 * transfer_flag + beta_4 * level_change
        _, _, pred_mean, pred_var = kalman_filter_with_drift(
            y, r_arr, rho, mu, q_value, mask, prior_mean, prior_var,
        )
        if not mask.any():
            continue
        var = pred_var[mask]
        err = y[mask] - pred_mean[mask]
        total += 0.5 * float(np.sum(np.log(2 * np.pi * var) + err**2 / var))
    return total


def _q_init_from_diffs(sequences: list[SeasonSequence]) -> float:
    y_diffs = []
    for y, _, mask, _, _, _, _, _ in sequences:
        obs = y[mask]
        if len(obs) > 1:
            y_diffs.extend(np.diff(obs).tolist())
    q_init = float(np.var(y_diffs)) if len(y_diffs) > 5 else 0.1
    return max(q_init, 1e-4)


def estimate_rho_autocorrelation(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, skill: str,
    clip: tuple[float, float] = (0.2, 0.95),
) -> float | None:
    """Estimates a population-level `rho` as the pooled lag-1 Pearson
    correlation of consecutive-season smoothed skill estimates — deliberately
    *not* a state-space MLE estimate.

    Real finding (2026-06-23): jointly fitting `rho` and the drift terms via
    pooled MLE is not just unreliable on short sequences (the original
    finding — see git history / plan doc — multi-start optimization landing
    on a wrong-but-genuinely-better-likelihood optimum), it stayed broken even
    after trying the natural fixes: pooling `rho` from only the long-career
    subset (still landed near 0 — even our longest real sequences, capped at
    7 seasons by the game-log backfill, aren't long enough to identify `rho`
    independently of the trend terms), and adding a Gaussian MAP-style prior
    penalty on `rho` (the likelihood's pull toward the degenerate rho->0
    solution was large enough — hundreds of NLL units in testing — to swamp
    even a fairly tight prior). All three attempts are preserved in
    tests/test_player_projection_phase2.py as documented failure modes, not
    deleted, since the next person touching this should not have to
    rediscover why a "smarter prior" or "longer subset" won't fix it either.

    Simple lag-1 autocorrelation sidesteps the problem because it isn't
    jointly estimated with anything — it's a single, well-identified
    statistic computed directly from the observed (smoothed) skill series,
    with no competing parameter for the optimizer to trade it against. It
    will run somewhat high when a real trend is present (trending series have
    inflated lag-1 correlation independent of true persistence), which is why
    `clip` exists — bounds it away from both false near-1.0 (would make the
    season-level filter barely move) and the MLE's degenerate near-0 region.
    """
    merged = skill_df[["player_id", "season", f"skill_{skill}"]].merge(
        covariates[["player_id", "season"]], on=["player_id", "season"], how="inner",
    ).sort_values(["player_id", "season"])
    merged["next_value"] = merged.groupby("player_id")[f"skill_{skill}"].shift(-1)
    consecutive = merged.dropna(subset=[f"skill_{skill}", "next_value"])
    if len(consecutive) < 50:
        return None
    raw_rho = float(np.corrcoef(consecutive[f"skill_{skill}"], consecutive["next_value"])[0, 1])
    if not np.isfinite(raw_rho):
        return None
    clipped_rho = float(np.clip(raw_rho, clip[0], clip[1]))
    # Log the raw value even when clipping doesn't change it — a value that
    # lands exactly at one of the bounds (like shooting_3p's 0.200 in the
    # first real run) is otherwise indistinguishable from "the raw value was
    # 0.19" vs. "the raw value was -0.4," which mean very different things.
    if clipped_rho != raw_rho:
        log.warning(
            "Skill %-24s rho clipped: raw=%.4f -> clipped=%.4f (n=%d pairs)",
            skill, raw_rho, clipped_rho, len(consecutive),
        )
    else:
        log.info("Skill %-24s rho (unclipped): %.4f (n=%d pairs)", skill, raw_rho, len(consecutive))
    return clipped_rho


def fit_season_model(
    sequences: list[SeasonSequence], fixed_rho: float | None = None,
) -> dict[str, float]:
    """Pooled MLE for one skill's season-grain sequences.

    If `fixed_rho` is given, optimizes only (beta_0..4, Q) with `rho` held
    fixed — this is the recommended path (see `estimate_pooled_rho`'s
    docstring for why joint estimation isn't reliable on this data's mostly
    2-4-season sequences). If `fixed_rho` is None, optimizes the full
    (rho, beta_0..4, Q) — kept available for the long-sequence subset
    `estimate_pooled_rho` itself uses, and for diagnostics.

    Initial guess: rho=0.8 (mean-reverting prior, not a pure random walk) when
    rho is free; all betas=0; Q from the empirical season-to-season variance —
    a flat/no-drift start the optimizer should move away from if the data
    supports real persistence/drift effects.
    """
    q_init = _q_init_from_diffs(sequences)

    if fixed_rho is not None:
        def _nll_fixed(params: np.ndarray) -> float:
            beta_0, beta_1, beta_2, beta_3, beta_4, log_q = params
            full = np.array([fixed_rho, beta_0, beta_1, beta_2, beta_3, beta_4, log_q])
            return _pooled_neg_log_likelihood(full, sequences)

        x0 = np.array([0.0, 0.0, 0.0, 0.0, 0.0, np.log(q_init)])
        result = minimize(
            _nll_fixed, x0, method="Nelder-Mead",
            options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-5},
        )
        fitted = dict(zip(PARAM_NAMES[1:], result.x))  # skip "rho"
        fitted["rho"] = float(fixed_rho)
    else:
        x0 = np.array([0.8, 0.0, 0.0, 0.0, 0.0, 0.0, np.log(q_init)])
        result = minimize(
            _pooled_neg_log_likelihood, x0, args=(sequences,),
            method="Nelder-Mead",
            options={"maxiter": 2000, "xatol": 1e-5, "fatol": 1e-5},
        )
        fitted = dict(zip(PARAM_NAMES, result.x))

    fitted["Q"] = float(np.exp(fitted.pop("log_Q")))
    fitted["neg_log_likelihood"] = float(result.fun)
    fitted["converged"] = bool(result.success)
    fitted["rho_fixed"] = fixed_rho is not None
    return fitted


def smooth_season_skill(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, skill: str,
) -> tuple[dict[str, float], pd.DataFrame]:
    """Fits the season-grain model for one skill: `rho` from pooled lag-1
    autocorrelation (see `estimate_rho_autocorrelation` for why this, not
    MLE), fixed, then drift terms (beta_0..4) fit by MLE on the full
    population. Returns (fitted params dict, frame with one row per
    (player_id, season): filtered mean/var plus the standardized one-step-
    ahead residual, used for block-correlation diagnostics in
    compute_block_correlations)."""
    rho = estimate_rho_autocorrelation(skill_df, covariates, skill)
    sequences = build_season_sequences(skill_df, covariates, skill)
    seq_list = list(sequences.values())
    fitted = fit_season_model(seq_list, fixed_rho=rho)
    rho, beta_0, beta_1, beta_2, beta_3, beta_4 = (
        fitted["rho"], fitted["beta_0"], fitted["beta_1"], fitted["beta_2"], fitted["beta_3"], fitted["beta_4"],
    )
    q_value = fitted["Q"]

    rows = []
    for player_id, (y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var) in sequences.items():
        mu = beta_0 + beta_1 * csi + beta_2 * csi**2 + beta_3 * transfer_flag + beta_4 * level_change
        a, p_var, pred_mean, pred_var = kalman_filter_with_drift(
            y, r_arr, rho, mu, q_value, mask, prior_mean, prior_var,
        )
        std_resid = (y - pred_mean) / np.sqrt(pred_var)
        seasons_for_player = np.arange(1, len(y) + 1)  # positional; joined back by caller if needed
        for i in range(len(y)):
            rows.append({
                "player_id": player_id,
                "season_rank": int(seasons_for_player[i]),
                f"phase2_skill_{skill}": float(a[i]),
                f"phase2_skill_var_{skill}": float(p_var[i]),
                f"std_resid_{skill}": float(std_resid[i]),
            })
    return fitted, pd.DataFrame(rows)


def fit_all_skills(skill_df: pd.DataFrame, covariates: pd.DataFrame) -> tuple[dict[str, dict], pd.DataFrame]:
    """Runs smooth_season_skill for every skill, merges into one per
    (player_id, season_rank) frame."""
    fitted_params: dict[str, dict] = {}
    merged: pd.DataFrame | None = None
    for skill in SKILLS:
        fitted, skill_df_result = smooth_season_skill(skill_df, covariates, skill)
        fitted_params[skill] = fitted
        merged = (
            skill_df_result if merged is None
            else merged.merge(skill_df_result, on=["player_id", "season_rank"], how="outer")
        )
        log.info(
            "Skill %-24s rho=%.3f (fixed=%s) beta=[%.3f %.3f %.3f %.3f %.3f] Q=%.4f converged=%s",
            skill, fitted["rho"], fitted["rho_fixed"], fitted["beta_0"], fitted["beta_1"], fitted["beta_2"],
            fitted["beta_3"], fitted["beta_4"], fitted["Q"], fitted["converged"],
        )
    return fitted_params, merged


def compute_block_correlations(residual_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Empirical correlation of standardized one-step-ahead residuals within
    each SKILL_BLOCKS group — the "block covariance" deliverable for 2a, per
    the module docstring's scope decision (diagnostic + shared-prior input,
    not a joint multivariate Kalman update)."""
    correlations: dict[str, pd.DataFrame] = {}
    for block_name, skills_in_block in SKILL_BLOCKS.items():
        cols = [f"std_resid_{s}" for s in skills_in_block if f"std_resid_{s}" in residual_df.columns]
        if len(cols) < 2:
            continue
        correlations[block_name] = residual_df[cols].corr()
    return correlations
