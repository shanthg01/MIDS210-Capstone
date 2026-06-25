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

import concurrent.futures
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import Engine, text

from portalpoint.modeling import player_projection as pp
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
    player_id, game_date, minutes, school_id, opponent_school_id, home_away,
    field_goals_made, field_goals_attempted,
    three_point_field_goals_made, three_point_field_goals_attempted,
    free_throws_made, free_throws_attempted,
    offensive_rebounds, defensive_rebounds,
    assists, steals, blocks, turnovers, fouls
FROM hoopr_player_game_logs
WHERE season = :season AND player_id IS NOT NULL
"""

TEAM_CONTEXT_SQL = """
SELECT school_id, season, adj_d, adj_tempo
FROM team_season_stats
WHERE season = :season
"""


def load_game_logs(engine: Engine, season: int) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(GAME_LOG_SQL), conn, params={"season": season})


def load_game_context(engine: Engine, season: int) -> pd.DataFrame:
    """Per-team-season context for Gap B's observation-layer adjustment
    (Issue #37 item 2): opponent defensive strength (`adj_d`), the player's
    own team pace (`adj_tempo`), and competition tier (`compute_level_tier`).
    Returned per `school_id` so the caller joins it onto game logs twice —
    once as the player's own school (for pace/tier), once as the opponent
    (for opponent_adj_d) — see `attach_game_context`."""
    with engine.connect() as conn:
        team_context = pd.read_sql(text(TEAM_CONTEXT_SQL), conn, params={"season": season})
    tier_df = compute_level_tier(engine, [season])
    return team_context.merge(tier_df, on=["school_id", "season"], how="left")


def attach_game_context(game_logs: pd.DataFrame, team_context: pd.DataFrame) -> pd.DataFrame:
    """Joins `load_game_context`'s per-team frame onto game_logs twice (own
    school for pace/tier, opponent school for opponent_adj_d), plus a numeric
    `home_flag` (1.0 home, 0.0 away/neutral). Pure function — no DB — so it's
    unit-testable with synthetic frames."""
    out = game_logs.copy()
    own = team_context.rename(columns={"school_id": "school_id", "adj_tempo": "team_pace", "tier": "tier"})
    opp = team_context.rename(columns={"school_id": "opponent_school_id", "adj_d": "opponent_adj_d"})
    out = out.merge(own[["school_id", "team_pace", "tier"]], on="school_id", how="left")
    out = out.merge(opp[["opponent_school_id", "opponent_adj_d"]], on="opponent_school_id", how="left")
    out["home_flag"] = (out["home_away"].astype(str).str.lower() == "home").astype(float)
    return out


CONTEXT_COLS = ["opponent_adj_d", "team_pace", "tier", "home_flag"]


def _context_design_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Builds the context design matrix, imputing missing context (failed
    team-join, etc.) with each column's own mean rather than dropping rows —
    every game-row must stay in the panel the Kalman filter sees, even if its
    context adjustment ends up being a no-op (imputed-to-mean contributes ~0
    net adjustment once centered in apply_context_adjustment)."""
    X = df[CONTEXT_COLS].astype(np.float64)
    for col in CONTEXT_COLS:
        X[col] = X[col].fillna(X[col].mean())
        if X[col].isna().all():
            X[col] = 0.0
    return X


