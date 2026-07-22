"""
Transfer Success evaluation pipeline (Model 5).

Empirical Bayes success probability over a 3-level hierarchy:
  global → archetype → archetype×offense → full cell (archetype×offense×defense).

Trained on an expanding window of prior seasons with time-decay weighting.
No separate ML model — success is defined as meeting or exceeding the
destination-adjusted projection baseline (value_per_100).

Cell key uses numeric cluster IDs, not the string system_label, to avoid
silent merge collisions when two (offense, defense) pairs share a label.
"""
from __future__ import annotations

import gc
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
from psycopg2.extras import execute_batch

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_VERSION: str  = "transfer-success-eb-v2"
PROJECTION_MODEL_VERSION: str = "player-destination-proj-v1"

SHRINKAGE_K: int   = 15   # pseudo-observations from cluster prior
CELL_MIN_N: int    = 5    # effective n below which shrinkage is meaningfully active
DECAY_LAMBDA: float = 0.9  # recency weight per season-of-age; <=1.0
MAX_COMPS: int     = 3    # max named historical comps per output row
INFERENCE_CHUNK_SIZE: int = 10_000  # active rows per inference scoring batch
UPSERT_CHUNK_SIZE: int = 500        # rows per DB upsert batch over RDS

# Hyperparameter grids for tune_transfer_success_hyperparameters().
# Dense at low K — median cell_n on eval rows is typically low single digits.
K_CELL_CANDIDATES: list[int] = [1, 2, 3, 5, 8, 12, 20, 30]
LAMBDA_CANDIDATES: list[float] = [0.85, 0.9, 0.95, 1.0]

# Numeric groupby key for cell-level rates. Avoids silent merges when two
# different (offense_cluster_id, defense_cluster_id) pairs produce identical
# system_label strings.
_CELL_KEY = ["player_cluster", "team_offense_cluster_id", "team_defense_cluster_id"]
_OFFENSE_PAIR_KEY = ["player_cluster", "team_offense_cluster_id"]

# Minimum labeled OOS rows required before fitting the projection covariate.
_MIN_BETA_FIT_ROWS: int = 20
_LOGIT_EPS: float = 1e-6

# Columns produced by compute_success_probability / post-processing — must be
# dropped before re-scoring in score_active_candidates to avoid merge collisions.
# Label/drift columns (success_label, minutes_drift, …) are kept for comps.
_RATE_OUTPUT_COLUMNS: frozenset[str] = frozenset({
    "cluster_raw_rate", "cluster_n", "cluster_shrinkage_w", "cluster_success_rate",
    "offense_pair_success_rate", "offense_pair_n", "offense_pair_shrinkage_w",
    "offense_pair_shrunk_rate", "cell_success_rate", "cell_n", "shrinkage_w",
    "has_prior_history", "prediction_level", "p_base", "projection_z",
    "beta_projection", "projection_adjustment", "success_probability", "success_tier",
    "similar_transfers", "explanation",
})

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# {model_version} must be filled before execution; passed by load_transfer_data().
TRANSFER_EVAL_SQL = """\
SELECT
    t.id                              AS transfer_id,
    t.player_id,
    p.full_name                       AS player_name,
    t.from_school_id,
    t.to_school_id,
    t.season,
    t.transfer_type,
    -- Pre-transfer snapshot
    t.pre_per,
    t.pre_minutes_per_game,
    t.pre_usage_rate,
    -- Post-transfer actuals.
    -- transfers.post_* are schema placeholders no ingestion script populates;
    -- confirmed empirically (all NULL). post_minutes_per_game and post_usage_rate
    -- are derived from player_season_stats at the destination season instead.
    -- t.post_per kept as-is (always NULL — success label uses he.adj_rapm_margin).
    t.post_per,
    (dest_pss.min_pct / 100.0) * 40.0 AS post_minutes_per_game,
    dest_pss.usage_rate               AS post_usage_rate,
    t.per_change,
    t.minutes_change,
    -- Destination-adjusted projection for the season actually played
    pp.value_per_100                  AS projected_value_per_100,
    pp.projected_minutes,
    pp.projected_usage,
    pp.value_ci_lower,
    pp.value_ci_upper,
    -- Player archetype as of the departure season (freshest pre-transfer read)
    pa.archetype_id                   AS player_cluster,
    pa.archetype_label                AS player_cluster_label,
    -- Destination team system from the departure season (t.season, NOT t.season+1).
    -- t.season+1 requires box-score data that doesn't exist yet at decision time.
    tsp.system_label                  AS team_cluster_label,
    tsp.offense_cluster_id            AS team_offense_cluster_id,
    tsp.defense_cluster_id            AS team_defense_cluster_id,
    -- Post-transfer RAPM outcome: adj_rapm_margin = off_adj_rapm - def_adj_rapm,
    -- the plain (non-production-weighted) margin — same RAPM lineage value_per_100
    -- is trained against. NOT adj_rapm_prod_margin (a playing-time-weighted variant).
    he.adj_rapm_margin                AS actual_value_per_100
FROM transfers t
JOIN players p
    ON  p.id = t.player_id
LEFT JOIN player_projections pp
    ON  pp.player_id       = t.player_id
    AND pp.school_id       = t.to_school_id
    AND pp.season          = (t.season + 1)
    AND pp.projection_mode = 'destination'
    AND pp.model_version   = '{model_version}'
LEFT JOIN player_archetypes pa
    ON  pa.player_id = t.player_id
    AND pa.season    = t.season
LEFT JOIN team_system_profiles tsp
    ON  tsp.school_id = t.to_school_id
    AND tsp.season    = t.season
LEFT JOIN hoop_explorer_player_stats he
    ON  he.player_id = t.player_id
    AND he.season    = (t.season + 1)
LEFT JOIN player_season_stats dest_pss
    ON  dest_pss.player_id = t.player_id
    AND dest_pss.school_id = t.to_school_id
    AND dest_pss.season    = (t.season + 1)
-- "Completed transfer" = real destination-season HoopExplorer record exists.
-- transfers.post_* is never populated, so he.player_id is the completion signal.
WHERE he.player_id IS NOT NULL
ORDER BY t.season, t.player_id
"""


def load_transfer_data(engine, model_version: str) -> pd.DataFrame:
    """Execute TRANSFER_EVAL_SQL and return the raw frame."""
    sql = TRANSFER_EVAL_SQL.format(model_version=model_version)
    conn = engine.raw_connection()
    try:
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


# Active portal candidates × all D1 schools with cluster context for forward scoring.
# target_season is the play year (transfers.season + 1). Portal year = target_season - 1.
# pa.season = target_season - 1: archetype from the departure season (same convention
# as TRANSFER_EVAL_SQL's pa.season = t.season where t.season IS the portal/departure year).
# tsp.season = target_season - 1: most recent completed team profile (live system can't
# have target_season's profile — same intentional choice as TRANSFER_EVAL_SQL's tsp.season = t.season).
ACTIVE_CANDIDATES_SQL = """\
SELECT DISTINCT
    ptf.player_id,
    p.full_name        AS player_name,
    ptf.school_id      AS to_school_id,
    :target_season     AS season,
    pa.archetype_id    AS player_cluster,
    pa.archetype_label AS player_cluster_label,
    tsp.system_label   AS team_cluster_label,
    tsp.offense_cluster_id AS team_offense_cluster_id,
    tsp.defense_cluster_id AS team_defense_cluster_id,
    pp.value_per_100   AS projected_value_per_100
FROM player_team_fit_scores ptf
JOIN players p ON p.id = ptf.player_id
LEFT JOIN player_archetypes pa
    ON  pa.player_id = ptf.player_id
    AND pa.season    = :target_season - 1
LEFT JOIN team_system_profiles tsp
    ON  tsp.school_id = ptf.school_id
    AND tsp.season    = :target_season - 1
LEFT JOIN player_projections pp
    ON  pp.player_id = ptf.player_id
    AND pp.school_id = ptf.school_id
    AND pp.season    = :target_season
    AND pp.projection_mode = 'destination'
    AND pp.model_version   = 'player-destination-proj-v1'
WHERE ptf.is_portal_candidate = true
  AND ptf.season = :target_season
"""

