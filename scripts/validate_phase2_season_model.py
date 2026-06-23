"""
scripts/validate_phase2_season_model.py

Diagnostic for Player Projection Phase 2a (cross-season, block-aware
state-space). Runs Phase 1's intra-season filter for every season 2020-2026,
then fits the new season-grain (rho, dev-curve, transfer/level-change) layer
on top. Prints fitted params per skill and the within-block residual
correlation matrices. Does not write to any table — pure validation, same
spirit as the now-folded-into-the-notebook Phase 1 diagnostic.

Usage:
  uv run python scripts/validate_phase2_season_model.py
"""
from __future__ import annotations

import logging
import sys

import pandas as pd

from portalpoint.modeling import player_projection_phase2 as pp2
from portalpoint.modeling.io import get_sync_engine

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEASONS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]


def main() -> None:
    engine = get_sync_engine()

    log.info("Building (or loading cached) per-season intra-season filtered states for %s", SEASONS)
    fitted_q_by_season, season_states = pp2.load_or_build_season_skill_states(engine, SEASONS)
    log.info("Season-states frame: %s rows", f"{len(season_states):,}")

    covariates = pp2.load_or_build_season_covariates(engine, season_states)
    log.info(
        "Covariates: %s player-seasons, %d with transfer_flag=1",
        f"{len(covariates):,}", int(covariates["transfer_flag"].sum()),
    )
    log.info(
        "career_season_index distribution:\n%s",
        covariates["career_season_index"].value_counts().sort_index().to_string(),
    )

    fitted_params, residual_df = pp2.fit_all_skills(season_states, covariates)

    log.info("=== Fitted season-grain params per skill ===")
    params_df = pd.DataFrame(fitted_params).T
    log.info("\n%s", params_df.round(4).to_string())

    log.info("=== Within-block residual correlations ===")
    block_corrs = pp2.compute_block_correlations(residual_df)
    for block_name, corr in block_corrs.items():
        log.info("--- %s ---\n%s", block_name, corr.round(3).to_string())


if __name__ == "__main__":
    main()