def fit_context_adjustment(obs_df: pd.DataFrame, skill: str) -> LinearRegression | None:
    """Fits a single weighted linear regression of the raw observed skill
    rate on opponent strength / team pace / competition tier / home-away —
    the additive observation-layer context term from plan doc §7's
    `y = Z*alpha + level_intercept + opponent_adjustment + ... + noise` form,
    fit once (not per-game-by-game), then subtracted from the observation
    before it reaches the Kalman update (`apply_context_adjustment`).

    Returns None if there isn't enough weighted, context-complete data to
    fit at all (e.g. an empty or all-missing-context frame) — callers must
    treat that as "no adjustment available," not crash.
    """
    y_col, w_col = f"y_{skill}", f"weight_{skill}"
    valid = obs_df[obs_df[w_col] > 0].dropna(subset=[y_col])
    if len(valid) < 30:
        return None
    X = _context_design_matrix(valid)
    weights = valid[w_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
    model = LinearRegression()
    model.fit(X, valid[y_col].to_numpy(dtype=np.float64), sample_weight=weights)
    return model


def apply_context_adjustment(obs_df: pd.DataFrame, skill: str, model: LinearRegression | None) -> pd.Series:
    """Returns a context-neutralized `y_{skill}` series: observed rate minus
    the fitted context effect, re-centered on the population-weighted mean
    predicted effect so the adjusted series stays on the same natural scale
    the rest of the pipeline (priors, R_t, etc.) expects — only the
    game-to-game variation *explained by context* is removed, not the
    skill's overall level. Returns the original column unchanged if `model`
    is None (no fit was possible)."""
    y_col, w_col = f"y_{skill}", f"weight_{skill}"
    if model is None:
        return obs_df[y_col]
    X = _context_design_matrix(obs_df)
    predicted = model.predict(X)
    weights = obs_df[w_col].clip(lower=1e-6).to_numpy(dtype=np.float64)
    population_mean_predicted = float(np.average(predicted, weights=weights))
    return obs_df[y_col] - (predicted - population_mean_predicted)


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
    engine: Engine, seasons: list[int], use_phase0_prior: bool = True, max_workers: int | None = None,
    player_id_subset: set[int] | None = None, use_context_adjustment: bool = False,
) -> tuple[dict[int, dict[str, float]], pd.DataFrame]:
    """Runs Phase 1's intra-season filter+smoother once per (season, skill)
    pair. Returns (fitted_Q per season per skill, one merged frame with
    skill_<s>/skill_var_<s> per player per season — the season-grain layer's
    raw "observations").

    `use_phase0_prior` (Gap D, Issue #37 reconciliation, 2026-06-23):
    when True (default), loads Phase 0's `shrink_skills()` output once for
    all seasons and passes each season's slice to `ppk.smooth_skill`'s
    `external_priors` argument, so the intra-season filter starts from
    Phase 0's position x season shrinkage estimate instead of a flat
    population mean — see `player_projection_kalman.build_player_sequences`'s
    docstring for the full reasoning. `shrink_skills` groups by season
    internally, so loading and shrinking once here (not per-season) is
    correct and avoids redundant work.

    Performance (2026-06-24): every (season, skill) pair is an independent
    fit — nothing about season 2023's `shooting_3p` fit depends on 2024's or
    on `passing_creation`'s. The original implementation called
    `ppk.smooth_all_skills` once per season, which looped over the 10 skills
    serially *inside* that call — meaning up to 70 independent fits (7
    seasons x 10 skills) ran one at a time. This flattens that into one flat
    list of up to 70 tasks submitted to a single `ProcessPoolExecutor`
    (`max_workers=None` uses all available cores), instead of nesting a
    skills-pool inside a seasons-loop — nested process pools are themselves a
    real footgun (multiprocessing-within-multiprocessing is fragile and often
    silently serializes or deadlocks), so this flat structure is deliberate,
    not an oversight. Results are collected into a plain dict first and then
    re-assembled in canonical `seasons`/`SKILLS` order — `as_completed()`'s
    order is non-deterministic across runs, and the original per-season
    merge order must stay reproducible regardless of which worker finishes
    first.

    `player_id_subset` (2026-06-24, proxy-run support): when given, restricts
    every season's game logs (and the Phase 0 prior lookup) to this player
    set before any fitting happens — not a sampling step inside the math
    itself, just a smaller population fed through the unchanged pipeline.
    For getting a fast read on model behavior (sign/magnitude of fitted
    rho/beta/Q per skill, sanity of smoothed trajectories) before committing
    to a full real-data run — not used in the production path.

    `use_context_adjustment` (Gap B, Issue #37 reconciliation, 2026-06-24):
    when True, attaches opponent/pace/tier/home-away context to each
    season's observations (`load_game_context`/`attach_game_context`) and,
    per skill, fits a context-adjustment regression and subtracts the
    explained context effect from that skill's `y_<skill>` column
    (`fit_context_adjustment`/`apply_context_adjustment`) *before* it reaches
    the Kalman filter — the additive `opponent_adjustment` term from the plan
    doc's §7 observation equation. Each skill needs its own context-adjusted
    copy of the season's observations (the fitted regression differs per
    skill), so this branch builds one `obs_df` per (season, skill) instead of
    sharing one per season across skills.
    """
    phase0_shrunk: pd.DataFrame | None = None
    if use_phase0_prior:
        phase0_df = pp.load_player_season_frame(engine)
        if player_id_subset is not None:
            phase0_df = phase0_df[phase0_df["player_id"].isin(player_id_subset)]
        phase0_shrunk = pp.shrink_skills(phase0_df)

    season_obs: dict[int, pd.DataFrame] = {}
    for season in seasons:
        game_logs = load_game_logs(engine, season)
        if player_id_subset is not None:
            game_logs = game_logs[game_logs["player_id"].isin(player_id_subset)]
        if game_logs.empty:
            log.warning("No game logs for season %d, skipping", season)
            continue
        obs_df = ppk.build_game_observations(game_logs)
        if use_context_adjustment:
            team_context = load_game_context(engine, season)
            obs_df = attach_game_context(obs_df, team_context)
        season_obs[season] = obs_df

    tasks: list[tuple[int, str, pd.DataFrame, pd.DataFrame | None]] = []
    for season, obs_df in season_obs.items():
        external_priors_df = None
        if phase0_shrunk is not None:
            season_priors = phase0_shrunk[phase0_shrunk["season"] == season]
            external_priors_df = season_priors if not season_priors.empty else None
        for skill in ppk.SKILLS:
            y_col, w_col = f"y_{skill}", f"weight_{skill}"
            # Slim to exactly what ppk.smooth_skill/build_player_sequences
            # reads (player_id + this skill's y_/weight_ only) -- a real
            # memory bug surfaced here on the first Gap B real run: building
            # all 70 (season, skill) tasks eagerly, each a *full* obs_df.copy()
            # (every skill's columns + raw game-log columns + context columns,
            # ~67MB with context attached), put ~4.7GB of redundant data in
            # this list before any submission even started, and crashed the
            # ProcessPoolExecutor's workers (BrokenProcessPool, almost
            # certainly an OS-level OOM kill, not a Python exception). Each
            # task only ever needed 3 columns.
            if use_context_adjustment:
                context_model = fit_context_adjustment(obs_df, skill)
                adjusted_y = apply_context_adjustment(obs_df, skill, context_model)
                skill_obs_df = pd.DataFrame({"player_id": obs_df["player_id"], y_col: adjusted_y, w_col: obs_df[w_col]})
            else:
                skill_obs_df = obs_df[["player_id", y_col, w_col]]
            tasks.append((season, skill, skill_obs_df, external_priors_df))

    results: dict[tuple[int, str], tuple[float, pd.DataFrame]] = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {
            executor.submit(ppk.smooth_skill, obs_df, skill, ext): (season, skill)
            for season, skill, obs_df, ext in tasks
        }
        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            results[key] = future.result()

    fitted_q_by_season: dict[int, dict[str, float]] = {}
    frames: list[pd.DataFrame] = []
    for season in seasons:
        if season not in season_obs:
            continue
        fitted_q: dict[str, float] = {}
        merged: pd.DataFrame | None = None
        for skill in ppk.SKILLS:
            q_value, skill_df = results[(season, skill)]
            fitted_q[skill] = q_value
            skill_df = skill_df.drop(columns=["_n_games_observed"])
            merged = skill_df if merged is None else merged.merge(skill_df, on="player_id", how="outer")
        merged["season"] = season
        fitted_q_by_season[season] = fitted_q
        frames.append(merged)
        n_with_prior = (
            len(phase0_shrunk[phase0_shrunk["season"] == season]) if phase0_shrunk is not None else 0
        )
        log.info(
            "Season %d: %d players, intra-season filter complete (%d with Phase 0 prior)",
            season, len(merged), n_with_prior,
        )
    merged_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return fitted_q_by_season, merged_all


