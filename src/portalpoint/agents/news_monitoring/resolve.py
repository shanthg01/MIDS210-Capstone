"""Entity resolution tools and cross-source deduplication for the news-monitoring agent."""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from langchain_core.tools import tool
from sqlalchemy import text as sql_text

from portalpoint.agents.news_monitoring.config import DEDUP_WINDOW_DAYS
from portalpoint.modeling.entity_resolution import (
    match_player,
    resolve_school,
)

log = logging.getLogger(__name__)

_PROGRAM_EVENTS_TRANSFER_CONFLICT = (
    "ON CONFLICT (event_type, source, player_id, event_date) "
    "WHERE event_type = 'transfer_entry' AND player_id IS NOT NULL DO NOTHING"
)
_PROGRAM_EVENTS_COACH_CONFLICT = (
    "ON CONFLICT (event_type, source, school_id, event_date) "
    "WHERE event_type = 'coach_departed' AND school_id IS NOT NULL DO NOTHING"
)


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------

def _get_engine():
    from portalpoint.modeling.io import apply_env_file, get_sync_engine

    apply_env_file()
    return get_sync_engine()


def _load_school_map(conn) -> dict[str, int]:
    rows = conn.execute(sql_text("SELECT id, name FROM schools")).fetchall()
    return {r.name: r.id for r in rows}


def _load_roster(conn, school_id: int, season: int) -> list[tuple[int, str, str]]:
    """Return (player_id, full_name, position) for school_id's roster in season."""
    rows = conn.execute(
        sql_text(
            "SELECT p.id, p.full_name, COALESCE(p.position, '') "
            "FROM player_season_stats pss "
            "JOIN players p ON p.id = pss.player_id "
            "WHERE pss.school_id = :school_id AND pss.season = :season"
        ),
        {"school_id": school_id, "season": season},
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _load_all_players(conn, season: int) -> list[tuple[int, str, str]]:
    """Full-population fallback: all players with stats in a given season."""
    rows = conn.execute(
        sql_text(
            "SELECT p.id, p.full_name, COALESCE(p.position, '') "
            "FROM player_season_stats pss "
            "JOIN players p ON p.id = pss.player_id "
            "WHERE pss.season = :season"
        ),
        {"season": season},
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------------------------------------------------------------------------
# transfer_player implementation
# ---------------------------------------------------------------------------

def _transfer_player_impl(player_name: str, school_from: str, season: int) -> str:
    """Core transfer-portal write pipeline (season supplied by caller)."""
    try:
        engine = _get_engine()
    except Exception as exc:
        return json.dumps({"success": False, "error": f"DB connection failed: {exc}"})

    entry_date = date.today().isoformat()

    try:
        with engine.begin() as conn:
            school_map = _load_school_map(conn)
            from_school_id = resolve_school(school_from, school_map)

            if from_school_id:
                roster = _load_roster(conn, from_school_id, season)
                if not roster:
                    roster = _load_roster(conn, from_school_id, season - 1)
            else:
                roster = []

            if not roster:
                log.warning(
                    "transfer_player: no roster for %s (id=%s) — falling back to full player table",
                    school_from,
                    from_school_id,
                )
                roster = _load_all_players(conn, season)

            player_id, match_confidence, status_tag = match_player(player_name, roster)

            if status_tag not in ("matched",) or player_id is None:
                return json.dumps({
                    "success": False,
                    "status": status_tag,
                    "queried_name": player_name,
                    "school_from": school_from,
                    "message": f"Player not matched (status={status_tag}). Manual review required.",
                })

            matched_name_row = conn.execute(
                sql_text("SELECT full_name FROM players WHERE id = :pid"),
                {"pid": player_id},
            ).fetchone()
            matched_name = matched_name_row[0] if matched_name_row else player_name

            conn.execute(
                sql_text(
                    "INSERT INTO program_events "
                    "  (event_type, school_id, player_id, event_date, source, "
                    "   raw_text, confidence, match_status, created_at) "
                    "VALUES "
                    "  ('transfer_entry', :school_id, :player_id, :event_date, "
                    "   'news-agent', :raw_text, :confidence, 'matched', NOW()) "
                    + _PROGRAM_EVENTS_TRANSFER_CONFLICT
                ),
                {
                    "school_id": from_school_id,
                    "player_id": player_id,
                    "event_date": entry_date,
                    "raw_text": f"News agent detected portal entry for {player_name} from {school_from}",
                    "confidence": match_confidence,
                },
            )

            source_key = f"news-{player_id}"
            conn.execute(
                sql_text(
                    "INSERT INTO transfer_portal_events "
                    "  (season, source, source_player_key, player_id, raw_player_name, "
                    "   match_confidence, match_status, from_school_id, status, portal_entry_date) "
                    "VALUES "
                    "  (:season, :source, :source_key, :player_id, :raw_player_name, "
                    "   :match_confidence, :match_status, :from_school_id, :status, :entry_date) "
                    "ON CONFLICT (source, source_player_key, season) DO UPDATE SET "
                    "  portal_entry_date = COALESCE(transfer_portal_events.portal_entry_date, EXCLUDED.portal_entry_date), "
                    "  from_school_id = COALESCE(transfer_portal_events.from_school_id, EXCLUDED.from_school_id), "
                    "  updated_at = NOW()"
                ),
                {
                    "season": season,
                    "source": "news-agent",
                    "source_key": source_key,
                    "player_id": player_id,
                    "raw_player_name": player_name,
                    "match_confidence": match_confidence,
                    "match_status": "matched",
                    "from_school_id": from_school_id,
                    "status": "Entered",
                    "entry_date": entry_date,
                },
            )

            conn.execute(
                sql_text(
                    "INSERT INTO transfers "
                    "  (player_id, from_school_id, season, portal_entry_date) "
                    "VALUES (:player_id, :from_school_id, :season, :entry_date) "
                    "ON CONFLICT (player_id, season) DO UPDATE SET "
                    "  portal_entry_date = COALESCE(transfers.portal_entry_date, EXCLUDED.portal_entry_date), "
                    "  from_school_id = COALESCE(transfers.from_school_id, EXCLUDED.from_school_id), "
                    "  updated_at = NOW()"
                ),
                {
                    "player_id": player_id,
                    "from_school_id": from_school_id,
                    "season": season,
                    "entry_date": entry_date,
                },
            )

    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc), "queried_name": player_name})

    try:
        from portalpoint.modeling.availability import sync_portal_candidate_flags

        sync_result = sync_portal_candidate_flags(engine, [season])
        rows_flagged = sync_result.get(season, 0)
    except Exception as exc:
        rows_flagged = 0
        log.warning("sync_portal_candidate_flags failed: %s", exc)

    return json.dumps({
        "success": True,
        "player_id": player_id,
        "matched_name": matched_name,
        "queried_name": player_name,
        "match_confidence": match_confidence,
        "from_school": school_from,
        "from_school_id": from_school_id,
        "portal_entry_date": entry_date,
        "season": season,
        "fit_score_rows_flagged": rows_flagged,
        "message": (
            f"{matched_name} (confidence={match_confidence:.2f}) written to "
            f"program_events + transfer_portal_events + transfers; "
            f"sync_portal_candidate_flags flagged {rows_flagged} fit-score row(s)."
        ),
    }, indent=2)


