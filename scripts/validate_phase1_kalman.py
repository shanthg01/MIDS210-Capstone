"""
scripts/validate_phase1_kalman.py

Phase 1 validation for the Player Projection model
(docs/models/player_projection_state_space_plan.md §15). Fits a per-skill
scalar Kalman filter/smoother on season-2026 game logs (the only season with
game-level data today) and checks:

  1. Did the pooled-MLE Q fit land somewhere sane (not at a bound)?
  2. Do Kalman end-of-season smoothed skill estimates correlate with Phase 0's
     independent season-aggregate shrinkage estimates for the same players?
     (Two very different estimators agreeing is the calibration signal called
     for in §15 — "validate convergence and calibration here before adding
     covariance structure.")
  3. Does a value model fit on Kalman skills perform comparably to Phase 0's
     value model, on the same season-2026-only HE-labeled subset (fair
     comparison — Phase 0's full resid_std numbers are pooled across 6
     seasons and are not directly comparable).

This is a diagnostic script, not a production rerun — it does not write to
player_projections. Phase 1 is explicitly a validation step before Phase 2
(block covariance, cross-season persistence once the 2020-2025 game-log
backfill lands), not a second parallel production model yet.

Usage:
  uv run python scripts/validate_phase1_kalman.py
"""
from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text

from portalpoint.modeling import player_projection as pp
from portalpoint.modeling import player_projection_kalman as ppk
from portalpoint.modeling.io import get_sync_engine

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEASON = 2026

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

SEASON_STATS_SQL = """
SELECT
    pss.player_id,
    pss.games_played, pss.min_pct,
    pss.fg3_pct, pss.rim_pct, pss.ft_pct, pss.usage_rate, pss.assist_rate,
    pss.tov_pct, pss.off_reb_pct, pss.def_reb_pct, pss.steal_pct, pss.block_pct,
    he.pos_class AS position, he.off_adj_rapm, he.def_adj_rapm
FROM player_season_stats pss
LEFT JOIN hoop_explorer_player_stats he
    ON he.player_id = pss.player_id AND he.season = pss.season
WHERE pss.season = :season AND pss.games_played >= :min_games
"""


def main() -> None:
    engine = get_sync_engine()

    with engine.connect() as conn:
        game_logs = pd.read_sql(text(GAME_LOG_SQL), conn, params={"season": SEASON})
    log.info("Loaded %s player-game rows for season %d", f"{len(game_logs):,}", SEASON)

    obs_df = ppk.build_game_observations(game_logs)
    fitted_q, kalman_df = ppk.smooth_all_skills(obs_df)
    kalman_df["season"] = SEASON

    log.info("Fitted Q per skill (pooled MLE):")
    for skill, q_value in fitted_q.items():
        at_lower = np.isclose(q_value, 1e-6, rtol=1e-3)
        at_upper = np.isclose(q_value, 2.0, rtol=1e-3)
        flag = " <-- AT BOUND, widen search range" if (at_lower or at_upper) else ""
        log.info("  %-24s Q=%.6f%s", skill, q_value, flag)

    with engine.connect() as conn:
        season_stats = pd.read_sql(
            text(SEASON_STATS_SQL), conn, params={"season": SEASON, "min_games": pp.MIN_GAMES},
        )
    season_stats = season_stats.drop_duplicates(subset=["player_id"], keep="first")

    # season_stats has no literal 'season' column (single-season query) — add
    # one so shrink_skills' groupby(season_col, position_col) still works.
    season_stats["season"] = SEASON
    phase0_df = pp.shrink_skills(season_stats)

    log.info("Calibration check — correlation between Kalman and Phase 0 skill estimates (season %d):", SEASON)
    # kalman_df and phase0_df both define skill_<skill> columns (by design —
    # see module docstring), so the merge suffixes exactly those; every other
    # column (position, off_adj_rapm, def_adj_rapm, ...) is unique to phase0_df.
    merged = kalman_df.merge(phase0_df, on=["player_id", "season"], how="inner", suffixes=("_kalman", "_phase0"))
    for skill in ppk.SKILLS:
        k_col, p_col = f"skill_{skill}_kalman", f"skill_{skill}_phase0"
        valid = merged[[k_col, p_col]].dropna()
        corr = valid[k_col].corr(valid[p_col]) if len(valid) > 10 else float("nan")
        log.info("  %-24s n=%-5d corr=%.3f", skill, len(valid), corr)

    log.info("Value model comparison, season %d only (fair vs. Phase 0's pooled 6-season fit):", SEASON)
    kalman_value_df = merged.copy()
    for skill in ppk.SKILLS:
        kalman_value_df[f"skill_{skill}"] = merged[f"skill_{skill}_kalman"]

    try:
        _, kalman_off_resid = pp.fit_value_model(kalman_value_df, "off_adj_rapm")
        _, kalman_def_resid = pp.fit_value_model(kalman_value_df, "def_adj_rapm")
        log.info("  Kalman skills  -> off_resid_std=%.3f def_resid_std=%.3f", kalman_off_resid, kalman_def_resid)
    except ValueError as exc:
        log.warning("  Kalman value model: %s", exc)

    phase0_only_df = phase0_df[phase0_df["player_id"].isin(merged["player_id"])]
    try:
        _, phase0_off_resid = pp.fit_value_model(phase0_only_df, "off_adj_rapm")
        _, phase0_def_resid = pp.fit_value_model(phase0_only_df, "def_adj_rapm")
        log.info("  Phase 0 skills -> off_resid_std=%.3f def_resid_std=%.3f", phase0_off_resid, phase0_def_resid)
    except ValueError as exc:
        log.warning("  Phase 0 value model: %s", exc)


if __name__ == "__main__":
    main()
