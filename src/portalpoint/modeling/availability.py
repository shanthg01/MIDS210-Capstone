"""Availability — defines the "available player" universe (transfer portal
candidates) used to scope recommendation-facing models.

Availability is season-scoped, not a permanent player attribute: a player_id
is "available" for a season if a matched transfer_portal_events row shows
them Entered or Committed that season. Committed is included (not just
Entered) so recently-committed players still surface — product decision from
PR #33 follow-up #1.

This is metadata only (`player_team_fit_scores.is_portal_candidate`) — it
does not change what Gap Matching scores. The full all-pairs scoring layer
stays intact for diagnostics, clustering, and projections; this is the flag
the recommendation-facing query surface filters on.
"""
from __future__ import annotations

from sqlalchemy import Engine

AVAILABLE_STATUSES = ("Entered", "Committed")

AVAILABLE_PLAYER_IDS_SQL = """
SELECT DISTINCT player_id
FROM transfer_portal_events
WHERE season = %s
  AND match_status = 'matched'
  AND status = ANY(%s)
  AND player_id IS NOT NULL
"""


def get_available_player_ids(engine: Engine, season: int) -> set[int]:
    """player_ids with a matched transfer_portal_events row (Entered or
    Committed) for `season`."""
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.execute(AVAILABLE_PLAYER_IDS_SQL, (season, list(AVAILABLE_STATUSES)))
            return {int(r[0]) for r in cur.fetchall()}
    finally:
        raw_conn.close()


SYNC_FLAG_SQL = """
UPDATE player_team_fit_scores
SET is_portal_candidate = true
WHERE season = %s
  AND player_id = ANY(%s)
  AND is_portal_candidate = false
"""


def sync_portal_candidate_flags(engine: Engine, seasons: list[int]) -> dict[int, int]:
    """Re-derive is_portal_candidate from transfer_portal_events for each
    season and flag any player_team_fit_scores rows that are out of date.

    Only flips false -> true (Committed counts as available indefinitely
    under AVAILABLE_STATUSES, so there's no "no longer available" case to
    flip back) — safe to call on every ingest/scoring run, every season,
    with no risk of flapping. Call this from wherever new transfer_portal_events
    rows land (ingest_transfers_247sports.py) or new player_team_fit_scores
    rows land (run_gap_matching.py) instead of running a separate backfill.
    """
    raw_conn = engine.raw_connection()
    updated: dict[int, int] = {}
    try:
        with raw_conn.cursor() as cur:
            for season in seasons:
                available_ids = get_available_player_ids(engine, season)
                if not available_ids:
                    updated[season] = 0
                    continue
                cur.execute(SYNC_FLAG_SQL, (season, list(available_ids)))
                updated[season] = cur.rowcount
        raw_conn.commit()
    finally:
        raw_conn.close()
    return updated