ACTIVE_CANDIDATES_CHUNK_SQL = """\
SELECT DISTINCT
    ptf.player_id,
    p.full_name        AS player_name,
    ptf.school_id      AS to_school_id,
    :target_season     AS season,
    pa.archetype_id    AS player_cluster,
    pa.archetype_label AS player_cluster_label,
    tsp.system_label   AS team_cluster_label,
    tsp.offense_cluster_id AS team_offense_cluster_id,
    tsp.defense_cluster_id AS team_defense_cluster_id,
    pp.value_per_100   AS projected_value_per_100
FROM player_team_fit_scores ptf
JOIN players p ON p.id = ptf.player_id
LEFT JOIN player_archetypes pa
    ON  pa.player_id = ptf.player_id
    AND pa.season    = :target_season - 1
LEFT JOIN team_system_profiles tsp
    ON  tsp.school_id = ptf.school_id
    AND tsp.season    = :target_season - 1
LEFT JOIN player_projections pp
    ON  pp.player_id = ptf.player_id
    AND pp.school_id = ptf.school_id
    AND pp.season    = :target_season
    AND pp.projection_mode = 'destination'
    AND pp.model_version   = 'player-destination-proj-v1'
WHERE ptf.is_portal_candidate = true
  AND ptf.season = :target_season
  AND (ptf.player_id, ptf.school_id) > (:last_player_id, :last_school_id)
ORDER BY ptf.player_id, ptf.school_id
LIMIT :chunk_size
"""

ACTIVE_CANDIDATES_COUNT_SQL = """\
SELECT COUNT(*) AS n
FROM player_team_fit_scores ptf
WHERE ptf.is_portal_candidate = true
  AND ptf.season = :target_season
"""


def load_active_candidates(engine, target_season: int) -> pd.DataFrame:
    """Load active portal candidates × all D1 schools for forward scoring."""
    from sqlalchemy import text
    with engine.connect() as conn:
        return pd.read_sql(
            text(ACTIVE_CANDIDATES_SQL),
            conn,
            params={"target_season": target_season},
        )


def count_active_candidates(engine, target_season: int) -> int:
    """Count portal-candidate × school rows for a target season."""
    from sqlalchemy import text
    with engine.connect() as conn:
        return int(conn.execute(
            text(ACTIVE_CANDIDATES_COUNT_SQL),
            {"target_season": target_season},
        ).scalar() or 0)


def iter_active_candidate_chunks(
    engine,
    target_season: int,
    chunk_size: int = INFERENCE_CHUNK_SIZE,
) -> Iterator[pd.DataFrame]:
    """Stream active candidate rows from RDS in keyset-ordered chunks."""
    from sqlalchemy import text

    last_player_id = 0
    last_school_id = 0
    while True:
        with engine.connect() as conn:
            chunk = pd.read_sql(
                text(ACTIVE_CANDIDATES_CHUNK_SQL),
                conn,
                params={
                    "target_season": target_season,
                    "last_player_id": last_player_id,
                    "last_school_id": last_school_id,
                    "chunk_size": chunk_size,
                },
            )
        if chunk.empty:
            break
        yield chunk
        last = chunk.iloc[-1]
        last_player_id = int(last["player_id"])
        last_school_id = int(last["to_school_id"])
        if len(chunk) < chunk_size:
            break


# ---------------------------------------------------------------------------
# Pipeline functions
# ---------------------------------------------------------------------------

def label_transfer_success(df: pd.DataFrame) -> pd.DataFrame:
    """Assign success_label to each completed transfer.

    Primary:  actual_value_per_100 >= projected_value_per_100.
    Fallback: post_per >= pre_per when RAPM or projection data is missing.
    Unlabeled rows (neither possible) get success=NaN, success_label=pd.NA.

    Uses nullable 'boolean' dtype so .astype(bool) raises on NA rows instead
    of silently coercing them to True (verified pandas behaviour with object dtype).
    """
    df = df.copy()

    has_rapm = df["actual_value_per_100"].notna() & df["projected_value_per_100"].notna()
    has_per  = df["post_per"].notna() & df["pre_per"].notna()

    df["success"] = np.nan

    df.loc[has_rapm, "success"] = (
        df.loc[has_rapm, "actual_value_per_100"] >= df.loc[has_rapm, "projected_value_per_100"]
    ).astype(float)

    fallback = ~has_rapm & has_per
    df.loc[fallback, "success"] = (
        df.loc[fallback, "post_per"] >= df.loc[fallback, "pre_per"]
    ).astype(float)

    df["success_label"] = df["success"].map({1.0: True, 0.0: False}).astype("boolean")
    df["value_vs_projection"] = df["actual_value_per_100"] - df["projected_value_per_100"]
    df["label_source"] = np.where(
        has_rapm, "rapm_vs_projection",
        np.where(fallback, "per_improvement", "missing"),
    )
    return df


def compute_drift(df: pd.DataFrame) -> pd.DataFrame:
    """Volume and efficiency drift: actual minus projected."""
    df = df.copy()
    df["minutes_drift"] = df["post_minutes_per_game"] - df["projected_minutes"]
    df["usage_drift"]   = df["post_usage_rate"]        - df["projected_usage"]
    return df


def _safe_logit(p: np.ndarray | pd.Series | float) -> np.ndarray:
    """Logit with clipping so 0/1 inputs do not explode."""
    arr = np.clip(np.asarray(p, dtype=float), _LOGIT_EPS, 1.0 - _LOGIT_EPS)
    return np.log(arr / (1.0 - arr))


def _sigmoid(x: np.ndarray | pd.Series | float) -> np.ndarray:
    """Sigmoid stable for large-magnitude logits."""
    arr = np.asarray(x, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(arr, -500.0, 500.0)))


def standardize_projection_by_season(
    df: pd.DataFrame,
    proj_col: str = "projected_value_per_100",
) -> pd.Series:
    """Within-season z-score of destination projections (relative difficulty)."""
    if proj_col not in df.columns:
        return pd.Series(0.0, index=df.index)

    def _zscore(group: pd.Series) -> pd.Series:
        if group.notna().sum() < 2:
            return pd.Series(0.0, index=group.index)
        mean = group.mean()
        std = group.std(ddof=0)
        if std == 0 or pd.isna(std):
            return pd.Series(0.0, index=group.index)
        return (group - mean) / std

    return df.groupby("season", group_keys=False)[proj_col].transform(_zscore).fillna(0.0)