@tool
def transfer_player(player_name: str, school_from: str) -> str:
    """Record a player's transfer-portal entry in the PortalPoint database.

    Pipeline:
    1. Fuzzy-match player_name against the from_school's season roster
       (position-aware, 3-pass threshold with full-roster fallback).
    2. INSERT into transfer_portal_events — makes the agent a feeder into the
       same pipeline used by ingest_transfers_247sports.py.
    3. UPSERT into transfers (portal_entry_date preserved on conflict).
    4. Call sync_portal_candidate_flags() so is_portal_candidate stays in sync
       with transfer_portal_events — NOT via a raw UPDATE.

    Args:
        player_name: Player's name as extracted by the LLM classifier.
        school_from: School the player is departing (raw name from news text).
    """
    return _transfer_player_impl(player_name, school_from, date.today().year)


# ---------------------------------------------------------------------------
# coach_departure implementation
# ---------------------------------------------------------------------------

def _coach_departure_impl(coach_name: str, school_from: str, season: int) -> str:
    """Core coaching-departure write pipeline (season unused but kept for symmetry)."""
    del season  # reserved for future coaches-table season scoping
    event_date = date.today().isoformat()

    try:
        engine = _get_engine()
        with engine.begin() as conn:
            school_map = _load_school_map(conn)
            school_id = resolve_school(school_from, school_map)

            conn.execute(
                sql_text(
                    "INSERT INTO program_events "
                    "  (event_type, school_id, player_id, event_date, source, "
                    "   raw_text, confidence, match_status, created_at) "
                    "VALUES "
                    "  ('coach_departed', :school_id, NULL, :event_date, "
                    "   'news-agent', :raw_text, 0.7, 'pending_review', NOW()) "
                    + _PROGRAM_EVENTS_COACH_CONFLICT
                ),
                {
                    "school_id": school_id,
                    "event_date": event_date,
                    "raw_text": f"News agent detected coaching departure: {coach_name} from {school_from}",
                },
            )

            if school_id is not None:
                result = conn.execute(
                    sql_text(
                        "UPDATE team_system_profiles "
                        "SET stale_flag = true, stale_reason = 'coaching_change' "
                        "WHERE school_id = :school_id"
                    ),
                    {"school_id": school_id},
                )
                rows_flagged = result.rowcount
            else:
                rows_flagged = 0

        return json.dumps({
            "event": "coach_departure",
            "coach_name": coach_name,
            "school_from": school_from,
            "school_id": school_id,
            "event_date": event_date,
            "status": "logged_to_program_events",
            "team_system_profiles_stale_flagged": rows_flagged,
            "message": (
                f"Coaching departure logged for {coach_name} ({school_from}). "
                f"Pending manual review — {rows_flagged} team_system_profiles row(s) "
                "flagged stale (scheme fit scores may be outdated until M2 reruns)."
            ),
        }, indent=2)

    except Exception as exc:
        log.exception("coach_departure DB write failed for %s / %s", coach_name, school_from)
        return json.dumps({
            "event": "coach_departure",
            "coach_name": coach_name,
            "school_from": school_from,
            "event_date": event_date,
            "status": "log_only_no_db",
            "team_system_profiles_stale_flagged": 0,
            "error": str(exc),
            "message": (
                f"DB write failed; event logged in-memory only. "
                f"Manual follow-up required for {coach_name} / {school_from}."
            ),
        }, indent=2)


