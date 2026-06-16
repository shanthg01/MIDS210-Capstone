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

**Fixed 2026-06-16:** Phase 2 Step 1 crosswalk validation surfaced 30 unmatched D1 schools
(see `STATUS.md`) — "X State" vs. "X St." below the fuzzy cutoff, a wrong `North Carolina`
alias, wrong `ULM`/`Alcorn` entries, and a stray `SE Louisiana → Louisiana` fuzzy collision
that was silently overwriting Louisiana's row on every ingest. All fixed in
`ESPN_TEAM_ALIASES`; `hoopr_team_season_stats` re-ingested for 2026 — 364/365 matched
(`IU Indianapolis` is a genuine missing-school gap, not a bug).

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

## Phase 2: Player-Level Features (planned, not started)

### Step 0 — verify schema before building

Team-level crosswalk worked because `home_team_id/name` sit directly in the parquet.
Player side may NOT have an equivalent `athlete_display_name` column. Inspect the
2026 parquet schema for any `athlete_*name*` column first. If absent, fall back to
parsing player name out of the `text` play-description field (ESPN commentary embeds
names: "Jalen Smith makes 2-pt jumper"). Do this before writing crosswalk logic —
avoids a repeat of the `points_attempted` / timing-column schema-drift bugs hit on
the team-level 2021 backfill.

### Crosswalk strategy

`players.espn_id` (nullable, unique) already exists on the model — no migration
needed for the crosswalk slot itself, only for the new stats table.

- Match key: `(full_name, team_id → school_id, season)` fuzzy match, same
  `difflib.SequenceMatcher` threshold (0.82) used for team names.
- Target hit rate: ~90% (unchanged from original estimate).
- Same-name collisions on one roster: flag, don't auto-match. Use jersey number as
  tiebreak if available; else leave unmatched for manual review.
- Store a match-confidence score on the row — lets feature-eng filter low-confidence
  joins later without re-running the match.
- Idempotent: only fill `players.espn_id` if currently NULL; never overwrite an
  existing match on re-run.

### Testing & evaluation

- Run crosswalk on 2026 first (fully ingested already) before widening to other seasons.
- Manual spot check: sample N=50 matched players against known rosters.
- Unmatched players: write the stats row anyway with `player_id = NULL`,
  `espn_athlete_id` populated, raw name/team stored for later manual backfill —
  same pattern `hoop_explorer_player_stats` already uses for `he_player_code`.
- Log match-rate stats (X/Y matched, pct) every ingest run so a future regression
  is visible immediately instead of silently degrading.

**Shipped 2026-06-16 (season 2026 only):** `compute_player_features()` added to
`ingest_hoopr.py`, keyed on `athlete_id_1`. Real DB run on 2026 — 4990 athletes,
90.0% matched (4493), 4 ambiguous, 478 unmatched, 15 no-school; `players.espn_id`
backfilled for 4490 (idempotent NULL-only update, 3 fewer than matched due to
duplicate matched_player_id across distinct ESPN athlete rows — expected, not a
bug); zero `(espn_athlete_id, season)` collisions; `espn_team_name` always
populated (no NULLs) per the unmatched-row manual-backfill requirement.
`pbp_clutch_ts_pct`/`pbp_assist_rate` and `possessions_tracked` use the
no-lineup-tracking proxies documented in the function docstring (FGA-only TS%
denominator, FGA+TOV possession proxy) — see `compute_player_features()` for
the full reasoning.

**2021-2025 backfill shipped 2026-06-16:** first attempt crashed mid-2021 on a
`players_espn_id_key` unique violation — two distinct `players` rows for the
same human ("Trent Hudgens Jr." vs "Trent Hudgens Jr", punctuation drift from
upstream ingests) both fuzzy-matched to the same `espn_athlete_id` across
season runs. Fixed by wrapping each `_backfill_espn_ids` row in a SAVEPOINT
(`session.begin_nested()`) so a collision is logged+skipped, not fatal to the
season's transaction. Re-ran clean, all 5 seasons:

