"""
scripts/ingest_hoop_explorer.py

Ingests Hoop Explorer CSV exports into PostgreSQL.

Populates:
  - hoop_explorer_team_stats   (play-style vectors, 4 factors, efficiency — ~365 teams/season)
  - hoop_explorer_player_stats (RAPM, play-style vectors, shot profile — ~2,958 players/season)

CSV acquisition
---------------
Hoop Explorer has no public API. CSVs must be obtained from the HE web UI export:
  1. Navigate to hoop-explorer.com → Player Leaderboard (or Team Stats Explorer)
  2. Set filters: Year=<season>, Tier=High (players) / Power 6 (teams)
  3. Click "Export CSV" — downloads the full filtered dataset
  4. Save to data/hoop_explorer/ with a descriptive filename

For automated refresh, set HE_PLAYER_EXPORT_URL and HE_TEAM_EXPORT_URL in .env
and pass --fetch. These URLs are session-authenticated; obtain them by inspecting
the network request made when clicking "Export CSV" in the browser.

Usage:
  # Load all per-season CSVs (all_player_stats_YY_YY.csv + all_team_explorer_stats_YY_YY.csv)
  uv run python scripts/ingest_hoop_explorer.py --all-seasons

  # Single season via explicit CSV paths
  uv run python scripts/ingest_hoop_explorer.py \\
      --player-csv data/hoop_explorer/all_player_stats_25_26.csv \\
      --team-csv   data/hoop_explorer/all_team_explorer_stats_25_26.csv

  # Default single-file mode (most recently modified *player* and *team* CSV)
  uv run python scripts/ingest_hoop_explorer.py

  # Fetch fresh from HE export URLs (requires HE_PLAYER_EXPORT_URL / HE_TEAM_EXPORT_URL in .env)
  uv run python scripts/ingest_hoop_explorer.py --fetch --season 2026

  # Dry run — parse and report match rates without writing to DB
  uv run python scripts/ingest_hoop_explorer.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import os
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portalpoint.db.models import (
    HoopExplorerPlayerStats,
    HoopExplorerTeamStats,
    Player,
    PlayerSeasonStats,
    School,
)
from portalpoint.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path("data/hoop_explorer")

# ---------------------------------------------------------------------------
# HE → canonical DB name mapping
# Barttorvik already aliases many names (e.g. "Connecticut" → "UConn" stored in DB).
# This map handles differences between what HE exports and what the schools table stores.
# Grow this list from the unmatched log written by --dry-run.
# ---------------------------------------------------------------------------
HE_TEAM_ALIASES: dict[str, str] = {
    # HE name → DB schools.name (post-barttorvik normalization).
    # Rule: only add an entry when HE name DIFFERS from DB name.
    # Schools where HE and barttorvik use the same abbreviation need no entry.

    # Barttorvik aliases that change the stored name
    "Miami (FL)":           "Miami",              # barttorvik "Miami FL" → alias "Miami"
    "Saint Mary's (CA)":    "St. Mary's",         # barttorvik "Saint Mary's" → alias "St. Mary's"
    "Loyola Maryland":      "Loyola (MD)",         # barttorvik alias

    # HE long-form → barttorvik stored abbreviated name
    "Southern California":  "Southern Cal",
    "St. John's (NY)":      "St. John's",

    # HE abbreviated → barttorvik full-name (barttorvik uses full state names for these)
    "App State":            "Appalachian State",
    "Army West Point":      "Army",
    "Boston U.":            "Boston University",
    "CSUN":                 "Cal State Northridge",  # barttorvik "Cal St. Northridge" → alias
    "Cal Baptist":          "Cal Baptist",
    "California Baptist":   "Cal Baptist",
    "Central Ark.":         "Central Arkansas",
    "Central Conn. St.":    "Central Connecticut",
    "Col. of Charleston":   "College of Charleston",
    "ETSU":                 "East Tennessee State",
    "Eastern Ill.":         "Eastern Illinois",
    "Eastern Ky.":          "Eastern Kentucky",
    "Eastern Wash.":        "Eastern Washington",
    "FDU":                  "Fairleigh Dickinson",
    "FGCU":                 "Florida Gulf Coast",
    "Ga. Southern":         "Georgia Southern",
    "Grambling":            "Grambling State",
    "Kansas City":          "UMKC",
    "LMU (CA)":             "Loyola Marymount",
    "Lamar University":     "Lamar",
    "McNeese":              "McNeese State",
    "Middle Tenn.":         "Middle Tennessee",
    "N.C. A&T":             "NC A&T",
    "N.C. Central":         "NC Central",
    "NIU":                  "Northern Illinois",
    "Nicholls":             "Nicholls State",
    "North Ala.":           "North Alabama",
    "Northern Ky.":         "Northern Kentucky",
    "Omaha":                "Nebraska Omaha",
    "Queens (NC)":          "Queens",
    "SFA":                  "Stephen F. Austin",
    "SIUE":                 "SIU Edwardsville",
    "South Fla.":           "South Florida",
    "Southeastern La.":     "Southeastern Louisiana",
    "Southern Ill.":        "Southern Illinois",
    "St. Thomas (MN)":      "St. Thomas",
    "UIC":                  "Illinois Chicago",
    "UIW":                  "Incarnate Word",
    "ULM":                  "UL Monroe",
    "UMES":                 "Maryland Eastern Shore",
    "UNCW":                 "UNC Wilmington",
    "UNI":                  "Northern Iowa",
    "UT Martin":            "Tennessee Martin",
    "UTRGV":                "UT Rio Grande Valley",
    "West Ga.":             "West Georgia",
    "Western Ill.":         "Western Illinois",
    "Western Ky.":          "Western Kentucky",
    "A&M-Corpus Christi":   "Texas A&M Corpus Chris",
    "Alcorn":               "Alcorn State",
    "Ark.-Pine Bluff":      "Ark.-Pine Bluff",
    "New Haven":            "New Haven",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    f = _safe_float(val)
    return None if f is None else int(f)


def _str_or_none(val) -> str | None:
    s = str(val or "").strip()
    return s if s and s.lower() not in ("nan", "none", "") else None


def _parse_he_year(year_str: str | None) -> int | None:
    """'2025/26' → 2026 (season-end convention matching barttorvik)."""
    s = str(year_str or "").strip()
    if "/" in s:
        parts = s.split("/")
        try:
            prefix = parts[0][:2]
            return int(prefix + parts[1])
        except (IndexError, ValueError):
            pass
    return _safe_int(s)


def _normalize_he_team(name: str | None) -> str:
    """Apply HE alias map. Returns the name unchanged if not in map."""
    if not name:
        return ""
    return HE_TEAM_ALIASES.get(name.strip(), name.strip())


def _he_player_name_to_standard(he_name: str | None) -> str | None:
    """'Boozer, Cameron' → 'Cameron Boozer'."""
    if not he_name:
        return None
    parts = he_name.strip().split(", ", 1)
    if len(parts) == 2:
        return f"{parts[1]} {parts[0]}"
    return he_name.strip()


def _fuzzy_match(name: str, candidates: list[str], threshold: float = 0.82) -> str | None:
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=threshold)
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# CSV acquisition
# ---------------------------------------------------------------------------

def find_local_csv(data_dir: Path, pattern: str) -> Path | None:
    """Return the most recently modified file matching glob pattern."""
    matches = sorted(data_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def fetch_csv_from_url(url: str, dest_path: Path, session_cookie: str | None = None) -> Path:
    """Download CSV from HE export URL. Saves timestamped copy to data/hoop_explorer/."""
    log.info("fetching %s", url)
    headers = {"Accept": "text/csv,application/csv,*/*"}
    if session_cookie:
        headers["Cookie"] = session_cookie
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    log.info("saved %s (%d bytes)", dest_path, len(resp.content))
    return dest_path


def load_csv(path: Path) -> list[dict]:
    df = pd.read_csv(path, dtype=str, on_bad_lines="skip")
    df.columns = [str(c).strip() for c in df.columns]
    return df.to_dict("records")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _upsert(session, model, conflict_cols: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    count = 0
    for row in rows:
        stmt = pg_insert(model).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row if k not in conflict_cols and k != "id"}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols)
        await session.execute(stmt)
        count += 1
    await session.commit()
    return count


async def _build_school_map(session) -> dict[str, int]:
    """Returns {school.name: school.id}."""
    result = await session.execute(select(School.id, School.name))
    return {row.name: row.id for row in result}


async def _build_player_map(session) -> dict[tuple[str, str], int]:
    """Returns {(full_name_lower, barttorvik_id): player.id} for best-effort matching."""
    result = await session.execute(select(Player.id, Player.full_name, Player.barttorvik_id))
    m: dict[tuple[str, str], int] = {}
    for row in result:
        m[(row.full_name.lower(), row.barttorvik_id or "")] = row.id
    return m


async def _build_player_roster_index(session) -> tuple[dict[tuple[int, int, str], int], dict[tuple[int, int], list[tuple[str, int]]]]:
    """Season/school roster index for safer HE player matching.

    HE player names are not globally unique, and the players table can contain
    duplicate names from separate source IDs. Prefer same-season, same-school
    matches before falling back to global name matching.
    """
    stmt = (
        select(PlayerSeasonStats.season, PlayerSeasonStats.school_id, Player.id, Player.full_name)
        .join(Player, Player.id == PlayerSeasonStats.player_id)
    )
    result = await session.execute(stmt)
    exact: dict[tuple[int, int, str], int] = {}
    by_school_season: dict[tuple[int, int], list[tuple[str, int]]] = {}
    for season, school_id, player_id, full_name in result:
        key = (int(season), int(school_id), full_name.lower())
        exact[key] = int(player_id)
        by_school_season.setdefault((int(season), int(school_id)), []).append((full_name.lower(), int(player_id)))
    return exact, by_school_season


# ---------------------------------------------------------------------------
# Ingest: teams
# ---------------------------------------------------------------------------

async def ingest_team_stats(
    session,
    rows: list[dict],
    school_map: dict[str, int],
    season: int,
    dry_run: bool = False,
) -> int:
    db_names = list(school_map.keys())
    unmatched: list[str] = []
    records: list[dict] = []

    for r in rows:
        he_name = _str_or_none(r.get("team_name"))
        if not he_name:
            continue

        canonical = _normalize_he_team(he_name)
        school_id = school_map.get(canonical)
        if school_id is None:
            # Fuzzy fallback
            fuzzy = _fuzzy_match(canonical, db_names)
            if fuzzy:
                school_id = school_map[fuzzy]
                log.debug("fuzzy match: '%s' → '%s'", he_name, fuzzy)
            else:
                unmatched.append(he_name)

        records.append({
            "school_id": school_id,
            "season": season,
            "he_team_id": _str_or_none(r.get("_id")),
            "he_team_name": he_name,
            "conf": _str_or_none(r.get("conf")),
            "wins": _safe_int(r.get("wins")),
            "losses": _safe_int(r.get("losses")),
            "wab": _safe_float(r.get("wab")),
            "power": _safe_float(r.get("power")),
            "off_adj_ppp": _safe_float(r.get("off_adj_ppp")),
            "def_adj_ppp": _safe_float(r.get("def_adj_ppp")),
            "adj_net": _safe_float(r.get("adj_net")),
            "tempo": _safe_float(r.get("tempo")),
            # Offensive 4 factors
            "off_efg": _safe_float(r.get("off_efg")),
            "off_to": _safe_float(r.get("off_to")),
            "off_ftr": _safe_float(r.get("off_ftr")),
            "off_orb": _safe_float(r.get("off_orb")),
            # Defensive 4 factors
            "def_efg": _safe_float(r.get("def_efg")),
            "def_to": _safe_float(r.get("def_to")),
            "def_ftr": _safe_float(r.get("def_ftr")),
            "def_orb": _safe_float(r.get("def_orb")),
            # Shot rates
            "off_threepr": _safe_float(r.get("off_threepr")),
            "off_twoprimr": _safe_float(r.get("off_twoprimr")),
            "off_twopmidr": _safe_float(r.get("off_twopmidr")),
            "def_threepr": _safe_float(r.get("def_threepr")),
            "def_twoprimr": _safe_float(r.get("def_twoprimr")),
            "def_twopmidr": _safe_float(r.get("def_twopmidr")),
            # Assist rates
            "off_assist": _safe_float(r.get("off_assist")),
            "off_ast_rim": _safe_float(r.get("off_ast_rim")),
            "off_ast_mid": _safe_float(r.get("off_ast_mid")),
            "off_ast_threep": _safe_float(r.get("off_ast_threep")),
            "def_assist": _safe_float(r.get("def_assist")),
            "def_ast_rim": _safe_float(r.get("def_ast_rim")),
            "def_ast_mid": _safe_float(r.get("def_ast_mid")),
            "def_ast_threep": _safe_float(r.get("def_ast_threep")),
            # Offensive play-style (12 types)
            "off_style_rim_attack_pct": _safe_float(r.get("off_style_rim_attack_pct")),
            "off_style_attack_kick_pct": _safe_float(r.get("off_style_attack_kick_pct")),
            "off_style_dribble_jumper_pct": _safe_float(r.get("off_style_dribble_jumper_pct")),
            "off_style_mid_range_pct": _safe_float(r.get("off_style_mid_range_pct")),
            "off_style_perimeter_cut_pct": _safe_float(r.get("off_style_perimeter_cut_pct")),
            "off_style_big_cut_roll_pct": _safe_float(r.get("off_style_big_cut_roll_pct")),
            "off_style_post_up_pct": _safe_float(r.get("off_style_post_up_pct")),
            "off_style_post_kick_pct": _safe_float(r.get("off_style_post_kick_pct")),
            "off_style_pick_pop_pct": _safe_float(r.get("off_style_pick_pop_pct")),
            "off_style_high_low_pct": _safe_float(r.get("off_style_high_low_pct")),
            "off_style_reb_scramble_pct": _safe_float(r.get("off_style_reb_scramble_pct")),
            "off_style_transition_pct": _safe_float(r.get("off_style_transition_pct")),
            # Defensive play-style (12 types)
            "def_style_rim_attack_pct": _safe_float(r.get("def_style_rim_attack_pct")),
            "def_style_attack_kick_pct": _safe_float(r.get("def_style_attack_kick_pct")),
            "def_style_dribble_jumper_pct": _safe_float(r.get("def_style_dribble_jumper_pct")),
            "def_style_mid_range_pct": _safe_float(r.get("def_style_mid_range_pct")),
            "def_style_perimeter_cut_pct": _safe_float(r.get("def_style_perimeter_cut_pct")),
            "def_style_big_cut_roll_pct": _safe_float(r.get("def_style_big_cut_roll_pct")),
            "def_style_post_up_pct": _safe_float(r.get("def_style_post_up_pct")),
            "def_style_post_kick_pct": _safe_float(r.get("def_style_post_kick_pct")),
            "def_style_pick_pop_pct": _safe_float(r.get("def_style_pick_pop_pct")),
            "def_style_high_low_pct": _safe_float(r.get("def_style_high_low_pct")),
            "def_style_reb_scramble_pct": _safe_float(r.get("def_style_reb_scramble_pct")),
            "def_style_transition_pct": _safe_float(r.get("def_style_transition_pct")),
            # Standalone transition / scramble rates + PPP
            "off_trans_pct":    _safe_float(r.get("off_trans_pct")),
            "off_trans_ppp":    _safe_float(r.get("off_trans_ppp")),
            "def_trans_pct":    _safe_float(r.get("def_trans_pct")),
            "def_trans_ppp":    _safe_float(r.get("def_trans_ppp")),
            "off_scramble_pct": _safe_float(r.get("off_scramble_pct")),
            "off_scramble_ppp": _safe_float(r.get("off_scramble_ppp")),
            "def_scramble_pct": _safe_float(r.get("def_scramble_pct")),
            "def_scramble_ppp": _safe_float(r.get("def_scramble_ppp")),
        })

    matched = sum(1 for r in records if r["school_id"] is not None)
    log.info(
        "teams: %d rows parsed, %d matched to schools (%d unmatched)",
        len(records), matched, len(unmatched),
    )
    if unmatched:
        log.warning("unmatched HE team names (add to HE_TEAM_ALIASES):\n  %s", "\n  ".join(sorted(set(unmatched))))

    if dry_run:
        return len(records)

    return await _upsert(session, HoopExplorerTeamStats, ["he_team_name", "season"], records)


# ---------------------------------------------------------------------------
# Ingest: players
# ---------------------------------------------------------------------------

async def ingest_player_stats(
    session,
    rows: list[dict],
    school_map: dict[str, int],
    player_map: dict[tuple[str, str], int],
    roster_exact: dict[tuple[int, int, str], int],
    roster_by_school_season: dict[tuple[int, int], list[tuple[str, int]]],
    season: int,
    dry_run: bool = False,
) -> int:
    db_school_names = list(school_map.keys())
    player_ids_by_name: dict[str, set[int]] = {}
    for name, _barttorvik_id in player_map:
        player_ids_by_name.setdefault(name.lower(), set()).add(player_map[(name, _barttorvik_id)])
    unique_player_by_name = {
        name: next(iter(player_ids))
        for name, player_ids in player_ids_by_name.items()
        if len(player_ids) == 1
    }
    unmatched_teams: list[str] = []
    unmatched_players: list[str] = []
    records: list[dict] = []

    for r in rows:
        he_code = _str_or_none(r.get("player_code"))
        if not he_code:
            continue

        he_team = _str_or_none(r.get("team"))
        canonical_team = _normalize_he_team(he_team) if he_team else ""
        school_id = school_map.get(canonical_team)
        if school_id is None and canonical_team:
            fuzzy = _fuzzy_match(canonical_team, db_school_names)
            if fuzzy:
                school_id = school_map[fuzzy]
            else:
                unmatched_teams.append(he_team or "")

        # Player match: prefer same season/team roster context, then fall back
        # only to globally unique names. This avoids duplicate-name/source-ID
        # collisions such as a HE row linking to a same-name player without the
        # BartTorvik player-season row used by downstream models.
        he_name_raw = _str_or_none(r.get("player_name"))
        std_name = _he_player_name_to_standard(he_name_raw)
        player_id: int | None = None
        if std_name:
            std_lower = std_name.lower()
            if school_id is not None:
                player_id = roster_exact.get((season, int(school_id), std_lower))
                if player_id is None:
                    roster = roster_by_school_season.get((season, int(school_id)), [])
                    roster_names = [name for name, _pid in roster]
                    fuzzy_name = _fuzzy_match(std_lower, roster_names, threshold=0.88)
                    if fuzzy_name:
                        player_id = next(pid for name, pid in roster if name == fuzzy_name)
            if player_id is None:
                player_id = unique_player_by_name.get(std_lower)
            if player_id is None:
                fuzzy_name = _fuzzy_match(std_lower, list(unique_player_by_name.keys()), threshold=0.90)
                if fuzzy_name:
                    player_id = unique_player_by_name[fuzzy_name]
            if player_id is None:
                unmatched_players.append(std_name)

        transfer_src = _str_or_none(r.get("transfer_src"))
        transfer_dest = _str_or_none(r.get("transfer_dest"))

        records.append({
            "player_id": player_id,
            "school_id": school_id,
            "season": season,
            "he_player_code": he_code,
            "he_ncaa_id": _str_or_none(r.get("roster.ncaa_id")),
            "he_team_name": he_team or "",
            "player_name": he_name_raw,
            "pos_class": _str_or_none(r.get("posClass")),
            "year_class": _str_or_none(r.get("roster.year_class")),
            "height": _str_or_none(r.get("roster.height")),
            "conf": _str_or_none(r.get("conf")),
            "transfer_src": transfer_src if transfer_src and transfer_src != "nan" else None,
            "transfer_dest": transfer_dest if transfer_dest and transfer_dest != "nan" else None,
            "off_team_poss_pct": _safe_float(r.get("off_team_poss_pct")),
            "adj_rtg_margin": _safe_float(r.get("adj_rtg_margin")),
            "adj_rapm_margin": _safe_float(r.get("adj_rapm_margin")),
            "off_adj_rapm": _safe_float(r.get("off_adj_rapm")),
            "def_adj_rapm": _safe_float(r.get("def_adj_rapm")),
            "adj_rapm_margin_pred": _safe_float(r.get("adj_rapm_margin_pred")),
            # Production-weighted RAPM + off/def predicted-high-major split — exist in the
            # raw CSV (confirmed against the header), previously never mapped here.
            "off_adj_rapm_prod": _safe_float(r.get("off_adj_rapm_prod")),
            "def_adj_prod_rapm": _safe_float(r.get("def_adj_prod_rapm")),
            "adj_rapm_prod_margin": _safe_float(r.get("adj_rapm_prod_margin")),
            "off_adj_rapm_pred": _safe_float(r.get("off_adj_rapm_pred")),
            "def_adj_rapm_pred": _safe_float(r.get("def_adj_rapm_pred")),
            "off_usage": _safe_float(r.get("off_usage")),
            "off_assist": _safe_float(r.get("off_assist")),
            "off_efg": _safe_float(r.get("off_efg")),
            "off_to": _safe_float(r.get("off_to")),
            "off_ftr": _safe_float(r.get("off_ftr")),
            "off_threepr": _safe_float(r.get("off_threepr")),
            "off_twoprimr": _safe_float(r.get("off_twoprimr")),
            "off_twopmidr": _safe_float(r.get("off_twopmidr")),
            "off_threep": _safe_float(r.get("off_threep")),
            "off_twoprim": _safe_float(r.get("off_twoprim")),
            "off_twopmid": _safe_float(r.get("off_twopmid")),
            "off_ft": _safe_float(r.get("off_ft")),
            "off_orb": _safe_float(r.get("off_orb")),
            "def_orb": _safe_float(r.get("def_orb")),
            "def_stl": _safe_float(r.get("def_stl")),
            "def_blk": _safe_float(r.get("def_blk")),
            # 15 play-style types
            "off_style_rim_attack_pct": _safe_float(r.get("off_style_rim_attack_pct")),
            "off_style_attack_kick_pct": _safe_float(r.get("off_style_attack_kick_pct")),
            "off_style_perimeter_sniper_pct": _safe_float(r.get("off_style_perimeter_sniper_pct")),
            "off_style_dribble_jumper_pct": _safe_float(r.get("off_style_dribble_jumper_pct")),
            "off_style_mid_range_pct": _safe_float(r.get("off_style_mid_range_pct")),
            "off_style_hits_cutter_pct": _safe_float(r.get("off_style_hits_cutter_pct")),
            "off_style_perimeter_cut_pct": _safe_float(r.get("off_style_perimeter_cut_pct")),
            "off_style_pnr_passer_pct": _safe_float(r.get("off_style_pnr_passer_pct")),
            "off_style_big_cut_roll_pct": _safe_float(r.get("off_style_big_cut_roll_pct")),
            "off_style_post_up_pct": _safe_float(r.get("off_style_post_up_pct")),
            "off_style_post_kick_pct": _safe_float(r.get("off_style_post_kick_pct")),
            "off_style_pick_pop_pct": _safe_float(r.get("off_style_pick_pop_pct")),
            "off_style_high_low_pct": _safe_float(r.get("off_style_high_low_pct")),
            "off_style_reb_scramble_pct": _safe_float(r.get("off_style_reb_scramble_pct")),
            "off_style_transition_pct": _safe_float(r.get("off_style_transition_pct")),
            # Position probability distributions
            "pos_confidence_pg": _safe_float(r.get("posConfidences[_PG_]")),
            "pos_confidence_sg": _safe_float(r.get("posConfidences[_SG_]")),
            "pos_confidence_sf": _safe_float(r.get("posConfidences[_SF_]")),
            "pos_confidence_pf": _safe_float(r.get("posConfidences[_PF_]")),
            "pos_confidence_c":  _safe_float(r.get("posConfidences[_C_]")),
        })

    matched_players = sum(1 for r in records if r["player_id"] is not None)
    matched_schools = sum(1 for r in records if r["school_id"] is not None)
    log.info(
        "players: %d rows parsed, %d matched to players (%.0f%%), %d matched to schools",
        len(records), matched_players,
        100 * matched_players / len(records) if records else 0,
        matched_schools,
    )
    if unmatched_players:
        log.warning(
            "%d players unmatched to barttorvik DB (sample): %s",
            len(unmatched_players),
            unmatched_players[:10],
        )
    if unmatched_teams:
        log.warning("unmatched player-side team names: %s", sorted(set(unmatched_teams))[:20])

    if dry_run:
        return len(records)

    return await _upsert(session, HoopExplorerPlayerStats, ["he_player_code", "season"], records)


def _try_s3_upload(local_path: Path, s3_key: str) -> None:
    """Upload a single file to S3; log warning and continue on any failure."""
    try:
        _script_dir = Path(__file__).resolve().parent
        _repo_root = next(
            p for p in [_script_dir, *_script_dir.parents]
            if (p / "pyproject.toml").exists()
        )
        import sys as _sys
        _sys.path.insert(0, str(_repo_root / "notebooks" / "utils"))
        from s3_helpers import upload
        upload(local_path, s3_key)
    except Exception as _exc:
        log.warning("S3 upload skipped for %s: %s", Path(local_path).name, _exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _find_per_season_pairs(data_dir: Path) -> list[tuple[Path, Path]]:
    """Return [(player_csv, team_csv)] pairs for per-season files (all_player_stats_YY_YY.csv)."""
    player_files = sorted(data_dir.glob("all_player_stats_??_??.csv"))
    team_files   = sorted(data_dir.glob("all_team_explorer_stats_??_??.csv"))
    # Match by position in sorted order (same suffix YY_YY); warn if counts differ
    pairs = []
    team_by_suffix = {p.stem.replace("all_team_explorer_stats_", ""): p for p in team_files}
    for pf in player_files:
        suffix = pf.stem.replace("all_player_stats_", "")
        tf = team_by_suffix.get(suffix)
        if tf:
            pairs.append((pf, tf))
        else:
            log.warning("no matching team CSV for player file %s (suffix=%s) — skipping", pf.name, suffix)
    return pairs


async def run(args: argparse.Namespace) -> None:
    # --- Resolve CSV paths ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if getattr(args, "all_seasons", False):
        pairs = _find_per_season_pairs(DATA_DIR)
        if not pairs:
            log.error("--all-seasons: no per-season CSVs found in %s", DATA_DIR)
            sys.exit(1)
        log.info("--all-seasons: found %d season pairs", len(pairs))
        async with AsyncSessionLocal() as session:
            school_map = await _build_school_map(session)
            player_map = await _build_player_map(session)
            roster_exact, roster_by_school_season = await _build_player_roster_index(session)
        for player_csv_path, team_csv_path in pairs:
            log.info("--- season pair: %s | %s ---", player_csv_path.name, team_csv_path.name)
            player_rows = load_csv(player_csv_path)
            team_rows   = load_csv(team_csv_path)
            sample_year = _str_or_none(team_rows[0].get("year")) if team_rows else None
            season = _parse_he_year(sample_year)
            if season is None:
                log.error("cannot infer season from %s year='%s' — skipping", team_csv_path.name, sample_year)
                continue
            log.info("season=%d", season)
            async with AsyncSessionLocal() as session:
                n_teams   = await ingest_team_stats(session, team_rows, school_map, season, dry_run=args.dry_run)
                n_players = await ingest_player_stats(
                    session, player_rows, school_map, player_map,
                    roster_exact, roster_by_school_season, season,
                    dry_run=args.dry_run,
                )
            log.info("season=%d upserted: %d teams, %d players", season, n_teams, n_players)
            _date = datetime.now().strftime("%Y-%m-%d")
            _try_s3_upload(player_csv_path, f"raw/hoop_explorer/{_date}/{season}_player_stats.csv")
            _try_s3_upload(team_csv_path,   f"raw/hoop_explorer/{_date}/{season}_team_stats.csv")
        log.info("--all-seasons done")
        return

    if args.fetch:
        player_url = args.player_url or os.environ.get("HE_PLAYER_EXPORT_URL")
        team_url = args.team_url or os.environ.get("HE_TEAM_EXPORT_URL")
        if not player_url or not team_url:
            log.error(
                "--fetch requires HE_PLAYER_EXPORT_URL and HE_TEAM_EXPORT_URL "
                "(set in .env or pass --player-url / --team-url)"
            )
            sys.exit(1)
        cookie = os.environ.get("HE_SESSION_COOKIE")
        player_csv_path = fetch_csv_from_url(
            player_url, DATA_DIR / f"player_stats_{timestamp}.csv", cookie
        )
        team_csv_path = fetch_csv_from_url(
            team_url, DATA_DIR / f"team_stats_{timestamp}.csv", cookie
        )
    else:
        player_csv_path = (
            Path(args.player_csv) if args.player_csv
            else find_local_csv(DATA_DIR, "*player*")
        )
        team_csv_path = (
            Path(args.team_csv) if args.team_csv
            else find_local_csv(DATA_DIR, "*team*")
        )
        if not player_csv_path or not player_csv_path.exists():
            log.error("no player CSV found in %s — pass --player-csv or --fetch", DATA_DIR)
            sys.exit(1)
        if not team_csv_path or not team_csv_path.exists():
            log.error("no team CSV found in %s — pass --team-csv or --fetch", DATA_DIR)
            sys.exit(1)

    log.info("player CSV: %s", player_csv_path)
    log.info("team CSV:   %s", team_csv_path)

    # --- Parse ---
    player_rows = load_csv(player_csv_path)
    team_rows = load_csv(team_csv_path)

    # Infer season from data if not provided
    season = args.season
    if season is None:
        sample_year = _str_or_none(team_rows[0].get("year")) if team_rows else None
        season = _parse_he_year(sample_year)
        if season is None:
            log.error("cannot infer season from CSV year='%s' — pass --season", sample_year)
            sys.exit(1)
        log.info("inferred season=%d from CSV year='%s'", season, sample_year)

    log.info("target season: %d", season)

    if args.dry_run:
        log.info("[dry-run] parsing only — no DB writes")
        async with AsyncSessionLocal() as session:
            school_map = await _build_school_map(session)
            player_map = await _build_player_map(session)
            roster_exact, roster_by_school_season = await _build_player_roster_index(session)
        await ingest_team_stats(None, team_rows, school_map, season, dry_run=True)
        await ingest_player_stats(
            None, player_rows, school_map, player_map,
            roster_exact, roster_by_school_season, season,
            dry_run=True,
        )
        log.info("[dry-run] done")
        return

    async with AsyncSessionLocal() as session:
        school_map = await _build_school_map(session)
        player_map = await _build_player_map(session)
        roster_exact, roster_by_school_season = await _build_player_roster_index(session)

        n_teams = await ingest_team_stats(session, team_rows, school_map, season)
        log.info("hoop_explorer_team_stats upserted: %d", n_teams)

        n_players = await ingest_player_stats(
            session, player_rows, school_map, player_map,
            roster_exact, roster_by_school_season, season,
        )
        log.info("hoop_explorer_player_stats upserted: %d", n_players)

    # Upload CSV exports to S3
    _date = datetime.now().strftime("%Y-%m-%d")
    for _path, _name in [
        (player_csv_path, "player_stats"),
        (team_csv_path,   "team_stats"),
    ]:
        _try_s3_upload(_path, f"raw/hoop_explorer/{_date}/{season}_{_name}.csv")

    log.info("done — season=%d", season)


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest Hoop Explorer CSV data into PostgreSQL")
    p.add_argument(
        "--all-seasons",
        action="store_true",
        help=(
            "Loop over all per-season CSVs in data/hoop_explorer/ "
            "(all_player_stats_YY_YY.csv + all_team_explorer_stats_YY_YY.csv). "
            "Season inferred from each CSV's 'year' column."
        ),
    )
    p.add_argument(
        "--player-csv",
        help="Path to player CSV (default: most recent *player* file in data/hoop_explorer/)",
    )
    p.add_argument(
        "--team-csv",
        help="Path to team CSV (default: most recent *team* file in data/hoop_explorer/)",
    )
    p.add_argument(
        "--fetch",
        action="store_true",
        help="Download fresh CSVs from HE_PLAYER_EXPORT_URL / HE_TEAM_EXPORT_URL",
    )
    p.add_argument(
        "--player-url",
        help="HE player export URL (overrides HE_PLAYER_EXPORT_URL env var)",
    )
    p.add_argument(
        "--team-url",
        help="HE team export URL (overrides HE_TEAM_EXPORT_URL env var)",
    )
    p.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season year (e.g. 2026 for 2025-26). Inferred from CSV 'year' column if omitted.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse CSV and report match rates without writing to DB",
    )
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