@tool
def coach_departure(coach_name: str, school_from: str) -> str:
    """Record a coaching departure event in the PortalPoint database.

    Covers any reason a head coach leaves (fired, resigns, retires, takes
    another job). Writes to program_events as event_type='coach_departed' and
    flags team_system_profiles stale for school_from (scheme fit may change
    under new staff). Manual review is required before any Model 2 re-run.

    Args:
        coach_name: Name of the departing head coach.
        school_from: School the coach is leaving.
    """
    return _coach_departure_impl(coach_name, school_from, date.today().year)


def build_action_tools(season: int) -> tuple:
    """Return (transfer_player, coach_departure) tools bound to ``season``."""

    @tool
    def transfer_player_for_season(player_name: str, school_from: str) -> str:
        """Record a player's transfer-portal entry in the PortalPoint database.

        Args:
            player_name: Player's name as extracted by the LLM classifier.
            school_from: School the player is departing (raw name from news text).
        """
        return _transfer_player_impl(player_name, school_from, season)

    @tool
    def coach_departure_for_season(coach_name: str, school_from: str) -> str:
        """Record a coaching departure event in the PortalPoint database.

        Args:
            coach_name: Name of the departing head coach.
            school_from: School the coach is leaving.
        """
        return _coach_departure_impl(coach_name, school_from, season)

    return transfer_player_for_season, coach_departure_for_season


# ---------------------------------------------------------------------------
# Cross-source deduplication (Gate 4)
# ---------------------------------------------------------------------------

def cross_source_dedup(
    portal_updates: list[dict[str, Any]],
    window_days: int = DEDUP_WINDOW_DAYS,
) -> tuple[list[dict], list[dict]]:
    """Collapse cross-source duplicate portal-entry events.

    Two events are considered duplicates when they share the same:
      - event_type (always 'transfer_entry' for portal updates)
      - player_id (resolved entity)
      - from_school_id
      - event_date within ±window_days

    When duplicates exist, the higher-confidence entry wins; ties go to the
    earlier-written entry.

    Returns:
        (deduped, duplicates) — two lists.  The DB writes have already happened
        (idempotent ON CONFLICT); this function provides bookkeeping + summary.
    """
    if not portal_updates:
        return [], []

    def _parse_date(s: str | None) -> date | None:
        if not s:
            return None
        try:
            return date.fromisoformat(str(s)[:10])
        except ValueError:
            return None

    seen: dict[tuple, dict] = {}
    duplicates: list[dict] = []

    for update in portal_updates:
        pid = update.get("player_id")
        school_id = update.get("from_school_id")
        evt_date = _parse_date(update.get("portal_entry_date"))
        confidence = update.get("match_confidence", 0.0) or 0.0

        if evt_date:
            day_bucket = (evt_date - date(2020, 1, 1)).days // window_days
        else:
            day_bucket = -1

        key = ("transfer_entry", pid, school_id, day_bucket)

        if key not in seen:
            seen[key] = update
        else:
            existing = seen[key]
            existing_conf = existing.get("match_confidence", 0.0) or 0.0
            if confidence > existing_conf:
                duplicates.append(existing)
                seen[key] = update
            else:
                duplicates.append(update)

    deduped = list(seen.values())
    return deduped, duplicates
