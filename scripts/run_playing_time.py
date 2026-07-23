"""
scripts/run_playing_time.py

Train and run the Playing Time / Rotation model, then sync Role Fit into
player_team_fit_scores.

Usage:
  uv run python scripts/run_playing_time.py --dry-run \
    --include-player-ids 101 --include-school-ids 9900301
  uv run python scripts/run_playing_time.py --target-season 2027 --source-season 2026
  uv run python scripts/run_playing_time.py --target-seasons 2024 2025 2026
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from portalpoint.modeling import playing_time as pt
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.mlflow_helpers import maybe_promote, setup_mlflow

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Playing Time / Role Fit model")
    parser.add_argument("--target-season", type=int, default=None, help="Score one season only")
    parser.add_argument(
        "--target-seasons",
        type=int,
        nargs="+",
        default=None,
        help="Score an explicit season list",
    )
    parser.add_argument("--train-seasons", type=int, nargs="+", default=None)
    parser.add_argument(
        "--source-season",
        type=int,
        default=None,
        help=(
            "Completed stat season used for candidate source profiles. "
            "Defaults to target season - 1."
        ),
    )
    parser.add_argument(
        "--roster-season",
        type=int,
        default=None,
        help=(
            "Roster snapshot/baseline season used as destination context. "
            "Defaults to source season."
        ),
    )
    parser.add_argument(
        "--fit-context-season",
        type=int,
        default=None,
        help=(
            "player_team_fit_scores season used for gap/scheme/program context. "
            "Defaults to roster season."
        ),
    )
    parser.add_argument(
        "--team-context-season",
        type=int,
        default=None,
        help=(
            "team_season_stats season used for destination quality/pace context. "
            "Defaults to source season."
        ),
    )
    parser.add_argument(
        "--model-family",
        choices=sorted(pt.MODEL_FAMILIES),
        default=pt.DEFAULT_MODEL_FAMILY,
        help="Model family for production scoring and validation artifact generation",
    )
    parser.add_argument(
        "--benchmark-model-families",
        choices=sorted(pt.MODEL_FAMILIES),
        nargs="+",
        default=[],
        help="Optional extra model families to validate side-by-side before production scoring",
    )
    parser.add_argument(
        "--school-chunk-size",
        type=int,
        default=75,
        help=(
            "Schools per INFERENCE_SQL query. Default 75, restored (2026-07-22) after real "
            "timed fetches (not just EXPLAIN cost units) showed the query itself is fast "
            "(bare filter+count over the full population: 0.48s server-side) — the real "
            "bottleneck is data transfer over the SSM tunnel, measured at ~0.76 MB/s "
            "regardless of chunk size (~2.7-2.9h total either way for the full population's "
            "~7GB of wide rows). Chunking does not reduce total transfer time, but caps how "
            "much progress a single tunnel drop destroys — the tunnel has died mid-run twice "
            "in one session (credential expiry; a separate idle-timeout-adjacent drop), so "
            "bounded-chunk resilience matters more here than shaving the total wall clock. "
            "A prior version of this default (365, single query) was based on abstract EXPLAIN "
            "cost estimates alone and did not hold up under real timing — see "
            "modeling/playing_time.py's RAW_CONNECTION_STATEMENT_TIMEOUT_MS comment."
        ),
    )
    parser.add_argument("--include-player-ids", type=int, nargs="+", default=[])
    parser.add_argument("--include-school-ids", type=int, nargs="+", default=[])
    parser.add_argument(
        "--validation-artifact-dir",
        type=Path,
        default=Path("data/model_validation/playing_time"),
        help="Directory for held-out validation CSV/JSON artifacts",
    )
    parser.add_argument("--skip-validation-artifact", action="store_true")
    parser.add_argument(
        "--skip-explanations",
        action="store_true",
        help="Skip per-row TreeSHAP generation for performance diagnostics",
    )
    parser.add_argument(
        "--explanation-scope",
        choices=("portal", "all"),
        default="portal",
        help=(
            "Rows receiving persisted TreeSHAP payloads. Default portal avoids multi-GB "
            "JSONB growth from the full player-school grid; all is intended for audits."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score and validate without DB writes or MLflow logging",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        default=False,
        help=(
            "Historical/population-restricted run (e.g. a scoped backtest season): writes real "
            "rows, but skips rolling_origin_validation entirely and never calls log_mlflow (no "
            "MLflow run, no model registration, no maybe_promote). Real finding (2026-07-14): a "
            "restricted population's rolling-origin CV fold is a much smaller, noisier sample "
            "than a real production run and previously fell through log_mlflow's gate-metric "
            "fallback bug into a false promotion (Delta=+98.6 pct, manually reverted; see "
            "resolve_promotion_gate_metric). --backfill makes the whole registry interaction "
            "structurally impossible instead of relying on catching it after the fact."
        ),
    )
    return parser.parse_args()


def fit_score_seasons(engine) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT season FROM player_team_fit_scores ORDER BY season")
        ).fetchall()
    seasons = [int(r[0]) for r in rows]
    if not seasons:
        raise RuntimeError("player_team_fit_scores is empty; run scheme fit and gap matching first")
    return seasons


def seasons_with_stats(engine) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT season FROM player_season_stats ORDER BY season")
        ).fetchall()
    return [int(r[0]) for r in rows]


def resolve_target_seasons(args: argparse.Namespace, engine) -> list[int]:
    if args.target_seasons and args.target_season is not None:
        raise RuntimeError("Use either --target-season or --target-seasons, not both")
    if args.target_seasons:
        return sorted(set(map(int, args.target_seasons)))
    if args.target_season is not None:
        return [int(args.target_season)]
    seasons = fit_score_seasons(engine)
    return [max(seasons) + 1]


def resolve_context_seasons(
    args: argparse.Namespace, target_season: int
) -> tuple[int, int, int, int]:
    source_season = int(args.source_season if args.source_season is not None else target_season - 1)
    roster_season = int(args.roster_season if args.roster_season is not None else source_season)
    fit_context_season = int(
        args.fit_context_season if args.fit_context_season is not None else roster_season
    )
    team_context_season = int(
        args.team_context_season if args.team_context_season is not None else source_season
    )
    return source_season, roster_season, fit_context_season, team_context_season


def fit_score_school_ids(engine, season: int, include_school_ids: list[int]) -> list[int]:
    if include_school_ids:
        return sorted(set(map(int, include_school_ids)))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT school_id FROM player_team_fit_scores "
                "WHERE season = :season ORDER BY school_id"
            ),
            {"season": season},
        ).fetchall()
    return [int(r[0]) for r in rows]


def apply_player_filter(df: pd.DataFrame, include_player_ids: list[int]) -> pd.DataFrame:
    if not include_player_ids or df.empty:
        return df
    return df[df["player_id"].isin(set(map(int, include_player_ids)))].reset_index(drop=True)


def explanation_positions(scored: pd.DataFrame, scope: str) -> np.ndarray:
    """Return row positions eligible for persisted explanations."""
    if scope == "all":
        return np.arange(len(scored), dtype=np.int64)
    if scope != "portal":
        raise ValueError(f"Unknown explanation scope: {scope}")
    if "is_portal_candidate" not in scored:
        raise RuntimeError("Portal explanation scope requires is_portal_candidate")
    mask = (
        pd.to_numeric(scored["is_portal_candidate"], errors="coerce")
        .fillna(0.0)
        .ge(0.5)
        .to_numpy()
    )
    return np.flatnonzero(mask)


def score_frame(
    models: pt.PlayingTimeModels, frame: pd.DataFrame, *, compress_usage: bool = True
) -> pd.DataFrame:
    scored = pt.predict_minutes_usage(models, frame)
    if scored.empty:
        return scored
    if compress_usage:
        # Compress usage to a per-school budget *before* role/role_fit so both reflect the
        # same expected_usage that ends up written to playing_time_projections, not the raw
        # value. Only correct for genuine candidate pools (build_inference_pairs' all-pairs
        # frame, where many candidates compete for the same roster slot) — NOT for
        # validation/training rows, which are real, already-disjoint roster members whose
        # usage already sums close to budget; compressing those double-penalizes accuracy
        # (confirmed: usage_mae roughly tripled in a real validation run before this guard).
        scored = pt.compress_usage_to_roster_budget(scored)
    usage_roles, usage_role_confidences = pt.derive_usage_roles(scored)
    scored["usage_role"] = usage_roles
    scored["usage_role_confidence"] = usage_role_confidences
    scored["role_fit"] = pt.compute_role_fit_scores(scored)
    return scored


VALIDATION_ARTIFACT_COLUMNS = [
    "player_id",
    "school_id",
    "season",
    "position",
    "prior_school_id",
    "prior_games_played",
    "prior_min_pct",
    "prior_usage_rate",
    "prior_college_seasons",
    "is_no_prior_college_season",
    "career_season_index",
    "years_since_first_observed",
    "expected_minutes",
    "actual_minutes",
    "minutes_error",
    "abs_minutes_error",
    "minutes_ci_lower",
    "minutes_ci_upper",
    "interval_width",
    "covered",
    "expected_minutes_share",
    "actual_minutes_share",
    "expected_usage",
    "actual_usage_rate",
    "usage_error",
    "abs_usage_error",
    "usage_role",
    "usage_role_confidence",
    "role_fit",
    "rotation_probability_model",
    "starter_probability_model",
    "heavy_minutes_probability",
    "high_usage_probability",
    "gap_match",
    "scheme_fit",
    "roster_player_count",
    "roster_open_minutes",
    "returning_minutes_position",
    "same_position_prior_minutes",
    "same_position_prior_max_minutes",
    "position_crowding_ratio",
    "opportunity_to_prior_minutes_ratio",
    "open_minutes_minus_returning_position",
    "prior_team_rotation_hhi",
    "prior_team_rotation_players",
    "returning_production",
    "sample_reliability",
    "projection_reliability",
    "roster_reliability",
    "position_confidence",
    "is_transfer",
    "candidate_changes_school",
    "is_portal_candidate",
    "transfer_match_confidence",
    "source_stat_season",
    "roster_context_season",
    "fit_context_season",
    "team_context_season",
    "sample_cohort",
    "position_group",
    "transfer_cohort",
]


def resolve_holdout_seasons(train_seasons: list[int], n_folds: int = 3) -> list[int]:
    """Rolling-origin holdout seasons: most recent seasons with >=2 prior train seasons each.

    Mirrors Player Projection's 3-fold rolling-origin convention (see Gap D/G in
    docs/models/player_projection_state_space_plan.md) instead of this model's original
    single-holdout (`season == max(train_seasons)` only), which is noisier with only one
    season's worth of validation signal.
    """
    ordered = sorted(set(int(s) for s in train_seasons))
    holdouts: list[int] = []
    for season in reversed(ordered):
        if len([s for s in ordered if s < season]) < 2:
            continue
        holdouts.append(season)
        if len(holdouts) >= n_folds:
            break
    return sorted(holdouts)


def validation_predictions(
    train_df: pd.DataFrame,
    train_seasons: list[int],
    *,
    model_family: str,
    holdout_season: int | None = None,
) -> tuple[dict[str, float], pd.DataFrame]:
    if len(train_seasons) < 3:
        return {}, pd.DataFrame()
    holdout = int(holdout_season) if holdout_season is not None else max(train_seasons)
    fit_df = train_df[train_df["season"] < holdout].reset_index(drop=True)
    val_df = train_df[train_df["season"] == holdout].reset_index(drop=True)
    if fit_df.empty or val_df.empty:
        return {}, pd.DataFrame()
    models = pt.fit_minutes_usage_models(fit_df, model_family=model_family)
    scored = score_frame(models, val_df, compress_usage=False)
    scored["covered"] = (scored["actual_minutes"] >= scored["minutes_ci_lower"]) & (
        scored["actual_minutes"] <= scored["minutes_ci_upper"]
    )
    scored["interval_width"] = scored["minutes_ci_upper"] - scored["minutes_ci_lower"]
    scored["minutes_error"] = scored["expected_minutes"] - scored["actual_minutes"]
    scored["abs_minutes_error"] = scored["minutes_error"].abs()
    scored["usage_error"] = scored["expected_usage"] - scored["actual_usage_rate"]
    scored["abs_usage_error"] = scored["usage_error"].abs()
    scored["sample_cohort"] = np.select(
        [
            scored["prior_games_played"].fillna(0) < 10,
            scored["prior_games_played"].between(10, 24, inclusive="both"),
            scored["prior_games_played"].fillna(0) >= 25,
        ],
        ["low_sample_gp_lt10", "mid_sample_gp_10_24", "high_sample_gp_ge25"],
        default="unknown",
    )
    scored["position_group"] = scored["position"].apply(pt.position_group)
    scored["transfer_cohort"] = np.where(
        scored["is_transfer"].fillna(0).astype(float) >= 0.5,
        "transfer",
        "returner_or_no_prior_school",
    )
    metrics = pt.validate_predictions(scored)
    metrics["holdout_validation_season"] = float(holdout)
    metrics["n_validation_train_rows"] = float(len(fit_df))
    metrics["model_family"] = model_family
    return metrics, scored


def validation_metrics(
    train_df: pd.DataFrame,
    train_seasons: list[int],
    *,
    model_family: str,
) -> dict[str, float]:
    metrics, _ = validation_predictions(train_df, train_seasons, model_family=model_family)
    return metrics


_NUMERIC_METRIC_KEYS_TO_AVERAGE = (
    "minutes_mae",
    "minutes_rmse",
    "minutes_bias",
    "usage_mae",
    "usage_rmse",
    "usage_bias",
    "interval_coverage",
    "interval_coverage_error_80pct",
    "avg_interval_width",
    "median_interval_width",
    "interval_width_to_mae_ratio",
    "minutes_distribution_tvd",
    "usage_distribution_tvd",
    "n_validation_rows",
)


def rolling_origin_validation(
    train_df: pd.DataFrame,
    train_seasons: list[int],
    *,
    model_family: str,
    n_folds: int = 3,
) -> tuple[dict[str, float], list[tuple[int, dict[str, float], pd.DataFrame]]]:
    """Multi-fold rolling-origin validation, replacing the original single-holdout fold.

    Returns mean-across-folds metrics under the *same* keys `validation_predictions` always
    used (so the MLflow promotion gate's `minutes_rmse` metric name in `log_mlflow` stays
    correct — renaming or dropping that key was exactly the bug class that caused a false
    auto-promotion for Player Projection's Phase 2a gate; see
    docs/models/player_projection_state_space_plan.md §22), plus per-fold metrics under
    `{metric}_fold_{season}` suffixes for detail. The per-fold `(holdout, metrics, scored)`
    list is also returned so callers (e.g. `write_validation_artifacts`) can still produce
    one artifact per fold, same as before this model had multiple folds.
    """
    holdout_seasons = resolve_holdout_seasons(train_seasons, n_folds=n_folds)
    if not holdout_seasons:
        return {}, []

    fold_results: list[tuple[int, dict[str, float], pd.DataFrame]] = []
    for holdout in holdout_seasons:
        metrics, scored = validation_predictions(
            train_df,
            train_seasons,
            model_family=model_family,
            holdout_season=holdout,
        )
        if not metrics:
            continue
        fold_results.append((holdout, metrics, scored))

    if not fold_results:
        return {}, []

    aggregated: dict[str, float] = {}
    for key in _NUMERIC_METRIC_KEYS_TO_AVERAGE:
        values = [m[key] for _, m, _ in fold_results if key in m]
        if values:
            aggregated[key] = float(np.mean(values))
    for holdout, metrics, _ in fold_results:
        for key, value in metrics.items():
            if isinstance(value, int | float):
                aggregated[f"{key}_fold_{holdout}"] = float(value)
    aggregated["n_validation_folds"] = float(len(fold_results))
    aggregated["model_family"] = model_family
    return aggregated, fold_results


def _cohort_summary(scored: pd.DataFrame, cohort_col: str) -> list[dict]:
    if scored.empty or cohort_col not in scored.columns:
        return []
    grouped = (
        scored.groupby(cohort_col, observed=True)
        .agg(
            n=("player_id", "size"),
            minutes_mae=("abs_minutes_error", "mean"),
            minutes_bias=("minutes_error", "mean"),
            usage_mae=("abs_usage_error", "mean"),
            interval_coverage=("covered", "mean"),
            avg_interval_width=("interval_width", "mean"),
            actual_minutes_mean=("actual_minutes", "mean"),
            predicted_minutes_mean=("expected_minutes", "mean"),
        )
        .reset_index()
    )
    return [
        {
            cohort_col: row[cohort_col],
            "n": int(row["n"]),
            "minutes_mae": round(float(row["minutes_mae"]), 4),
            "minutes_bias": round(float(row["minutes_bias"]), 4),
            "usage_mae": round(float(row["usage_mae"]), 4),
            "interval_coverage": round(float(row["interval_coverage"]), 4),
            "avg_interval_width": round(float(row["avg_interval_width"]), 4),
            "actual_minutes_mean": round(float(row["actual_minutes_mean"]), 4),
            "predicted_minutes_mean": round(float(row["predicted_minutes_mean"]), 4),
        }
        for _, row in grouped.iterrows()
    ]


def _bucket_calibration_summary(
    scored: pd.DataFrame,
    bucket_col: str,
) -> list[dict]:
    if scored.empty or bucket_col not in scored.columns:
        return []
    grouped = (
        scored.groupby(bucket_col, observed=True)
        .agg(
            n=("player_id", "size"),
            actual_minutes_mean=("actual_minutes", "mean"),
            predicted_minutes_mean=("expected_minutes", "mean"),
            minutes_mae=("abs_minutes_error", "mean"),
            actual_usage_mean=("actual_usage_rate", "mean"),
            predicted_usage_mean=("expected_usage", "mean"),
            usage_mae=("abs_usage_error", "mean"),
            interval_coverage=("covered", "mean"),
            avg_interval_width=("interval_width", "mean"),
        )
        .reset_index()
    )
    rows = []
    for _, row in grouped.iterrows():
        rows.append(
            {
                bucket_col: str(row[bucket_col]),
                "n": int(row["n"]),
                "actual_minutes_mean": round(float(row["actual_minutes_mean"]), 4),
                "predicted_minutes_mean": round(float(row["predicted_minutes_mean"]), 4),
                "minutes_bias": round(
                    float(row["predicted_minutes_mean"] - row["actual_minutes_mean"]),
                    4,
                ),
                "minutes_mae": round(float(row["minutes_mae"]), 4),
                "actual_usage_mean": round(float(row["actual_usage_mean"]), 4),
                "predicted_usage_mean": round(float(row["predicted_usage_mean"]), 4),
                "usage_bias": round(
                    float(row["predicted_usage_mean"] - row["actual_usage_mean"]),
                    4,
                ),
                "usage_mae": round(float(row["usage_mae"]), 4),
                "interval_coverage": round(float(row["interval_coverage"]), 4),
                "avg_interval_width": round(float(row["avg_interval_width"]), 4),
            }
        )
    return rows


def write_validation_artifacts(
    scored: pd.DataFrame,
    metrics: dict[str, float],
    artifact_dir: Path,
    train_seasons: list[int],
    model_family: str,
) -> list[Path]:
    if scored.empty or not metrics:
        return []
    artifact_dir.mkdir(parents=True, exist_ok=True)
    holdout = int(metrics["holdout_validation_season"])
    base = f"playing_time_validation_s{holdout}_{pt.MODEL_VERSION}_{model_family}"
    csv_path = artifact_dir / f"{base}.csv"
    json_path = artifact_dir / f"{base}_summary.json"

    cols = [c for c in VALIDATION_ARTIFACT_COLUMNS if c in scored.columns]
    scored[cols].sort_values(
        ["abs_minutes_error", "player_id", "school_id"],
        ascending=[False, True, True],
    ).to_csv(csv_path, index=False)

    minutes_bins = [0, 8, 16, 24, 32, 40.01]
    minutes_labels = ["0-8", "8-16", "16-24", "24-32", "32-40"]
    usage_bins = [0, 14, 18, 22, 26, 100]
    usage_labels = ["0-14", "14-18", "18-22", "22-26", "26+"]
    actual_min_bucket = pd.cut(
        scored["actual_minutes"],
        minutes_bins,
        labels=minutes_labels,
        include_lowest=True,
        right=False,
    )
    pred_min_bucket = pd.cut(
        scored["expected_minutes"],
        minutes_bins,
        labels=minutes_labels,
        include_lowest=True,
        right=False,
    )
    actual_usage_bucket = pd.cut(
        scored["actual_usage_rate"],
        usage_bins,
        labels=usage_labels,
        include_lowest=True,
        right=False,
    )
    pred_usage_bucket = pd.cut(
        scored["expected_usage"],
        usage_bins,
        labels=usage_labels,
        include_lowest=True,
        right=False,
    )
    scored_for_summary = scored.copy()
    scored_for_summary["predicted_minutes_bucket"] = pred_min_bucket.astype(str)
    scored_for_summary["predicted_usage_bucket"] = pred_usage_bucket.astype(str)
    if len(scored_for_summary) >= 5:
        scored_for_summary["interval_width_bucket"] = pd.qcut(
            scored_for_summary["interval_width"].rank(method="first"),
            q=5,
            labels=["narrowest", "narrow", "middle", "wide", "widest"],
        ).astype(str)
    else:
        scored_for_summary["interval_width_bucket"] = "all"

    summary = {
        "model_version": pt.MODEL_VERSION,
        "model_family": model_family,
        "train_seasons": train_seasons,
        "holdout_validation_season": holdout,
        "metrics": {
            k: round(float(v), 6) for k, v in metrics.items() if isinstance(v, int | float)
        },
        "minutes_distribution": {
            "actual": {
                str(k): int(v) for k, v in actual_min_bucket.value_counts().sort_index().items()
            },
            "predicted": {
                str(k): int(v) for k, v in pred_min_bucket.value_counts().sort_index().items()
            },
        },
        "usage_distribution": {
            "actual": {
                str(k): int(v) for k, v in actual_usage_bucket.value_counts().sort_index().items()
            },
            "predicted": {
                str(k): int(v) for k, v in pred_usage_bucket.value_counts().sort_index().items()
            },
        },
        "cohorts": {
            "sample": _cohort_summary(scored, "sample_cohort"),
            "position": _cohort_summary(scored, "position_group"),
            "transfer": _cohort_summary(scored, "transfer_cohort"),
        },
        "calibration": {
            "predicted_minutes_bucket": _bucket_calibration_summary(
                scored_for_summary,
                "predicted_minutes_bucket",
            ),
            "predicted_usage_bucket": _bucket_calibration_summary(
                scored_for_summary,
                "predicted_usage_bucket",
            ),
            "interval_width_bucket": _bucket_calibration_summary(
                scored_for_summary,
                "interval_width_bucket",
            ),
        },
    }
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return [csv_path, json_path]


def write_shap_summary_artifacts(
    models: pt.PlayingTimeModels,
    scored_sample: pd.DataFrame,
    artifact_dir: Path,
) -> list[Path]:
    """Write bounded-sample SHAP beeswarm plots for MLflow."""
    if scored_sample.empty:
        return []
    import matplotlib.pyplot as plt
    import shap

    artifact_dir.mkdir(parents=True, exist_ok=True)
    inputs = pt.build_playing_time_inference_inputs(models, scored_sample)
    targets = (
        (
            "expected_minutes",
            models.minutes_model,
            inputs.x_minutes_augmented,
            inputs.minutes_feature_names,
            pt.MAX_PLAYER_MINUTES,
        ),
        (
            "expected_usage",
            models.usage_model,
            inputs.x_usage_augmented,
            inputs.usage_feature_names,
            1.0,
        ),
    )
    paths: list[Path] = []
    for target, model, matrix, feature_names, scale in targets:
        values = shap.TreeExplainer(model)(matrix, check_additivity=True)
        plt.figure(figsize=(10, 7))
        shap.summary_plot(
            np.asarray(values.values) * scale,
            matrix,
            feature_names=list(feature_names),
            max_display=15,
            show=False,
        )
        plt.title(f"Playing Time — {target} SHAP summary")
        plt.tight_layout()
        path = artifact_dir / f"playing_time_{target}_shap_summary.png"
        plt.savefig(path, dpi=160, bbox_inches="tight")
        plt.close()
        paths.append(path)
    return paths


def log_mlflow(
    models: pt.PlayingTimeModels,
    metrics: dict[str, float],
    target_seasons: list[int],
    train_seasons: list[int],
    scored_rows: int,
    written_rows: int,
    model_family: str,
    artifact_paths: list[Path] | None = None,
) -> None:
    client = setup_mlflow("playing-time")
    import mlflow
    import mlflow.pyfunc

    class PlayingTimePyfunc(mlflow.pyfunc.PythonModel):
        def __init__(self, feature_medians):
            self.feature_medians = feature_medians

        def predict(self, context, model_input):
            return model_input

    season_label = (
        f"{min(target_seasons)}-{max(target_seasons)}"
        if len(target_seasons) > 1
        else str(target_seasons[0])
    )
    with mlflow.start_run(run_name=f"playing-time-s{season_label}-script") as run:
        mlflow.log_params(
            {
                "model_version": pt.MODEL_VERSION,
                "target_seasons": ",".join(str(s) for s in target_seasons),
                "train_seasons": ",".join(str(s) for s in train_seasons),
                "model_family": model_family,
                "minutes_feature_count": len(models.minutes_features),
                "usage_feature_count": len(models.usage_features),
                "source": "script",
            }
        )
        numeric_metrics = {k: float(v) for k, v in metrics.items() if isinstance(v, int | float)}
        mlflow.log_metrics(
            {
                **models.train_metrics,
                **numeric_metrics,
                "n_records_scored": float(scored_rows),
                "n_records_written": float(written_rows),
            }
        )
        mlflow.pyfunc.log_model(
            artifact_path="playing_time_model",
            python_model=PlayingTimePyfunc(models.feature_medians),
        )
        for path in artifact_paths or []:
            artifact_group = "explainability" if "shap" in path.name else "validation"
            mlflow.log_artifact(str(path), artifact_path=artifact_group)
        run_id = run.info.run_id

    gate_metric = pt.resolve_promotion_gate_metric(numeric_metrics)
    if gate_metric is None:
        log.warning(
            "No real held-out minutes_rmse in this run's metrics (target_seasons=%s did not "
            "produce rolling-origin CV folds — likely a single-season or population-restricted "
            "run) — skipping maybe_promote rather than comparing an incompatible fallback metric.",
            target_seasons,
        )
    else:
        result = maybe_promote(
            client,
            "playing-time-rotation",
            run_id,
            "playing_time_model",
            metric_name="minutes_rmse",
            new_value=float(gate_metric),
            higher_is_better=False,
        )
        log.info("MLflow run %s — %s", run_id, result)


def main() -> None:
    args = parse_args()
    engine = get_sync_engine()
    target_seasons = resolve_target_seasons(args, engine)
    max_source_season = max(resolve_context_seasons(args, season)[0] for season in target_seasons)
    available_seasons = seasons_with_stats(engine)
    train_seasons = args.train_seasons or [s for s in available_seasons if s <= max_source_season]
    if len(train_seasons) < 2:
        raise RuntimeError(f"Need at least two train seasons, got {train_seasons}")
    log.info("Target seasons: %s", target_seasons)
    log.info(
        "Season context: %s",
        {
            season: {
                "source_season": resolve_context_seasons(args, season)[0],
                "roster_season": resolve_context_seasons(args, season)[1],
                "fit_context_season": resolve_context_seasons(args, season)[2],
                "team_context_season": resolve_context_seasons(args, season)[3],
            }
            for season in target_seasons
        },
    )

    train_df = pt.build_training_examples(engine, train_seasons)
    log.info("Training examples: %s rows across seasons %s", f"{len(train_df):,}", train_seasons)
    fallback_rate = pt.neutral_projection_fallback_rate(train_df)
    log_fn = log.warning if fallback_rate > 0.01 else log.info
    log_fn(
        "Same-season neutral-projection fallback rate: %.4f (fraction of rows whose joined "
        "player_projections row was not %s — those rows had value_per_100/skill_percentiles/"
        "uncertainty nulled by null_leaking_neutral_projection_features() to prevent same-season "
        "production from leaking into minutes/usage labels; a high rate means most rows are "
        "training on roster/fit/team context alone until player-proj-phase2a-fcast-v1 is "
        "backfilled for these seasons)",
        fallback_rate,
        pt.PLAYER_PROJECTION_MODEL_VERSION,
    )
    benchmark_families = list(dict.fromkeys([*args.benchmark_model_families, args.model_family]))
    benchmark_results: dict[str, dict[str, float]] = {}
    val_metrics: dict[str, float] = {}
    validation_artifacts: list[Path] = []
    if args.backfill:
        log.info("--backfill set — skipping rolling_origin_validation entirely")
        benchmark_families = []
    for model_family in benchmark_families:
        family_metrics, fold_results = rolling_origin_validation(
            train_df,
            train_seasons,
            model_family=model_family,
        )
        if not family_metrics:
            continue
        benchmark_results[model_family] = family_metrics
        log.info(
            "Rolling-origin validation metrics [%s, %d fold(s)]: %s",
            model_family,
            int(family_metrics.get("n_validation_folds", 0)),
            {
                k: round(v, 4)
                for k, v in family_metrics.items()
                if isinstance(v, int | float) and "_fold_" not in k
            },
        )
        if not args.skip_validation_artifact:
            for holdout, fold_metrics, fold_scored in fold_results:
                family_artifacts = write_validation_artifacts(
                    fold_scored,
                    fold_metrics,
                    args.validation_artifact_dir,
                    train_seasons,
                    model_family,
                )
                validation_artifacts.extend(family_artifacts)
                if family_artifacts:
                    log.info(
                        "Wrote validation artifacts [%s, fold=%s]: %s",
                        model_family,
                        holdout,
                        ", ".join(str(path) for path in family_artifacts),
                    )
        if model_family == args.model_family:
            val_metrics = family_metrics
    if benchmark_results:
        leader = min(
            benchmark_results.items(),
            key=lambda item: item[1].get("minutes_rmse", float("inf")),
        )
        log.info(
            "Validation benchmark leader by minutes_rmse: %s (%0.4f)",
            leader[0],
            leader[1].get("minutes_rmse", float("nan")),
        )

    models = pt.fit_minutes_usage_models(train_df, model_family=args.model_family)
    log.info("Training metrics: %s", {k: round(v, 4) for k, v in models.train_metrics.items()})

    total_scored = 0
    total_written = 0
    role_fit_values: list[float] = []
    interval_widths: list[float] = []
    explanation_sample_parts: list[pd.DataFrame] = []
    explanation_sample_size = 0
    max_explanation_sample_size = 500

    for target_season in target_seasons:
        source_season, roster_season, fit_context_season, team_context_season = (
            resolve_context_seasons(
                args,
                target_season,
            )
        )
        school_ids = fit_score_school_ids(engine, fit_context_season, args.include_school_ids)
        if not school_ids:
            log.warning(
                "No fit-score school IDs found for context season %s; skipping target season %s",
                fit_context_season,
                target_season,
            )
            continue

        materialize_start = time.monotonic()
        pt.materialize_inference_staging(
            engine,
            target_season=target_season,
            source_season=source_season,
            roster_season=roster_season,
        )
        log.info(
            "season=%s materialized INFERENCE_SQL staging tables in %.1fs",
            target_season,
            time.monotonic() - materialize_start,
        )

        for start in range(0, len(school_ids), args.school_chunk_size):
            batch = school_ids[start : start + args.school_chunk_size]
            chunk_label = (start + 1, min(start + len(batch), len(school_ids)), len(school_ids))
            read_start = time.monotonic()
            frame = pt.build_inference_pairs(
                engine,
                target_season,
                batch,
                source_season=source_season,
                roster_season=roster_season,
                fit_context_season=fit_context_season,
                team_context_season=team_context_season,
            )
            log.info(
                "season=%s schools %d-%d/%d build_inference_pairs (read) returned %s rows in %.1fs",
                target_season, *chunk_label, f"{len(frame):,}", time.monotonic() - read_start,
            )
            frame = apply_player_filter(frame, args.include_player_ids)
            if frame.empty:
                continue
            score_start = time.monotonic()
            scored = score_frame(models, frame)
            log.info(
                "season=%s schools %d-%d/%d score_frame (model inference) in %.1fs",
                target_season, *chunk_label, time.monotonic() - score_start,
            )
            if not args.skip_explanations:
                explain_start = time.monotonic()
                positions = explanation_positions(scored, args.explanation_scope)
                explanations: list[dict | None] = [None] * len(scored)
                if len(positions):
                    explained_subset = pt.attach_playing_time_explanations(
                        models,
                        scored.iloc[positions].copy(),
                    )
                    for position, payload in zip(
                        positions,
                        explained_subset["explanation"],
                    ):
                        explanations[int(position)] = payload
                    scored["explanation"] = explanations
                else:
                    scored["explanation"] = explanations
                log.info(
                    "season=%s schools %d-%d/%d generated TreeSHAP explanations "
                    "for %s/%s rows (scope=%s) in %.1fs",
                    target_season,
                    start + 1,
                    min(start + len(batch), len(school_ids)),
                    len(school_ids),
                    f"{len(positions):,}",
                    f"{len(scored):,}",
                    args.explanation_scope,
                    time.monotonic() - explain_start,
                )
                remaining = max_explanation_sample_size - explanation_sample_size
                if remaining > 0 and len(positions):
                    sample_part = scored.iloc[positions[:remaining]].copy()
                    explanation_sample_parts.append(sample_part)
                    explanation_sample_size += len(sample_part)
            build_records_start = time.monotonic()
            projection_records, sync_records = pt.build_playing_time_records(scored)
            log.info(
                "season=%s schools %d-%d/%d build_playing_time_records in %.1fs",
                target_season, *chunk_label, time.monotonic() - build_records_start,
            )
            total_scored += len(scored)
            role_fit_values.extend(scored["role_fit"].astype(float).tolist())
            interval_widths.extend(
                (
                    scored["minutes_ci_upper"].astype(float)
                    - scored["minutes_ci_lower"].astype(float)
                ).tolist()
            )
            if args.dry_run:
                log.info(
                    "[dry-run] season=%s schools %d-%d/%d scored=%s",
                    target_season,
                    start + 1,
                    min(start + len(batch), len(school_ids)),
                    len(school_ids),
                    f"{len(scored):,}",
                )
                continue
            upsert_start = time.monotonic()
            written = pt.upsert_playing_time_projections(engine, projection_records)
            log.info(
                "season=%s schools %d-%d/%d upsert_playing_time_projections wrote %s rows in %.1fs",
                target_season, *chunk_label, f"{written:,}", time.monotonic() - upsert_start,
            )
            sync_start = time.monotonic()
            synced = pt.sync_role_fit_scores(engine, sync_records)
            log.info(
                "season=%s schools %d-%d/%d sync_role_fit_scores synced %s rows in %.1fs",
                target_season, *chunk_label, f"{synced:,}", time.monotonic() - sync_start,
            )
            total_written += written
            log.info(
                "season=%s schools %d-%d/%d scored=%s playing_time_written=%s fit_scores_synced=%s",
                target_season,
                start + 1,
                min(start + len(batch), len(school_ids)),
                len(school_ids),
                f"{len(scored):,}",
                f"{written:,}",
                f"{synced:,}",
            )

    if total_scored == 0:
        raise RuntimeError("No playing-time records were scored")

    if explanation_sample_parts and not args.dry_run and not args.backfill:
        explanation_artifacts = write_shap_summary_artifacts(
            models,
            pd.concat(explanation_sample_parts, ignore_index=True),
            args.validation_artifact_dir / "explainability",
        )
        validation_artifacts.extend(explanation_artifacts)
        log.info(
            "Wrote explainability artifacts from %s sampled rows: %s",
            f"{explanation_sample_size:,}",
            ", ".join(str(path) for path in explanation_artifacts),
        )

    run_metrics = {
        "mean_role_fit": float(np.mean(role_fit_values)),
        "std_role_fit": float(np.std(role_fit_values)),
        "avg_interval_width_scored": float(np.mean(interval_widths)),
    }
    log.info(
        "Scored %s rows. Summary: %s",
        f"{total_scored:,}",
        {k: round(v, 4) for k, v in run_metrics.items()},
    )

    if args.backfill:
        log.info("--backfill set — skipping log_mlflow entirely (no run, no registration, no promote)")
    elif not args.dry_run:
        log_mlflow(
            models,
            {**val_metrics, **run_metrics},
            target_seasons,
            train_seasons,
            total_scored,
            total_written,
            args.model_family,
            validation_artifacts,
        )
    else:
        log.info("[dry-run] skipped DB writes and MLflow logging")


if __name__ == "__main__":
    main()