def load_or_build_season_skill_states(
    engine: Engine, seasons: list[int], cache_dir: Path | None = None, force_rebuild: bool = False,
    use_phase0_prior: bool = True, use_context_adjustment: bool = False,
) -> tuple[dict[int, dict[str, float]], pd.DataFrame]:
    """Cached wrapper around build_season_skill_states. The ~2h intra-season
    filtering pass is identical every time it's run against the same seasons
    *and* the same prior-sourcing (deterministic given the same data) — there's
    no reason to pay that cost once per caller (2b, 2c, 2d, and re-running 2a's
    own diagnostics all need this exact output). `use_phase0_prior` is part of
    the cache filename (Gap D, Issue #37 reconciliation) specifically so a
    flat-prior cache and a Phase-0-prior cache can't collide — set
    force_rebuild=True after a real upstream data change (e.g. another
    game-log backfill), not needed just to flip this flag. `use_context_adjustment`
    (Gap B) is part of the cache filename for the same reason.

    `seasons` is part of the cache filename too (real bug, found 2026-06-25):
    the original version only varied by prior/context suffix, so calling
    this with a *different* `seasons` list (e.g. a quick 2-season check after
    a full 2020-2026 run) would silently load the wrong cached states —
    same data shape, wrong season coverage, no error."""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    prior_suffix = "phase0prior" if use_phase0_prior else "flatprior"
    context_suffix = "ctxadj" if use_context_adjustment else "noctx"
    seasons_suffix = "-".join(str(s) for s in sorted(seasons))
    states_path = cache_dir / f"season_states_{prior_suffix}_{context_suffix}_{seasons_suffix}.parquet"
    q_path = cache_dir / f"fitted_q_by_season_{prior_suffix}_{context_suffix}_{seasons_suffix}.json"

    if not force_rebuild and states_path.exists() and q_path.exists():
        log.info("Loading cached season skill states from %s", states_path)
        season_states = pd.read_parquet(states_path)
        fitted_q_by_season = {int(k): v for k, v in json.loads(q_path.read_text()).items()}
        return fitted_q_by_season, season_states

    fitted_q_by_season, season_states = build_season_skill_states(
        engine, seasons, use_phase0_prior=use_phase0_prior, use_context_adjustment=use_context_adjustment,
    )
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
    is a single fast no-op call once both are warm.

    Cache filename includes the seasons actually present in `season_states`
    (same real-bug fix as `load_or_build_season_skill_states` — a constant
    `"covariates.parquet"` filename would silently return covariates built
    for a different season range)."""
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    seasons_suffix = "-".join(str(s) for s in sorted(season_states["season"].unique().tolist()))
    covariates_path = cache_dir / f"covariates_{seasons_suffix}.parquet"

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


SeasonSequence = tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, float, np.ndarray,
]
# y, R, mask, career_season_index, transfer_flag, level_change, prior_mean, prior_var, seasons
#
# `seasons` (2026-06-25): the real season year per row, same order as `y` --
# added because `smooth_season_skill`'s output previously carried only a
# synthetic positional `season_rank` (np.arange(1, len(y)+1)), independently
# re-derived from this same sequence rather than reading the real value that
# was already in scope here. That's a real risk, not just style: if any
# skill's `dropna(subset=[y_col, var_col])` above drops a *different* season
# for a player than another skill does (plausible — different skills can
# have different per-season data availability), their `season_rank`
# sequences silently desync relative to each other, and `fit_all_skills`'
# merge (previously keyed on `season_rank`) would merge mismatched seasons
# across skills with no error. Carrying the real season through removes that
# whole class of risk — merges can key on `season` directly.

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
        seasons_arr = g["season"].to_numpy(dtype=np.int64)
        sequences[int(player_id)] = (
            y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, seasons_arr,
        )
    return sequences


def _pooled_neg_log_likelihood(params: np.ndarray, sequences: list[SeasonSequence]) -> float:
    rho, beta_0, beta_1, beta_2, beta_3, beta_4, log_q = params
    q_value = float(np.exp(log_q))
    if not (0.0 <= rho <= 1.2) or q_value <= 0:
        return np.inf
    total = 0.0
    for y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, _ in sequences:
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
    for y, _, mask, _, _, _, _, _, _ in sequences:
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


MAX_SEQUENCES_FOR_SEASON_SEARCH = 1000


def fit_season_model(
    sequences: list[SeasonSequence], fixed_rho: float | None = None,
    max_sequences_for_search: int | None = MAX_SEQUENCES_FOR_SEASON_SEARCH, random_state: int = 0,
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

    Performance note (2026-06-24): this is a pooled population-level fit
    (beta_0..4, Q), the same shape of cost/identifiability tradeoff as
    `player_projection_kalman.fit_q_mle`'s Q-search — found, via a live
    `py-spy dump` of an actual stuck run, to be the *real* dominant cost in
    Phase 2a's full pipeline (Nelder-Mead on 6 free dimensions needs far more
    function evaluations than the 1-D Brent search that motivated the
    original Q-search subsampling fix, and each evaluation loops over every
    player's full sequence). Same fix, same justification: search on a
    deterministic random subsample, since `beta_0..4`/`Q` are pooled
    estimates that don't need the full population to converge to essentially
    the same value.
    """
    search_sequences = sequences
    if max_sequences_for_search is not None and len(sequences) > max_sequences_for_search:
        rng = np.random.default_rng(random_state)
        idx = rng.choice(len(sequences), size=max_sequences_for_search, replace=False)
        search_sequences = [sequences[i] for i in idx]

    q_init = _q_init_from_diffs(search_sequences)

    if fixed_rho is not None:
        def _nll_fixed(params: np.ndarray) -> float:
            beta_0, beta_1, beta_2, beta_3, beta_4, log_q = params
            full = np.array([fixed_rho, beta_0, beta_1, beta_2, beta_3, beta_4, log_q])
            return _pooled_neg_log_likelihood(full, search_sequences)

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
            _pooled_neg_log_likelihood, x0, args=(search_sequences,),
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
    for player_id, (y, r_arr, mask, csi, transfer_flag, level_change, prior_mean, prior_var, seasons_arr) in sequences.items():
        mu = beta_0 + beta_1 * csi + beta_2 * csi**2 + beta_3 * transfer_flag + beta_4 * level_change
        a, p_var, pred_mean, pred_var = kalman_filter_with_drift(
            y, r_arr, rho, mu, q_value, mask, prior_mean, prior_var,
        )
        std_resid = (y - pred_mean) / np.sqrt(pred_var)
        seasons_for_player = np.arange(1, len(y) + 1)  # positional ordering only -- see "season" below for the real key
        for i in range(len(y)):
            rows.append({
                "player_id": player_id,
                "season": int(seasons_arr[i]),
                "season_rank": int(seasons_for_player[i]),
                f"phase2_skill_{skill}": float(a[i]),
                f"phase2_skill_var_{skill}": float(p_var[i]),
                f"std_resid_{skill}": float(std_resid[i]),
            })
    return fitted, pd.DataFrame(rows)


