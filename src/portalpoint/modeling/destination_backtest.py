"""Destination Projection — Historical Backtest & Error Diagnostics.

Compares the Destination-Adjusted Projection model's *actual production output*
(re-scored point-in-time per historical season — see
docs/models/destination_projection_backtest_plan.md §5) against *actual
realized* per-game stats for real historical transfers. This is a second,
complementary validation layer on top of destination_projection.py's own
compute_cohort_validation()/run_rolling_origin_cv() — those validate only the
role_usage_delta submodel's regression target (value_delta); this validates the
full 4-delta pipeline's final box-score output. See the plan doc §1-2 for the
full "what this does NOT duplicate" rationale.

Pure functions only — no MLflow, no orchestration. scripts/run_destination_backtest.py
does the backfill-check/orchestration/logging; notebooks/models/destination_backtest.ipynb
does the exploratory cohort/cluster review (§7b — clustering is intentionally not
here, matching M1/M2's interactive-not-scripted precedent for exploratory work).
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from portalpoint.modeling.destination_projection import MODEL_VERSION, assign_competition_tiers
from portalpoint.modeling.player_projection import MIN_GAMES

# (destination_box_score key, player_season_stats actual column) — the 6 fields
# translate_rates_to_destination_stats() actually writes to
# player_projections.projected_box_score for destination-mode rows. Not the
# 11-skill Phase 0/2a taxonomy (shooting_3p, passing_creation, ...) — those are
# Kalman-layer skill percentiles, not stored per-game production, so there's
# no real per-game "actual" to compare them against without a separate
# translation step this plan doesn't need (the box score is already the
# translated, comparable-to-real-stats output).
BACKTEST_STATS: dict[str, tuple[str, str]] = {
    "pts": ("pts_per_game", "points_per_game"),
    "reb": ("reb_per_game", "rebounds_per_game"),
    "ast": ("ast_per_game", "assists_per_game"),
    "stl": ("stl_per_game", "steals_per_game"),
    "blk": ("blk_per_game", "blocks_per_game"),
    "tov": ("tov_per_game", "turnovers_per_game"),
}

_POPULATION_SQL = """
SELECT
    t.player_id,
    t.to_school_id      AS dest_school_id,
    t.from_school_id     AS source_school_id,
    t.season + 1         AS dest_season,
    t.season              AS source_season,
    t.pre_usage_rate      AS source_usage_rate,
    p.position
FROM transfers t
JOIN players p ON p.id = t.player_id
WHERE t.player_id IS NOT NULL
  AND t.to_school_id IS NOT NULL
  AND t.season + 1 BETWEEN :min_dest_season AND :max_dest_season
"""

_ACTUAL_OUTCOMES_SQL = """
SELECT
    player_id,
    school_id AS dest_school_id,
    season    AS dest_season,
    games_played,
    points_per_game,
    rebounds_per_game,
    assists_per_game,
    steals_per_game,
    blocks_per_game,
    turnovers_per_game
FROM player_season_stats
WHERE player_id = ANY(:player_ids)
  AND season = ANY(:seasons)
"""

_PROJECTED_OUTCOMES_SQL = """
SELECT
    player_id,
    school_id AS dest_school_id,
    season    AS dest_season,
    projected_box_score,
    explanation
FROM player_projections
WHERE projection_mode = 'destination'
  AND model_version = :model_version
  AND player_id = ANY(:player_ids)
  AND season = ANY(:seasons)
"""

_ARCHETYPE_SQL = """
SELECT player_id, season, archetype_label
FROM player_archetypes
WHERE player_id = ANY(:player_ids)
  AND season = ANY(:seasons)
