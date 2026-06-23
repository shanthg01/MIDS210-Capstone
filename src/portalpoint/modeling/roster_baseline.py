"""Shared roster baseline construction for roster-aware models.

This module defines the modeling roster baseline: the players who count as
being on a school's roster when computing roster need, playing-time
opportunity, or team-strength projections.

It is deliberately separate from availability. Availability answers "who can
be recommended as a portal candidate?" Roster baseline answers "who is already
on the target roster outlook?"
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import Engine

from portalpoint.modeling.db_writers import upsert_with_season_replace

BASELINE_STATUS_RETURNING = "returning"
BASELINE_STATUS_CHANGED_SCHOOL = "changed_school_next_season"
BASELINE_STATUS_SNAPSHOT = "latest_snapshot"
BASELINE_STATUS_PRIOR_FALLBACK = "prior_roster_fallback"

SNAPSHOT_MEMBERS_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (school_id)
        id AS snapshot_id,
        school_id,
        season,
        snapshot_date
    FROM roster_snapshots
    WHERE season = %s
    ORDER BY school_id, snapshot_date DESC, id DESC
)
SELECT
    l.school_id AS baseline_school_id,
    rsp.player_id,
    l.season,
    l.snapshot_id,
    l.snapshot_date
FROM latest l
JOIN roster_snapshot_players rsp ON rsp.snapshot_id = l.snapshot_id
WHERE rsp.player_id IS NOT NULL
"""

EXPLICIT_DEPARTURE_PAIRS_SQL = """
SELECT player_id, from_school_id AS school_id
FROM transfers
WHERE season = %s
  AND player_id IS NOT NULL
  AND from_school_id IS NOT NULL
UNION
SELECT player_id, school_id
FROM hoop_explorer_player_stats
WHERE season = %s
  AND player_id IS NOT NULL
  AND school_id IS NOT NULL
  AND transfer_dest = 'NBA'
UNION
SELECT pss.player_id, pss.school_id
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
WHERE pss.season = %s
  AND lower(p.class_year) IN ('senior', 'graduate')
UNION
SELECT player_id, school_id
FROM hoop_explorer_player_stats
WHERE season = %s
  AND player_id IS NOT NULL
  AND school_id IS NOT NULL
  AND year_class IN ('Sr', 'Gr')
"""


DELETE_BASELINE_MEMBERS_SQL = "DELETE FROM roster_baseline_members WHERE season = ANY(%s)"

UPSERT_BASELINE_MEMBERS_SQL = """
INSERT INTO roster_baseline_members (player_id, school_id, season, baseline_status)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_roster_baseline_member DO UPDATE SET
    baseline_status = EXCLUDED.baseline_status,
    computed_at = now()
"""


@dataclass(frozen=True)
class RosterBaselineSummary:
    rows: int
    historical_rows: int
    snapshot_rows: int
    fallback_rows: int
    snapshot_schools: int
    fallback_schools: int


def build_historical_members(stats_df: pd.DataFrame, seasons: list[int]) -> pd.DataFrame:
    """Roster outlook for seasons where ``season + 1`` exists.

    If a player appears at a school in S+1 and has a feature row in S, that
    player counts in that school's S-cycle baseline. This captures returning
    players and incoming transfers from one consistent source without needing
    historical roster snapshots.
    """
    cols = ["player_id", "school_id", "season"]
    empty = pd.DataFrame(columns=["player_id", "baseline_school_id", "season", "baseline_status"])
    if stats_df.empty or not set(cols).issubset(stats_df.columns):
        return empty

    keys = stats_df[cols].dropna().copy()
    keys["player_id"] = keys["player_id"].astype(int)
    keys["school_id"] = keys["school_id"].astype(int)
    keys["season"] = keys["season"].astype(int)

    seasons_with_features = set(keys["season"].unique())
    target_seasons = {int(s) for s in seasons if int(s) + 1 in seasons_with_features}
    if not target_seasons:
        return empty

    next_roster = keys.rename(columns={"school_id": "baseline_school_id", "season": "next_season"})
    next_roster["season"] = next_roster["next_season"] - 1
    next_roster = next_roster[next_roster["season"].isin(target_seasons)]

    prior = keys.rename(columns={"school_id": "source_school_id"})[
        ["player_id", "season", "source_school_id"]
    ]
    members = next_roster.merge(prior, on=["player_id", "season"], how="inner")
    members["baseline_status"] = BASELINE_STATUS_CHANGED_SCHOOL
    same_school = members["baseline_school_id"] == members["source_school_id"]
    members.loc[same_school, "baseline_status"] = BASELINE_STATUS_RETURNING

    return members[
        ["player_id", "baseline_school_id", "season", "baseline_status"]
    ].drop_duplicates()