def fit_all_skills(
    skill_df: pd.DataFrame, covariates: pd.DataFrame, max_workers: int | None = None,
) -> tuple[dict[str, dict], pd.DataFrame]:
    """Runs smooth_season_skill for every skill, merges into one per
    (player_id, season) frame (season, not season_rank — see SeasonSequence's
    docstring for why season is the more reliable merge key).

    Performance (2026-06-24): each skill's `smooth_season_skill` fit is fully
    independent (different column slice of `skill_df`, no shared state) —
    confirmed via `py-spy dump` to be the actual dominant cost in the full
    pipeline (see `fit_season_model`'s docstring). Runs all `len(SKILLS)`
    fits through one `ProcessPoolExecutor` (`max_workers=None` uses every
    available core) instead of the original serial loop. Results are
    collected into a dict first and reassembled in canonical `SKILLS` order
    before merging — `as_completed()`'s order is non-deterministic across
    runs, and the merge order/output must stay reproducible regardless of
    which worker finishes first.
    """
    results: dict[str, tuple[dict, pd.DataFrame]] = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_skill = {
            executor.submit(smooth_season_skill, skill_df, covariates, skill): skill for skill in SKILLS
        }
        for future in concurrent.futures.as_completed(future_to_skill):
            skill = future_to_skill[future]
            results[skill] = future.result()

    fitted_params: dict[str, dict] = {}
    merged: pd.DataFrame | None = None
    for skill in SKILLS:
        fitted, skill_df_result = results[skill]
        fitted_params[skill] = fitted
        # Merge key is the real "season" (2026-06-25), not "season_rank" --
        # season_rank is computed independently inside each skill's own
        # build_season_sequences call, and two skills can drop different
        # seasons for the same player (different per-skill data
        # availability), desyncing their season_rank sequences relative to
        # each other. "season" doesn't have that risk. Each skill's own
        # season_rank column is dropped before merging (not a reliable
        # cross-skill value once it's no longer the join key) -- callers
        # needing season_rank should recompute it from `covariates`'
        # `career_season_index` after joining on (player_id, season).
        skill_df_result = skill_df_result.drop(columns=["season_rank"])
        merged = (
            skill_df_result if merged is None
            else merged.merge(skill_df_result, on=["player_id", "season"], how="outer")
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


# Gap A (Issue #37 reconciliation, 2026-06-23): only the 2 blocks that
# actually validated against §6's hypotheses (plan doc §22) — creation
# (passing_creation vs turnover_avoidance resid corr 0.35) and rebounding
# (offensive vs defensive rebounding resid corr 0.41). shooting_touch (weak/
# mixed) and defensive_playmaking (near-zero) are deliberately excluded —
# blending priors within an unvalidated block would manufacture a false
# cross-skill signal, not capture a real one.
VALIDATED_BLOCKS = ("creation", "rebounding")


def blend_block_priors(
    residual_df: pd.DataFrame,
    block_correlations: dict[str, pd.DataFrame],
    validated_blocks: tuple[str, ...] = VALIDATED_BLOCKS,
) -> pd.DataFrame:
    """Upgrades the block-correlation diagnostic into an actual shared-prior
    mechanism, for validated blocks only (Gap A).

    For each skill in a validated block, finds its most-correlated block-mate
    and applies a simple linear (MMSE-style) adjustment:

        adjusted_skill = phase2_skill
                          + correlation * std_resid[block-mate]
                            * sqrt(phase2_skill_var)

    This nudges a skill's season-grain estimate using the correlated
    block-mate's standardized one-step-ahead residual (how much that skill
    over/under-performed its own model's expectation that season), scaled by
    the empirical correlation and converted back into the skill's natural
    units via its own forecast variance. This is the documented "shared
    priors informed by correlated skills" version from plan doc §6's table —
    not a joint multivariate Kalman update (full covariance filtering stays
    deferred, see the implementation-time plan's "Explicitly Deferred"
    section for the concrete triggers to revisit that).

    Adds `phase2_skill_{skill}_blended` columns for skills in
    `validated_blocks`; skills outside those blocks (or blocks missing from
    `block_correlations`) are left unchanged — no `_blended` column is added
    for them, so callers can tell which skills actually got this treatment.
    """
    out = residual_df.copy()
    for block_name in validated_blocks:
        skills_in_block = SKILL_BLOCKS.get(block_name, [])
        corr = block_correlations.get(block_name)
        if corr is None or len(skills_in_block) < 2:
            continue
        for skill in skills_in_block:
            other_skills = [s for s in skills_in_block if s != skill]
            available = [s for s in other_skills if f"std_resid_{s}" in out.columns]
            if not available:
                continue
            best_mate, best_corr = max(
                ((s, corr.loc[f"std_resid_{skill}", f"std_resid_{s}"]) for s in available),
                key=lambda pair: abs(pair[1]),
            )
            base_col, var_col = f"phase2_skill_{skill}", f"phase2_skill_var_{skill}"
            if base_col not in out.columns or var_col not in out.columns:
                continue
            adjustment = best_corr * out[f"std_resid_{best_mate}"] * np.sqrt(out[var_col].clip(lower=0.0))
            out[f"{base_col}_blended"] = out[base_col] + adjustment
    return out


# Gap C (Issue #37 reconciliation, 2026-06-24): real per-40/per-100 rate
# projections for `projected_rates`/`projected_box_score` (item 3), feeding
# Gap F's eventual write path. Two stages, per the plan:
#
# Stage 2B (conditional rates) turns out to need no new regression at all:
# `passing_creation`/`offensive_rebounding`/`defensive_rebounding`/
# `steal_disruption`/`block_rim_protection`/`turnover_avoidance` are *already*
# per-40 rates by construction (`player_projection_kalman.RATE_PER_40_SKILLS`
# literally defines them that way) — their Phase 2a smoothed state already
# *is* the projected per-40 rate. Stage 2B is `STAGE_2B_RATE_SKILLS` below: a
# direct relabeling, not a model.
#
# Stage 2A (possession outcome) is the real new work: `shot_creation_usage`
# is a single total-volume number (FGA + 0.44*FTA per 40); none of the
# existing skills capture how that volume splits across 2PA/3PA/FT-trip
# attempts. Documented adaptation (same honesty standard as the rest of this
# module): we only have aggregated box-score counts, not true per-possession
# PBP, so "possession outcome" here means season-aggregated per-40 attempt
# *rates* for {2PA, 3PA, FT-trip}, regressed on the shooting-percentage
# skills + total volume + position — not a literal multinomial over
# possession-level events, and not constrained to sum to `shot_creation_usage`
# exactly (a real simplification, stated rather than hidden). The plan doc's
# "other" category (possessions a player didn't personally end) is dropped
# entirely — nothing in the available box-score data identifies it, and
# inventing a residual category would manufacture a number with no real
# basis. Make/miss splits for 2PA/3PA are then derived by multiplying the
# fitted attempt rate by the corresponding shooting-percentage skill
# (already a probability in [0, 1]) — `FT trip` has no make/miss split in
# the plan's own category list (the make/miss is `free_throw_touch`'s job).

STAGE_2A_FEATURE_SKILLS = ["shooting_3p", "shooting_2p_finishing", "free_throw_touch", "shot_creation_usage"]
STAGE_2A_TARGETS = ("rate_2pa_attempted", "rate_3pa_attempted", "rate_ft_trip")
STAGE_2B_RATE_SKILLS = {
    "rate_assist": "passing_creation",
    "rate_oreb": "offensive_rebounding",
    "rate_dreb": "defensive_rebounding",
    "rate_stl": "steal_disruption",
    "rate_blk": "block_rim_protection",
    "rate_tov": "turnover_avoidance",
}

ATTEMPT_RATE_SQL = """
SELECT
    player_id, season,
    SUM(minutes) AS total_minutes,
    SUM(field_goals_attempted - three_point_field_goals_attempted) AS fg2a,
    SUM(three_point_field_goals_attempted) AS fg3a,
    SUM(free_throws_attempted) AS fta
FROM hoopr_player_game_logs
WHERE season = ANY(:seasons) AND player_id IS NOT NULL
GROUP BY player_id, season
"""


MIN_MINUTES_FOR_RATE_TARGET = 40.0  # roughly one game's worth -- see docstring


def build_attempt_rate_targets(engine: Engine, seasons: list[int]) -> pd.DataFrame:
    """Season-aggregated per-40 attempt-rate regression *targets* for Stage
    2A — real box-score totals, not the smoothed skill states (those are the
    *features*, built separately by `build_season_skill_states`).

    Real bug, found on the first real-data run (2026-06-24): the original
    version only `clip(lower=1e-6)`'d `total_minutes` before dividing —
    correct for avoiding a literal division by zero, but a player with a few
    seconds of garbage-time minutes and even one attempt produces an
    astronomical per-40 rate (1 attempt / 1e-6 minutes * 40 ≈ 40 million),
    which dominates the Ridge fit's residual variance entirely (observed
    `resid_std` ≈ 227,000 on real data — nonsensical). Fixed by dropping rows
    below `MIN_MINUTES_FOR_RATE_TARGET` outright, matching Phase 0's own
    `MIN_GAMES`-floor convention (drop low-sample rows rather than compute a
    garbage rate for them), instead of letting a near-zero denominator
    through.
    """
    with engine.connect() as conn:
        totals = pd.read_sql(text(ATTEMPT_RATE_SQL), conn, params={"seasons": seasons})
    return _compute_attempt_rates(totals)


def _compute_attempt_rates(totals: pd.DataFrame) -> pd.DataFrame:
    """Pure rate-computation step factored out of `build_attempt_rate_targets`
    so the minutes-floor fix is unit-testable without a DB connection."""
    totals = totals[totals["total_minutes"] >= MIN_MINUTES_FOR_RATE_TARGET].copy()
    minutes = totals["total_minutes"]
    totals["rate_2pa_attempted"] = totals["fg2a"] / minutes * 40.0
    totals["rate_3pa_attempted"] = totals["fg3a"] / minutes * 40.0
    totals["rate_ft_trip"] = totals["fta"] / minutes * 40.0
    return totals[["player_id", "season", "total_minutes", *STAGE_2A_TARGETS]]


def _stage_2a_design_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """[skill_<feature>] + position dummies — same shape/convention as
    `player_projection.build_design_matrix`, but Stage 2A's smaller feature
    set (shooting + volume skills only, not the full SKILLS list)."""
    skill_cols = [f"skill_{s}" for s in STAGE_2A_FEATURE_SKILLS]
    pos_dummies = pd.get_dummies(df["position"], prefix="pos") if "position" in df.columns else pd.DataFrame(index=df.index)
    X = pd.concat([df[skill_cols].fillna(0.0), pos_dummies], axis=1)
    return X.reindex(columns=skill_cols + list(pos_dummies.columns), fill_value=0.0)


def fit_attempt_rate_models(
    states_df: pd.DataFrame, alpha: float = 5.0,
) -> dict[str, tuple[Pipeline, float]]:
    """Fits one weighted Ridge model per Stage 2A target. `states_df` must
    have `skill_<feature>` columns (Stage 2A's feature set), `position`, the
    3 target columns from `build_attempt_rate_targets`, and `total_minutes`
    (used as the sample weight — more minutes, more reliable a season's
    attempt-rate target is). Returns {target: (fitted_pipeline,
    residual_std)} — residual_std mirrors `player_projection.fit_value_model`'s
    uncertainty convention."""
    models: dict[str, tuple[Pipeline, float]] = {}
    for target in STAGE_2A_TARGETS:
        train = states_df.dropna(subset=[target, *[f"skill_{s}" for s in STAGE_2A_FEATURE_SKILLS]])
        if len(train) < 30:
            continue
        X = _stage_2a_design_matrix(train)
        y = train[target].to_numpy(dtype=np.float64)
        weights = train["total_minutes"].clip(lower=1e-6).to_numpy(dtype=np.float64) if "total_minutes" in train else None
        model = Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=alpha))])
        fit_kwargs = {"ridge__sample_weight": weights} if weights is not None else {}
        model.fit(X, y, **fit_kwargs)
        resid_std = float(np.std(y - model.predict(X)))
        models[target] = (model, resid_std)
    return models


