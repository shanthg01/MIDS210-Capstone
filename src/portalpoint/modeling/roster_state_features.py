"""Roster-State Features — derived roster-composition facts per snapshot.

Plain SQL aggregation, not a model: no fit step, no MLflow run, no versioned
artifact. Counts/sums only — turning these into "gap" scores is Gap Matching's
job (gap_matching.py already owns the league-benchmark logic); duplicating
that here would fork the definition of "gap" across two places.

"Departing" is derived by diffing player_season_stats[school, season] (last
completed season's roster) against the snapshot's actual matched players —
no day-over-day snapshot history needed.
"""
from __future__ import annotations

import pandas as pd

from portalpoint.modeling.gap_matching import POS_COLS, POS_NAMES, assign_soft_positions

PRODUCTION_COL = "points_per_game"
# player_season_stats.per is never populated (ingest_barttorvik.py hardcodes it to None —
# no barttorvik source field maps to it, unlike bpm which is real and populated). Use bpm
# as the impact metric instead; confirmed via live DB check (2026-07-14): per is 0/27,050
# non-null across every season 2021-2026.
IMPACT_COL = "bpm"


def safe_bigint_series(values) -> pd.Series:
    """Build a nullable-Int64 pandas Series from raw values that may include None.

    `pd.DataFrame(cur.fetchall(), columns=cols)` silently upcasts an int+None
    column to float64 (numpy has no native nullable-int type), which loses
    precision on player_id — a 63-bit BigInteger (see db/player_ids.py) —
    since float64 only has a 52-bit mantissa. Confirmed live (2026-07-14):
    this corrupted returning/incoming player_id lookups for 346/357 roster
    snapshots (any snapshot with >=1 unmatched "new" player, i.e. almost all
    of them), silently zeroing out returning_minutes/returning_production/
    team_frontcourt_need. Use this instead of trusting pandas' default
    per-column dtype inference for any raw-cursor id column that can be null.
    """
    return pd.array([None if v is None else int(v) for v in values], dtype="Int64")


def weighted_position_sum(df: pd.DataFrame, value_col: str) -> dict[str, float]:
    """Sum value_col across players, weighted by each player's soft position
    vector (POS_COLS) — same weighted-sum pattern as gap_matching.build_roster_gap_vectors."""
    if df.empty:
        return {pos: 0.0 for pos in POS_NAMES}
    weights = df[POS_COLS].values.astype(float)
    values = pd.to_numeric(df[value_col], errors="coerce").fillna(0.0).values
    totals = (weights * values[:, None]).sum(axis=0)
    return {pos: round(float(t), 2) for pos, t in zip(POS_NAMES, totals)}


def position_difference(total: dict[str, float], subtract: dict[str, float]) -> dict[str, float]:
    return {pos: round(total.get(pos, 0.0) - subtract.get(pos, 0.0), 2) for pos in POS_NAMES}


def archetype_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "archetype_label" not in df.columns:
        return {}
    counts = df["archetype_label"].dropna().value_counts()
    return {label: int(n) for label, n in counts.items()}


def class_balance(snapshot_df: pd.DataFrame, returning_status: str) -> dict[str, int]:
    """Uses roster_snapshot_players.class_year directly (freshly scraped this
    week) rather than player_season_stats/players.class_year, which can be a
    season stale — and is the only source available at all for true freshmen,
    who have no player_season_stats row to join against."""
    if snapshot_df.empty:
        return {}
    subset = snapshot_df[snapshot_df["returning_status"] == returning_status]
    counts = subset["class_year"].dropna().value_counts()
    return {cls: int(n) for cls, n in counts.items()}


def compute_total_minutes(df: pd.DataFrame) -> pd.Series:
    """player_season_stats.minutes_per_game is broken (already documented and
    replaced elsewhere in this codebase — see migration d3b7e2a1c498 and
    CLAUDE.md: "replaces broken minutes_per_game as MPG filter"; confirmed
    again here live — e.g. a player at 83.6% of team minutes shows
    minutes_per_game=1.6). min_pct (0-100, % of team's available minutes) is
    the reliable field, so the *_minutes_by_position outputs below are sums
    of minutes SHARE, not literal minute totals — documented, not a literal
    minute count."""
    return pd.to_numeric(df["min_pct"], errors="coerce").fillna(0.0)


def build_roster_state_features(
    snapshot_id: int,
    school_id: int,
    season: int,
    snapshot_df: pd.DataFrame,
    prior_roster_df: pd.DataFrame,
    returning_df: pd.DataFrame,
    departing_df: pd.DataFrame,
    incoming_df: pd.DataFrame,
) -> dict:
    """prior/returning/departing/incoming_df are player_season_stats rows (any
    school, season) joined to HE pos_confidence_* + player_archetypes, already
    soft-positioned via assign_soft_positions(). prior_roster_df is this
    school's full roster last season — the "total" side of the
    open-minutes/usage subtraction. snapshot_df is the raw roster_snapshot_players
    rows for this snapshot — used for class_balance (see its docstring for why)."""
    prior_total_minutes = weighted_position_sum(prior_roster_df, "total_minutes")
    prior_total_usage = weighted_position_sum(prior_roster_df, "usage_rate")
    returning_minutes = weighted_position_sum(returning_df, "total_minutes")
    returning_usage = weighted_position_sum(returning_df, "usage_rate")

    return {
        "snapshot_id": snapshot_id,
        "school_id": school_id,
        "season": season,
        "returning_minutes_by_position": returning_minutes,
        "departing_minutes_by_position": weighted_position_sum(departing_df, "total_minutes"),
        "incoming_transfer_minutes_by_position": weighted_position_sum(incoming_df, "total_minutes"),
        "open_minutes_by_position": position_difference(prior_total_minutes, returning_minutes),
        "open_usage_by_position": position_difference(prior_total_usage, returning_usage),
        "returning_production": round(float(pd.to_numeric(returning_df[PRODUCTION_COL], errors="coerce").fillna(0.0).sum()), 2) if not returning_df.empty else 0.0,
        "returning_player_impact": round(float(pd.to_numeric(returning_df[IMPACT_COL], errors="coerce").fillna(0.0).sum()), 2) if not returning_df.empty else 0.0,
        "class_balance": {
            **class_balance(snapshot_df, "returning"),
            **{f"incoming_{k}": v for k, v in class_balance(snapshot_df, "new").items()},
            **{f"transfer_in_{k}": v for k, v in class_balance(snapshot_df, "transfer_in").items()},
        },
        "returning_archetype_counts": archetype_counts(returning_df),
        "departing_archetype_counts": archetype_counts(departing_df),
        "incoming_archetype_counts": archetype_counts(incoming_df),
    }


def _fill_unweighted_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Defensive guard: assign_soft_positions should always emit positive
    weights, but roster-state sums should not silently drop a player if a future
    position source creates an all-zero row."""
    df = df.copy()
    row_sums = df[POS_COLS].sum(axis=1)
    unweighted = row_sums < 1e-6
    if unweighted.any():
        df.loc[unweighted, POS_COLS] = 1.0 / len(POS_NAMES)
    return df


def prepare_player_pool(df: pd.DataFrame) -> pd.DataFrame:
    """Shared prep for any player_season_stats-shaped pool: soft positions +
    total_minutes. Mirrors gap_matching.py's own load+assign_soft_positions step."""
    if df.empty:
        return df
    df = assign_soft_positions(df)
    df = _fill_unweighted_rows(df)
    df["total_minutes"] = compute_total_minutes(df)
    return df