"""

_TEAM_TIER_SQL = """
SELECT school_id, season, adj_em
FROM team_season_stats
WHERE season = ANY(:seasons)
"""


def load_backtest_population(
    engine: Engine, min_dest_season: int = 2023, max_dest_season: int = 2026
) -> pd.DataFrame:
    """Real historical transfers whose destination season has already completed.

    min_dest_season defaults to 2023, not 2022 — confirmed 2026-07-14 (real
    run_playing_time.py failure) that 2022 can't be point-in-time re-scored at
    all: barttorvik data starts at season 2021, so target_season=2022's Playing
    Time model would need >= 2 prior seasons to train on and only has one.
    Same structural limit destination_projection.run_rolling_origin_cv already
    encodes (never evaluates the first available season either). See
    destination_projection_backtest_plan.md §13.

    2027 (the live inference target) has no actual outcome yet and is excluded
    by construction (max_dest_season defaults to 2026). Games-played floor is
    applied later, in load_actual_outcomes, once real games_played is known.
    """
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_POPULATION_SQL),
            conn,
            params={"min_dest_season": min_dest_season, "max_dest_season": max_dest_season},
        )
    return df


def load_actual_outcomes(
    engine: Engine, population_df: pd.DataFrame, min_games: int = MIN_GAMES
) -> pd.DataFrame:
    """Real per-game stats at the destination school/season, games-played floor applied.

    Reuses Phase 0's MIN_GAMES convention (drop low-sample rows) rather than a
    separately-invented floor.
    """
    if population_df.empty:
        return pd.DataFrame()
    player_ids = population_df["player_id"].unique().tolist()
    seasons = population_df["dest_season"].unique().tolist()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_ACTUAL_OUTCOMES_SQL),
            conn,
            params={"player_ids": player_ids, "seasons": seasons},
        )
    if df.empty:
        return df
    merged = population_df[["player_id", "dest_school_id", "dest_season"]].merge(
        df, on=["player_id", "dest_school_id", "dest_season"], how="inner"
    )
    return merged[merged["games_played"] >= min_games].reset_index(drop=True)


def load_projected_outcomes(
    engine: Engine, population_df: pd.DataFrame, model_version: str = MODEL_VERSION
) -> pd.DataFrame:
    """Real production destination-mode projections for the same player/school/season keys.

    Flattens projected_box_score's 6 stats into flat columns (proj_pts, proj_reb, ...)
    for direct comparison in compute_residuals — no re-derivation, this is exactly
    what translate_rates_to_destination_stats() already wrote to the DB.
    """
    if population_df.empty:
        return pd.DataFrame()
    player_ids = population_df["player_id"].unique().tolist()
    seasons = population_df["dest_season"].unique().tolist()
    with engine.connect() as conn:
        df = pd.read_sql(
            text(_PROJECTED_OUTCOMES_SQL),
            conn,
            params={"player_ids": player_ids, "seasons": seasons, "model_version": model_version},
        )
    if df.empty:
        return df

    def _parse(x: Any) -> dict:
        if isinstance(x, str):
            try:
                return json.loads(x)
            except (json.JSONDecodeError, TypeError):
                return {}
        return x or {}

    boxes = df["projected_box_score"].map(_parse)
    for stat, (proj_key, _) in BACKTEST_STATS.items():
        df[f"proj_{stat}"] = boxes.apply(lambda b, k=proj_key: float(b.get(k, 0.0) or 0.0))

    merged = population_df[["player_id", "dest_school_id", "dest_season"]].merge(
        df, on=["player_id", "dest_school_id", "dest_season"], how="inner"
    )
    return merged.reset_index(drop=True)


def compute_residuals(actual_df: pd.DataFrame, projected_df: pd.DataFrame) -> pd.DataFrame:
    """One row per player-transfer with actual, projected, residual, and pct error per stat.

    residual = actual - projected (positive = model underprojected). Pure
    arithmetic over already-computed values — no re-fitting.
    """
    if actual_df.empty or projected_df.empty:
        return pd.DataFrame()

    merged = actual_df.merge(
        projected_df, on=["player_id", "dest_school_id", "dest_season"], how="inner"
    )
    if merged.empty:
        return merged

    for stat, (_, actual_col) in BACKTEST_STATS.items():
        actual = merged[actual_col]
        proj = merged[f"proj_{stat}"]
        merged[f"residual_{stat}"] = actual - proj
        # Avoid divide-by-zero for stats near 0 (e.g. a bench player's real blocks/game).
        safe_actual = actual.where(actual.abs() > 1e-6, np.nan)
        merged[f"pct_error_{stat}"] = ((actual - proj) / safe_actual * 100.0).round(2)

    return merged


def summarize_residuals(
    residual_df: pd.DataFrame, group_by: str | None = None, min_group_n: int = 10
) -> dict[str, Any]:
    """Mean/median/RMSE/MAE per stat, optionally grouped by an existing column.

    group_by can be any column already present on residual_df (position,
    tier_direction, archetype_label, usage_delta_bucket, ...) — reuses whichever
    cohort dimension the caller has already joined in, same slice definitions
    destination_projection.compute_cohort_validation uses where they overlap
    (tier direction, position group), just applied to box-score residuals
    across every stat instead of only value_delta.
    """
    if residual_df.empty:
        return {}

    def _stat_summary(df: pd.DataFrame) -> dict[str, float]:
        out: dict[str, float] = {"n": float(len(df))}
        for stat in BACKTEST_STATS:
            resid = df[f"residual_{stat}"].dropna()
            if resid.empty:
                continue
            out[f"{stat}_mean_residual"] = round(float(resid.mean()), 3)
            out[f"{stat}_median_residual"] = round(float(resid.median()), 3)
            out[f"{stat}_rmse"] = round(float(np.sqrt((resid ** 2).mean())), 3)
            out[f"{stat}_mae"] = round(float(resid.abs().mean()), 3)
        return out

    if group_by is None:
        return _stat_summary(residual_df)

    if group_by not in residual_df.columns:
        return {}

    results: dict[str, Any] = {}
    for group_val, group_df in residual_df.groupby(group_by):
        if len(group_df) < min_group_n:
            continue
        results[str(group_val)] = _stat_summary(group_df)
    return results


def enrich_with_cohorts(engine: Engine, population_df: pd.DataFrame) -> pd.DataFrame:
    """Join archetype_label (source season) and tier_direction onto the population.

    archetype_label is joined at source_season — "what kind of player was this
    heading into the portal", not the destination season (which hasn't happened
    at decision time). tier_direction reuses
    destination_projection.assign_competition_tiers on team_season_stats.adj_em,
    same derivation as the production pipeline's own tier assignment — not a
    second, forked definition of "tier".
    """
    if population_df.empty:
        return population_df
    df = population_df.copy()
    player_ids = df["player_id"].unique().tolist()
    source_seasons = df["source_season"].unique().tolist()
    dest_seasons = df["dest_season"].unique().tolist()

    with engine.connect() as conn:
        arch_df = pd.read_sql(
            text(_ARCHETYPE_SQL),
            conn,
            params={"player_ids": player_ids, "seasons": source_seasons},
        )
        tier_df = pd.read_sql(
            text(_TEAM_TIER_SQL),
            conn,
            params={"seasons": list(set(source_seasons) | set(dest_seasons))},
        )

    if not arch_df.empty:
        arch_df = arch_df.rename(columns={"season": "source_season"})
        df = df.merge(arch_df, on=["player_id", "source_season"], how="left")
    else:
        df["archetype_label"] = None

    if not tier_df.empty:
        tier_df = tier_df.copy()
        tier_df["tier"] = assign_competition_tiers(tier_df["adj_em"], tier_df["season"])
        source_tiers = tier_df.rename(columns={"school_id": "source_school_id", "season": "source_season"})[
            ["source_school_id", "source_season", "tier"]
        ].rename(columns={"tier": "source_tier"})
        dest_tiers = tier_df.rename(columns={"school_id": "dest_school_id", "season": "dest_season"})[
            ["dest_school_id", "dest_season", "tier"]
        ].rename(columns={"tier": "dest_tier"})
        df = df.merge(source_tiers, on=["source_school_id", "source_season"], how="left")
        df = df.merge(dest_tiers, on=["dest_school_id", "dest_season"], how="left")
        tier_delta = df["source_tier"].astype("Float64") - df["dest_tier"].astype("Float64")
        # np.select requires plain bool ndarrays — a nullable Float64 comparison yields a
        # pandas BooleanArray (with pd.NA for missing tiers), which np.select rejects outright.
        # NA rows correctly fall through to every condition being False -> default=None.
        df["tier_direction"] = np.select(
            [
                (tier_delta > 0).to_numpy(dtype=bool, na_value=False),
                (tier_delta < 0).to_numpy(dtype=bool, na_value=False),
                (tier_delta == 0).to_numpy(dtype=bool, na_value=False),
            ],
            ["up", "down", "same"],
            default=None,
        )
    else:
        df["source_tier"] = None
        df["dest_tier"] = None
        df["tier_direction"] = None

    return df
