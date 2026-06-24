"""
scripts/run_player_projection.py

Non-interactive Phase 0 rerun of the Player Projection model
(player-projection-shrinkage-v1). Season-level only — no game-log
state-space machinery yet (Phase 1/2, see
docs/models/player_projection_state_space_plan.md §15).

Writes neutral-mode rows (school_id NULL) to player_projections for every
player-season with enough games played. Safe to re-run: upserts via the
partial unique index on (player_id, season, model_version) WHERE
school_id IS NULL.

Usage:
  uv run python scripts/run_player_projection.py
"""
from __future__ import annotations

import logging
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text

from portalpoint.modeling import player_projection as pp
from portalpoint.modeling.io import find_repo_root, get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PLAYER_SQL = """
SELECT
    pss.player_id,
    pss.season,
    he.pos_class AS position,
    pss.games_played,
    pss.min_pct,
    pss.fg3_pct,
    pss.rim_pct,
    pss.ft_pct,
    pss.usage_rate,
    pss.assist_rate,
    pss.tov_pct,
    pss.off_reb_pct,
    pss.def_reb_pct,
    pss.steal_pct,
    pss.block_pct,
    he.off_adj_rapm,
    he.def_adj_rapm,
    he.off_adj_rapm_prod,
    he.adj_rapm_prod_margin
FROM player_season_stats pss
LEFT JOIN hoop_explorer_player_stats he
    ON he.player_id = pss.player_id AND he.season = pss.season
WHERE pss.games_played >= :min_games
"""


def main() -> None:
    engine = get_sync_engine()

    with engine.connect() as conn:
        df = pd.read_sql(text(PLAYER_SQL), conn, params={"min_games": pp.MIN_GAMES})
    # he LEFT JOIN can in principle match more than one HE row per
    # (player_id, season) if a future data issue duplicates he_player_code
    # mappings — guard against silently duplicating pss rows.
    df = df.drop_duplicates(subset=["player_id", "season"], keep="first").reset_index(drop=True)
    log.info("Loaded %s player-seasons (games_played >= %d)", f"{len(df):,}", pp.MIN_GAMES)

    df = pp.shrink_skills(df)
    df = pp.skill_percentiles(df)

    off_model, off_resid_std = pp.fit_value_model(df, "off_adj_rapm")
    def_model, def_resid_std = pp.fit_value_model(df, "def_adj_rapm")
    n_labeled = int(df[["off_adj_rapm", "def_adj_rapm"]].dropna().shape[0])
    log.info(
        "Fit value models on %s HE-labeled rows: off_resid_std=%.3f def_resid_std=%.3f",
        f"{n_labeled:,}", off_resid_std, def_resid_std,
    )

    df = pp.project_value(df, off_model, def_model, off_resid_std, def_resid_std)

    # Secondary-label robustness check (plan doc §5/§8): off_adj_rapm_prod /
    # adj_rapm_prod_margin mix per-possession impact with playing-time share,
    # so they're not retraining targets — but our offense-only projection
    # should still track them directionally. Logged, not asserted on; a low
    # correlation here is a real signal worth investigating, not a hard failure.
    prod_check = df[["off_value_per_100", "off_adj_rapm_prod"]].dropna()
    if len(prod_check) > 30:
        prod_corr = prod_check["off_value_per_100"].corr(prod_check["off_adj_rapm_prod"])
        log.info(
            "Robustness check: off_value_per_100 vs off_adj_rapm_prod corr=%.3f (n=%s)",
            prod_corr, f"{len(prod_check):,}",
        )
    margin_check = df[["value_per_100", "adj_rapm_prod_margin"]].dropna()
    if len(margin_check) > 30:
        margin_corr = margin_check["value_per_100"].corr(margin_check["adj_rapm_prod_margin"])
        log.info(
            "Robustness check: value_per_100 vs adj_rapm_prod_margin corr=%.3f (n=%s)",
            margin_corr, f"{len(margin_check):,}",
        )

    records = pp.build_neutral_records(df)
    upserted = pp.upsert_neutral_projections(engine, records)
    log.info("Upserted %s rows into player_projections (neutral mode)", f"{upserted:,}")

    if not records:
        raise RuntimeError("No player projection records were scored")

    values = np.fromiter((r[4] for r in records), dtype=np.float64, count=len(records))
    mean_value = float(values.mean())
    std_value = float(values.std())

    client = setup_mlflow("player-projection")
    import mlflow
    import mlflow.pyfunc

    class PlayerProjectionPyfunc(mlflow.pyfunc.PythonModel):
        """Wraps the two Ridge value models for the MLflow registry."""

        def __init__(self, off_model, def_model):
            self.off_model = off_model
            self.def_model = def_model

        def predict(self, context, model_input):
            X = pp.build_design_matrix(model_input)
            off = self.off_model.predict(X)
            defn = self.def_model.predict(X)
            return [{"value_per_100": float(o + d)} for o, d in zip(off, defn)]

    with mlflow.start_run(run_name=f"player-projection-s{int(df['season'].max())}-script") as run:
        mlflow.log_params({
            "seasons": ",".join(str(s) for s in sorted(df["season"].unique().tolist())),
            "model_version": pp.MODEL_VERSION,
            "min_games": pp.MIN_GAMES,
            "shrinkage_k": pp.SHRINKAGE_K,
            "ridge_alpha": pp.RIDGE_ALPHA,
            "source": "script",
        })
        mlflow.log_metrics({
            "mean_value_per_100": mean_value,
            "std_value_per_100": std_value,
            "off_resid_std": off_resid_std,
            "def_resid_std": def_resid_std,
            "total_resid_std": float(np.sqrt(off_resid_std**2 + def_resid_std**2)),
            "n_records_written": float(len(records)),
            "n_he_labeled_rows": float(n_labeled),
        })
        mlflow.pyfunc.log_model(
            artifact_path="player_projection_model",
            python_model=PlayerProjectionPyfunc(off_model, def_model),
        )
        run_id = run.info.run_id

    total_resid_std = float(np.sqrt(off_resid_std**2 + def_resid_std**2))
    result = maybe_promote(
        client, "player-projection", run_id, "player_projection_model",
        metric_name="total_resid_std", new_value=total_resid_std, higher_is_better=False,
    )
    log.info("MLflow run %s — %s", run_id, result)

    # Save/upload only after scoring, DB write, and MLflow logging complete, so
    # the shared S3 artifacts cannot get ahead of the production table state.
    root = find_repo_root()
    models_dir = root / "data" / "models"
    paths = pp.save_artifacts(models_dir, off_model, def_model, off_resid_std, def_resid_std)
    for name, path in paths.items():
        log.info("Saved %s: %s", name, path)
    try:
        sys.path.insert(0, str(root / "notebooks" / "utils"))
        from s3_helpers import upload

        for name, path in paths.items():
            upload(path, f"models/player_projection/{path.name}")
    except Exception as exc:
        log.warning("S3 upload skipped: %s", exc)


if __name__ == "__main__":
    main()