def project_rates(
    states_df: pd.DataFrame, attempt_models: dict[str, tuple[Pipeline, float]],
    pace: pd.Series | None = None,
) -> pd.DataFrame:
    """Combines Stage 2A's fitted attempt-rate models with the shooting-
    percentage skills (make/miss split) and Stage 2B's direct skill read-off
    into one per-40 + per-100 rate frame per (player_id, season).

    `pace` (optional): per-row team `adj_tempo` (possessions/40min) for a
    per-100-possession conversion (`per_100 = per_40 / (pace / 100)`).
    Without it, falls back to a fixed NCAA Division I average pace
    (~68 possessions/40min) — an approximation stated here, not hidden.
    """
    out = states_df[["player_id", "season"]].copy()
    X = _stage_2a_design_matrix(states_df)

    for target, (model, _resid_std) in attempt_models.items():
        out[target] = model.predict(X).clip(min=0.0)

    if "rate_2pa_attempted" in out.columns and "skill_shooting_2p_finishing" in states_df.columns:
        pct = states_df["skill_shooting_2p_finishing"].clip(0.0, 1.0)
        out["rate_2pa_make"] = out["rate_2pa_attempted"] * pct
        out["rate_2pa_miss"] = out["rate_2pa_attempted"] * (1.0 - pct)
    if "rate_3pa_attempted" in out.columns and "skill_shooting_3p" in states_df.columns:
        pct = states_df["skill_shooting_3p"].clip(0.0, 1.0)
        out["rate_3pa_make"] = out["rate_3pa_attempted"] * pct
        out["rate_3pa_miss"] = out["rate_3pa_attempted"] * (1.0 - pct)

    for rate_col, skill in STAGE_2B_RATE_SKILLS.items():
        skill_col = f"skill_{skill}"
        if skill_col in states_df.columns:
            out[rate_col] = states_df[skill_col].clip(lower=0.0)

    DEFAULT_PACE = 68.0
    pace_arr = pace.to_numpy(dtype=np.float64) if pace is not None else np.full(len(out), DEFAULT_PACE)
    pace_arr = np.where(np.isfinite(pace_arr) & (pace_arr > 0), pace_arr, DEFAULT_PACE)
    per40_cols = [c for c in out.columns if c not in ("player_id", "season")]
    for col in per40_cols:
        out[f"{col}_per100"] = out[col] / (pace_arr / 100.0)

    return out


