"""
scripts/ingest_hoopr.py

Ingests hoopR ESPN play-by-play and game-log data into PostgreSQL.

Data source: sportsdataverse GitHub releases (same hoopR/ESPN scrape, different
release tag per table grain) — https://github.com/sportsdataverse/sportsdataverse-data/releases/download/
  espn_mens_college_basketball_pbp/play_by_play_{season}.parquet
  espn_mens_college_basketball_schedules/mbb_schedule_{season}.parquet
  espn_mens_college_basketball_team_boxscores/team_box_{season}.parquet
  espn_mens_college_basketball_player_boxscores/player_box_{season}.parquet

Populates (always, from PBP):
  - hoopr_team_season_stats    (~365 D1 teams, 11 PBP-derived features per season)
  - hoopr_player_season_stats  (~5-8K ESPN athletes/season, name+team+season fuzzy
                                 crosswalk to players.id, ~90% hit rate; unmatched
                                 rows kept with player_id=NULL for manual backfill)

Populates (with --game-logs, from schedule + box score parquet):
  - hoopr_games             (one row per ESPN game)
  - hoopr_team_game_logs    (one row per team per game)
  - hoopr_player_game_logs  (one row per player per game; player_id resolved via
                              players.espn_id direct lookup first — already ~90%
                              backfilled by the season-level ingest above — then the
                              same fuzzy name+roster fallback used for season stats)

Raw PBP is NOT stored in PostgreSQL (2.9M rows/season — no row-level query use case).
Aggregated features only. Raw parquet uploaded to s3://portalpoint-data/raw/hoopr/

Coordinate system (ESPN PBP, center-origin, feet):
  - Full-court horizontal axis: -47 to +47, baskets at ±41.75
  - Vertical axis: -25 to +25 (sidelines)
  - Normalize to half-court: norm_x = abs(coordinate_x), rim at (41.75, 0.0)
  - Filter overflow sentinel values: |coordinate_x| <= 50, |coordinate_y| <= 30

Usage:
  # Download current season + ingest
  uv run python scripts/ingest_hoopr.py

  # Use locally cached parquet (skip download)
  uv run python scripts/ingest_hoopr.py --local-parquet notebooks/data/mbb_pbp_2026.parquet

  # Dry run — compute features, report match rates, no DB writes
  uv run python scripts/ingest_hoopr.py --dry-run

  # Multiple seasons
  uv run python scripts/ingest_hoopr.py --season 2025 --season 2026
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd
import requests
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from portalpoint.db.models import (
    HoopRGame,
    HoopRPlayerGameLog,
    HoopRPlayerSeasonStats,
    HoopRTeamGameLog,
    HoopRTeamSeasonStats,
    Player,
    PlayerSeasonStats,
    School,
)
from portalpoint.db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path("data/hoopr")

SPORTSDATAVERSE_RELEASES = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"

# (release_tag, filename_template) — same hoopR/ESPN scrape, different release per table grain
PBP_RELEASE = ("espn_mens_college_basketball_pbp", "play_by_play_{season}.parquet")
SCHEDULE_RELEASE = ("espn_mens_college_basketball_schedules", "mbb_schedule_{season}.parquet")
TEAM_BOX_RELEASE = ("espn_mens_college_basketball_team_boxscores", "team_box_{season}.parquet")
PLAYER_BOX_RELEASE = ("espn_mens_college_basketball_player_boxscores", "player_box_{season}.parquet")

# ---------------------------------------------------------------------------
# Court geometry — ESPN center-origin coordinate system
# ---------------------------------------------------------------------------
# Full court: x in [-47, +47], baskets at ±41.75 ft from center
# Normalize: norm_x = abs(coordinate_x), norm_y = coordinate_y
# Valid shot coords: |coordinate_x| <= 50, |coordinate_y| <= 30 (filters INT32 sentinels)
RIM_NORM_X = 41.75      # rim position after folding half-courts (norm_x axis)
RIM_NORM_Y = 0.0        # rim at center of backboard (norm_y axis)
COORD_MAX_X = 50.0      # filter out overflow sentinel values
COORD_MAX_Y = 30.0

# Zone distance boundaries from rim (feet)
ZONE1_MAX_DIST = 6.0        # restricted area
ZONE2_MAX_DIST = 20.75      # 3PT arc radius (~22ft corners, 23.75ft top)

# Zone 3 (corner 3): beyond arc AND |norm_y| >= 22
CORNER_3_MIN_Y = 22.0
# Zone 4 (straight-on 3): beyond arc AND |norm_y| < 8
STRAIGHT_3_MAX_Y = 8.0
# Zone 5 (wing 3): everything else beyond arc

# Possession timing
POSS_MAX_SEC = 45.0     # filter noise: possession duration > 45s = invalid
TRANSITION_MAX_SEC = 7.0  # shots within 7s of possession start = transition

# Clutch window (player-only feature): final period, <=2min left, <=5pt margin
CLUTCH_MAX_SEC_REMAINING = 120.0
CLUTCH_MAX_MARGIN = 5

# Rim shot event types in ESPN PBP
_RIM_TYPES = {"LayUpShot", "DunkShot", "TipShot"}

# No athlete_*name* column in any cached season (2021-2026) — confirmed Phase 2
# Step 0 (docs/hoopr_integration_plan.md). Parse player name out of `text` instead.
# Marker-based, order matters — same approach scripts/crosswalk_hoopr_players.py
# validated at 89.8% hit rate.
_PLAYER_NAME_MARKERS = [
    " makes ", " misses ", " Defensive Rebound", " Offensive Rebound",
    " Steal", " Block", " bad pass", " traveling turnover", " turnover",
    " Turnover", " subbing in", " subbing out",
]
_FOUL_RE = re.compile(r"^(?:Technical )?Foul on (.+?)\.?$")


def _extract_player_name(type_text: str, raw_text: str) -> str | None:
    if not isinstance(raw_text, str):
        return None
    if type_text in ("PersonalFoul", "Technical Foul"):
        m = _FOUL_RE.match(raw_text)
        return m.group(1).rstrip(".") if m else None
    for marker in _PLAYER_NAME_MARKERS:
        idx = raw_text.find(marker)
        if idx > 0:
            return raw_text[:idx].strip()
    return None

# D1 team ESPN IDs (from eda_hoopr.ipynb — 365 teams, 2025-26 season)
D1_TEAM_IDS: set[int] = {
      2,   5,   6,   8,   9,  12,  13,  16,  21,  23,  24,  25,  26,  27,  28,
     30,  36,  38,  41,  43,  44,  45,  46,  47,  48,  50,  52,  55,  56,  57,
     58,  59,  61,  62,  66,  68,  70,  71,  77,  79,  82,  84,  85,  87,  88,
     91,  93,  94,  96,  97,  98,  99, 103, 104, 107, 108, 111, 113, 116, 119,
    120, 127, 130, 135, 139, 140, 142, 145, 147, 149, 150, 151, 152, 153, 154,
    155, 156, 158, 159, 160, 161, 163, 164, 166, 167, 171, 172, 179, 183, 189,
    193, 194, 195, 197, 198, 201, 202, 204, 213, 218, 219, 221, 222, 225, 227,
    228, 231, 232, 233, 235, 236, 238, 239, 242, 245, 248, 249, 250, 251, 252,
    253, 254, 256, 257, 258, 259, 261, 264, 265, 269, 270, 275, 276, 277, 278,
    279, 282, 284, 288, 290, 292, 294, 295, 299, 300, 301, 302, 304, 305, 309,
    311, 314, 315, 322, 324, 325, 326, 328, 331, 333, 338, 339, 344, 349, 350,
    356, 357, 399, 526,
    2000, 2005, 2006, 2010, 2011, 2016, 2026, 2029, 2031, 2032, 2046, 2050,
    2057, 2065, 2066, 2083, 2084, 2086, 2097, 2099, 2110, 2115, 2116, 2117,
    2127, 2130, 2132, 2142, 2154, 2166, 2168, 2169, 2172, 2174, 2181, 2182,
    2184, 2193, 2197, 2198, 2199, 2210, 2217, 2226, 2229, 2230, 2239, 2241,
    2244, 2247, 2250, 2253, 2261, 2272, 2275, 2277, 2287, 2294, 2296, 2305,
    2306, 2309, 2320, 2325, 2329, 2330, 2335, 2344, 2348, 2349, 2350, 2351,
    2352, 2363, 2368, 2377, 2378, 2379, 2382, 2385, 2390, 2393, 2400, 2405,
    2413, 2415, 2426, 2427, 2428, 2429, 2430, 2433, 2437, 2439, 2440, 2441,
    2443, 2447, 2448, 2449, 2450, 2453, 2454, 2458, 2459, 2460, 2463, 2464,
    2466, 2473, 2483, 2492, 2501, 2502, 2504, 2506, 2507, 2509, 2511, 2514,
    2515, 2520, 2523, 2529, 2534, 2535, 2539, 2540, 2541, 2545, 2546, 2547,
    2550, 2561, 2565, 2567, 2569, 2571, 2572, 2579, 2582, 2598, 2599, 2603,
    2608, 2612, 2617, 2619, 2623, 2627, 2628, 2630, 2633, 2634, 2635, 2636,
    2638, 2640, 2641, 2643, 2649, 2653, 2655, 2670, 2674, 2678, 2681, 2692,
    2698, 2710, 2711, 2717, 2724, 2729, 2737, 2739, 2747, 2750, 2751, 2752,
    2754, 2755, 2771, 2803, 2815, 2837, 2856, 2870, 2885, 2900, 2908, 2916,
    2934, 3084, 3101, 112358,
}

# ESPN team name → barttorvik/DB school name
ESPN_TEAM_ALIASES: dict[str, str] = {
    "Connecticut": "UConn",
    "Saint Mary's": "St. Mary's",
    "Pitt": "Pittsburgh",
    "Southern California": "Southern Cal",
    "USF": "South Florida",
    "UMass": "Massachusetts",
    "URI": "Rhode Island",
    "UIC": "Illinois Chicago",
    "UIW": "Incarnate Word",
    "UL Monroe": "Louisiana Monroe",
    "UNI": "Northern Iowa",
    "UMES": "Maryland Eastern Shore",
    "UNCW": "UNC Wilmington",
    "UNC Charlotte": "Charlotte",
    "App State": "Appalachian State",
    "Boston U.": "Boston University",
    "FGCU": "Florida Gulf Coast",
    "FDU": "Fairleigh Dickinson",
    "Grambling": "Grambling State",
    "UMKC": "UMKC",
    "LMU": "Loyola Marymount",
    "McNeese": "McNeese State",
    "Middle Tennessee": "Middle Tennessee",
    "NIU": "Northern Illinois",
    "Nicholls": "Nicholls State",
    "North Alabama": "North Alabama",
    "Northern Kentucky": "Northern Kentucky",
    "Nebraska Omaha": "Nebraska Omaha",
    "SFA": "Stephen F. Austin",
    "SIUE": "SIU Edwardsville",
    "SE Louisiana": "Southeastern Louisiana",
    "Southern Illinois": "Southern Illinois",
    "UT Martin": "Tennessee Martin",
    "UTRGV": "UT Rio Grande Valley",
    "Alcorn State": "Alcorn St.",
    "Bethune-Cookman": "Bethune Cookman",
    # "X State" (ESPN full word) -> "X St." (barttorvik/DB abbreviation) — found via
    # Phase 2 player crosswalk validation (docs/hoopr_integration_plan.md): these all
    # scored below the 0.82 fuzzy cutoff (e.g. "Kent State" vs "Kent St." = 0.778),
    # so they were silently unmatched in hoopr_team_season_stats too, not just here.
    "Pennsylvania": "Penn",
    "Kent State": "Kent St.",
    "Ohio State": "Ohio St.",
    "Texas State": "Texas St.",
    "Boise State": "Boise St.",
    "Oregon State": "Oregon St.",
    "Idaho State": "Idaho St.",
    "Fresno State": "Fresno St.",
    "Ball State": "Ball St.",
    "Wright State": "Wright St.",
    "Utah State": "Utah St.",
    "Penn State": "Penn St.",
    "Kansas State": "Kansas St.",
    "Iowa State": "Iowa St.",
    "Weber State": "Weber St.",
    "Murray State": "Murray St.",
    "Morgan State": "Morgan St.",
    "Coppin State": "Coppin St.",
    "San José State": "San Jose St.",
    "South Carolina Upstate": "USC Upstate",
    "St. Thomas-Minnesota": "St. Thomas",
    "Kansas City": "UMKC",
    "Omaha": "Nebraska Omaha",
    "California Baptist": "Cal Baptist",
    "Loyola Maryland": "Loyola MD",
    "Long Island University": "LIU",
    "American University": "American",
    "Queens University": "Queens",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    try:
        f = float(val)
        return None if (f != f) else int(f)
    except (TypeError, ValueError):
        return None


def _safe_date(val):
    if val is None or (isinstance(val, float) and val != val):
        return None
    ts = pd.Timestamp(val)
    return None if pd.isna(ts) else ts.date()


def _normalize_espn_name(name: str | None) -> str:
    if not name:
        return ""
    return ESPN_TEAM_ALIASES.get(name.strip(), name.strip())


def _fuzzy_match(name: str, candidates: list[str], threshold: float = 0.82) -> str | None:
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=threshold)
    return matches[0] if matches else None


def _resolve_school(raw_location: str, school_map: dict[str, int]) -> int | None:
    """Box score / schedule parquet uses team_location ('UConn', 'Pittsburgh') —
    already closer to canonical DB names than PBP's home_team_name ('Connecticut',
    'Pitt'), but same alias-then-fuzzy fallback applies for the names that don't
    match directly."""
    canonical = _normalize_espn_name(raw_location)
    if canonical in school_map:
        return school_map[canonical]
    fuzzy = _fuzzy_match(canonical, list(school_map.keys()))
    return school_map.get(fuzzy) if fuzzy else None


def _build_team_name_map(pbp: pd.DataFrame) -> dict[str, str]:
    """Build {str(float(espn_id)): team_name} matching team_id float64 key format."""
    frames = []
    for id_col, name_col in [("home_team_id", "home_team_name"), ("away_team_id", "away_team_name")]:
        if id_col in pbp.columns and name_col in pbp.columns:
            frames.append(
                pbp[[id_col, name_col]]
                .rename(columns={id_col: "id", name_col: "name"})
                .drop_duplicates()
                .dropna()
            )
    if not frames:
        return {}
    combined = pd.concat(frames).drop_duplicates()
    # team_id column is float64, so keys must be str(float) e.g. "2083.0"
    return (
        combined
        .assign(id=lambda x: x["id"].astype(float).astype(str))
        .set_index("id")["name"]
        .to_dict()
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_parquet(season: int, dest_dir: Path, release: tuple[str, str] = PBP_RELEASE) -> Path:
    release_tag, filename_template = release
    filename = filename_template.format(season=season)
    url = f"{SPORTSDATAVERSE_RELEASES}/{release_tag}/{filename}"
    dest = dest_dir / filename
    log.info("downloading %s", url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=600, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
            fh.write(chunk)
    log.info("saved %s (%d MB)", dest.name, dest.stat().st_size // (1024 * 1024))
    return dest


def _get_or_download(season: int, dest_dir: Path, release: tuple[str, str], force_download: bool) -> Path:
    filename = release[1].format(season=season)
    cached = dest_dir / filename
    if cached.exists() and not force_download:
        log.info("using cached parquet: %s", cached)
        return cached
    return download_parquet(season, dest_dir, release)


# ---------------------------------------------------------------------------
# Feature computation — matches eda_hoopr.ipynb approach exactly
# ---------------------------------------------------------------------------

def compute_team_features(pbp: pd.DataFrame) -> pd.DataFrame:
    """Aggregate PBP rows into one feature row per D1 team.

    Coordinate system: ESPN center-origin (feet).
    Normalize to half-court: norm_x = abs(coordinate_x), rim at (41.75, 0.0).
    Zone boundaries match eda_hoopr.ipynb spatial_features_df.

    Returns DataFrame indexed by espn_team_name with columns:
        espn_team_id, espn_team_name, games_tracked, possessions_tracked,
        pbp_possession_sec, pbp_rim_pct, pbp_three_pct, pbp_mid_pct,
        pbp_zone1_restricted_pct ... pbp_zone5_wing3_pct,
        pbp_turnover_rate, pbp_transition_rate
    """
    log.info("raw PBP rows: %d", len(pbp))

    # ---- D1 filter ----
    pbp = pbp[
        pbp["home_team_id"].isin(D1_TEAM_IDS) &
        pbp["away_team_id"].isin(D1_TEAM_IDS)
    ].copy()
    log.info("D1-only rows: %d", len(pbp))

    # ---- Team name map ----
    team_name_map = _build_team_name_map(pbp)
    log.info("ESPN ID→name map: %d teams", len(team_name_map))

    # team_id is float64 in PBP; str() gives "2083.0" matching map keys
    pbp["team_name"] = pbp["team_id"].astype(str).map(team_name_map)

    # ---- Shot profile + spatial zones ----
    # Filter to valid shot coordinates (sentinel overflow = INT32_MAX ≈ 2.1e8)
    shots = pbp[pbp["shooting_play"]].copy()
    shots = shots.loc[
        shots["coordinate_x"].abs().le(COORD_MAX_X) &
        shots["coordinate_y"].abs().le(COORD_MAX_Y)
    ].copy()

    # Half-court normalization (EDA approach: fold both baskets to one side)
    shots["norm_x"] = shots["coordinate_x"].abs()
    shots["norm_y"] = shots["coordinate_y"]
    shots["dist_from_rim"] = np.sqrt(
        (shots["norm_x"] - RIM_NORM_X) ** 2 +
        (shots["norm_y"] - RIM_NORM_Y) ** 2
    )

    # Shot categories
    dist       = shots["dist_from_rim"]
    norm_y_abs = shots["norm_y"].abs()
    beyond_arc = dist > ZONE2_MAX_DIST

    shots["is_rim"] = shots["type_text"].isin(_RIM_TYPES)
    if "points_attempted" in shots.columns:
        shots["is_three"] = (shots["points_attempted"] == 3) & ~shots["is_rim"]
    else:
        # pre-2023 schemas lack points_attempted; geometry is equivalent
        shots["is_three"] = beyond_arc & ~shots["is_rim"]
    shots["is_mid"] = ~shots["is_three"] & ~shots["is_rim"]

    # Spatial zones (matching EDA zone boundaries)

    zone_conds = [
        dist <= ZONE1_MAX_DIST,
        (dist > ZONE1_MAX_DIST) & (dist <= ZONE2_MAX_DIST),
        beyond_arc & (norm_y_abs >= CORNER_3_MIN_Y),
        beyond_arc & (norm_y_abs < STRAIGHT_3_MAX_Y),
    ]
    zone_choices = ["zone1_restricted", "zone2_mid", "zone3_corner3", "zone4_straight3"]
    shots["zone"] = np.select(zone_conds, zone_choices, default="zone5_wing3")

    shots_named = shots[shots["team_name"].notna()].copy()

    shot_agg = shots_named.groupby("team_name").agg(
        total_shots=("shooting_play", "count"),
        rim_shots=("is_rim", "sum"),
        three_shots=("is_three", "sum"),
        mid_shots=("is_mid", "sum"),
        z1=("zone", lambda z: (z == "zone1_restricted").sum()),
        z2=("zone", lambda z: (z == "zone2_mid").sum()),
        z3=("zone", lambda z: (z == "zone3_corner3").sum()),
        z4=("zone", lambda z: (z == "zone4_straight3").sum()),
        z5=("zone", lambda z: (z == "zone5_wing3").sum()),
    ).reset_index()

    total = shot_agg["total_shots"].clip(lower=1)
    shot_agg["pbp_rim_pct"]                = shot_agg["rim_shots"]   / total
    shot_agg["pbp_three_pct"]              = shot_agg["three_shots"]  / total
    shot_agg["pbp_mid_pct"]               = shot_agg["mid_shots"]    / total
    shot_agg["pbp_zone1_restricted_pct"]  = shot_agg["z1"] / total
    shot_agg["pbp_zone2_mid_pct"]         = shot_agg["z2"] / total
    shot_agg["pbp_zone3_corner3_pct"]     = shot_agg["z3"] / total
    shot_agg["pbp_zone4_straight3_pct"]   = shot_agg["z4"] / total
    shot_agg["pbp_zone5_wing3_pct"]       = shot_agg["z5"] / total

    # ---- Possession timing (EDA approach) ----
    # start/end_period_seconds_remaining absent in pre-2023 schemas; skip timing features if so.
    _has_timing = (
        "start_period_seconds_remaining" in pbp.columns and
        "end_period_seconds_remaining" in pbp.columns
    )
    if not _has_timing:
        log.warning("timing columns absent for this season — pbp_possession_sec and pbp_transition_rate will be NULL")

    if _has_timing:
        df_w = pbp.sort_values(["game_id", "half", "game_play_number"]).copy()
        df_w["game_id_s"] = df_w["game_id"].astype(str)
        df_w["half_s"]    = df_w["half"].astype(str)

        is_turnover  = df_w["type_text"].str.contains("Turnover|Steal", case=False, na=False)
        is_def_reb   = df_w["type_text"] == "Defensive Rebound"
        is_made_shot = df_w["scoring_play"] & df_w["shooting_play"]
        df_w["poss_end"] = is_turnover | is_def_reb | is_made_shot

        df_w["poss_num"] = (
            df_w.groupby(["game_id_s", "half_s"])["poss_end"]
            .shift(1).fillna(False).astype(int)
        )
        df_w["poss_num"] = df_w.groupby(["game_id_s", "half_s"])["poss_num"].cumsum()
        df_w["poss_id"]  = df_w["game_id_s"] + "_" + df_w["half_s"] + "_" + df_w["poss_num"].astype(str)

        poss_team = (
            df_w[(df_w["shooting_play"] | is_turnover) & df_w["team_name"].notna()]
            .groupby("poss_id")["team_name"]
            .first()
        )

        poss_metrics = df_w.groupby("poss_id").agg(
            start_time=("start_period_seconds_remaining", "max"),
            end_time=("end_period_seconds_remaining", "min"),
        )
        poss_metrics["duration"] = poss_metrics["start_time"] - poss_metrics["end_time"]
        poss_metrics["team_name"] = poss_metrics.index.map(poss_team)
        poss_metrics = poss_metrics.dropna(subset=["team_name"])
        poss_metrics = poss_metrics[poss_metrics["duration"].between(0, POSS_MAX_SEC)]

        tempo_df = (
            poss_metrics.groupby("team_name")["duration"]
            .agg(pbp_possession_sec="mean", n_possessions="count")
            .reset_index()
            .query("n_possessions > 5")
        )
    else:
        is_turnover = pbp["type_text"].str.contains("Turnover|Steal", case=False, na=False)
        tempo_df = pd.DataFrame(columns=["team_name", "pbp_possession_sec", "n_possessions"])

    # ---- Turnover rate ----
    to_counts = (
        pbp[is_turnover & pbp["team_name"].notna()]
        .groupby("team_name")
        .size()
        .reset_index(name="n_turnovers")
    )

    # ---- Transition rate ----
    if _has_timing:
        shots_full = pbp[pbp["shooting_play"]].copy()
        shots_full["play_sec"] = (
            pd.to_numeric(shots_full["start_period_seconds_remaining"], errors="coerce") -
            pd.to_numeric(shots_full["end_period_seconds_remaining"], errors="coerce")
        )
        shots_full["team_name"] = shots_full["team_id"].astype(str).map(team_name_map)
        fast = shots_full[shots_full["play_sec"].between(0, TRANSITION_MAX_SEC) & shots_full["team_name"].notna()]
        transition_counts = (
            fast.groupby("team_name")
            .size()
            .reset_index(name="n_transition_shots")
        )
    else:
        transition_counts = pd.DataFrame(columns=["team_name", "n_transition_shots"])

    # ---- Game counts ----
    game_counts = (
        pbp[["team_name", "game_id"]].dropna(subset=["team_name"])
        .drop_duplicates()
        .groupby("team_name")["game_id"]
        .nunique()
        .reset_index(name="games_tracked")
    )

    # ---- Merge all ----
    result = shot_agg.merge(tempo_df, on="team_name", how="left")
    result = result.merge(to_counts, on="team_name", how="left")
    result = result.merge(transition_counts, on="team_name", how="left")
    result = result.merge(game_counts, on="team_name", how="left")

    result["possessions_tracked"] = result["n_possessions"].fillna(0).astype(int)
    result["pbp_turnover_rate"] = (
        result["n_turnovers"].fillna(0) / result["possessions_tracked"].clip(lower=1)
    )
    result["pbp_transition_rate"] = (
        result["n_transition_shots"].fillna(0) / result["total_shots"].clip(lower=1)
    )

    # Attach ESPN team_id (reverse map: name → id)
    name_to_id = {v: k for k, v in team_name_map.items()}
    result["espn_team_id"] = result["team_name"].map(name_to_id)
    result = result.rename(columns={"team_name": "espn_team_name"})

    log.info("features computed for %d teams", len(result))
    return result


def compute_player_features(pbp: pd.DataFrame) -> pd.DataFrame:
    """Aggregate PBP rows into one feature row per ESPN athlete_id_1.

    Mirrors compute_team_features' shot-zone/turnover/transition logic, keyed
    on athlete_id_1 instead of team_id, plus two player-only metrics with no
    team-level equivalent (pbp_clutch_ts_pct, pbp_assist_rate).

    No on-court lineup tracking exists in this PBP feed, so there's no true
    per-player possession count. possessions_tracked is a usage proxy —
    shot attempts + turnovers committed by this player — and turnover_rate /
    assist_rate are both expressed against it.

    pbp_clutch_ts_pct is an approximation: the dataset has zero
    MissedFreeThrow rows in any cached season (confirmed by exhaustive
    type_text enumeration), so true FTA (makes+misses) isn't computable.
    Denominator uses FG attempts only (MadeFreeThrow rows excluded from FGA,
    but their points still count in the numerator) — biased slightly high
    for high-FT-volume players in clutch minutes. Documented limitation, not
    a bug.

    Returns DataFrame indexed by athlete_id_1 with columns:
        espn_athlete_id, raw_display_name, espn_team_name, shot_attempts_tracked,
        games_tracked, possessions_tracked, pbp_rim_pct ... pbp_zone5_wing3_pct,
        pbp_turnover_rate, pbp_transition_rate, pbp_clutch_ts_pct, pbp_assist_rate
    """
    pbp = pbp[
        pbp["home_team_id"].isin(D1_TEAM_IDS) &
        pbp["away_team_id"].isin(D1_TEAM_IDS)
    ].copy()

    team_name_map = _build_team_name_map(pbp)
    pbp["team_name"] = pbp["team_id"].astype(str).map(team_name_map)
    pbp["raw_name"] = [
        _extract_player_name(t, x) for t, x in zip(pbp["type_text"], pbp["text"])
    ]

    # ---- Identity: mode raw_name / team per athlete (handles rare text-parse noise) ----
    named = pbp[pbp["athlete_id_1"].notna() & pbp["raw_name"].notna()].copy()
    identity = (
        named.groupby("athlete_id_1")
        .agg(
            raw_display_name=("raw_name", lambda s: s.value_counts().idxmax()),
            espn_team_name=("team_name", lambda s: s.dropna().value_counts().idxmax() if s.notna().any() else None),
        )
        .reset_index()
    )

    # ---- Shot profile + spatial zones (identical geometry to team level) ----
    shots = pbp[pbp["shooting_play"] & pbp["athlete_id_1"].notna()].copy()
    shots = shots.loc[
        shots["coordinate_x"].abs().le(COORD_MAX_X) &
        shots["coordinate_y"].abs().le(COORD_MAX_Y)
    ].copy()
    shots["norm_x"] = shots["coordinate_x"].abs()
    shots["norm_y"] = shots["coordinate_y"]
    shots["dist_from_rim"] = np.sqrt(
        (shots["norm_x"] - RIM_NORM_X) ** 2 +
        (shots["norm_y"] - RIM_NORM_Y) ** 2
    )

    dist       = shots["dist_from_rim"]
    norm_y_abs = shots["norm_y"].abs()
    beyond_arc = dist > ZONE2_MAX_DIST

    shots["is_rim"] = shots["type_text"].isin(_RIM_TYPES)
    if "points_attempted" in shots.columns:
        shots["is_three"] = (shots["points_attempted"] == 3) & ~shots["is_rim"]
    else:
        shots["is_three"] = beyond_arc & ~shots["is_rim"]
    shots["is_mid"] = ~shots["is_three"] & ~shots["is_rim"]

    zone_conds = [
        dist <= ZONE1_MAX_DIST,
        (dist > ZONE1_MAX_DIST) & (dist <= ZONE2_MAX_DIST),
        beyond_arc & (norm_y_abs >= CORNER_3_MIN_Y),
        beyond_arc & (norm_y_abs < STRAIGHT_3_MAX_Y),
    ]
    zone_choices = ["zone1_restricted", "zone2_mid", "zone3_corner3", "zone4_straight3"]
    shots["zone"] = np.select(zone_conds, zone_choices, default="zone5_wing3")

    shot_agg = shots.groupby("athlete_id_1").agg(
        total_shots=("shooting_play", "count"),
        rim_shots=("is_rim", "sum"),
        three_shots=("is_three", "sum"),
        mid_shots=("is_mid", "sum"),
        z1=("zone", lambda z: (z == "zone1_restricted").sum()),
        z2=("zone", lambda z: (z == "zone2_mid").sum()),
        z3=("zone", lambda z: (z == "zone3_corner3").sum()),
        z4=("zone", lambda z: (z == "zone4_straight3").sum()),
        z5=("zone", lambda z: (z == "zone5_wing3").sum()),
    ).reset_index()

    total = shot_agg["total_shots"].clip(lower=1)
    shot_agg["pbp_rim_pct"]               = shot_agg["rim_shots"]  / total
    shot_agg["pbp_three_pct"]             = shot_agg["three_shots"] / total
    shot_agg["pbp_mid_pct"]               = shot_agg["mid_shots"]  / total
    shot_agg["pbp_zone1_restricted_pct"]  = shot_agg["z1"] / total
    shot_agg["pbp_zone2_mid_pct"]         = shot_agg["z2"] / total
    shot_agg["pbp_zone3_corner3_pct"]     = shot_agg["z3"] / total
    shot_agg["pbp_zone4_straight3_pct"]   = shot_agg["z4"] / total
    shot_agg["pbp_zone5_wing3_pct"]       = shot_agg["z5"] / total
    shot_agg["shot_attempts_tracked"]     = shot_agg["total_shots"]

    # FG-only universe — excludes MadeFreeThrow, used for transition/clutch FGA below
    fg_only = shots[shots["type_text"] != "MadeFreeThrow"].copy()

    # ---- Turnovers (same type_text rule as team level) ----
    is_turnover = pbp["type_text"].str.contains("Turnover|Steal", case=False, na=False)
    to_counts = (
        pbp[is_turnover & pbp["athlete_id_1"].notna()]
        .groupby("athlete_id_1")
        .size()
        .reset_index(name="n_turnovers")
    )

    # ---- Transition rate (FG attempt within 7s of period-clock elapsed) ----
    _has_timing = (
        "start_period_seconds_remaining" in pbp.columns and
        "end_period_seconds_remaining" in pbp.columns
    )
    if _has_timing:
        fg_only["play_sec"] = (
            pd.to_numeric(fg_only["start_period_seconds_remaining"], errors="coerce") -
            pd.to_numeric(fg_only["end_period_seconds_remaining"], errors="coerce")
        )
        fast = fg_only[fg_only["play_sec"].between(0, TRANSITION_MAX_SEC)]
        transition_counts = fast.groupby("athlete_id_1").size().reset_index(name="n_transition_shots")
    else:
        transition_counts = pd.DataFrame(columns=["athlete_id_1", "n_transition_shots"])

    # ---- Clutch TS% (final period, <=2min, <=5pt margin; FGA-only denominator) ----
    if _has_timing:
        last_period = pbp.groupby("game_id")["period_number"].transform("max")
        margin = (pbp["home_score"] - pbp["away_score"]).abs()
        clutch_mask = (
            (pbp["period_number"] == last_period) &
            (pbp["end_period_seconds_remaining"] <= CLUTCH_MAX_SEC_REMAINING) &
            (margin <= CLUTCH_MAX_MARGIN)
        )
        clutch_pbp = pbp[clutch_mask & pbp["athlete_id_1"].notna()]
        clutch_fga = clutch_pbp[
            clutch_pbp["shooting_play"] & (clutch_pbp["type_text"] != "MadeFreeThrow")
        ].groupby("athlete_id_1").size().rename("clutch_fga")
        clutch_pts = clutch_pbp[clutch_pbp["scoring_play"]].groupby("athlete_id_1")["score_value"].sum().rename("clutch_pts")
        clutch_df = pd.concat([clutch_fga, clutch_pts], axis=1).reset_index()
        clutch_df["pbp_clutch_ts_pct"] = clutch_df["clutch_pts"] / (2 * clutch_df["clutch_fga"].clip(lower=1))
        clutch_df = clutch_df[["athlete_id_1", "pbp_clutch_ts_pct"]]
    else:
        clutch_df = pd.DataFrame(columns=["athlete_id_1", "pbp_clutch_ts_pct"])

    # ---- Assists (athlete_id_2 on made-shot rows — confirmed via sample text rows) ----
    assist_counts = (
        pbp[pbp["scoring_play"] & pbp["shooting_play"] & pbp["athlete_id_2"].notna()]
        .groupby("athlete_id_2")
        .size()
        .reset_index(name="n_assists")
        .rename(columns={"athlete_id_2": "athlete_id_1"})
    )

    # ---- Games tracked ----
    game_counts = (
        pbp[["athlete_id_1", "game_id"]].dropna(subset=["athlete_id_1"])
        .drop_duplicates()
        .groupby("athlete_id_1")["game_id"]
        .nunique()
        .reset_index(name="games_tracked")
    )

    # ---- Merge ----
    result = identity.merge(shot_agg, on="athlete_id_1", how="left")
    result = result.merge(to_counts, on="athlete_id_1", how="left")
    result = result.merge(transition_counts, on="athlete_id_1", how="left")
    result = result.merge(clutch_df, on="athlete_id_1", how="left")
    result = result.merge(assist_counts, on="athlete_id_1", how="left")
    result = result.merge(game_counts, on="athlete_id_1", how="left")

    result["shot_attempts_tracked"] = result["shot_attempts_tracked"].fillna(0).astype(int)
    result["n_turnovers"] = result["n_turnovers"].fillna(0)
    result["possessions_tracked"] = (result["shot_attempts_tracked"] + result["n_turnovers"]).astype(int)
    result["pbp_turnover_rate"] = result["n_turnovers"] / result["possessions_tracked"].clip(lower=1)
    result["pbp_transition_rate"] = (
        result["n_transition_shots"].fillna(0) / result["shot_attempts_tracked"].clip(lower=1)
    )
    result["pbp_assist_rate"] = result["n_assists"].fillna(0) / result["possessions_tracked"].clip(lower=1)
    result["espn_athlete_id"] = result["athlete_id_1"].astype(float).astype(int).astype(str)

    log.info("player features computed for %d athletes", len(result))
    return result


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _build_school_map(session) -> dict[str, int]:
    result = await session.execute(select(School.id, School.name))
    return {row.name: row.id for row in result}


async def _build_espn_id_map(session) -> dict[str, int]:
    """players.espn_id -> players.id, for players already backfilled by the
    season-level hoopr ingest (~90% per docstring above) — game-log ingest
    tries this direct lookup before falling back to fuzzy roster matching."""
    result = await session.execute(select(Player.id, Player.espn_id).where(Player.espn_id.isnot(None)))
    return {row.espn_id: row.id for row in result}


async def _upsert(session, rows: list[dict]) -> int:
    if not rows:
        return 0
    for row in rows:
        stmt = pg_insert(HoopRTeamSeasonStats).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row if k not in ("id", "school_id", "season")}
        stmt = stmt.on_conflict_do_update(index_elements=["school_id", "season"], set_=update_cols)
        await session.execute(stmt)
    await session.commit()
    return len(rows)


async def ingest_features(
    session,
    features: pd.DataFrame,
    school_map: dict[str, int],
    season: int,
    dry_run: bool = False,
) -> int:
    db_names = list(school_map.keys())
    unmatched: list[str] = []
    records: list[dict] = []

    for _, row in features.iterrows():
        espn_name = str(row["espn_team_name"])
        canonical = _normalize_espn_name(espn_name)
        school_id = school_map.get(canonical)
        if school_id is None:
            fuzzy = _fuzzy_match(canonical, db_names)
            if fuzzy:
                school_id = school_map[fuzzy]
                log.debug("fuzzy: '%s' → '%s'", espn_name, fuzzy)
            else:
                unmatched.append(espn_name)
                continue

        records.append({
            "school_id": school_id,
            "season": season,
            "espn_team_id": str(row["espn_team_id"]) if pd.notna(row.get("espn_team_id")) else None,
            "espn_team_name": espn_name,
            "pbp_possession_sec":       _safe_float(row.get("pbp_possession_sec")),
            "pbp_rim_pct":              _safe_float(row.get("pbp_rim_pct")),
            "pbp_three_pct":            _safe_float(row.get("pbp_three_pct")),
            "pbp_mid_pct":              _safe_float(row.get("pbp_mid_pct")),
            "pbp_zone1_restricted_pct": _safe_float(row.get("pbp_zone1_restricted_pct")),
            "pbp_zone2_mid_pct":        _safe_float(row.get("pbp_zone2_mid_pct")),
            "pbp_zone3_corner3_pct":    _safe_float(row.get("pbp_zone3_corner3_pct")),
            "pbp_zone4_straight3_pct":  _safe_float(row.get("pbp_zone4_straight3_pct")),
            "pbp_zone5_wing3_pct":      _safe_float(row.get("pbp_zone5_wing3_pct")),
            "pbp_turnover_rate":        _safe_float(row.get("pbp_turnover_rate")),
            "pbp_transition_rate":      _safe_float(row.get("pbp_transition_rate")),
            "games_tracked":            int(row.get("games_tracked") or 0),
            "possessions_tracked":      int(row.get("possessions_tracked") or 0),
        })

    matched = len(records)
    total   = len(features)
    log.info(
        "teams: %d feature rows, %d matched (%.0f%%), %d unmatched",
        total, matched, 100 * matched / max(total, 1), len(unmatched),
    )
    if unmatched:
        log.warning(
            "unmatched ESPN names (add to ESPN_TEAM_ALIASES):\n  %s",
            "\n  ".join(sorted(set(unmatched))[:30]),
        )
    if dry_run:
        return total
    return await _upsert(session, records)


async def _build_roster(session, season: int) -> pd.DataFrame:
    stmt = (
        select(PlayerSeasonStats.player_id, PlayerSeasonStats.school_id, Player.full_name)
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.season == season)
        .distinct()
    )
    result = await session.execute(stmt)
    return pd.DataFrame(result.all(), columns=["player_id", "school_id", "full_name"])


def _match_player_rosters(agg: pd.DataFrame, roster: pd.DataFrame, threshold: float = 0.82) -> pd.DataFrame:
    """Fuzzy-match raw_display_name against the resolved school's roster.

    Same-name collisions on one roster are flagged (status='ambiguous'), not
    auto-matched — per docs/hoopr_integration_plan.md Phase 2 spec. No jersey
    number available in PBP text to tiebreak.
    """
    roster_by_school: dict[int, list[str]] = (
        roster.groupby("school_id")["full_name"].apply(list).to_dict()
    )
    pid_lookup = {(r.school_id, r.full_name): r.player_id for r in roster.itertuples()}

    rows = []
    for r in agg.itertuples():
        school_id = r.school_id
        if school_id is None or (isinstance(school_id, float) and np.isnan(school_id)):
            rows.append({"status": "no_school", "matched_player_id": None, "confidence": None})
            continue
        candidates = roster_by_school.get(school_id, [])
        matches = difflib.get_close_matches(r.raw_display_name, candidates, n=2, cutoff=threshold)
        if not matches:
            rows.append({"status": "unmatched", "matched_player_id": None, "confidence": None})
        elif len(matches) > 1:
            rows.append({"status": "ambiguous", "matched_player_id": None, "confidence": None})
        else:
            name = matches[0]
            conf = difflib.SequenceMatcher(None, r.raw_display_name, name).ratio()
            rows.append({
                "status": "matched",
                "matched_player_id": pid_lookup.get((school_id, name)),
                "confidence": round(conf, 3),
            })

    return pd.concat([agg.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


async def _backfill_espn_ids(session, matched: pd.DataFrame) -> int:
    """Idempotent: only fill players.espn_id if currently NULL, never overwrite.

    espn_id is unique on players. Two distinct player_id rows (e.g. duplicate
    "Trent Hudgens Jr." / "Trent Hudgens Jr" from upstream name-punctuation
    drift) can each fuzzy-match the same espn_athlete_id across seasons —
    a savepoint per row means that collision is skipped+logged, not fatal
    to the other 4000+ valid backfills in the same season's transaction.
    """
    n = 0
    n_skipped = 0
    for r in matched.itertuples():
        stmt = (
            update(Player)
            .where(Player.id == r.matched_player_id, Player.espn_id.is_(None))
            .values(espn_id=r.espn_athlete_id)
        )
        try:
            async with session.begin_nested():
                result = await session.execute(stmt)
            n += result.rowcount
        except IntegrityError:
            n_skipped += 1
            log.warning(
                "espn_id backfill skipped (collision): player_id=%s espn_athlete_id=%s already used by another player",
                r.matched_player_id, r.espn_athlete_id,
            )
    await session.commit()
    if n_skipped:
        log.info("espn_id backfill collisions skipped: %d", n_skipped)
    return n


async def _upsert_players(session, rows: list[dict]) -> int:
    if not rows:
        return 0
    for row in rows:
        stmt = pg_insert(HoopRPlayerSeasonStats).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row if k not in ("id", "espn_athlete_id", "season")}
        stmt = stmt.on_conflict_do_update(index_elements=["espn_athlete_id", "season"], set_=update_cols)
        await session.execute(stmt)
    await session.commit()
    return len(rows)


async def ingest_player_features(
    session,
    player_features: pd.DataFrame,
    school_map: dict[str, int],
    season: int,
    dry_run: bool = False,
) -> int:
    db_names = list(school_map.keys())

    def resolve_school(espn_name) -> int | None:
        if not isinstance(espn_name, str):
            return None
        canonical = _normalize_espn_name(espn_name)
        if canonical in school_map:
            return school_map[canonical]
        fuzzy = _fuzzy_match(canonical, db_names)
        return school_map.get(fuzzy) if fuzzy else None

    agg = player_features.copy()
    agg["school_id"] = agg["espn_team_name"].map(resolve_school)

    roster = await _build_roster(session, season)
    matched = _match_player_rosters(agg, roster)

    counts = matched["status"].value_counts()
    total = len(matched)
    log.info(
        "players: %d feature rows | matched %d (%.1f%%) | ambiguous %d | unmatched %d | no_school %d",
        total,
        int(counts.get("matched", 0)), 100 * counts.get("matched", 0) / max(total, 1),
        int(counts.get("ambiguous", 0)), int(counts.get("unmatched", 0)), int(counts.get("no_school", 0)),
    )

    if dry_run:
        return total

    matched_rows = matched[matched["status"] == "matched"]
    n_backfilled = await _backfill_espn_ids(session, matched_rows)
    log.info("players.espn_id backfilled: %d (idempotent — existing values untouched)", n_backfilled)

    records: list[dict] = []
    for row in matched.itertuples():
        school_id = row.school_id
        if isinstance(school_id, float) and np.isnan(school_id):
            school_id = None
        records.append({
            "player_id": row.matched_player_id if row.status == "matched" else None,
            "school_id": school_id,
            "season": season,
            "espn_athlete_id": row.espn_athlete_id,
            "raw_display_name": row.raw_display_name,
            "espn_team_name": str(row.espn_team_name) if pd.notna(row.espn_team_name) else "UNKNOWN",
            "match_confidence": row.confidence,
            "pbp_rim_pct":              _safe_float(row.pbp_rim_pct),
            "pbp_three_pct":            _safe_float(row.pbp_three_pct),
            "pbp_mid_pct":              _safe_float(row.pbp_mid_pct),
            "pbp_zone1_restricted_pct": _safe_float(row.pbp_zone1_restricted_pct),
            "pbp_zone2_mid_pct":        _safe_float(row.pbp_zone2_mid_pct),
            "pbp_zone3_corner3_pct":    _safe_float(row.pbp_zone3_corner3_pct),
            "pbp_zone4_straight3_pct":  _safe_float(row.pbp_zone4_straight3_pct),
            "pbp_zone5_wing3_pct":      _safe_float(row.pbp_zone5_wing3_pct),
            "pbp_turnover_rate":        _safe_float(row.pbp_turnover_rate),
            "pbp_transition_rate":      _safe_float(row.pbp_transition_rate),
            "pbp_clutch_ts_pct":        _safe_float(getattr(row, "pbp_clutch_ts_pct", None)),
            "pbp_assist_rate":          _safe_float(row.pbp_assist_rate),
            "shot_attempts_tracked":    int(row.shot_attempts_tracked or 0),
            "games_tracked":            int(row.games_tracked or 0),
            "possessions_tracked":      int(row.possessions_tracked or 0),
        })

    return await _upsert_players(session, records)


# ---------------------------------------------------------------------------
# Game logs (--game-logs) — schedule + box score parquet, game-level grain
# ---------------------------------------------------------------------------

def _d1_only(df: pd.DataFrame, team_id_col: str, opponent_id_col: str) -> pd.DataFrame:
    """Same both-sides-D1 filter the season-aggregate PBP ingest already applies."""
    df = df.copy()
    df["_team_id_num"] = pd.to_numeric(df[team_id_col], errors="coerce")
    df["_opp_id_num"] = pd.to_numeric(df[opponent_id_col], errors="coerce")
    return df[df["_team_id_num"].isin(D1_TEAM_IDS) & df["_opp_id_num"].isin(D1_TEAM_IDS)]


def _chunked(rows: list[dict], size: int = 1000):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


async def _bulk_upsert(session, model, rows: list[dict], conflict_cols: list[str], chunk_size: int = 1000) -> int:
    """Multi-row INSERT ... ON CONFLICT DO UPDATE per chunk, instead of one
    execute() round-trip per row — the per-row loop pattern the season-level
    ingest functions use (_upsert/_upsert_players above) doesn't scale past a
    few thousand rows; hoopr_player_game_logs is ~180K rows/season."""
    if not rows:
        return 0
    exclude = set(conflict_cols) | {"id"}
    for chunk in _chunked(rows, chunk_size):
        stmt = pg_insert(model).values(chunk)
        set_cols = {k: stmt.excluded[k] for k in chunk[0] if k not in exclude}
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=set_cols)
        await session.execute(stmt)
    await session.commit()
    return len(rows)


async def ingest_games(
    session,
    schedule: pd.DataFrame,
    school_map: dict[str, int],
    season: int,
    dry_run: bool = False,
) -> int:
    df = _d1_only(schedule, "home_id", "away_id")

    records: list[dict] = []
    unmatched: list[str] = []
    for _, row in df.iterrows():
        home_school_id = _resolve_school(str(row.get("home_location") or ""), school_map)
        away_school_id = _resolve_school(str(row.get("away_location") or ""), school_map)
        if home_school_id is None:
            unmatched.append(str(row.get("home_location")))
        if away_school_id is None:
            unmatched.append(str(row.get("away_location")))

        records.append({
            "espn_game_id": str(row["game_id"]),
            "season": season,
            "game_date": _safe_date(row.get("game_date")),
            "home_school_id": home_school_id,
            "away_school_id": away_school_id,
            "home_espn_team_id": str(row["home_id"]) if pd.notna(row.get("home_id")) else None,
            "away_espn_team_id": str(row["away_id"]) if pd.notna(row.get("away_id")) else None,
            "home_score": _safe_int(row.get("home_score")),
            "away_score": _safe_int(row.get("away_score")),
            "neutral_site": bool(row["neutral_site"]) if pd.notna(row.get("neutral_site")) else None,
            "venue": str(row["venue_full_name"]) if pd.notna(row.get("venue_full_name")) else None,
        })

    total = len(records)
    log.info(
        "games: %d D1-vs-D1 rows | %d unmatched school-location lookups",
        total, len(unmatched),
    )
    if unmatched:
        log.warning(
            "unmatched school locations (add to ESPN_TEAM_ALIASES):\n  %s",
            "\n  ".join(sorted(set(unmatched))[:30]),
        )
    if dry_run:
        return total
    return await _bulk_upsert(session, HoopRGame, records, ["espn_game_id"])


async def ingest_team_game_logs(
    session,
    team_box: pd.DataFrame,
    school_map: dict[str, int],
    season: int,
    dry_run: bool = False,
) -> int:
    df = _d1_only(team_box, "team_id", "opponent_team_id")

    records: list[dict] = []
    unmatched: list[str] = []
    for _, row in df.iterrows():
        school_id = _resolve_school(str(row.get("team_location") or ""), school_map)
        opponent_school_id = _resolve_school(str(row.get("opponent_team_location") or ""), school_map)
        if school_id is None:
            unmatched.append(str(row.get("team_location")))

        records.append({
            "espn_game_id": str(row["game_id"]),
            "season": season,
            "game_date": _safe_date(row.get("game_date")),
            "school_id": school_id,
            "espn_team_id": str(row["team_id"]),
            "opponent_school_id": opponent_school_id,
            "home_away": row.get("team_home_away"),
            "points": _safe_int(row.get("team_score")),
            "opponent_points": _safe_int(row.get("opponent_team_score")),
            "field_goals_made": _safe_int(row.get("field_goals_made")),
            "field_goals_attempted": _safe_int(row.get("field_goals_attempted")),
            "three_point_field_goals_made": _safe_int(row.get("three_point_field_goals_made")),
            "three_point_field_goals_attempted": _safe_int(row.get("three_point_field_goals_attempted")),
            "free_throws_made": _safe_int(row.get("free_throws_made")),
            "free_throws_attempted": _safe_int(row.get("free_throws_attempted")),
            "offensive_rebounds": _safe_int(row.get("offensive_rebounds")),
            "defensive_rebounds": _safe_int(row.get("defensive_rebounds")),
            "total_rebounds": _safe_int(row.get("total_rebounds")),
            # ESPN box schema has turnovers/team_turnovers/total_turnovers — "turnovers"
            # is the standard per-possession box stat; team_turnovers separately tracks
            # shot-clock/team-only violations not attributable to a player.
            "assists": _safe_int(row.get("assists")),
            "steals": _safe_int(row.get("steals")),
            "blocks": _safe_int(row.get("blocks")),
            "turnovers": _safe_int(row.get("turnovers")),
            "fouls": _safe_int(row.get("fouls")),
            "points_in_paint": _safe_int(row.get("points_in_paint")),
            "fast_break_points": _safe_int(row.get("fast_break_points")),
            "turnover_points": _safe_int(row.get("turnover_points")),
        })

    total = len(records)
    matched = total - len(unmatched)
    log.info(
        "team game logs: %d D1-vs-D1 rows, %d matched (%.0f%%), %d unmatched",
        total, matched, 100 * matched / max(total, 1), len(unmatched),
    )
    if unmatched:
        log.warning("unmatched team locations:\n  %s", "\n  ".join(sorted(set(unmatched))[:30]))
    if dry_run:
        return total
    return await _bulk_upsert(session, HoopRTeamGameLog, records, ["espn_game_id", "espn_team_id"])


async def ingest_player_game_logs(
    session,
    player_box: pd.DataFrame,
    school_map: dict[str, int],
    espn_id_map: dict[str, int],
    season: int,
    dry_run: bool = False,
) -> int:
    """player_id resolution: direct players.espn_id lookup first (covers the
    ~90% already backfilled by the season-level ingest), then the same fuzzy
    name+roster fallback (_match_player_rosters/_build_roster) the season-level
    ingest uses for its own unmatched remainder."""
    df = _d1_only(player_box, "team_id", "opponent_team_id").copy()
    df["espn_athlete_id"] = df["athlete_id"].astype(float).astype("int64").astype(str)
    df["raw_display_name"] = df["athlete_display_name"]
    df["school_id"] = df["team_location"].map(lambda loc: _resolve_school(str(loc or ""), school_map))
    df["opponent_school_id"] = df["opponent_team_location"].map(
        lambda loc: _resolve_school(str(loc or ""), school_map)
    )

    direct_mask = df["espn_athlete_id"].isin(espn_id_map)
    direct = df[direct_mask].copy()
    direct["matched_player_id"] = direct["espn_athlete_id"].map(espn_id_map)
    direct["status"] = "matched"
    direct["confidence"] = 1.0

    remainder = df[~direct_mask].copy()
    if remainder.empty:
        fuzzy = remainder.assign(status=[], matched_player_id=[], confidence=[])
    else:
        roster = await _build_roster(session, season)
        fuzzy = _match_player_rosters(remainder, roster)

    matched = pd.concat([direct, fuzzy], ignore_index=True)

    counts = matched["status"].value_counts()
    total = len(matched)
    log.info(
        "player game logs: %d D1-vs-D1 rows | matched %d (%.1f%%) | ambiguous %d | unmatched %d | no_school %d",
        total,
        int(counts.get("matched", 0)), 100 * counts.get("matched", 0) / max(total, 1),
        int(counts.get("ambiguous", 0)), int(counts.get("unmatched", 0)), int(counts.get("no_school", 0)),
    )

    if dry_run:
        return total

    newly_matched = fuzzy[fuzzy["status"] == "matched"] if not remainder.empty else remainder
    n_backfilled = await _backfill_espn_ids(session, newly_matched)
    log.info("players.espn_id backfilled from game logs: %d (idempotent)", n_backfilled)

    records: list[dict] = []
    for row in matched.itertuples():
        school_id = row.school_id if not (isinstance(row.school_id, float) and pd.isna(row.school_id)) else None
        opponent_school_id = (
            row.opponent_school_id
            if not (isinstance(row.opponent_school_id, float) and pd.isna(row.opponent_school_id))
            else None
        )
        records.append({
            "espn_game_id": str(row.game_id),
            "season": season,
            "game_date": _safe_date(row.game_date),
            "player_id": row.matched_player_id if row.status == "matched" else None,
            "espn_athlete_id": row.espn_athlete_id,
            "raw_display_name": row.raw_display_name,
            "school_id": school_id,
            "opponent_school_id": opponent_school_id,
            "home_away": getattr(row, "home_away", None),
            "starter": bool(row.starter) if pd.notna(getattr(row, "starter", None)) else None,
            "minutes": _safe_float(getattr(row, "minutes", None)),
            "field_goals_made": _safe_int(getattr(row, "field_goals_made", None)),
            "field_goals_attempted": _safe_int(getattr(row, "field_goals_attempted", None)),
            "three_point_field_goals_made": _safe_int(getattr(row, "three_point_field_goals_made", None)),
            "three_point_field_goals_attempted": _safe_int(getattr(row, "three_point_field_goals_attempted", None)),
            "free_throws_made": _safe_int(getattr(row, "free_throws_made", None)),
            "free_throws_attempted": _safe_int(getattr(row, "free_throws_attempted", None)),
            "offensive_rebounds": _safe_int(getattr(row, "offensive_rebounds", None)),
            "defensive_rebounds": _safe_int(getattr(row, "defensive_rebounds", None)),
            "rebounds": _safe_int(getattr(row, "rebounds", None)),
            "assists": _safe_int(getattr(row, "assists", None)),
            "steals": _safe_int(getattr(row, "steals", None)),
            "blocks": _safe_int(getattr(row, "blocks", None)),
            "turnovers": _safe_int(getattr(row, "turnovers", None)),
            "fouls": _safe_int(getattr(row, "fouls", None)),
            "points": _safe_int(getattr(row, "points", None)),
            "match_confidence": _safe_float(row.confidence),
            "match_status": row.status,
        })

    return await _bulk_upsert(session, HoopRPlayerGameLog, records, ["espn_game_id", "espn_athlete_id"])


def _try_s3_upload(local_path: Path, s3_key: str) -> None:
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
        log.warning("S3 upload skipped for %s: %s", local_path.name, _exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for season in args.season:
        log.info("=== season %d ===", season)

        if not args.skip_season_stats:
            if args.local_parquet:
                parquet_path = Path(args.local_parquet)
                if not parquet_path.exists():
                    log.error("local parquet not found: %s", parquet_path)
                    log.error("re-run EDA notebook to regenerate, or omit --local-parquet to download")
                    sys.exit(1)
            else:
                cached = DATA_DIR / f"play_by_play_{season}.parquet"
                alt_cache = Path("notebooks/data") / f"mbb_pbp_{season}.parquet"
                if alt_cache.exists() and not args.force_download:
                    log.info("using EDA cache: %s", alt_cache)
                    parquet_path = alt_cache
                elif cached.exists() and not args.force_download:
                    log.info("using cached parquet: %s", cached)
                    parquet_path = cached
                else:
                    parquet_path = download_parquet(season, DATA_DIR)

            log.info("loading %s", parquet_path)
            pbp = pd.read_parquet(parquet_path)
            log.info("loaded %d rows, %d cols", len(pbp), len(pbp.columns))

            features = compute_team_features(pbp)
            player_features = compute_player_features(pbp)

            if args.dry_run:
                log.info("[dry-run] features computed — no DB writes")
                show_cols = [
                    "espn_team_name", "pbp_possession_sec", "pbp_rim_pct",
                    "pbp_three_pct", "pbp_zone1_restricted_pct",
                    "pbp_zone3_corner3_pct", "games_tracked",
                ]
                print(features[[c for c in show_cols if c in features.columns]].head(30).to_string(index=False))
                show_player_cols = [
                    "raw_display_name", "espn_team_name", "pbp_rim_pct", "pbp_three_pct",
                    "pbp_clutch_ts_pct", "pbp_assist_rate", "shot_attempts_tracked", "games_tracked",
                ]
                print(player_features[[c for c in show_player_cols if c in player_features.columns]].head(30).to_string(index=False))
            else:
                async with AsyncSessionLocal() as session:
                    school_map = await _build_school_map(session)
                    n = await ingest_features(session, features, school_map, season)
                    log.info("hoopr_team_season_stats upserted: %d rows", n)

                    n_players = await ingest_player_features(session, player_features, school_map, season)
                    log.info("hoopr_player_season_stats upserted: %d rows", n_players)

                _date = datetime.now().strftime("%Y-%m-%d")
                _try_s3_upload(parquet_path, f"raw/hoopr/{_date}/play_by_play_{season}.parquet")

        if args.game_logs:
            log.info("--- game logs (season %d) ---", season)
            schedule_path = _get_or_download(season, DATA_DIR, SCHEDULE_RELEASE, args.force_download)
            team_box_path = _get_or_download(season, DATA_DIR, TEAM_BOX_RELEASE, args.force_download)
            player_box_path = _get_or_download(season, DATA_DIR, PLAYER_BOX_RELEASE, args.force_download)

            schedule_df = pd.read_parquet(schedule_path)
            team_box_df = pd.read_parquet(team_box_path)
            player_box_df = pd.read_parquet(player_box_path)
            log.info(
                "loaded schedule=%d team_box=%d player_box=%d rows",
                len(schedule_df), len(team_box_df), len(player_box_df),
            )

            _verb = "[dry-run] computed" if args.dry_run else "upserted"
            async with AsyncSessionLocal() as session:
                school_map = await _build_school_map(session)

                n_games = await ingest_games(session, schedule_df, school_map, season, dry_run=args.dry_run)
                log.info("hoopr_games %s: %d rows", _verb, n_games)

                n_team_logs = await ingest_team_game_logs(
                    session, team_box_df, school_map, season, dry_run=args.dry_run
                )
                log.info("hoopr_team_game_logs %s: %d rows", _verb, n_team_logs)

                espn_id_map = await _build_espn_id_map(session)
                n_player_logs = await ingest_player_game_logs(
                    session, player_box_df, school_map, espn_id_map, season, dry_run=args.dry_run
                )
                log.info("hoopr_player_game_logs %s: %d rows", _verb, n_player_logs)

            if not args.dry_run:
                _date = datetime.now().strftime("%Y-%m-%d")
                for p in (schedule_path, team_box_path, player_box_path):
                    _try_s3_upload(p, f"raw/hoopr/game_logs/{_date}/{p.name}")

    log.info("done")


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest hoopR ESPN PBP and game-log data into PostgreSQL")
    p.add_argument(
        "--season",
        type=int,
        action="append",
        default=None,
        metavar="YEAR",
        help="Season end year (e.g. 2026). Repeatable. Default: 2026.",
    )
    p.add_argument(
        "--local-parquet",
        metavar="PATH",
        help=(
            "Path to local .parquet file (skips download). "
            "EDA cache is at notebooks/data/mbb_pbp_2026.parquet. "
            "Single season only. Applies to the season-aggregate PBP step only."
        ),
    )
    p.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download even if a cached parquet already exists.",
    )
    p.add_argument(
        "--game-logs",
        action="store_true",
        help=(
            "Also ingest hoopr_games / hoopr_team_game_logs / hoopr_player_game_logs "
            "from schedule + box score parquet (game-level grain) for each --season."
        ),
    )
    p.add_argument(
        "--skip-season-stats",
        action="store_true",
        help="Skip the season-aggregate PBP step (hoopr_team/player_season_stats). Use with --game-logs for a game-logs-only run.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute features and print sample — no DB writes.",
    )
    args = p.parse_args()
    if args.season is None:
        args.season = [2026]
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
