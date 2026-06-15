# hoopR Integration Plan

## What hoopR Provides

ESPN play-by-play data via [sportsdataverse](https://github.com/sportsdataverse/sportsdataverse-data/releases).
~2.9M rows per season, 57 columns per event. All 365 D1 teams covered.

**Key columns used:**

| Column | Used for |
|---|---|
| `type_text` | event type (LayUpShot, DunkShot, JumpShot, Turnover, …) |
| `shooting_play` | filter to shot attempts |
| `points_attempted` | 2 vs. 3 detection |
| `coordinate_x/y` | half-court spatial location (feet, basket at 25, 4.75) |
| `start/end_period_seconds_remaining` | possession timing |
| `team_id` | ESPN team ID → mapped via `home/away_team_name` → school_id |
| `home_team_id/name`, `away_team_id/name` | build ESPN ID→name map |

---

## Model Relevance

| Model | Relevance | What hoopR adds |
|---|---|---|
| **M2 Team System Clustering** | **HIGH** | `pbp_possession_sec` more granular than `adj_tempo`; 5 spatial zones expand feature space beyond barttorvik's 3 shot-type buckets |
| **M3 Scheme Fit Scorer** | **HIGH** | Direct spatial fit: compare player's zone tendencies to team's zone usage; extends scheme vector from 5-dim to include restricted area vs. corner 3 split |
| **M5 Transfer Success** | **MEDIUM (Phase 2)** | Player clutch performance, shot quality by zone — requires ESPN↔barttorvik player ID crosswalk |
| **M4 Playing Time** | **LOW** | No per-player minutes data in PBP beyond shot counts |

---

## Engineered Features (Team-Level, Phase 1)

All features stored in `hoopr_team_season_stats` (~365 rows/season).

| Feature | Source | Notes |
|---|---|---|
| `pbp_possession_sec` | avg time per offensive possession (s) | computed from shot + turnover timing; valid range 1–35s |
| `pbp_rim_pct` | LayUp/Dunk/Tip shots + shots within 4ft of rim | overlap with barttorvik rim_rate but event-level |
| `pbp_three_pct` | `points_attempted == 3` / total shots | |
| `pbp_mid_pct` | 1 - rim - three | |
| `pbp_zone1_restricted_pct` | shots within 4ft of basket | restricted area attempts |
| `pbp_zone2_mid_pct` | 2PT shots outside restricted area | |
| `pbp_zone3_corner3_pct` | 3PT where `coordinate_y < 7.5` | corner 3 identity |
| `pbp_zone4_straight3_pct` | above-break 3PT, center | `|x - 25| < 8.5` |
| `pbp_zone5_wing3_pct` | above-break 3PT, wing | |
| `pbp_turnover_rate` | turnovers / tracked possessions | |
| `pbp_transition_rate` | shots within 7s of possession start / total shots | fast-break identity |

**Zone geometry (ESPN coordinates, feet, half-court):**
- Basket at `(RIM_X=25.0, RIM_Y=4.75)`
- Zone 1: `dist_from_rim < 4.0`
- Zone 2: `dist_from_rim >= 4.0` and not 3PT
- Zone 3: `3PT and y < 7.5` (corner)
- Zone 4: `3PT and y >= 7.5 and |x-25| < 8.5` (above break, center)
- Zone 5: `3PT and y >= 7.5 and |x-25| >= 8.5` (above break, wing)

---

## Join Strategy

**Team-level (Phase 1 — clean):**
ESPN `team_id` → `home/away_team_name` map built from same parquet → fuzzy match → `schools.name` → `school_id`.
Same difflib approach as `ingest_hoop_explorer.py` (threshold 0.82).
Alias map `ESPN_TEAM_ALIASES` in `ingest_hoopr.py` handles common divergences.

**Player-level (Phase 2 — deferred):**
ESPN `athlete_id_1` (~5.1M range) has no direct barttorvik crosswalk.
Strategy: name + team + season fuzzy match (same as HE player match), expect ~90% hit rate.
Requires `players.espn_id` to be populated (currently nullable but not ingested).

---

## Infrastructure Changes

### New DB table: `hoopr_team_season_stats`

See `alembic/versions/c1e8f4a2b5d3_add_hoopr_team_season_stats.py`.
ORM: `src/portalpoint/db/models.py` → `HoopRTeamSeasonStats`.

### New ingest script: `scripts/ingest_hoopr.py`

```bash
# Download season parquet, compute features, upsert to DB, upload raw parquet to S3
uv run python scripts/ingest_hoopr.py

# Use locally cached parquet (skip download)
uv run python scripts/ingest_hoopr.py --local-parquet data/hoopr/mbb_pbp_2026.parquet

# Dry run — print feature table, no DB writes
uv run python scripts/ingest_hoopr.py --dry-run

# Multiple seasons
uv run python scripts/ingest_hoopr.py --season 2025 --season 2026
```

**Data flow:**
```
GitHub releases (parquet, ~120MB)
    → data/hoopr/play_by_play_{season}.parquet  (local cache)
    → aggregate to ~365 rows
    → hoopr_team_season_stats  (PostgreSQL)
    → s3://portalpoint-data/raw/hoopr/YYYY-MM-DD/play_by_play_{season}.parquet
```

Raw PBP is NOT stored in PostgreSQL (2.9M rows × season with no row-level query use case).

### Feature engineering notebook: `notebooks/features/feature_eng_m1_m2_m3.ipynb`

New SQL query `HOOPR_TEAM_SQL` added to Section 1 (Load Data from DB).
New Section 6 (hoopR PBP Enrichment) left-joins `pbp_*` columns into `team_style`.
Graceful fallback if table not yet populated — adds NULL columns, prints run instruction.
Parquet export unchanged — new columns are automatically included.

### Airflow DAG

Add `ingest_hoopr.py` to `daily_data_ingestion_dag` (2 AM EST).
hoopR parquets are stable historical releases; downloading and re-processing is idempotent.

---

## Execution Order

```
1. alembic upgrade head          # creates hoopr_team_season_stats
2. ingest_hoopr.py               # populate features
3. feature_eng_m1_m2_m3.ipynb   # re-run (picks up hoopR columns)
4. team_clustering.ipynb         # re-run with expanded feature set
5. scheme_fit_scorer.ipynb       # update scheme vector to include spatial zones
```

---

## Phase 2: Player-Level Features (deferred)

Requires:
1. ESPN↔barttorvik player crosswalk (name + team + season fuzzy match — est. ~90% hit rate)
2. `players.espn_id` populated from hoopR data
3. New table `hoopr_player_season_stats`

Features to add per player:
- `pbp_player_zone_pct_*` — shot zone distribution (5 zones)
- `pbp_player_clutch_ts_pct` — TS% in last 2min, ≤5pt margin
- Feed into M5 (transfer success predictor) as additional features

---

## Open Questions

1. **Coordinate system validation** — EDA confirms 365 teams covered but did not
   audit `(RIM_X=25.0, RIM_Y=4.75)` assumption across all ESPN game records.
   Run `--dry-run` and spot-check zone ratios vs. known barttorvik rim/3PT rates.

2. **OT periods** — possession timing valid across OT periods; `period > 2` included
   but `start_period_seconds_remaining` resets each period. Current logic handles this
   via per-play elapsed time.

3. **2025 season backfill** — hoopR releases are available for prior seasons.
   `--season 2025 --season 2026` ingests both; team clustering benefits from multi-year
   stability check before expanding to 3+ seasons.

4. **Scheme vector dimension** — Model 3 currently uses 5-dim barttorvik vector
   (`three_pct, rim_pct, usage, assisted_pct, pace`). Adding hoopR zones makes it
   10-dim. Validate cosine similarity still discriminates before shipping to production.