# Gap F (Issue #37 reconciliation, 2026-06-24): writes real skill_states/
# uncertainty/projected_rates/projected_box_score for Phase 2a, instead of
# Phase 0's empty `{}` placeholders for those same fields. Writes to the same
# `player_projections` table under a *different* `model_version` — the
# table's partial unique index is on (player_id, season, model_version)
# WHERE school_id IS NULL, so Phase 2a rows never collide with or overwrite
# Phase 0's. `pp.upsert_neutral_projections` is fully generic (keyed off
# model_version, not the row content), so it's reused as-is here, not
# duplicated.
MODEL_VERSION_PHASE2A = "player-projection-phase2a-v2"
MODEL_VERSION_PHASE2A_FORECAST = "player-proj-phase2a-fcast-v1"
FORECAST_OFF_EXTRA_FEATURES = ["source_off_value_per_100", "source_value_per_100"]
FORECAST_DEF_EXTRA_FEATURES = ["source_def_value_per_100", "source_value_per_100"]


def forecast_next_season_states(
    phase2_states: pd.DataFrame,
    covariates: pd.DataFrame,
    fitted_params: dict[str, dict],
) -> pd.DataFrame:
    """One-step-ahead neutral forecasts from observed season states.

    The same-season Phase 2a state rows answer "what did this player's
    observed season imply about his skill state?" Production player
    projection needs the next question: "given that observed state, what is
    the best estimate for the target season?" This applies the fitted
    season-grain transition equation once:

        alpha[t+1|t] = rho * alpha[t] + mu[t+1]

    `season` in the returned frame is the target/projected season, while
    `source_observed_season` records the season used to forecast it. Historical
    target seasons use known target-season transfer/level-change covariates
    when available; future rows without a known destination default to neutral
    no-transfer/no-level-change covariates.
    """
    source = phase2_states.copy()
    source["source_observed_season"] = source["season"].astype(int)
    source["season"] = source["source_observed_season"] + 1

    observed_cov = covariates.rename(columns={
        "season": "source_observed_season",
        "career_season_index": "source_career_season_index",
    })
    target_cov = covariates.rename(columns={
        "career_season_index": "target_career_season_index",
        "transfer_flag": "target_transfer_flag",
        "level_change": "target_level_change",
    })
    out = (
        source
        .merge(
            observed_cov[["player_id", "source_observed_season", "source_career_season_index"]],
            on=["player_id", "source_observed_season"], how="left",
        )
        .merge(
            target_cov[["player_id", "season", "target_career_season_index", "target_transfer_flag", "target_level_change"]],
            on=["player_id", "season"], how="left",
        )
    )
    out["target_career_season_index"] = out["target_career_season_index"].fillna(out["source_career_season_index"] + 1)
    out["target_transfer_flag"] = out["target_transfer_flag"].fillna(0.0)
    out["target_level_change"] = out["target_level_change"].fillna(0.0)

    for skill in SKILLS:
        skill_col = f"skill_{skill}"
        var_col = f"skill_var_{skill}"
        if skill_col not in out.columns or var_col not in out.columns:
            continue
        params = fitted_params[skill]
        csi = out["target_career_season_index"].to_numpy(dtype=np.float64)
        transfer = out["target_transfer_flag"].to_numpy(dtype=np.float64)
        level = out["target_level_change"].to_numpy(dtype=np.float64)
        mu = (
            params["beta_0"]
            + params["beta_1"] * csi
            + params["beta_2"] * csi**2
            + params["beta_3"] * transfer
            + params["beta_4"] * level
        )
        rho = float(params["rho"])
        out[skill_col] = rho * out[skill_col].to_numpy(dtype=np.float64) + mu
        out[var_col] = rho * rho * out[var_col].clip(lower=0.0).to_numpy(dtype=np.float64) + float(params["Q"])

    drop_cols = [
        "source_career_season_index", "target_career_season_index",
        "target_transfer_flag", "target_level_change",
    ]
    return out.drop(columns=[c for c in drop_cols if c in out.columns])