def load_latest_snapshot_members(engine: Engine, season: int) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["player_id", "baseline_school_id", "season", "baseline_status"])
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute(SNAPSHOT_MEMBERS_SQL, (season,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    finally:
        raw_conn.close()
    if not rows:
        return empty
    members = pd.DataFrame(rows, columns=cols)
    members["baseline_status"] = BASELINE_STATUS_SNAPSHOT
    return members[
        ["player_id", "baseline_school_id", "season", "baseline_status"]
    ].drop_duplicates()


def load_explicit_departure_pairs(engine: Engine, season: int) -> set[tuple[int, int]]:
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute(EXPLICIT_DEPARTURE_PAIRS_SQL, (season, season, season, season))
            return {(int(player_id), int(school_id)) for player_id, school_id in cur.fetchall()}
    finally:
        raw_conn.close()


def build_prior_fallback_members(
    stats_df: pd.DataFrame,
    season: int,
    exclude_school_ids: set[int],
    departed_pairs: set[tuple[int, int]],
) -> pd.DataFrame:
    """Fallback for latest-season schools with no roster snapshot.

    Uses same-season player_season_stats, subtracting explicit upstream
    departures and likely eligibility expirations. This keeps all schools
    scoreable while making the fallback visible in summary/logging.
    """
    cols = ["player_id", "school_id", "season"]
    empty = pd.DataFrame(columns=["player_id", "baseline_school_id", "season", "baseline_status"])
    s_df = stats_df.loc[stats_df["season"] == season, cols].dropna().copy()
    if s_df.empty:
        return empty
    s_df["player_id"] = s_df["player_id"].astype(int)
    s_df["school_id"] = s_df["school_id"].astype(int)
    s_df = s_df[~s_df["school_id"].isin(exclude_school_ids)]
    if departed_pairs:
        keep = ~s_df.apply(
            lambda r: (int(r["player_id"]), int(r["school_id"])) in departed_pairs,
            axis=1,
        )
        s_df = s_df[keep]
    if s_df.empty:
        return empty
    return pd.DataFrame(
        {
            "player_id": s_df["player_id"],
            "baseline_school_id": s_df["school_id"],
            "season": s_df["season"],
            "baseline_status": BASELINE_STATUS_PRIOR_FALLBACK,
        }
    ).drop_duplicates()


def apply_members_to_features(stats_df: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    """Attach player-season feature rows to roster-baseline membership."""
    if stats_df.empty or members.empty:
        return stats_df.iloc[0:0].copy()

    features = stats_df.copy()
    features = features.rename(columns={"school_id": "source_school_id"})
    baseline = members.merge(features, on=["player_id", "season"], how="inner")
    baseline["school_id"] = baseline["baseline_school_id"].astype(int)
    return baseline.drop(columns=["baseline_school_id"]).reset_index(drop=True)


def build_roster_baseline_frame(
    stats_df: pd.DataFrame,
    engine: Engine,
    seasons: list[int],
    latest_season: int | None = None,
) -> tuple[pd.DataFrame, RosterBaselineSummary]:
    """Build the shared roster baseline feature frame for modeling.

    Historical seasons use player_season_stats(S+1) as the roster-outlook
    source. The latest season uses the latest roster snapshot when available,
    with an expected-departure fallback for schools without a usable snapshot.
    """
    if latest_season is None:
        latest_season = max(int(s) for s in seasons)

    historical_members = build_historical_members(stats_df, seasons)
    snapshot_members = load_latest_snapshot_members(engine, latest_season)
    snapshot_schools = (
        set(snapshot_members["baseline_school_id"].astype(int).unique())
        if not snapshot_members.empty
        else set()
    )
    departed_pairs = load_explicit_departure_pairs(engine, latest_season)
    fallback_members = build_prior_fallback_members(
        stats_df, latest_season, snapshot_schools, departed_pairs
    )

    members = pd.concat([historical_members, snapshot_members, fallback_members], ignore_index=True)
    members = members.drop_duplicates(subset=["player_id", "baseline_school_id", "season"])
    baseline = apply_members_to_features(stats_df, members)

    summary = RosterBaselineSummary(
        rows=int(len(baseline)),
        historical_rows=int(len(historical_members)),
        snapshot_rows=int(len(snapshot_members)),
        fallback_rows=int(len(fallback_members)),
        snapshot_schools=int(len(snapshot_schools)),
        fallback_schools=(
            int(fallback_members["baseline_school_id"].nunique())
            if not fallback_members.empty
            else 0
        ),
    )
    return baseline, summary


def write_roster_baseline_members(engine: Engine, baseline_df: pd.DataFrame, seasons: list[int]) -> int:
    """Persist build_roster_baseline_frame()'s membership rows to
    roster_baseline_members — the single real computation that
    fit_score_service.get_roster_baseline_membership() reads, instead of
    re-deriving the same rules a second time at API-read time. Called by both
    scripts/run_gap_matching.py and notebooks/models/gap_matching.ipynb so the
    two paths can't produce different answers.

    Full delete-by-season-then-insert, matching every other modeling writer
    in this codebase (see db_writers.upsert_with_season_replace's docstring).
    """
    if baseline_df.empty:
        return 0
    rows = baseline_df[["player_id", "school_id", "season", "baseline_status"]].drop_duplicates(
        subset=["player_id", "school_id", "season"]
    )
    records = [
        (int(r.player_id), int(r.school_id), int(r.season), r.baseline_status)
        for r in rows.itertuples(index=False)
    ]
    _, upserted = upsert_with_season_replace(
        engine,
        UPSERT_BASELINE_MEMBERS_SQL,
        records,
        delete_sql=DELETE_BASELINE_MEMBERS_SQL,
        delete_params=([int(s) for s in seasons],),
    )
    return upserted