def fit_projection_beta(
    train_df: pd.DataFrame,
    *,
    p_base_col: str = "p_base",
    z_col: str = "projection_z",
    success_col: str = "success",
    min_rows: int = _MIN_BETA_FIT_ROWS,
    ridge_lambda: float = 0.25,
) -> float:
    """Fit β in logit(p_base) + β·z via penalized MLE on prior-season OOS rows.

    Returns 0.0 when there are too few labeled rows or no projection spread.
    """
    from scipy.optimize import minimize_scalar

    mask = (
        train_df[success_col].notna()
        & train_df[p_base_col].notna()
        & train_df[z_col].notna()
    )
    if "has_prior_history" in train_df.columns:
        mask &= train_df["has_prior_history"].fillna(False)
    if "projected_value_per_100" in train_df.columns:
        mask &= train_df["projected_value_per_100"].notna()

    if mask.sum() < min_rows:
        return 0.0

    y = train_df.loc[mask, success_col].astype(float).to_numpy()
    offset = _safe_logit(train_df.loc[mask, p_base_col].to_numpy())
    z = train_df.loc[mask, z_col].to_numpy()
    if np.std(z) < 1e-9:
        return 0.0

    def objective(beta: float) -> float:
        probs = _sigmoid(offset + beta * z)
        probs = np.clip(probs, _LOGIT_EPS, 1.0 - _LOGIT_EPS)
        nll = float(-np.sum(y * np.log(probs) + (1.0 - y) * np.log(1.0 - probs)))
        return nll + ridge_lambda * (beta ** 2)

    result = minimize_scalar(objective, bounds=(-5.0, 0.0), method="bounded")
    beta = float(result.x)
    if abs(beta) < 0.01:
        return 0.0
    return beta


def apply_projection_covariate_adjustment(
    df: pd.DataFrame,
    beta: float,
    *,
    p_base_col: str = "p_base",
    z_col: str = "projection_z",
) -> pd.DataFrame:
    """Apply log-odds offset: logit(p_final) = logit(p_base) + β·z."""
    out = df.copy()
    out["beta_projection"] = beta
    if abs(beta) < 1e-9:
        out["success_probability"] = out[p_base_col]
        out["projection_adjustment"] = 0.0
        return out

    z = out[z_col].fillna(0.0).to_numpy(dtype=float)
    p_base = out[p_base_col].to_numpy(dtype=float)
    out["success_probability"] = _sigmoid(_safe_logit(p_base) + beta * z)
    out["projection_adjustment"] = out["success_probability"] - out[p_base_col]
    return out


def summarize_projection_beta(df_scored: pd.DataFrame) -> dict[str, float]:
    """Aggregate season-level β values for MLflow logging."""
    if "beta_projection" not in df_scored.columns:
        return {}
    betas = df_scored.drop_duplicates("season")["beta_projection"].astype(float)
    if len(betas) == 0:
        return {}
    return {
        "beta_projection_median": float(betas.median()),
        "beta_projection_mean": float(betas.mean()),
    }


TIER_ORDER: list[str] = ["Very Low", "Low", "Moderate", "High", "Very High"]


def _eval_mask(df_scored: pd.DataFrame) -> pd.Series:
    """Rows included in out-of-sample Brier / calibration metrics."""
    return df_scored["success"].notna() & df_scored["has_prior_history"]


def compute_expected_calibration_error(
    y_true: np.ndarray | pd.Series,
    y_prob: np.ndarray | pd.Series,
    n_bins: int = 5,
) -> float:
    """Weighted |accuracy − confidence| across quantile bins (notebook Section 7a)."""
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    if len(y_true) == 0:
        return float("nan")

    edges = np.unique(np.percentile(y_prob, np.linspace(0, 100, n_bins + 1)))
    if len(edges) < 2:
        return 0.0

    ece = 0.0
    n = len(y_true)
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if i == len(edges) - 2:
            in_bin = (y_prob >= lo) & (y_prob <= hi)
        else:
            in_bin = (y_prob >= lo) & (y_prob < hi)
        if not in_bin.any():
            continue
        avg_conf = float(y_prob[in_bin].mean())
        avg_acc = float(y_true[in_bin].mean())
        ece += abs(avg_acc - avg_conf) * in_bin.sum() / n
    return float(ece)


def compute_tier_calibration(df_scored: pd.DataFrame) -> pd.DataFrame:
    """Per-tier predicted vs actual success rates on eval rows."""
    eval_df = df_scored.loc[_eval_mask(df_scored)]
    if len(eval_df) == 0:
        return pd.DataFrame(
            columns=[
                "success_tier",
                "n",
                "mean_predicted_prob",
                "actual_success_rate",
                "calibration_gap",
            ],
        )

    tier_stats = (
        eval_df.groupby("success_tier", observed=False)
        .agg(
            n=("success", "count"),
            mean_predicted_prob=("success_probability", "mean"),
            actual_success_rate=("success", "mean"),
        )
        .reindex(TIER_ORDER)
        .reset_index()
    )
    tier_stats["calibration_gap"] = (
        tier_stats["mean_predicted_prob"] - tier_stats["actual_success_rate"]
    ).abs()
    return tier_stats


def summarize_calibration_metrics(df_scored: pd.DataFrame) -> dict[str, float]:
    """Calibration metrics for MLflow logging."""
    eval_df = df_scored.loc[_eval_mask(df_scored)]
    if len(eval_df) == 0:
        return {}

    y_true = eval_df["success"].astype(int)
    y_prob = eval_df["success_probability"].astype(float)
    tier_df = compute_tier_calibration(df_scored)

    metrics: dict[str, float] = {
        "ece": compute_expected_calibration_error(y_true, y_prob),
    }
    if len(tier_df) > 0 and tier_df["calibration_gap"].notna().any():
        metrics["tier_calibration_gap_max"] = float(tier_df["calibration_gap"].max())

    very_low = tier_df.loc[tier_df["success_tier"] == "Very Low", "calibration_gap"]
    if len(very_low) > 0 and pd.notna(very_low.iloc[0]):
        metrics["low_tier_calibration_gap"] = float(very_low.iloc[0])
    return metrics


def build_hyperparameters_payload(
    *,
    shrinkage_k: int,
    decay_lambda: float,
    beta_projection: float | None = None,
) -> dict[str, float | str]:
    """Serializable hyperparameter snapshot for MLflow artifacts."""
    payload: dict[str, float | str] = {
        "model_version": MODEL_VERSION,
        "shrinkage_k": shrinkage_k,
        "decay_lambda": decay_lambda,
        # v2 uses a single K at all hierarchy levels until per-level tuning lands.
        "k_cell": shrinkage_k,
        "k_offense_pair": shrinkage_k,
        "k_cluster": shrinkage_k,
    }
    if beta_projection is not None and not np.isnan(beta_projection):
        payload["beta_projection"] = float(beta_projection)
    return payload


