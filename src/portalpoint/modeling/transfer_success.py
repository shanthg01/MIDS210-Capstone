"""
Transfer Success evaluation pipeline (Model 5).

Empirical Bayes success probability over (player_cluster × team_system) cells,
trained on an expanding window of prior seasons with time-decay weighting.
No separate ML model — success is defined as meeting or exceeding the
destination-adjusted projection baseline (value_per_100).

Cell key uses numeric cluster IDs, not the string system_label, to avoid
silent merge collisions when two (offense, defense) pairs share a label.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHRINKAGE_K: int   = 15   # pseudo-observations from cluster prior
CELL_MIN_N: int    = 5    # effective n below which shrinkage is meaningfully active
DECAY_LAMBDA: float = 0.9  # recency weight per season-of-age; <=1.0
MAX_COMPS: int     = 3    # max named historical comps per output row

# Numeric groupby key for cell-level rates. Avoids silent merges when two
# different (offense_cluster_id, defense_cluster_id) pairs produce identical
# system_label strings.
_CELL_KEY = ["player_cluster", "team_offense_cluster_id", "team_defense_cluster_id"]

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


def compute_success_probability(
    df: pd.DataFrame,
    shrinkage_k: int = SHRINKAGE_K,
    decay_lambda: float = DECAY_LAMBDA,
) -> pd.DataFrame:
    """Hierarchical shrinkage probability over (player_cluster × team_system) cells.

    Expanding window (no leakage): each season S is scored using only labeled rows
    from seasons < S. The earliest season falls back to an uninformative 0.5.

    Time-decay weighting: weight = decay_lambda ** (S - s) for a prior-season row
    at season s. Cell/cluster/global rates are decay-weighted averages; the 'n' in
    the shrinkage formula is the sum of decay weights (effective sample size).

    Cell key: (player_cluster, team_offense_cluster_id, team_defense_cluster_id) —
    numeric IDs, not the string system_label, to prevent silent merge collisions.

    Fallback hierarchy (all from strictly earlier seasons):
      1. Cell rate (player_cluster × offense_cluster_id × defense_cluster_id)
      2. Player cluster rate (prior for shrinkage; used when team system is unknown)
      3. Population rate (used when player_cluster itself is NULL)
    """
    df = df.copy()
    seasons = sorted(df["season"].dropna().unique())
    scored_frames = []

    for season in seasons:
        train = df[(df["season"] < season) & df["success"].notna()].copy()
        score = df.loc[df["season"] == season].copy()

        if len(train) == 0:
            score["cluster_success_rate"] = 0.5
            score["cell_success_rate"]    = 0.5
            score["cell_n"]               = 0.0
            score["has_prior_history"]    = False
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

        # Level 2: player cluster prior
        cluster_global = (
            train[train["player_cluster"].notna()]
            .groupby("player_cluster")
            .agg(
                cluster_success_rate=("weighted_success", "sum"),
                cluster_weight=("decay_weight", "sum"),
            )
            .reset_index()
        )
        cluster_global["cluster_success_rate"] /= cluster_global["cluster_weight"]

        # Level 1: cell rate keyed on numeric cluster IDs
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
            cluster_global[["player_cluster", "cluster_success_rate"]],
            on="player_cluster", how="left",
        )
        score = score.merge(
            cell_stats[_CELL_KEY + ["cell_success_rate", "cell_n"]],
            on=_CELL_KEY, how="left",
        )

        score["cluster_success_rate"] = score["cluster_success_rate"].fillna(
            score["player_cluster"].isna().map({True: unknown_archetype_rate, False: global_rate})
        )
        score["cell_n"]            = score["cell_n"].fillna(0.0)
        score["cell_success_rate"] = score["cell_success_rate"].fillna(score["cluster_success_rate"])
        score["has_prior_history"] = True

        scored_frames.append(score)

    df = pd.concat(scored_frames).sort_index()

    df["shrinkage_w"] = df["cell_n"] / (df["cell_n"] + shrinkage_k)
    df["success_probability"] = (
        df["shrinkage_w"] * df["cell_success_rate"]
        + (1 - df["shrinkage_w"]) * df["cluster_success_rate"]
    )
    df["success_tier"] = pd.cut(
        df["success_probability"],
        bins=[0.0, 0.35, 0.50, 0.65, 0.80, 1.01],
        labels=["Very Low", "Low", "Moderate", "High", "Very High"],
        include_lowest=True,
    )
    return df


def attach_similar_transfers(df: pd.DataFrame, max_comps: int = MAX_COMPS) -> pd.DataFrame:
    """Attach named historical comps per transfer, expanding window (no leakage).

    Comps are drawn from the same (player_cluster × team system) cell, using
    numeric cluster IDs as the lookup key to match compute_success_probability's
    cell definition exactly.
    """
    df = df.copy()
    seasons = sorted(df["season"].dropna().unique())
    comp_cols = [
        "player_name", "season", "value_vs_projection", "success_label",
        "minutes_drift", "usage_drift",
        "actual_value_per_100", "projected_value_per_100",
        "post_minutes_per_game", "projected_minutes",
        "post_usage_rate", "projected_usage",
    ]
    scored_frames = []

    for season in seasons:
        train = df[
            (df["season"] < season) & df["success"].notna()
            & df["player_cluster"].notna()
            & df["team_offense_cluster_id"].notna()
            & df["team_defense_cluster_id"].notna()
        ].copy()
        score = df.loc[df["season"] == season].copy()

        if len(train) == 0:
            score["similar_transfers"] = [[] for _ in range(len(score))]
        else:
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

        scored_frames.append(score)

    return pd.concat(scored_frames).sort_index()


def build_explanation(row: pd.Series, cell_min_n: int = CELL_MIN_N) -> str:
    """Human-readable explanation string for one transfer row."""
    if pd.isna(row.get("player_cluster")):
        prob = row.get("success_probability", 0.0)
        return (
            f"No player archetype assigned — insufficient prior-season data to classify this "
            f"player. Historical success rate for unclassified players: {prob:.0%} "
            "(reflects that this group historically underperforms the overall population, "
            "not an archetype-specific comparison)."
        )

    tier  = row.get("success_tier", "Unknown")
    prob  = row.get("success_probability", 0.0)
    n     = row.get("cell_n", 0.0)
    comps = row.get("similar_transfers") or []
    team  = row.get("team_cluster_label") or "this program style"

    if not comps:
        return (
            f"{tier} historical success rate ({prob:.0%}) for this archetype at {team} — "
            "no directly comparable historical transfers yet; estimate relies on the broader "
            "archetype average rather than real precedent for this exact pairing."
        )

    precedent = (
        f"backed by real precedent for this exact pairing ({n:.1f} effective historical transfers)"
        if n >= cell_min_n else
        f"mostly from the broader archetype average — only {n:.1f} effective transfers directly "
        "comparable to this exact pairing"
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
    )


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
