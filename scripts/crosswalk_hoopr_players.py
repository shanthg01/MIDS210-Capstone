"""
scripts/crosswalk_hoopr_players.py

Phase 2 Step 1 (docs/hoopr_integration_plan.md) — validate ESPN athlete_id_1 ->
players.id crosswalk feasibility before committing to a migration.

Step 0 confirmed no athlete_display_name column exists in any cached season
(2021-2026) parquet. Player name must be parsed out of the `text` play
description field instead. This script:

  1. Parses a raw player name per PBP row from `text` (marker-based, varies by
     `type_text` — shots/rebounds/steals/blocks/turnovers have name as a
     leading prefix; fouls have name after "on").
  2. Aggregates to one (raw_name, espn_team_id) per athlete_id_1 via mode.
  3. Resolves espn_team_id -> school_id (reuses ingest_hoopr.py's team alias +
     fuzzy-match logic).
  4. Fuzzy-matches raw_name against that school's roster for the season
     (player_season_stats join players), difflib threshold 0.82 — same
     threshold ingest_hoopr.py uses for team names.
  5. Reports hit rate and prints a random N-player spot-check sample for
     manual eyeball review.

No DB writes. Validation only — players.espn_id backfill is Phase 2 Step 3.

Usage:
  uv run python scripts/crosswalk_hoopr_players.py --season 2026
  uv run python scripts/crosswalk_hoopr_players.py --season 2026 --spot-check 50
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_hoopr import D1_TEAM_IDS, _build_team_name_map, _fuzzy_match, _normalize_espn_name  # noqa: E402
from portalpoint.modeling.io import get_sync_engine

DATA_DIR = Path("data/hoopr")
MATCH_THRESHOLD = 0.82

# Marker-based name extraction — order matters, first match wins.
_LEADING_MARKERS = [
    " makes ", " misses ", " Defensive Rebound", " Offensive Rebound",
    " Steal", " Block", " bad pass", " traveling turnover", " turnover",
    " Turnover", " subbing in", " subbing out",
]
_FOUL_RE = re.compile(r"^(?:Technical )?Foul on (.+?)\.?$")


def _extract_name(type_text: str, raw_text: str) -> str | None:
    if not isinstance(raw_text, str):
        return None
    if type_text in ("PersonalFoul", "Technical Foul"):
        m = _FOUL_RE.match(raw_text)
        return m.group(1).rstrip(".") if m else None
    for marker in _LEADING_MARKERS:
        idx = raw_text.find(marker)
        if idx > 0:
            return raw_text[:idx].strip()
    return None



def build_athlete_table(pbp: pd.DataFrame) -> pd.DataFrame:
    """One row per ESPN athlete_id_1: mode raw_name, mode team_id, row count."""
    pbp = pbp[pbp["home_team_id"].isin(D1_TEAM_IDS) & pbp["away_team_id"].isin(D1_TEAM_IDS)].copy()
    pbp["raw_name"] = [
        _extract_name(t, x) for t, x in zip(pbp["type_text"], pbp["text"])
    ]
    valid = pbp[pbp["athlete_id_1"].notna() & pbp["raw_name"].notna()].copy()

    agg = (
        valid.groupby("athlete_id_1")
        .agg(
            raw_name=("raw_name", lambda s: s.value_counts().idxmax()),
            espn_team_id=("team_id", lambda s: s.value_counts().idxmax()),
            n_rows=("raw_name", "size"),
        )
        .reset_index()
    )

    team_name_map = _build_team_name_map(pbp)
    agg["espn_team_name"] = agg["espn_team_id"].astype(str).map(team_name_map)
    return agg


def resolve_school_ids(agg: pd.DataFrame, school_names: list[str], school_map: dict[str, int]) -> pd.DataFrame:
    def resolve(name: str | None) -> int | None:
        if not isinstance(name, str):
            return None
        canonical = _normalize_espn_name(name)
        if canonical in school_map:
            return school_map[canonical]
        fuzzy = _fuzzy_match(canonical, school_names)
        return school_map.get(fuzzy) if fuzzy else None

    agg = agg.copy()
    agg["school_id"] = agg["espn_team_name"].map(resolve)
    return agg


def match_rosters(agg: pd.DataFrame, roster: pd.DataFrame, threshold: float = MATCH_THRESHOLD) -> pd.DataFrame:
    """Fuzzy-match raw_name against the resolved school's roster, same season."""
    roster_by_school: dict[int, list[str]] = (
        roster.groupby("school_id")["full_name"].apply(list).to_dict()
    )
    pid_lookup = {(r.school_id, r.full_name): r.player_id for r in roster.itertuples()}

    rows = []
    for r in agg.itertuples():
        if r.school_id is None or (isinstance(r.school_id, float) and np.isnan(r.school_id)):
            rows.append({"status": "no_school", "matched_player_id": None, "matched_name": None, "confidence": 0.0})
            continue
        candidates = roster_by_school.get(r.school_id, [])
        matches = difflib.get_close_matches(r.raw_name, candidates, n=2, cutoff=threshold)
        if not matches:
            rows.append({"status": "unmatched", "matched_player_id": None, "matched_name": None, "confidence": 0.0})
        elif len(matches) > 1:
            rows.append({"status": "ambiguous", "matched_player_id": None, "matched_name": ", ".join(matches), "confidence": 0.0})
        else:
            name = matches[0]
            conf = difflib.SequenceMatcher(None, r.raw_name, name).ratio()
            rows.append({
                "status": "matched",
                "matched_player_id": pid_lookup.get((r.school_id, name)),
                "matched_name": name,
                "confidence": round(conf, 3),
            })

    result = pd.concat([agg.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    return result


def run(season: int, spot_check: int) -> None:
    parquet_path = DATA_DIR / f"play_by_play_{season}.parquet"
    if not parquet_path.exists():
        print(f"ERROR: {parquet_path} not found — run ingest_hoopr.py first")
        sys.exit(1)

    print(f"Loading {parquet_path} ...")
    pbp = pd.read_parquet(parquet_path)
    print(f"Loaded {len(pbp):,} rows")

    agg = build_athlete_table(pbp)
    print(f"\nUnique ESPN athletes (D1, name-extractable): {len(agg):,}")

    engine = get_sync_engine()

    with engine.connect() as conn:
        schools = pd.read_sql(text("SELECT id, name FROM schools"), conn)
        roster = pd.read_sql(
            text(
                "SELECT DISTINCT pss.player_id, pss.school_id, p.full_name "
                "FROM player_season_stats pss "
                "JOIN players p ON p.id = pss.player_id "
                "WHERE pss.season = :season"
            ),
            conn,
            params={"season": season},
        )
    print(f"DB roster rows for season {season}: {len(roster):,}  ({roster['school_id'].nunique()} schools)")

    school_map = dict(zip(schools["name"], schools["id"]))
    agg = resolve_school_ids(agg, list(school_map.keys()), school_map)
    n_no_school = agg["school_id"].isna().sum()
    print(f"Athletes with unresolved school: {n_no_school:,} / {len(agg):,}")

    result = match_rosters(agg, roster)

    print("\n=== Crosswalk hit rate ===")
    counts = result["status"].value_counts()
    total = len(result)
    for status in ["matched", "ambiguous", "unmatched", "no_school"]:
        n = int(counts.get(status, 0))
        print(f"  {status:<10s}: {n:6,d}  ({n/total:.1%})")
    print(f"  TOTAL     : {total:6,d}")

    matched_pct = counts.get("matched", 0) / total
    print(f"\nMatch rate: {matched_pct:.1%}  (target ~90%)")

    if spot_check > 0:
        sample = result[result["status"] == "matched"].sample(
            n=min(spot_check, (result["status"] == "matched").sum()), random_state=42
        )
        print(f"\n=== Spot check (N={len(sample)}) ===")
        print(
            sample[["raw_name", "matched_name", "confidence", "espn_team_name", "n_rows"]]
            .sort_values("confidence")
            .to_string(index=False)
        )

        amb = result[result["status"] == "ambiguous"]
        if len(amb):
            print(f"\n=== Ambiguous (sample of {min(10, len(amb))}, flagged not auto-matched) ===")
            print(amb[["raw_name", "matched_name", "espn_team_name"]].head(10).to_string(index=False))

        unm = result[result["status"] == "unmatched"]
        if len(unm):
            print(f"\n=== Unmatched (sample of {min(10, len(unm))}) ===")
            print(unm[["raw_name", "espn_team_name", "n_rows"]].sort_values("n_rows", ascending=False).head(10).to_string(index=False))


def main() -> None:
    p = argparse.ArgumentParser(description="Validate ESPN athlete -> players.id crosswalk feasibility")
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--spot-check", type=int, default=50)
    args = p.parse_args()
    run(args.season, args.spot_check)


if __name__ == "__main__":
    main()