def write_calibration_artifacts(
    df_scored: pd.DataFrame,
    output_dir: Path,
    *,
    shrinkage_k: int,
    decay_lambda: float,
    beta_projection: float | None = None,
    brier_score: float | None = None,
) -> dict[str, Path]:
    """Write calibration_curve.png, tier_calibration.csv, hyperparameters.json."""
    output_dir.mkdir(parents=True, exist_ok=True)

    tier_df = compute_tier_calibration(df_scored)
    tier_path = output_dir / "tier_calibration.csv"
    tier_df.to_csv(tier_path, index=False)

    hyper_path = output_dir / "hyperparameters.json"
    hyper_path.write_text(
        json.dumps(
            build_hyperparameters_payload(
                shrinkage_k=shrinkage_k,
                decay_lambda=decay_lambda,
                beta_projection=beta_projection,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    plot_path = output_dir / "calibration_curve.png"
    eval_df = df_scored.loc[_eval_mask(df_scored)]
    if len(eval_df) > 0:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.calibration import calibration_curve

        y_true = eval_df["success"].astype(int)
        y_prob = eval_df["success_probability"].astype(float)
        frac_pos, mean_pred_prob = calibration_curve(
            y_true, y_prob, n_bins=5, strategy="quantile",
        )

        fig, axes = plt.subplots(1, 2, figsize=(12, 4))

        axes[0].plot([0, 1], [0, 1], "k--", linewidth=1, label="Perfect calibration")
        axes[0].plot(mean_pred_prob, frac_pos, "o-", color="steelblue", label="Model")
        axes[0].set_xlabel("Mean predicted probability")
        axes[0].set_ylabel("Fraction of positives")
        title = "Calibration curve"
        if brier_score is not None and not np.isnan(brier_score):
            title += f" (Brier={brier_score:.4f})"
        axes[0].set_title(title)
        axes[0].legend(loc="lower right")
        axes[0].set_xlim(0, 1)
        axes[0].set_ylim(0, 1)

        valid_tiers = tier_df[tier_df["n"].fillna(0) > 0]
        if len(valid_tiers) > 0:
            global_rate = float(y_true.mean())
            bars = axes[1].bar(
                valid_tiers["success_tier"],
                valid_tiers["actual_success_rate"],
                color="steelblue",
                edgecolor="white",
                width=0.6,
            )
            axes[1].axhline(global_rate, color="tomato", linestyle="--", linewidth=1,
                            label=f"Global rate ({global_rate:.1%})")
            for bar, (_, row) in zip(bars, valid_tiers.iterrows()):
                axes[1].text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.01,
                    f"n={int(row['n'])}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )
            axes[1].set_ylabel("Actual success rate")
            axes[1].set_title("Success rate by confidence tier")
            axes[1].legend(loc="upper left")
            axes[1].set_ylim(0, 1)

        plt.tight_layout()
        fig.savefig(plot_path, dpi=160, bbox_inches="tight")
        plt.close(fig)

    return {
        "calibration_curve": plot_path,
        "tier_calibration": tier_path,
        "hyperparameters": hyper_path,
    }


def _infer_prediction_level(row: pd.Series, cell_min_n: int = CELL_MIN_N) -> str:
    """Which hierarchy level most influenced the final probability."""
    if pd.isna(row.get("player_cluster")):
        return "unknown_archetype"
    if not row.get("has_prior_history", True):
        return "bootstrap"
    w_cell = float(row.get("shrinkage_w") or 0)
    n_cell = float(row.get("cell_n") or 0)
    n_op = float(row.get("offense_pair_n") or 0)
    w_op = float(row.get("offense_pair_shrinkage_w") or 0)
    w_cluster = float(row.get("cluster_shrinkage_w") or 0)

    if n_cell >= cell_min_n and w_cell >= 0.25:
        return "cell"
    if n_op >= cell_min_n and w_op >= 0.25:
        return "offense_pair"
    if w_cluster >= 0.25:
        return "cluster"
    return "global"


@dataclass
class InferenceRateContext:
    """Precomputed EB rate tables for forward-scoring one target season."""

    target_season: int
    shrinkage_k: int
    decay_lambda: float
    global_rate: float
    unknown_archetype_rate: float
    cluster_global: pd.DataFrame
    offense_pair_stats: pd.DataFrame
    cell_stats: pd.DataFrame
    beta_projection: float
    train_for_comps: pd.DataFrame


def _build_rate_tables_for_season(
    train: pd.DataFrame,
    season: int,
    decay_lambda: float,
) -> tuple[float, float, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Decay-weighted hierarchy rate tables from labeled training rows."""
    train = train.copy()
    train["decay_weight"] = decay_lambda ** (season - train["season"])
    train["weighted_success"] = train["decay_weight"] * train["success"]

    global_rate = train["weighted_success"].sum() / train["decay_weight"].sum()
    unk = train[train["player_cluster"].isna()]
    unknown_archetype_rate = (
        unk["weighted_success"].sum() / unk["decay_weight"].sum()
        if len(unk) > 0 else global_rate
    )

    cluster_global = (
        train[train["player_cluster"].notna()]
        .groupby("player_cluster")
        .agg(
            cluster_raw_rate=("weighted_success", "sum"),
            cluster_n=("decay_weight", "sum"),
        )
        .reset_index()
    )
    cluster_global["cluster_raw_rate"] /= cluster_global["cluster_n"]

    offense_pair_stats = (
        train[
            train["player_cluster"].notna()
            & train["team_offense_cluster_id"].notna()
        ]
        .groupby(_OFFENSE_PAIR_KEY)
        .agg(
            offense_pair_success_rate=("weighted_success", "sum"),
            offense_pair_n=("decay_weight", "sum"),
        )
        .reset_index()
    )
    offense_pair_stats["offense_pair_success_rate"] /= offense_pair_stats["offense_pair_n"]

    cell_stats = (
        train[
            train["team_offense_cluster_id"].notna()
            & train["team_defense_cluster_id"].notna()
            & train["player_cluster"].notna()
        ]
        .groupby(_CELL_KEY)
        .agg(
            cell_success_rate=("weighted_success", "sum"),
            cell_n=("decay_weight", "sum"),
        )
        .reset_index()
    )
    cell_stats["cell_success_rate"] /= cell_stats["cell_n"]

    return (
        global_rate,
        unknown_archetype_rate,
        cluster_global,
        offense_pair_stats,
        cell_stats,
    )


def _apply_rate_tables_to_score(
    score: pd.DataFrame,
    ctx: InferenceRateContext,
) -> pd.DataFrame:
    """Apply precomputed EB tables to candidate rows (no training pass)."""
    shrinkage_k = ctx.shrinkage_k
    global_rate = ctx.global_rate
    unknown_archetype_rate = ctx.unknown_archetype_rate

    score = score.merge(
        ctx.cluster_global[["player_cluster", "cluster_raw_rate", "cluster_n"]],
        on="player_cluster", how="left",
    )
    score = score.merge(
        ctx.offense_pair_stats[_OFFENSE_PAIR_KEY + ["offense_pair_success_rate", "offense_pair_n"]],
        on=_OFFENSE_PAIR_KEY, how="left",
    )
    score = score.merge(
        ctx.cell_stats[_CELL_KEY + ["cell_success_rate", "cell_n"]],
        on=_CELL_KEY, how="left",
    )

    score["cluster_raw_rate"] = score["cluster_raw_rate"].fillna(
        score["player_cluster"].isna().map({True: unknown_archetype_rate, False: global_rate})
    )
    score["cluster_n"] = score["cluster_n"].fillna(0.0)

    score["cluster_shrinkage_w"] = score["cluster_n"] / (score["cluster_n"] + shrinkage_k)
    score["cluster_success_rate"] = (
        score["cluster_shrinkage_w"] * score["cluster_raw_rate"]
        + (1 - score["cluster_shrinkage_w"]) * global_rate
    )

    score["offense_pair_n"] = score["offense_pair_n"].fillna(0.0)
    score["offense_pair_success_rate"] = score["offense_pair_success_rate"].fillna(
        score["cluster_success_rate"]
    )
    score["offense_pair_shrinkage_w"] = (
        score["offense_pair_n"] / (score["offense_pair_n"] + shrinkage_k)
    )
    score["offense_pair_shrunk_rate"] = (
        score["offense_pair_shrinkage_w"] * score["offense_pair_success_rate"]
        + (1 - score["offense_pair_shrinkage_w"]) * score["cluster_success_rate"]
    )

    score["cell_n"] = score["cell_n"].fillna(0.0)
    score["cell_success_rate"] = score["cell_success_rate"].fillna(
        score["offense_pair_shrunk_rate"]
    )
    score["has_prior_history"] = True
    score["shrinkage_w"] = score["cell_n"] / (score["cell_n"] + shrinkage_k)
    score["p_base"] = (
        score["shrinkage_w"] * score["cell_success_rate"]
        + (1 - score["shrinkage_w"]) * score["offense_pair_shrunk_rate"]
    )
    score["projection_z"] = standardize_projection_by_season(score)
    score = apply_projection_covariate_adjustment(score, ctx.beta_projection)
    score["prediction_level"] = score.apply(_infer_prediction_level, axis=1)
    score["success_tier"] = pd.cut(
        score["success_probability"],
        bins=[0.0, 0.35, 0.50, 0.65, 0.80, 1.01],
        labels=["Very Low", "Low", "Moderate", "High", "Very High"],
        include_lowest=True,
    )
    return score


def build_inference_rate_context(
    df_historical: pd.DataFrame,
    target_season: int,
    shrinkage_k: int = SHRINKAGE_K,
    decay_lambda: float = DECAY_LAMBDA,
) -> InferenceRateContext:
    """Precompute EB rate tables and projection beta for inference at target_season."""
    hist = df_historical[df_historical["season"] < target_season]
    train = hist[hist["success"].notna()].copy()
    if len(train) == 0:
        raise ValueError(f"No labeled training rows before season {target_season}")

    (
        global_rate,
        unknown_archetype_rate,
        cluster_global,
        offense_pair_stats,
        cell_stats,
    ) = _build_rate_tables_for_season(train, target_season, decay_lambda)

    hist_for_beta = hist[[c for c in hist.columns if c not in _RATE_OUTPUT_COLUMNS]]
    hist_scored = compute_success_probability(
        hist_for_beta, shrinkage_k=shrinkage_k, decay_lambda=decay_lambda,
    )
    beta_rows = hist_scored[
        hist_scored["success_label"].notna() & hist_scored["has_prior_history"]
    ]
    beta = (
        fit_projection_beta(beta_rows)
        if len(beta_rows) >= _MIN_BETA_FIT_ROWS else 0.0
    )

    train_for_comps = train[
        train["player_cluster"].notna()
        & train["team_offense_cluster_id"].notna()
        & train["team_defense_cluster_id"].notna()
    ]

    log.info(
        "Inference rate context: season=%d, train_n=%d, beta=%.4f, global_rate=%.3f",
        target_season, len(train), beta, global_rate,
    )

    return InferenceRateContext(
        target_season=target_season,
        shrinkage_k=shrinkage_k,
        decay_lambda=decay_lambda,
        global_rate=global_rate,
        unknown_archetype_rate=unknown_archetype_rate,
        cluster_global=cluster_global,
        offense_pair_stats=offense_pair_stats,
        cell_stats=cell_stats,
        beta_projection=beta,
        train_for_comps=train_for_comps,
    )


def apply_inference_rates(
    score: pd.DataFrame,
    ctx: InferenceRateContext,
) -> pd.DataFrame:
    """Score candidate rows using a precomputed inference context."""
    score = score.copy(deep=False)
    score["season"] = ctx.target_season
    return _apply_rate_tables_to_score(score, ctx)


def score_active_candidate_chunk(
    chunk: pd.DataFrame,
    ctx: InferenceRateContext,
) -> pd.DataFrame:
    """Score one active-candidate chunk: rates, comps, explanation."""
    scored = apply_inference_rates(chunk, ctx)
    scored = _attach_similar_transfers_for_season(ctx.train_for_comps, scored)
    scored["explanation"] = scored.apply(build_explanation, axis=1)
    return scored


def compute_success_probability(
    df: pd.DataFrame,
    shrinkage_k: int = SHRINKAGE_K,
    decay_lambda: float = DECAY_LAMBDA,
    seasons_to_score: list[int] | None = None,
) -> pd.DataFrame:
    """Hierarchical shrinkage probability over (player_cluster × team_system) cells.

    Expanding window (no leakage): each season S is scored using only labeled rows
    from seasons < S. The earliest season falls back to an uninformative 0.5.

    Time-decay weighting: weight = decay_lambda ** (S - s) for a prior-season row
    at season s. Cell/cluster/global rates are decay-weighted averages; the 'n' in
    the shrinkage formula is the sum of decay weights (effective sample size).

    v2 hierarchy (each level shrinks toward its parent):
      1. Global rate
      2. Archetype rate shrunk toward global
      3. Archetype × offense-cluster rate shrunk toward archetype
      4. Full cell (archetype × offense × defense) shrunk toward archetype × offense

    Fallback when a level has no direct data uses the parent's shrunk rate.

    Args:
        seasons_to_score: If set, only score these seasons (train still uses full df).
            Used by inference to avoid re-scoring historical OOS rows.
    """
    df = df.copy()
    seasons = sorted(df["season"].dropna().unique())
    if seasons_to_score is not None:
        score_set = set(seasons_to_score)
        seasons = [s for s in seasons if s in score_set]
    scored_frames = []
    historical_oos: list[pd.DataFrame] = []

    for season in seasons:
        train = df[(df["season"] < season) & df["success"].notna()].copy()
        score = df.loc[df["season"] == season].copy()

        if len(train) == 0:
            score["cluster_raw_rate"]           = 0.5
            score["cluster_success_rate"]       = 0.5
            score["offense_pair_success_rate"]  = 0.5
            score["offense_pair_shrunk_rate"]   = 0.5
            score["cell_success_rate"]          = 0.5
            score["cell_n"]                     = 0.0
            score["cluster_n"]                  = 0.0
            score["offense_pair_n"]             = 0.0
            score["cluster_shrinkage_w"]        = 0.0
            score["offense_pair_shrinkage_w"]   = 0.0
            score["shrinkage_w"]                = 0.0
            score["has_prior_history"]          = False
            score["prediction_level"]           = "bootstrap"
            score["p_base"]                     = 0.5
            score["projection_z"]               = standardize_projection_by_season(score)
            score = apply_projection_covariate_adjustment(score, beta=0.0)
            historical_oos.append(score)
            scored_frames.append(score)
            continue

        train["decay_weight"]     = decay_lambda ** (season - train["season"])
        train["weighted_success"] = train["decay_weight"] * train["success"]

        global_rate = train["weighted_success"].sum() / train["decay_weight"].sum()

        # Players with no archetype are a distinct lower-success group (~30% vs ~54%
        # overall); using the general population rate breaks tier calibration.
        unk = train[train["player_cluster"].isna()]
        unknown_archetype_rate = (
            unk["weighted_success"].sum() / unk["decay_weight"].sum()
            if len(unk) > 0 else global_rate
        )

        # Level 2: archetype raw rate
        cluster_global = (
            train[train["player_cluster"].notna()]
            .groupby("player_cluster")
            .agg(
                cluster_raw_rate=("weighted_success", "sum"),
                cluster_n=("decay_weight", "sum"),
            )
            .reset_index()
        )
        cluster_global["cluster_raw_rate"] /= cluster_global["cluster_n"]

        # Level 3: archetype × offense-cluster raw rate
        offense_pair_stats = (
            train[
                train["player_cluster"].notna()
                & train["team_offense_cluster_id"].notna()
            ]
            .groupby(_OFFENSE_PAIR_KEY)
            .agg(
                offense_pair_success_rate=("weighted_success", "sum"),
                offense_pair_n=("decay_weight", "sum"),
            )
            .reset_index()
        )
        offense_pair_stats["offense_pair_success_rate"] /= offense_pair_stats["offense_pair_n"]

        # Level 4: full cell raw rate
        cell_stats = (
            train[
                train["team_offense_cluster_id"].notna()
                & train["team_defense_cluster_id"].notna()
                & train["player_cluster"].notna()
            ]
            .groupby(_CELL_KEY)
            .agg(
                cell_success_rate=("weighted_success", "sum"),
                cell_n=("decay_weight", "sum"),
            )
            .reset_index()
        )
        cell_stats["cell_success_rate"] /= cell_stats["cell_n"]

        score = score.merge(
            cluster_global[["player_cluster", "cluster_raw_rate", "cluster_n"]],
            on="player_cluster", how="left",
        )
        score = score.merge(
            offense_pair_stats[_OFFENSE_PAIR_KEY + ["offense_pair_success_rate", "offense_pair_n"]],
            on=_OFFENSE_PAIR_KEY, how="left",
        )
        score = score.merge(
            cell_stats[_CELL_KEY + ["cell_success_rate", "cell_n"]],
            on=_CELL_KEY, how="left",
        )

        # Archetype fallback: unknown archetype group or global
        score["cluster_raw_rate"] = score["cluster_raw_rate"].fillna(
            score["player_cluster"].isna().map({True: unknown_archetype_rate, False: global_rate})
        )
        score["cluster_n"] = score["cluster_n"].fillna(0.0)

        # Shrink archetype toward global
        score["cluster_shrinkage_w"] = score["cluster_n"] / (score["cluster_n"] + shrinkage_k)
        score["cluster_success_rate"] = (
            score["cluster_shrinkage_w"] * score["cluster_raw_rate"]
            + (1 - score["cluster_shrinkage_w"]) * global_rate
        )

        # Offense-pair fallback → shrunk archetype parent
        score["offense_pair_n"] = score["offense_pair_n"].fillna(0.0)
        score["offense_pair_success_rate"] = score["offense_pair_success_rate"].fillna(
            score["cluster_success_rate"]
        )
        score["offense_pair_shrinkage_w"] = (
            score["offense_pair_n"] / (score["offense_pair_n"] + shrinkage_k)
        )
        score["offense_pair_shrunk_rate"] = (
            score["offense_pair_shrinkage_w"] * score["offense_pair_success_rate"]
            + (1 - score["offense_pair_shrinkage_w"]) * score["cluster_success_rate"]
        )

        # Cell fallback → shrunk offense-pair parent
        score["cell_n"] = score["cell_n"].fillna(0.0)
        score["cell_success_rate"] = score["cell_success_rate"].fillna(
            score["offense_pair_shrunk_rate"]
        )
        score["has_prior_history"] = True

        score["shrinkage_w"] = score["cell_n"] / (score["cell_n"] + shrinkage_k)
        score["p_base"] = (
            score["shrinkage_w"] * score["cell_success_rate"]
            + (1 - score["shrinkage_w"]) * score["offense_pair_shrunk_rate"]
        )
        score["projection_z"] = standardize_projection_by_season(score)

        if historical_oos:
            hist = pd.concat(historical_oos, ignore_index=False)
            beta = fit_projection_beta(hist)
        else:
            beta = 0.0
        score = apply_projection_covariate_adjustment(score, beta)

        historical_oos.append(score)
        scored_frames.append(score)

    df = pd.concat(scored_frames).sort_index()

    if "prediction_level" not in df.columns:
        df["prediction_level"] = df.apply(_infer_prediction_level, axis=1)
    else:
        missing_level = df["prediction_level"].isna()
        df.loc[missing_level, "prediction_level"] = df.loc[missing_level].apply(
            _infer_prediction_level, axis=1,
        )
    df["success_tier"] = pd.cut(
        df["success_probability"],
        bins=[0.0, 0.35, 0.50, 0.65, 0.80, 1.01],
        labels=["Very Low", "Low", "Moderate", "High", "Very High"],
        include_lowest=True,
    )
    return df


def summarize_shrinkage_sample_sizes(
    df_scored: pd.DataFrame,
    shrinkage_k: int = SHRINKAGE_K,
    cell_min_n: int = CELL_MIN_N,
) -> dict[str, float]:
    """Percentile summary of decay-weighted cell_n and cluster_n on eval rows.

    Only rows with a labeled outcome and prior-season history are included —
    same mask as the out-of-sample Brier metric.
    """
    mask = df_scored["success"].notna() & df_scored["has_prior_history"]
    eval_df = df_scored.loc[mask]
    if len(eval_df) == 0:
        return {}

    out: dict[str, float] = {"n_eval_rows": float(len(eval_df))}
    for col in ("cell_n", "offense_pair_n", "cluster_n"):
        if col not in eval_df.columns:
            continue
        series = eval_df[col].astype(float)
        for pct in (10, 25, 50, 75, 90):
            out[f"{col}_p{pct}"] = float(series.quantile(pct / 100.0))
        out[f"{col}_max"] = float(series.max())

    cell_n = eval_df["cell_n"].astype(float)
    out["cell_n_median"] = float(cell_n.median())
    out["pct_cell_n_below_5"] = float((cell_n < cell_min_n).mean() * 100.0)
    if "shrinkage_w" in eval_df.columns:
        out["pct_shrinkage_w_below_0_25"] = float(
            (eval_df["shrinkage_w"] < 0.25).mean() * 100.0
        )
    else:
        shrinkage_w = cell_n / (cell_n + shrinkage_k)
        out["pct_shrinkage_w_below_0_25"] = float((shrinkage_w < 0.25).mean() * 100.0)
    return out


def tune_transfer_success_hyperparameters(
    df: pd.DataFrame,
    k_candidates: list[int] | None = None,
    lambda_candidates: list[float] | None = None,
) -> tuple[int, float, pd.DataFrame]:
    """Grid-search shrinkage_k and decay_lambda via expanding-window Brier.

    For each (K, λ) pair, runs ``compute_success_probability`` on the full
    labeled frame and scores only rows with ``success.notna()`` and
    ``has_prior_history``. Returns the pair with minimum mean Brier, or
    production defaults (SHRINKAGE_K, DECAY_LAMBDA) when the grid is empty.

    Args:
        df: Transfer frame with success labels (call ``label_transfer_success``
            first if needed).
        k_candidates: Cell-level K grid (default K_CELL_CANDIDATES).
        lambda_candidates: Time-decay grid (default LAMBDA_CANDIDATES).
    """
    from sklearn.metrics import brier_score_loss

    if k_candidates is None:
        k_candidates = K_CELL_CANDIDATES
    if lambda_candidates is None:
        lambda_candidates = LAMBDA_CANDIDATES

    if "success" not in df.columns:
        df = label_transfer_success(df)

    results: list[dict] = []
    for k in k_candidates:
        for decay_lambda in lambda_candidates:
            scored = compute_success_probability(
                df, shrinkage_k=k, decay_lambda=decay_lambda,
            )
            mask = scored["success"].notna() & scored["has_prior_history"]
            if mask.sum() == 0:
                continue
            brier = brier_score_loss(
                scored.loc[mask, "success"].astype(int),
                scored.loc[mask, "success_probability"],
            )
            results.append({
                "shrinkage_k": k,
                "decay_lambda": decay_lambda,
                "brier_score": brier,
                "n_eval": int(mask.sum()),
            })

    grid_df = pd.DataFrame(results)
    if grid_df.empty:
        return SHRINKAGE_K, DECAY_LAMBDA, grid_df

    best = grid_df.loc[grid_df["brier_score"].idxmin()]
    return int(best["shrinkage_k"]), float(best["decay_lambda"]), grid_df


def _attach_similar_transfers_for_season(
    train: pd.DataFrame,
    score: pd.DataFrame,
    max_comps: int = MAX_COMPS,
) -> pd.DataFrame:
    """Attach comps to score rows using labeled prior-season train rows."""
    comp_cols = [
        "player_name", "season", "value_vs_projection", "success_label",
        "minutes_drift", "usage_drift",
        "actual_value_per_100", "projected_value_per_100",
        "post_minutes_per_game", "projected_minutes",
        "post_usage_rate", "projected_usage",
    ]
    score = score.copy(deep=False)
    if len(train) == 0:
        score["similar_transfers"] = [[] for _ in range(len(score))]
        return score

    groups = {
        key: g[comp_cols].sort_values("season", ascending=False).head(max_comps).to_dict("records")
        for key, g in train.groupby(_CELL_KEY)
    }

    def _lookup(row, _groups=groups):
        if (pd.isna(row["player_cluster"])
                or pd.isna(row["team_offense_cluster_id"])
                or pd.isna(row["team_defense_cluster_id"])):
            return []
        return _groups.get(
            (row["player_cluster"], row["team_offense_cluster_id"], row["team_defense_cluster_id"]),
            [],
        )

    score["similar_transfers"] = score.apply(_lookup, axis=1)
    return score


def attach_similar_transfers(df: pd.DataFrame, max_comps: int = MAX_COMPS) -> pd.DataFrame:
    """Attach named historical comps per transfer, expanding window (no leakage).

    Comps are drawn from the same (player_cluster × team system) cell, using
    numeric cluster IDs as the lookup key to match compute_success_probability's
    cell definition exactly.
    """
    df = df.copy()
    seasons = sorted(df["season"].dropna().unique())
    scored_frames = []

    for season in seasons:
        train = df[
            (df["season"] < season) & df["success"].notna()
            & df["player_cluster"].notna()
            & df["team_offense_cluster_id"].notna()
            & df["team_defense_cluster_id"].notna()
        ].copy()
        score = df.loc[df["season"] == season].copy()
        scored_frames.append(_attach_similar_transfers_for_season(train, score, max_comps))

    return pd.concat(scored_frames).sort_index()


def _projection_adjustment_clause(row: pd.Series) -> str:
    """Plain-language note when projection difficulty moved the estimate."""
    adj = row.get("projection_adjustment")
    z = row.get("projection_z")
    if pd.isna(adj) or pd.isna(z) or abs(float(adj)) < 0.005:
        return ""
    direction = "down" if float(adj) < 0 else "up"
    return (
        f" Projection difficulty adjusted this estimate {direction} "
        f"({abs(float(adj)):.0%}) because this player's destination projection is "
        f"{'above' if float(z) > 0 else 'below'} the season average."
    )


def build_explanation(row: pd.Series, cell_min_n: int = CELL_MIN_N) -> str:
    """Human-readable explanation string for one transfer row."""
    if pd.isna(row.get("player_cluster")):
        prob = row.get("success_probability", 0.0)
        return (
            f"No player archetype assigned — insufficient prior-season data to classify this "
            f"player. Historical success rate for unclassified players: {prob:.0%} "
            "(reflects that this group historically underperforms the overall population, "
            "not an archetype-specific comparison)."
            f"{_projection_adjustment_clause(row)}"
        )

    tier  = row.get("success_tier", "Unknown")
    prob  = row.get("success_probability", 0.0)
    n     = row.get("cell_n", 0.0)
    n_op  = row.get("offense_pair_n", 0.0)
    comps = row.get("similar_transfers") or []
    team  = row.get("team_cluster_label") or "this program style"
    level = row.get("prediction_level") or _infer_prediction_level(row, cell_min_n)

    if not comps:
        if level == "offense_pair":
            return (
                f"{tier} historical success rate ({prob:.0%}) for this archetype in a similar "
                f"offensive system at {team} — no directly comparable full-system transfers yet; "
                f"estimate draws on archetype × offense precedent ({n_op:.1f} effective transfers) "
                f"rather than this exact offensive/defensive pairing."
                f"{_projection_adjustment_clause(row)}"
            )
        return (
            f"{tier} historical success rate ({prob:.0%}) for this archetype at {team} — "
            "no directly comparable historical transfers yet; estimate relies on the broader "
            f"archetype average rather than real precedent for this exact pairing."
            f"{_projection_adjustment_clause(row)}"
        )

    if level == "cell":
        precedent = (
            f"backed by real precedent for this exact pairing ({n:.1f} effective historical transfers)"
            if n >= cell_min_n else
            f"mostly from archetype × offense precedent — only {n:.1f} effective transfers "
            "directly comparable to this exact offensive/defensive pairing"
        )
    elif level == "offense_pair":
        precedent = (
            f"grounded in archetype × offensive-system precedent ({n_op:.1f} effective transfers)"
            if n_op >= cell_min_n else
            f"mostly from the broader archetype average — only {n_op:.1f} effective transfers "
            "at the archetype × offense level"
        )
    else:
        precedent = (
            "mostly from the broader archetype average — sparse precedent at the "
            "archetype × system level"
        )

    def _fmt(c: dict) -> str:
        outcome = "met/exceeded" if c["success_label"] else "fell short of"
        return (
            f"{c['player_name']} ({int(c['season'])}): {outcome} projection — "
            f"value {c['actual_value_per_100']:.1f} "
            f"(proj {c['projected_value_per_100']:.1f}, {c['value_vs_projection']:+.1f})"
        )

    comp_strs = "; ".join(_fmt(c) for c in comps)
    return (
        f"{tier} historical success rate ({prob:.0%}) for this archetype at {team}, "
        f"{precedent}. Similar transfers: {comp_strs}."
        f"{_projection_adjustment_clause(row)}"
    )


def iter_scored_active_candidate_chunks(
    df_historical: pd.DataFrame,
    target_season: int,
    shrinkage_k: int = SHRINKAGE_K,
    decay_lambda: float = DECAY_LAMBDA,
    chunk_size: int = INFERENCE_CHUNK_SIZE,
    *,
    engine=None,
    df_active: pd.DataFrame | None = None,
    n_active: int | None = None,
):
    """Yield scored active-candidate chunks using precomputed rates + SQL streaming.

    Provide ``engine`` to stream candidates from RDS, or ``df_active`` for tests.
    """
    ctx = build_inference_rate_context(
        df_historical, target_season, shrinkage_k=shrinkage_k, decay_lambda=decay_lambda,
    )

    if engine is not None:
        chunk_iter: Iterator[pd.DataFrame] = iter_active_candidate_chunks(
            engine, target_season, chunk_size=chunk_size,
        )
        total = n_active if n_active is not None else count_active_candidates(engine, target_season)
    elif df_active is not None:
        total = len(df_active)
        chunk_iter = (
            df_active.iloc[start:start + chunk_size]
            for start in range(0, total, chunk_size)
        )
    else:
        raise ValueError("iter_scored_active_candidate_chunks requires engine or df_active")

    processed = 0
    for chunk in chunk_iter:
        chunk_scored = score_active_candidate_chunk(chunk, ctx)
        processed += len(chunk_scored)
        log.info(
            "Prepared inference chunk: %d / %d active rows",
            processed, total,
        )
        yield chunk_scored
        del chunk, chunk_scored
        gc.collect()


def score_active_candidates(
    df_active: pd.DataFrame,
    df_historical: pd.DataFrame,
    target_season: int,
    shrinkage_k: int = SHRINKAGE_K,
    decay_lambda: float = DECAY_LAMBDA,
) -> pd.DataFrame:
    """Forward-score active portal candidates using historical cell rates.

    Args:
        df_active:    Output of load_active_candidates (no success column).
        df_historical: Labeled historical frame (label + drift columns sufficient).
        target_season: Season being scored (play year, e.g. 2027).

    Returns:
        Only the active candidate rows, with success_probability and tier.
    """
    return pd.concat(
        iter_scored_active_candidate_chunks(
            df_historical=df_historical,
            target_season=target_season,
            shrinkage_k=shrinkage_k,
            decay_lambda=decay_lambda,
            df_active=df_active,
            n_active=len(df_active),
        ),
        ignore_index=True,
    )


UPSERT_SQL = """\
INSERT INTO transfer_success_scores (
    player_id, to_school_id, season,
    player_cluster, team_offense_cluster_id, team_defense_cluster_id,
    team_cluster_label,
    success_probability, success_tier,
    cell_n, shrinkage_w, cluster_success_rate, cell_success_rate,
    explanation, similar_transfers,
    model_version, computed_at, expires_at
) VALUES (
    %(player_id)s, %(to_school_id)s, %(season)s,
    %(player_cluster)s, %(team_offense_cluster_id)s, %(team_defense_cluster_id)s,
    %(team_cluster_label)s,
    %(success_probability)s, %(success_tier)s,
    %(cell_n)s, %(shrinkage_w)s, %(cluster_success_rate)s, %(cell_success_rate)s,
    %(explanation)s, %(similar_transfers)s,
    %(model_version)s, %(computed_at)s, %(expires_at)s
)
ON CONFLICT (player_id, to_school_id, season, model_version)
DO UPDATE SET
    player_cluster             = EXCLUDED.player_cluster,
    team_offense_cluster_id    = EXCLUDED.team_offense_cluster_id,
    team_defense_cluster_id    = EXCLUDED.team_defense_cluster_id,
    team_cluster_label         = EXCLUDED.team_cluster_label,
    success_probability        = EXCLUDED.success_probability,
    success_tier               = EXCLUDED.success_tier,
    cell_n                     = EXCLUDED.cell_n,
    shrinkage_w                = EXCLUDED.shrinkage_w,
    cluster_success_rate       = EXCLUDED.cluster_success_rate,
    cell_success_rate          = EXCLUDED.cell_success_rate,
    explanation                = EXCLUDED.explanation,
    similar_transfers          = EXCLUDED.similar_transfers,
    computed_at                = EXCLUDED.computed_at,
    expires_at                 = EXCLUDED.expires_at
"""


def _coerce_upsert_value(val):
    if isinstance(val, float) and np.isnan(val):
        return None
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return None if np.isnan(val) else float(val)
    if val is pd.NA or (not isinstance(val, (list, dict)) and pd.isna(val)):
        return None
    return val


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return _coerce_upsert_value(obj)


def _record_to_upsert_row(rec: dict, *, now: datetime, expires: datetime) -> dict:
    sim = rec.get("similar_transfers") or []
    if not isinstance(sim, (list, str)):
        sim = []
    return {
        "player_id":                int(rec["player_id"]),
        "to_school_id":             int(rec["to_school_id"]),
        "season":                   int(rec["season"]),
        "player_cluster":           _coerce_upsert_value(rec.get("player_cluster")),
        "team_offense_cluster_id":  _coerce_upsert_value(rec.get("team_offense_cluster_id")),
        "team_defense_cluster_id":  _coerce_upsert_value(rec.get("team_defense_cluster_id")),
        "team_cluster_label":       rec.get("team_cluster_label"),
        "success_probability":      float(rec["success_probability"]),
        "success_tier":             str(rec["success_tier"]) if pd.notna(rec.get("success_tier")) else None,
        "cell_n":                   _coerce_upsert_value(rec.get("cell_n")),
        "shrinkage_w":              _coerce_upsert_value(rec.get("shrinkage_w")),
        "cluster_success_rate":     _coerce_upsert_value(rec.get("cluster_success_rate")),
        "cell_success_rate":        _coerce_upsert_value(rec.get("cell_success_rate")),
        "explanation":              rec.get("explanation"),
        "similar_transfers":        json.dumps(_json_safe(sim)),
        "model_version":            MODEL_VERSION,
        "computed_at":              now,
        "expires_at":               expires,
    }


def upsert_transfer_success_scores(
    engine,
    records: list[dict] | pd.DataFrame,
    expires_days: int = 7,
    chunk_size: int = UPSERT_CHUNK_SIZE,
) -> int:
    """Upsert scored active candidates into transfer_success_scores.

    Args:
        engine:      SQLAlchemy sync engine.
        records:     Scored rows as a list of dicts or DataFrame from score_active_candidates.
        expires_days: TTL; rows expire this many days after computed_at.
        chunk_size:  Rows per executemany batch (avoids materializing the full payload).

    Returns:
        Number of rows upserted.
    """
    n_total = len(records) if isinstance(records, pd.DataFrame) else len(records)
    if n_total == 0:
        return 0

    log.info("Upserting %d rows to transfer_success_scores", n_total)
    now = datetime.now(tz=timezone.utc)
    expires = now + timedelta(days=expires_days)

    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        n = 0
        n_chunks = (n_total + chunk_size - 1) // chunk_size
        for chunk_idx, start in enumerate(range(0, n_total, chunk_size), start=1):
            if isinstance(records, pd.DataFrame):
                chunk_recs = records.iloc[start:start + chunk_size].to_dict("records")
            else:
                chunk_recs = records[start:start + chunk_size]
            rows = [_record_to_upsert_row(rec, now=now, expires=expires) for rec in chunk_recs]
            execute_batch(cur, UPSERT_SQL, rows, page_size=min(len(rows), 100))
            n += len(rows)
            conn.commit()
            if chunk_idx == 1 or chunk_idx % 10 == 0 or chunk_idx == n_chunks:
                log.info(
                    "Upsert progress: %d / %d rows (batch %d / %d)",
                    n, n_total, chunk_idx, n_chunks,
                )
    finally:
        conn.close()
    return n


def run_transfer_success_pipeline(
    df_transfers: pd.DataFrame,
    shrinkage_k: int = SHRINKAGE_K,
    decay_lambda: float = DECAY_LAMBDA,
) -> pd.DataFrame:
    """End-to-end Transfer Success pipeline.

    Args:
        df_transfers: Output of TRANSFER_EVAL_SQL (one completed transfer per row).
        shrinkage_k:  Bayesian shrinkage constant K (default 15).
        decay_lambda: Time-decay per season-of-age, <=1.0 (default 0.9).

    Returns:
        Annotated DataFrame with success_label, drift metrics,
        success_probability, similar_transfers, and explanation.
    """
    out = label_transfer_success(df_transfers)
    out = compute_drift(out)
    out = compute_success_probability(out, shrinkage_k=shrinkage_k, decay_lambda=decay_lambda)
    out = attach_similar_transfers(out)
    out["explanation"] = out.apply(build_explanation, axis=1)
    return out
