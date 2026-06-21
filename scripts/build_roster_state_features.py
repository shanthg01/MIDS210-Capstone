"""
scripts/build_roster_state_features.py

Builds roster_state_features — derived roster-composition facts per
roster_snapshots row (returning/departing/incoming minutes & usage by
position, returning production/impact, class balance, archetype counts).

Plain SQL aggregation, not a model: no MLflow run, no versioned artifact,
no notebook counterpart. Deliberately not named run_*.py — that prefix means
"model run with MLflow" everywhere else in this codebase (M1-M3, Gap Matching).

Requires scripts/ingest_roster_snapshots.py to have run first (needs at least
one roster_snapshots row to compute against).

Usage:
  uv run python scripts/build_roster_state_features.py
  uv run python scripts/build_roster_state_features.py --schools Duke
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import pandas as pd

from portalpoint.modeling import roster_state_features as rsf
from portalpoint.modeling.db_writers import upsert_with_season_replace
from portalpoint.modeling.io import get_sync_engine

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SNAPSHOTS_SQL = """
SELECT rs.id AS snapshot_id, rs.school_id, rs.season, s.name AS school_name
FROM roster_snapshots rs
JOIN schools s ON s.id = rs.school_id
"""

SNAPSHOT_PLAYERS_SQL = """
SELECT player_id, returning_status, class_year
FROM roster_snapshot_players
WHERE snapshot_id = %s
"""

PLAYER_POOL_SQL = """
SELECT
    pss.player_id, pss.school_id, pss.season, pss.games_played, pss.min_pct,
    pss.usage_rate, pss.points_per_game, pss.per,
    p.position,
    hep.pos_confidence_pg, hep.pos_confidence_sg, hep.pos_confidence_sf,
    hep.pos_confidence_pf, hep.pos_confidence_c,
    pa.archetype_label
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
LEFT JOIN hoop_explorer_player_stats hep ON hep.player_id = pss.player_id AND hep.season = pss.season
LEFT JOIN player_archetypes pa ON pa.player_id = pss.player_id AND pa.season = pss.season
WHERE {where}
"""

UPSERT_SQL = """
INSERT INTO roster_state_features
    (snapshot_id, school_id, season,
     returning_minutes_by_position, departing_minutes_by_position,
     incoming_transfer_minutes_by_position, open_minutes_by_position, open_usage_by_position,
     returning_production, returning_player_impact, class_balance,
     returning_archetype_counts, departing_archetype_counts, incoming_archetype_counts)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_roster_state_features_snapshot DO UPDATE SET
    returning_minutes_by_position = EXCLUDED.returning_minutes_by_position,
    departing_minutes_by_position = EXCLUDED.departing_minutes_by_position,
    incoming_transfer_minutes_by_position = EXCLUDED.incoming_transfer_minutes_by_position,
    open_minutes_by_position = EXCLUDED.open_minutes_by_position,
    open_usage_by_position = EXCLUDED.open_usage_by_position,
    returning_production = EXCLUDED.returning_production,
    returning_player_impact = EXCLUDED.returning_player_impact,
    class_balance = EXCLUDED.class_balance,
    returning_archetype_counts = EXCLUDED.returning_archetype_counts,
    departing_archetype_counts = EXCLUDED.departing_archetype_counts,
    incoming_archetype_counts = EXCLUDED.incoming_archetype_counts,
    updated_at = now()
"""


def _player_pool(conn, where: str, params: tuple) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(PLAYER_POOL_SQL.format(where=where), params)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def build_for_snapshot(conn, snapshot_id: int, school_id: int, season: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(SNAPSHOT_PLAYERS_SQL, (snapshot_id,))
        cols = [d[0] for d in cur.description]
        snapshot_df = pd.DataFrame(cur.fetchall(), columns=cols)

    matched = snapshot_df[snapshot_df["player_id"].notna()]
    returning_ids = matched.loc[matched["returning_status"] == "returning", "player_id"].tolist()
    transfer_in_ids = matched.loc[matched["returning_status"] == "transfer_in", "player_id"].tolist()
    current_matched_ids = matched["player_id"].tolist()

    prior_roster_df = rsf.prepare_player_pool(
        _player_pool(conn, "pss.school_id = %s AND pss.season = %s", (school_id, season))
    )
    departed_ids = [pid for pid in prior_roster_df.get("player_id", pd.Series(dtype=int)).tolist() if pid not in current_matched_ids]

    returning_df = rsf.prepare_player_pool(
        _player_pool(conn, "pss.player_id = ANY(%s) AND pss.season = %s", (returning_ids, season))
    ) if returning_ids else pd.DataFrame()
    departing_df = prior_roster_df[prior_roster_df["player_id"].isin(departed_ids)] if not prior_roster_df.empty else prior_roster_df
    incoming_df = rsf.prepare_player_pool(
        _player_pool(conn, "pss.player_id = ANY(%s) AND pss.season = %s", (transfer_in_ids, season))
    ) if transfer_in_ids else pd.DataFrame()

    return rsf.build_roster_state_features(
        snapshot_id, school_id, season,
        snapshot_df, prior_roster_df, returning_df, departing_df, incoming_df,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Build roster_state_features for existing roster_snapshots rows")
    p.add_argument("--schools", nargs="+", metavar="NAME", help="Limit to these schools.name values (default: all snapshots)")
    p.add_argument("--dry-run", action="store_true", help="Compute and print — no DB writes")
    args = p.parse_args()

    engine = get_sync_engine()
    conn = engine.raw_connection()

    sql = SNAPSHOTS_SQL
    params = ()
    if args.schools:
        sql += " WHERE s.name = ANY(%s)"
        params = (args.schools,)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        snapshots = pd.DataFrame(cur.fetchall(), columns=cols)
    log.info("snapshots to process: %d", len(snapshots))

    rows = []
    for _, snap in snapshots.iterrows():
        result = build_for_snapshot(conn, int(snap["snapshot_id"]), int(snap["school_id"]), int(snap["season"]))
        log.info(
            "%s: returning_minutes=%s departing_minutes=%s incoming_minutes=%s",
            snap["school_name"],
            sum(result["returning_minutes_by_position"].values()),
            sum(result["departing_minutes_by_position"].values()),
            sum(result["incoming_transfer_minutes_by_position"].values()),
        )
        rows.append((
            result["snapshot_id"], result["school_id"], result["season"],
            json.dumps(result["returning_minutes_by_position"]),
            json.dumps(result["departing_minutes_by_position"]),
            json.dumps(result["incoming_transfer_minutes_by_position"]),
            json.dumps(result["open_minutes_by_position"]),
            json.dumps(result["open_usage_by_position"]),
            result["returning_production"],
            result["returning_player_impact"],
            json.dumps(result["class_balance"]),
            json.dumps(result["returning_archetype_counts"]),
            json.dumps(result["departing_archetype_counts"]),
            json.dumps(result["incoming_archetype_counts"]),
        ))

    conn.rollback()
    conn.close()

    if args.dry_run:
        log.info("[dry-run] %d rows computed — no DB writes", len(rows))
        return

    _, upserted = upsert_with_season_replace(engine, UPSERT_SQL, rows, page_size=500)
    log.info("upserted %d roster_state_features rows", upserted)


if __name__ == "__main__":
    main()
