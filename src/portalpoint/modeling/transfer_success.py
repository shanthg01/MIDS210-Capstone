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

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_VERSION: str  = "transfer-success-eb-v1"

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
    tsp.defense_cluster_id AS team_defense_cluster_id
FROM player_team_fit_scores ptf
JOIN players p ON p.id = ptf.player_id
LEFT JOIN player_archetypes pa
    ON  pa.player_id = ptf.player_id
    AND pa.season    = :target_season - 1
LEFT JOIN team_system_profiles tsp
    ON  tsp.school_id = ptf.school_id
    AND tsp.season    = :target_season - 1
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


def score_active_candidates(
    df_active: pd.DataFrame,
    df_historical: pd.DataFrame,
    target_season: int,
    shrinkage_k: int = SHRINKAGE_K,
    decay_lambda: float = DECAY_LAMBDA,
) -> pd.DataFrame:
    """Forward-score active portal candidates using historical cell rates.

    Appends active candidates (success=NaN, season=target_season) to the
    labeled historical frame, then runs compute_success_probability. The
    expanding-window logic naturally treats the target season as the score
    season and uses ALL historical labeled rows as training.

    Args:
        df_active:    Output of load_active_candidates (no success column).
        df_historical: Labeled historical frame from run_transfer_success_pipeline.
        target_season: Season being scored (play year, e.g. 2027).

    Returns:
        Only the active candidate rows, with success_probability, tier,
        similar_transfers, and explanation all populated.
    """
    # Align columns: active frame has no success/label columns yet.
    active = df_active.copy()
    active["season"] = target_season
    active["success"] = np.nan
    active["success_label"] = pd.NA
    active["has_prior_history"] = False

    # df_historical already carries its own scored/derived columns
    # (cluster_success_rate, cell_success_rate, cell_n, shrinkage_w,
    # success_probability, success_tier, has_prior_history, similar_transfers,
    # explanation) from run_backtest's full run_transfer_success_pipeline pass.
    # Drop them all before concatenating — otherwise the merge inside
    # compute_success_probability's per-season loop collides with the
    # pre-existing same-named columns (pandas suffixes duplicates _x/_y instead
    # of the plain name it expects), breaking the very next .fillna() call with
    # a KeyError. Recomputing them fresh across the combined frame is correct
    # (and cheap) regardless — it's what already happens for historical seasons
    # on every standalone backtest run. Real bug this fixes: without also
    # dropping similar_transfers/explanation here and recomputing them below,
    # every forward-scored active row silently got NaN for both — attach_similar_
    # transfers/build_explanation were never being called on the active rows at all.
    _DERIVED_COLS = [
        "cluster_success_rate", "cell_success_rate", "cell_n",
        "shrinkage_w", "success_probability", "success_tier", "has_prior_history",
        "similar_transfers", "explanation",
    ]
    historical_clean = df_historical.drop(columns=_DERIVED_COLS, errors="ignore")
    historical_clean["has_prior_history"] = False

    # Add any historical columns missing from active (post_*, drift, etc.)
    for col in historical_clean.columns:
        if col not in active.columns:
            active[col] = np.nan

    # Historical rows used purely as training; active rows scored in their season.
    # Override historical seasons to be < target_season (they already are).
    combined = pd.concat(
        [historical_clean[active.columns], active],
        ignore_index=True,
    )

    scored = compute_success_probability(
        combined, shrinkage_k=shrinkage_k, decay_lambda=decay_lambda
    )
    scored = attach_similar_transfers(scored)
    scored["explanation"] = scored.apply(build_explanation, axis=1)
    return scored[scored["season"] == target_season].copy()


UPSERT_SQL = """\
INSERT INTO transfer_success_scores (
    player_id, to_school_id, season,
    player_cluster, team_offense_cluster_id, team_defense_cluster_id,
    team_cluster_label,
    success_probability, success_tier,
    cell_n, shrinkage_w, cluster_success_rate, cell_success_rate,
    explanation, similar_transfers,
    model_version, computed_at, expires_at
) VALUES %s
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


def _coerce_json_scalar(val):
    """NaN -> None (json.dumps would otherwise emit the bare token NaN, valid
    Python JSON but rejected by Postgres's stricter JSON parser), numpy
    int/float/bool -> native Python types (json.dumps can't serialize numpy
    scalars directly)."""
    if isinstance(val, float) and np.isnan(val):
        return None
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        return None if np.isnan(val) else float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    return val


def _sanitize_similar_transfers(sim) -> list[dict]:
    """Sanitize each comp dict's values before json.dumps.

    Comp dicts (minutes_drift/usage_drift/etc, from compute_drift()) can be
    NaN when either side of a comp's drift is missing — real error hit on the
    first run that actually populated similar_transfers: "invalid input syntax
    for type json ... Token 'NaN' is invalid".
    """
    if not isinstance(sim, list):
        return []
    return [{k: _coerce_json_scalar(v) for k, v in comp.items()} for comp in sim]


def build_upsert_rows(
    records: list[dict],
    model_version: str = MODEL_VERSION,
    computed_at: datetime | None = None,
    expires_days: int = 7,
) -> list[tuple]:
    """Build the tuple rows upsert_transfer_success_scores writes via execute_values.

    Pulled out as its own pure function so the NaN-sanitization above is
    testable without a DB connection.
    """
    now = computed_at or datetime.now(tz=timezone.utc)
    expires = now + timedelta(days=expires_days)

    rows = []
    for rec in records:
        sim = _sanitize_similar_transfers(rec.get("similar_transfers") or [])
        rows.append((
            int(rec["player_id"]),
            int(rec["to_school_id"]),
            int(rec["season"]),
            _coerce_json_scalar(rec.get("player_cluster")),
            _coerce_json_scalar(rec.get("team_offense_cluster_id")),
            _coerce_json_scalar(rec.get("team_defense_cluster_id")),
            rec.get("team_cluster_label"),
            float(rec["success_probability"]),
            str(rec["success_tier"]) if pd.notna(rec.get("success_tier")) else None,
            _coerce_json_scalar(rec.get("cell_n")),
            _coerce_json_scalar(rec.get("shrinkage_w")),
            _coerce_json_scalar(rec.get("cluster_success_rate")),
            _coerce_json_scalar(rec.get("cell_success_rate")),
            rec.get("explanation"),
            json.dumps(sim),
            model_version,
            now,
            expires,
        ))
    return rows


def upsert_transfer_success_scores(
    engine,
    records: list[dict],
    expires_days: int = 7,
) -> int:
    """Upsert scored active candidates into transfer_success_scores.

    Uses execute_values (batched multi-row INSERT) rather than executemany —
    real bug found running this against 456K real candidate rows: plain
    psycopg2 executemany issues one round-trip per row, which over an SSM
    tunnel took 90+ minutes with no progress and no end in sight. execute_values
    batches many rows per round-trip, matching destination_projection.py's
    own pattern for its similarly-sized (~450K row) upsert.

    Args:
        engine:      SQLAlchemy sync engine.
        records:     List of dicts from score_active_candidates (one per row).
        expires_days: TTL; rows expire this many days after computed_at.

    Returns:
        Number of rows upserted.
    """
    if not records:
        return 0

    rows = build_upsert_rows(records, expires_days=expires_days)

    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        # cur.rowcount after execute_values only reflects the last page, not the
        # full batch (page_size=1000 chunks a 456K-row write into ~457 statements) —
        # return len(rows) directly, same fix destination_projection.py already needed.
        execute_values(cur, UPSERT_SQL, rows, page_size=1000)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


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