| Season | Teams matched | Player rows | Player match % | espn_id collisions skipped |
|---|---|---|---|---|
| 2021 | 174/174 | 4,734 | 87.2% | 2 |
| 2022 | 234/235 | 4,855 | 90.1% | 10 |
| 2023 | 184/185 | 4,889 | 90.1% | 18 |
| 2024 | 172/172 | 4,853 | 91.8% | 28 |
| 2025 | 363/364 | 4,964 | 90.9% | 4 |
| 2026 | 364/365 | 4,990 | 90.0% | — (ran before the fix existed) |

Verified via direct SQL: zero `(espn_athlete_id, season)` duplicates, zero
`players.espn_id` duplicates, zero empty `espn_team_name` across all 6
seasons.

**Data-completeness caveat (new finding, not a bug):** `hoopr_team_season_stats`
team coverage is roughly half of D1 for 2021-2024 (172-235 teams) vs. near-full
for 2025-2026 (363-364 teams) — ESPN's PBP tracking clearly expanded coverage
in the last two seasons. Multi-season models (M1/M2) pooling 2021-2026 will see
much sparser hoopR features for the earlier years; barttorvik/Hoop Explorer
coverage is unaffected since they're independent sources. Flag this when
validating cosine discrimination for the 10-dim scheme vector (Open Question 4).

### New table: `hoopr_player_season_stats`

Mirrors the full breadth of `hoopr_team_season_stats` (parity-by-default — anything
aggregated at team level from PBP gets aggregated at player level too via the same
groupby logic, just keyed on `athlete_id_1` instead of `team_id`), plus player-only
additions and volume/coverage columns:

| Column | Source | Notes |
|---|---|---|
| `player_id` | crosswalk | FK → `players.id`, nullable |
| `espn_athlete_id` | PBP `athlete_id_1` | |
| `season` | | |
| `raw_display_name` | PBP/text-parsed | for manual backfill on unmatched rows |
| `match_confidence` | crosswalk | fuzzy-match score |
| `pbp_rim_pct` / `pbp_three_pct` / `pbp_mid_pct` | mirrors team table | |
| `pbp_zone1_restricted_pct` … `pbp_zone5_wing3_pct` | mirrors team table | 5 spatial zones |
| `pbp_turnover_rate` | mirrors team table | |
| `pbp_transition_rate` | mirrors team table | |
| `pbp_clutch_ts_pct` | new | TS% last 2min, ≤5pt margin |
| `pbp_assist_rate` | new | `athlete_id_2` is the assister column in PBP — cheap groupby, playmaking signal team table can't have |
| `shot_attempts_tracked` | new | raw n, not just a rate — needed because per-player samples are much smaller than per-team |
| `games_tracked` / `possessions_tracked` | mirrors team table coverage metadata | |

**Why volume/coverage matters more here than at team level:** a team season is
~30 games of stable rates; a bench player's season can be 5 games. A zone% built
from 8 shot attempts is noise, not signal. Storing `shot_attempts_tracked` alongside
the rate lets M1 clustering and the future Bayesian Role Fit model (M4) weight or
filter low-n players instead of treating them as equally confident — directly
addresses Open Design Question 1 in `CLAUDE.md` (limited data for players < 10 games).

Unique constraint: `(espn_athlete_id, season)`.

### Ingestion pipeline

Extend `ingest_hoopr.py` (not a new script — already holds the PBP parsing/zone
logic in memory for the season):
1. Build athlete crosswalk per season → upsert `players.espn_id`.
2. Aggregate player-level features (table above) → upsert `hoopr_player_season_stats`.
3. Run immediately after the existing team-level aggregation in the same pass —
   no separate CLI flag needed, same parquet already loaded.

### Feature engineering notebook: `feature_eng_m1_m2_m3.ipynb`

New section: left-join `hoopr_player_season_stats` onto `player_season_stats` by
`player_id + season`. Unmatched players (NULL `player_id`) drop out naturally via
the join. Adds `pbp_player_*` columns to `player_features.parquet`.