def build_phase2_records(
    projected_df: pd.DataFrame, projected_rates_df: pd.DataFrame | None = None,
    archetypes_df: pd.DataFrame | None = None, model_version: str = MODEL_VERSION_PHASE2A,
) -> list[tuple]:
    """Phase 2a's analog of `player_projection.build_neutral_records`.
    `projected_df` must be `pp.project_value`'s output (value_per_100/CI/
    `_resid_std`) applied to a `pp.skill_percentiles`'d Phase 2a state frame
    (`skill_<s>`/`skill_var_<s>`/`pctile_<s>` columns) — same shape Phase 0
    uses, just sourced from Phase 2a's season-grain states instead of Phase
    0's single-season shrinkage. `projected_rates_df` is Gap C's
    `project_rates` output (optional — left-joined by (player_id, season));
    players below Gap C's `MIN_MINUTES_FOR_RATE_TARGET` floor get `{}` for
    `projected_box_score`/`projected_rates` specifically (same "real but
    incomplete, not a crash" convention Phase 0 itself uses for fields it
    hasn't built yet), not dropped from the table.

    `projected_box_score` is a real per-40 derived summary (points, rebounds,
    assists, steals, blocks, turnovers) computed from Gap C's category rates
    — `pts_per_40` needs free-throw *makes*, not just the trip rate Gap C
    fits directly, so it's derived here as `rate_ft_trip * skill_free_throw_touch`.

    `archetypes_df` (Gap E, Issue #37 reconciliation): optional frame with
    `player_id`/`season`/`archetype_label`/`confidence` columns (same shape
    `player_projection_eval.join_archetype_metadata` expects/produces) — when
    given, adds those two fields into `explanation` for players with a
    matched archetype row. Evaluation/explanation metadata *only*, per
    Issue #37's explicit constraint — never touches `skill_states`, the
    design matrix, or either value model. Missing for a player-season simply
    means no archetype keys are added, not a crash or a dropped row.
    """
    computed_at = datetime.now(timezone.utc)
    expires_at = computed_at + timedelta(days=pp.EXPIRES_DAYS)

    rates_lookup: dict[tuple[int, int], dict] = {}
    if projected_rates_df is not None:
        rate_cols = [c for c in projected_rates_df.columns if c not in ("player_id", "season")]
        for _, rr in projected_rates_df.iterrows():
            rates_lookup[(int(rr["player_id"]), int(rr["season"]))] = {
                c: round(float(rr[c]), 3) for c in rate_cols
            }

    archetype_lookup: dict[tuple[int, int], dict] = {}
    if archetypes_df is not None:
        for _, ar in archetypes_df.iterrows():
            if pd.isna(ar.get("archetype_label")):
                continue
            archetype_lookup[(int(ar["player_id"]), int(ar["season"]))] = {
                "archetype_label": ar["archetype_label"],
                "archetype_confidence": float(ar["confidence"]) if pd.notna(ar.get("confidence")) else None,
            }

    records: list[tuple] = []
    for _, r in projected_df.iterrows():
        skill_states = {
            s: round(float(-r[f"skill_{s}"] if s in pp.INVERTED_SKILLS else r[f"skill_{s}"]), 4)
            for s in ppk.SKILLS
        }
        skill_pcts = {s: float(r[f"pctile_{s}"]) for s in ppk.SKILLS}
        residual_std = float(r.get("_residual_std", r.get("_resid_std", 0.0)))
        value_std = float(r.get("_value_std", r.get("_resid_std", residual_std)))
        skill_state_value_std = float(r.get("_skill_state_value_std", 0.0))
        uncertainty = {
            "residual_std": round(residual_std, 3),
            "value_std": round(value_std, 3),
            "skill_state_value_std": round(skill_state_value_std, 3),
            "ci_scale": round(float(r.get("_ci_scale", 1.0)), 3),
            "skill_state_var": {
                s: round(float(r[f"skill_var_{s}"]), 4) for s in ppk.SKILLS if f"skill_var_{s}" in r.index
            },
        }
        explanation = {
            "source": "phase2a_next_season_forecast" if "source_observed_season" in r.index else "phase2a_season_grain_state_space",
            "skill_state_direction": {
                s: "higher_is_better" if s not in pp.INVERTED_SKILLS else "stored_as_negative_rate_so_higher_is_better"
                for s in ppk.SKILLS
            },
            **archetype_lookup.get((int(r["player_id"]), int(r["season"])), {}),
        }
        if "source_observed_season" in r.index and pd.notna(r["source_observed_season"]):
            explanation["source_observed_season"] = int(r["source_observed_season"])
            explanation["target_projected_season"] = int(r["season"])
            explanation["forecast_horizon_seasons"] = int(r["season"] - r["source_observed_season"])
        source_value_fields = [
            "source_value_per_100", "source_off_value_per_100", "source_def_value_per_100",
        ]
        source_values = {
            field: round(float(r[field]), 3)
            for field in source_value_fields
            if field in r.index and pd.notna(r[field])
        }
        if source_values:
            explanation["source_internal_value_prior"] = source_values
        if "_value_drivers" in r.index and isinstance(r["_value_drivers"], dict):
            explanation["value_components"] = {
                "off_value_per_100": round(float(r["off_value_per_100"]), 3),
                "raw_def_value_per_100": round(float(r["def_value_per_100"]), 3),
                "total_value_formula": pp.TOTAL_VALUE_FORMULA,
            }
            explanation["value_drivers"] = r["_value_drivers"]

        rates = rates_lookup.get((int(r["player_id"]), int(r["season"])), {})
        box_score: dict = {}
        if rates:
            ft_makes_per_40 = rates.get("rate_ft_trip", 0.0) * float(r.get("skill_free_throw_touch", 0.0))
            box_score = {
                "pts_per_40": round(
                    2 * rates.get("rate_2pa_make", 0.0) + 3 * rates.get("rate_3pa_make", 0.0) + ft_makes_per_40, 2,
                ),
                "reb_per_40": round(rates.get("rate_oreb", 0.0) + rates.get("rate_dreb", 0.0), 2),
                "ast_per_40": round(rates.get("rate_assist", 0.0), 2),
                "stl_per_40": round(rates.get("rate_stl", 0.0), 2),
                "blk_per_40": round(rates.get("rate_blk", 0.0), 2),
                "tov_per_40": round(rates.get("rate_tov", 0.0), 2),
            }

        records.append((
            int(r["player_id"]), None, int(r["season"]), "neutral",
            round(float(r["value_per_100"]), 3),
            round(float(r["value_ci_lower"]), 3),
            round(float(r["value_ci_upper"]), 3),
            None, None,
            json.dumps(box_score), json.dumps(rates),
            json.dumps(skill_states), json.dumps(skill_pcts),
            json.dumps(uncertainty),
            json.dumps(explanation),
            model_version, computed_at, expires_at,
        ))
    return records