### Modeling notebooks

- **M1 (`player_clustering.ipynb`)**: add new features to clustering input, re-check
  silhouette at k=9 (dimensionality changes, k may shift), log as new MLflow run —
  `maybe_promote()` handles compare-and-promote automatically.
- **M5 (Transfer Success, not yet built)**: `pbp_clutch_ts_pct` and `pbp_assist_rate`
  are the intended SHAP-relevant features once M5 is built — not in scope to build
  M5 now, just confirm these columns land in the feature table so M5 isn't blocked later.

### Execution order

```
0. Inspect 2026 parquet schema for player-identity columns          ✅ done — no athlete_*name* col, text field used
1. Build crosswalk fn, run on 2026, spot-check N=50                  ✅ done — 89.8% match rate (scripts/crosswalk_hoopr_players.py)
2. If ~90% hit rate confirmed → write Alembic migration + ORM model  ✅ done — e47b1d6a9c52, HoopRPlayerSeasonStats
3. Extend ingest_hoopr.py (crosswalk + aggregation), re-run all seasons 2021+  ✅ done — all 6 seasons (2021-2026)
4. Extend feature_eng_m1_m2_m3.ipynb, regenerate player_features.parquet         ✅ done — see below
5. Extend player_clustering.ipynb, new MLflow run, compare/promote
6. Update STATUS.md + dataflow_diagram.mmd once shipped
```

**Step 4 results:** Added `HOOPR_PLAYER_SQL` (Section 1) joined onto `feat_df` keyed on
`player_id + season`, matched rows only (`player_id IS NOT NULL` filtered upstream — unmatched
ESPN athletes can't join onto `player_season_stats` anyway). 9 duplicate `(player_id, season)`
keys (fuzzy-match collisions in the crosswalk) deduped by `match_confidence` then
`shot_attempts_tracked` before merge, so the join can't fan out player rows.

New columns are raw-enrichment-only — not folded into `MODEL_FEATURES`, not scaled, not PCA'd.
Whether to add them to the K-Means vector is decided in Step 5 (`player_clustering.ipynb`).

Regenerated and re-uploaded both output parquets (pooled across all 6 seasons, 2021-2026):

| Output | Rows | Cols | Notes |
|---|---|---|---|
| `player_features.parquet` | 23,913 | 44 | hoopR player coverage: 6,950/23,913 (29%) — tracks the ESPN PBP coverage gap (Open Question 3); 16,606 of the matched rows are low-volume (< 50 tracked shot attempts) |
| `team_style_vectors.parquet` | 2,154 | 46 | unchanged row count — Step 4 only added player-level columns |

Both uploaded to `s3://portalpoint-data/raw/features/`.

**Bug found + fixed during this run:** `_to_parquet_safe()`'s raw-Python-list pyarrow writer
inferred column type from list order — a column mixing `NaN` with strings (e.g. `conference`
NULL for some pooled-season teams) made `pa.array()` guess `double` from leading NaNs, then
fail on the first string (`ArrowInvalid: Could not convert 'B12'...`). Fixed by replacing NaN
with `None` before list conversion — `None` is a universal null marker for `pa.array`'s type
inference regardless of column dtype, so it can't be misled by element order.

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
   stability check before expanding to 3+ seasons. Notebook side is ready: `feature_eng_m1_m2_m3.ipynb`
   queries `SEASONS = range(2021, 2027)`, and M1/M2 pool whatever seasons are present in
   `player_features.parquet`/`team_style_vectors.parquet` rather than a hardcoded single
   season. Remaining work is the actual ingest backfill (barttorvik + HE + hoopR for
   2021–2025), not notebook changes.

4. **Scheme vector dimension** — Model 3 currently uses 5-dim barttorvik vector
   (`three_pct, rim_pct, usage, assisted_pct, pace`). Adding hoopR zones makes it
   10-dim. Validate cosine similarity still discriminates before shipping to production.
