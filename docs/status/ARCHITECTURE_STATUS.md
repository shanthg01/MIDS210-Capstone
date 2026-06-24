# PortalPoint Architecture Status

**Last updated:** June 23, 2026
**Scope:** Infrastructure, data stores, database schema, ingest, S3/MLflow, and runbook context.

Model-specific context lives in [`MODEL_STATUS.md`](MODEL_STATUS.md). Product/API/frontend context lives in
[`APPLICATION_STATUS.md`](APPLICATION_STATUS.md).

---

## Deployment Stance

PortalPoint is local-first until beta.

| Layer | Current approach | Cloud / shared path |
|---|---|---|
| App database | Docker Postgres on port `5433` | Supabase Postgres optional for shared dev/staging |
| Cache | Docker Redis on port `6379` | Defer until real fit-score cache is needed |
| API | Local FastAPI via `uvicorn` | EC2/ECS deferred |
| Raw/model storage | Local gitignored `data/` plus S3 | `s3://portalpoint-data/` |
| MLflow tracking metadata | Local `mlruns.db` SQLite | Could move to hosted DB or MLflow server later |
| MLflow artifacts | Local/S3 depending script/notebook setup | `s3://portalpoint-data/mlflow/` |

No EC2/ECS container deployment is planned before beta. GitHub Actions cron is preferred before Airflow; Airflow remains a later option if orchestration complexity justifies it.

---

## Local Runbook

Start local infrastructure:

```bash
docker compose up -d
```

Install and migrate:

```bash
uv sync
uv run alembic upgrade head
```

Start backend:

```bash
uv run uvicorn portalpoint.main:app --reload
```

Start frontend:

```bash
cd frontend
npm install
npm run dev
```

Useful URLs:

| Service | URL |
|---|---|
| Frontend | `http://localhost:5173` |
| API root | `http://localhost:8000` |
| Swagger | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |
| Health | `http://localhost:8000/health` |

---

## Environment

Copy `.env.example` to `.env`. Local defaults are intended to work with Docker Compose.

Important values:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async SQLAlchemy URL for app/runtime. Local default uses Postgres `localhost:5433`. |
| `REDIS_URL` | Redis URL for future caching. |
| `JWT_SECRET` | Required for auth token signing. |
| `JWT_EXPIRY_SECONDS` | Increase locally if frequent re-login is annoying. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Shared S3 access keys. Never commit. |
| `AWS_DEFAULT_REGION` | `us-east-1`. |
| `S3_BUCKET` | `portalpoint-data`. |
| `MLFLOW_TRACKING_URI` | SQLite or MLflow server URL; do not use an `s3://` URI. |

S3 onboarding guide: [`../aws_s3_setup.md`](../aws_s3_setup.md).

---

## Database

| Component | State |
|---|---|
| Database | PostgreSQL 15 via Docker |
| ORM | SQLAlchemy |
| Migrations | Alembic |
| Async app access | `postgresql+asyncpg://...` |
| Modeling sync access | `src/portalpoint/modeling/io.py` converts the async app URL to a sync psycopg2 URL for scripts/notebooks |

Applied migration chain:

| Migration | Purpose |
|---|---|
| `064d7a23e792` | Initial schema |
| `b683e0eae93e` | Add `barttorvik_id` to players |
| `4f15ed03ddbf` | Pivot to program-facing user model |
| `4d2553a387cc` | Expanded BartTorvik player/team fields |
| `a3f7b2c9e1d0` | Hoop Explorer tables |
| `c1e8f4a2b5d3` | hoopR team season stats |
| `e47b1d6a9c52` | hoopR player season stats |
| `f2a9c3d7e841` | HE `pos_confidence_*` (player) + `off/def_trans_pct/ppp`, `off/def_scramble_pct/ppp` (team) |
| `d3b7e2a1c498` | `player_season_stats.min_pct` — barttorvik team minutes % (replaces broken `minutes_per_game` as MPG filter) |
| `b5d2e9f4` | `player_team_fit_scores.season` — enables multi-season fit score storage; `uq_fit_score` rebuilt on `(player_id, school_id, season)` |
| `9c8b7a6d5e4f` | Adds `archetype_memberships` (player_archetypes) + `offense_memberships`/`defense_memberships`/`system_memberships` (team_system_profiles) — all JSONB, top-three soft cluster memberships |
| `2f6a1c9d8b30` | Adds `off_ast_rim/mid/threep` + `def_ast_rim/mid/threep` to `hoop_explorer_team_stats` — **migration only adds columns; must re-run `ingest_hoop_explorer.py --all-seasons` to populate them** (confirmed: columns were 100% NULL until ingest was rerun) |
| `7a3e2d1c9b44` | Expands `team_system_profiles.system_label` from VARCHAR(50) to VARCHAR(100) — needed for M2's new combined `"{offense} / {defense}"` label format (max observed: 63 chars) |
| `d8e5c2a9f163` | Adds `hoopr_games`, `hoopr_team_game_logs`, `hoopr_player_game_logs` (game-level grain — Issue #17 items 1-2) |
| `f4a7c1e9b026` | Adds `transfer_portal_events`, `roster_snapshots`, `roster_snapshot_players` (Issue #17 items 3-4); adds `uq_transfers_player_season` unique constraint to `transfers` (enables upsert) |
| `b9c3f7a2d514` | Adds `roster_state_features` (Issue #17 item 6) |
| `c7f1a9d3e652` | Adds `player_team_fit_scores.is_portal_candidate` + index |
| `d4e8b1f3a927` | Adds `roster_baseline_members` |
| `e6a2c8f1b734` | Adds `player_projections` (Player Projection Phase 0) — two partial unique indexes (`WHERE school_id IS NULL` / `IS NOT NULL`) instead of one `UniqueConstraint`, since `school_id` is nullable and Postgres treats every `NULL` as distinct under a normal unique constraint |
| `f1c4a8d3e570` | Adds `hoop_explorer_player_stats.off_adj_rapm_prod`/`def_adj_prod_rapm`/`adj_rapm_prod_margin`/`off_adj_rapm_pred`/`def_adj_rapm_pred` — **migration only adds columns; must re-run `ingest_hoop_explorer.py --all-seasons` to populate.** Confirmed after rerun: only `off_adj_rapm_prod`/`adj_rapm_prod_margin` actually populate (100%) — the other 3 are empty in HE's raw export across all 6 seasons, an HE export-configuration limit, not an ingest bug |

Important tables:

| Table | Purpose |
|---|---|
| `players`, `schools` | Core entities |
| `player_school_seasons` | Player-team-season linkage |
| `player_season_stats` | BartTorvik normalized player stats |
| `team_season_stats` | BartTorvik normalized team stats |
| `hoop_explorer_player_stats` | HE player exports — ~16,750 rows (6 seasons 2021-2026, all D1); includes RAPM, 15 play-type pcts, `pos_confidence_pg/sg/sf/pf/c` |
| `hoop_explorer_team_stats` | HE team exports — ~2,170 rows (6 seasons 2021-2026); includes 12 off/def play-type pcts, `off/def_trans_pct/ppp`, `off/def_scramble_pct/ppp` |
| `hoopr_team_season_stats` | ESPN PBP-derived team features |
| `hoopr_player_season_stats` | ESPN PBP-derived player features (87-92% crosswalk per season, 2021-2026) |
| `hoopr_games` | One row per ESPN game (schedule parquet) — `home/away_school_id`, scores, `neutral_site` |
| `hoopr_team_game_logs` | One row per team per game (team box score parquet) |
| `hoopr_player_game_logs` | One row per player per game (player box score parquet); `player_id` resolved via `players.espn_id` first, fuzzy roster match second |
| `player_archetypes` | M1 cluster assignments; `archetype_memberships` JSONB (top-3 soft memberships) |
| `team_system_profiles` | M2 cluster assignments; two-layer (`offense_cluster_id`/`defense_cluster_id`) plus `offense_memberships`/`defense_memberships`/`system_memberships` JSONB |
| `player_team_fit_scores` | Scheme/gap/role/program/overall fit scores; multi-season (`season` col, 1.34M rows 2021-2026); `scheme_fit`+`gap_match` real, `role_fit`/`program_fit` stubbed |
| `transfer_portal_events` | Raw 247Sports scrape staging — every scraped row, matched or not; `player_id` nullable; `portal_entry_date`/`commitment_date` fill in incrementally across repeated scrapes |
| `transfers` | Promoted transfer records (matched rows only) — `(player_id, season)` unique, supports upsert; backfills `pre_per`/`pre_minutes_per_game`/`pre_usage_rate` from `player_season_stats` |
| `roster_snapshots` / `roster_snapshot_players` | Point-in-time roster composition per school per scrape date (barttorvik `rostercast.php`); `returning_status` (`returning`/`transfer_in`/`new`) computed by diffing against `player_season_stats`, not given by the source |
| `roster_state_features` | One row per `roster_snapshots` row — derived facts (counts/sums, not gap scores): returning/departing/incoming minutes+usage by position (as min_pct share — see note below), returning production/impact, class balance, archetype counts. Built by `scripts/build_roster_state_features.py` (plain script, not a model — no MLflow) |
| `roster_baseline_members` | Shared roster-membership snapshot consumed by Gap Matching (`gap-cos-v4`) and intended for Role Fit/Team Rating Projection too — see `portalpoint.modeling.roster_baseline` |
| `player_projections` | **Real, 27,047 rows (2021-2026), Player Projection Phase 0.** Neutral-mode only (`school_id` NULL) — destination mode is Phase 2+, gated on Role Fit. Written by `scripts/run_player_projection.py` / `notebooks/models/player_projection_state_space.ipynb`; served by `GET /api/players/{id}/projection` |
| `predictions` | Future transfer success outputs |
| `team_rating_projections` | Future team impact outputs |
| `recommendations` | Future ranked player recommendations |
| `users`, `user_preferences`, `user_shortlists` | Program-facing app state |

**Known data gaps:** `player_school_seasons` is still empty (0 rows) — no VerbalCommits ingest yet. `transfers` backfill is done for 2021-2026 (499/628/774/1,037/1,346/1,251 rows respectively, confirmed 2026-06-23). Season 2020 was scraped (371 raw rows in `transfer_portal_events`) but produced zero matches — all rows landed `match_status='no_school'`; `transfers` has no 2020 rows and this needs separate investigation, not a rerun of the same command. **Game-level data backfill is done (2026-06-23):** `hoopr_player_game_logs`/`hoopr_games`/`hoopr_team_game_logs` now cover all 7 seasons 2020-2026 (player-game row counts: 162,813 / 121,547 / 171,896 / 176,962 / 180,527 / 184,504 / 178,108 — 1,176,237 total). This was blocked mid-backfill by an unrelated stray Postgres session that had been `idle in transaction` since before the backfill even started, silently holding a lock on `hoopr_games` inserts — `pg_terminate_backend()` on that session unblocked it; not a backfill-script bug.

**`players.position` was hardcoded `'G'` for all players, now fixed (2026-06-23):** root cause was a literal placeholder in `ingest_barttorvik.py` predating this fix window (`"position": "G",  # ... update from cbbpy later`). A real fix (`_infer_position()`, maps barttorvik role + height to PG/SG/SF/PF/C) landed 2026-06-21 (commit `5be701e`) but the ingest was never re-run afterward. Reran `ingest_barttorvik.py --seasons 2021 2022 2023 2024 2025 2026` — confirmed real distribution (SG=5172, C=3947, PG=1874, SF=1354, PF=956). This also un-stalled `gap_matching.py`'s `players_position` fallback layer (previously dead — `'G'` can never match `"PG"`/etc.).

**Gap Matching is now `gap-cos-v4`, all-pairs with shared roster baseline (2026-06-22 branch):** scoring still covers every eligible player×school×season pair, but team gap vectors no longer use raw `player_season_stats` plus a narrow portal-departure subtraction. `scripts/run_gap_matching.py` now builds roster vectors through `portalpoint.modeling.roster_baseline`: historical seasons use `player_season_stats(S+1)` to infer the actual target roster outlook; the latest season uses latest `roster_snapshots` when available, with same-season stats minus expected departures (`transfers`, HE `transfer_dest='NBA'`, senior/graduate class markers) for schools without usable snapshots. `player_team_fit_scores.is_portal_candidate` (migration `c7f1a9d3e652`) still flags portal candidates via `portalpoint.modeling.availability`; this is intentionally separate from roster-baseline membership.

**Critical gotcha — `players.id` is not portable across environments (discovered 2026-06-19).** It's a local Postgres auto-increment surrogate key, not a stable identifier. Committed `data/features/*.parquet` files embed raw `player_id` integers — if they were built against a *different* local DB's `players` table (e.g. a teammate's machine, even running identical ingest code), those integers mean nothing on your machine. Confirmed in practice: a committed `player_features.parquet` had 4,781 of 8,696 distinct `player_id`s (55%) missing from a different machine's `players` table, causing a `ForeignKeyViolation` on `player_archetypes` insert. **Fix:** regenerate `data/features/*.parquet` locally via `feature_eng_m1_m2_m3.ipynb` (it queries the live DB directly — `JOIN players p ON p.id = pss.player_id` — so regenerated output always matches your local `players` table) before running M1/M2 after pulling someone else's parquet commit. Do not trust a pulled parquet file's `player_id`s without regenerating.

---

## S3 Bucket

Bucket: `s3://portalpoint-data`  
Region: `us-east-1`  
Access: IAM group `PortalPoint-Dev`, one programmatic user per teammate.

Bucket layout:

```text
s3://portalpoint-data/
  raw/barttorvik/YYYY-MM-DD/
  raw/hoop_explorer/YYYY-MM-DD/
  raw/hoopr/YYYY-MM-DD/
  raw/hoopr/game_logs/YYYY-MM-DD/
  raw/features/
  models/player_clustering/
  models/team_clustering/
  models/transfer_success/
  mlflow/
```

Policy/ops notes:

- Public access is blocked.
- Default encryption is SSE-S3.
- Teammates do not need IAM configuration in their own AWS accounts.
- Raw data and large features are not committed to git; S3 is the shared source of truth.

---

## Ingest And Feature Pipeline

| Stage | Script / notebook | Status | Output |
|---|---|---|---|
| BartTorvik ingest | `scripts/ingest_barttorvik.py` | Complete; re-run 2026-06-23 to pick up the `players.position` fix (`_infer_position()`) | Player/team stats in Postgres; raw CSVs in S3 |
| Hoop Explorer ingest | `scripts/ingest_hoop_explorer.py --all-seasons` | Complete — 6 seasons 2021-2026; assist-split backfill (2026-06-19); RAPM-prod/pred field mapping extended + re-run (2026-06-23, migration `f1c4a8d3e570`) | `hoop_explorer_team_stats` (~2,151 rows) + `hoop_explorer_player_stats` (~16,750 rows, all D1 tiers); `pos_confidence_*` + trans/scramble + assist-split + `off_adj_rapm_prod`/`adj_rapm_prod_margin` cols populated; raw files in S3 |
| hoopR PBP ingest | `scripts/ingest_hoopr.py` | Complete — 6 seasons 2021-2026 | `hoopr_team_season_stats` + `hoopr_player_season_stats`; raw parquet in S3 |
| hoopR game logs | `scripts/ingest_hoopr.py --game-logs` | **Complete for all 7 seasons 2020-2026** (was 2026-only; backfilled 2026-06-23) — same sportsdataverse-data host as PBP, different release tag (schedule + team/player box score parquet) | `hoopr_games` + `hoopr_team_game_logs` + `hoopr_player_game_logs` — 1,176,237 player-game rows total |
| Player Projection Phase 0 | `scripts/run_player_projection.py` / `notebooks/models/player_projection_state_space.ipynb` | Complete — `player-projection-shrinkage-v1` | `player_projections` (27,047 rows, neutral mode); served by `GET /api/players/{id}/projection` |
| Player Projection Phase 1 | `notebooks/models/player_projection_state_space.ipynb` Cells 8-12 | Validation only, no production script — single-season (2026) Kalman filter/smoother per skill; `R_t` scaling bug fixed 2026-06-23 | No DB write; validates Phase 2 readiness |
| 247Sports transfer ingest | `scripts/ingest_transfers_247sports.py` | Complete for 2021-2026 (499/628/774/1,037/1,346/1,251 promoted by season); 2020 scraped but 0 matched, needs investigation | `transfer_portal_events` (raw) + `transfers` (promoted) |
| barttorvik roster snapshots | `scripts/ingest_roster_snapshots.py` | Complete — 357 distinct schools (target ~365) | `roster_snapshots` + `roster_snapshot_players` |
| Roster-state features | `scripts/build_roster_state_features.py` | Plain script, not a model (no MLflow); depends on a roster snapshot existing — should be re-run against the full 357-school snapshot set if it was last run against a narrower subset | `roster_state_features` |
| Feature engineering | `notebooks/features/feature_eng_m1_m2_m3.ipynb` | ✅ Complete — all 6 seasons, regenerated 2026-06-19/20 for local sync | Produces gitignored `data/features/player_features.parquet` and `data/features/team_style_vectors.parquet`; must be regenerated against each local DB because surrogate `players.id` values are not portable |
| Model scripts | `scripts/run_player_clustering.py`, `scripts/run_team_clustering.py`, `scripts/run_scheme_fit.py`, `scripts/run_gap_matching.py` | ✅ Complete baseline — M1/M2/M3 refresh locally and log to MLflow; Scheme Fit (`scheme-cos-v3`) and Gap Matching (`gap-cos-v4` code path) are both all-pairs now — run Scheme Fit first | Preferred non-interactive rerun path; writes DB outputs, local artifacts, MLflow runs, and S3 model uploads when credentials are configured |
| Model notebooks | `notebooks/models/*.ipynb` | M1-M3 + Gap Matching complete | Use for retuning, validation plots, and product-copy refinement; scripts should be used for ordinary local refresh |

Suggested rebuild order from a fresh DB:

```text
1. alembic upgrade head
2. ingest_barttorvik.py --seasons 2021 2022 2023 2024 2025 2026   # --seasons required to populate min_pct across all years
3. ingest_hoop_explorer.py --all-seasons          # picks up all ??_?? season pairs incl 20_21
4. ingest_hoopr.py --season <year>  (repeat per season or iterate 2021-2026)
5. ingest_hoopr.py --season <year> --game-logs --skip-season-stats  (repeat per season — game-level grain, separate from step 4's season-aggregate)
6. ingest_transfers_247sports.py --seasons <year>  (repeat per season — see full backfill command below)
7. ingest_roster_snapshots.py                     # current-state snapshot only, not season-backfillable (rostercast.php has no historical view)
8. build_roster_state_features.py                 # derived from step 7's snapshot(s) + transfers + player_season_stats — not a model, no MLflow
9. feature_eng_m1_m2_m3.ipynb                     # execute with the portalpoint kernel
10. run_player_clustering.py
11. run_team_clustering.py
12. run_scheme_fit.py                              # scheme-cos-v3 — all-pairs; run before run_gap_matching.py
13. run_gap_matching.py                            # gap-cos-v4 — all-pairs, shared roster baseline
14. run_player_projection.py                        # player-projection-shrinkage-v1 — Phase 0, writes player_projections (neutral mode)
```

**Transfer-portal backfill (Issue #17 item 3) — done for 2021-2026, 2020 unresolved:**

```bash
uv run python scripts/ingest_transfers_247sports.py --seasons 2020 2021 2022 2023 2024 2025 2026
```

~2 min/season scraped (110 pages for 2026; fewer for earlier, smaller-portal-era seasons). Idempotent — safe to re-run. 2021-2026 are confirmed populated in `transfers`. Re-running the 2020 season alone (`--seasons 2020`) reproduces the same 371 `transfer_portal_events` rows, all `match_status='no_school'` — this is not a transient scrape failure, the matching logic itself is rejecting every 2020 row. Needs debugging in `ingest_transfers_247sports.py`'s matcher, not another backfill run.

**Roster snapshot run (all D1 schools, Issue #17 item 4) — done:**

```bash
uv run python scripts/ingest_roster_snapshots.py
```

~3 min (365 schools × 0.5s `REQUEST_DELAY`, matching `ingest_barttorvik.py`'s existing convention — see the robots.txt `Crawl-Delay: 10` note in `ingest_transfers_247sports.py`'s module docstring history; not strictly complied with, same as the rest of this repo's barttorvik scraping). Confirmed: 357 distinct schools in `roster_snapshots` (target ~365 — gap likely a handful of schools missing from barttorvik's rostercast coverage, not an ingest bug). One snapshot per school per calendar day — re-running same-day upserts in place; run again on a later date to accumulate a new snapshot row and enable day-over-day roster diffing. `--schools "Duke" "North Carolina"` limits to a subset for a fast test.

**After pulling a migration that adds new source columns** (e.g. `2f6a1c9d8b30`'s HE assist-split columns): the migration only adds the column — it does NOT backfill data. You must re-run the relevant ingest script (`ingest_hoop_explorer.py --all-seasons` in that case) before the new columns have any data, and re-run `feature_eng_m1_m2_m3.ipynb` afterward so the parquet picks up the populated values. Confirmed: skipping the ingest rerun left `off_ast_*`/`def_ast_*` 100% NULL, which silently zeroed out `he_team_cluster_available` for every team and crashed `team_clustering.ipynb`'s scaler fit with "0 samples."

Gitignored local data:

```text
data/hoopr/
notebooks/data/
data/features/
data/models/*.pkl
mlruns/
mlruns.db
```

---

## MLflow Architecture

MLflow is used by both the model notebooks and the non-interactive scripts.

| Store | Current state |
|---|---|
| Tracking metadata | `mlruns.db` SQLite at repo root |
| Artifacts | S3 `s3://portalpoint-data/mlflow/` when configured; deployable model artifacts also upload under `s3://portalpoint-data/models/<model>/` |
| Helper | `src/portalpoint/modeling/mlflow_helpers.py` |

Do not set `MLFLOW_TRACKING_URI` to S3. S3 is for artifacts, not tracking metadata.

`mlflow` is part of the project dependency set in `pyproject.toml`. The local SQLite tracking DB is intentionally gitignored; teammates should share important artifacts through S3, not by committing `mlruns.db`.

---

## Architecture References

| Doc | Purpose |
|---|---|
| [`../diagram_1_three_layer_architecture.md`](../diagram_1_three_layer_architecture.md) | High-level architecture. |
| [`../diagram_2_solution_architecture.md`](../diagram_2_solution_architecture.md) | Solution-level system view. |
| [`../diagram_3_data_science_workflow.md`](../diagram_3_data_science_workflow.md) | Data science lifecycle and model deployment view. |
| [`../diagram_4_database_architecture.md`](../diagram_4_database_architecture.md) | Database architecture reference. |
| [`../dataflow_diagram.mmd`](../dataflow_diagram.mmd) | Mermaid dataflow diagram. |
| [`../aws_s3_setup.md`](../aws_s3_setup.md) | S3 setup and teammate onboarding. |

---

## Architecture Open Questions

1. When should Supabase replace or supplement local Docker Postgres for shared development?
2. ✅ Resolved — Redis caching is enabled in `fit_scores.py` (cache-aside, 30min TTL, fails open on Redis errors; see `src/portalpoint/db/redis_client.py`).
3. Is GitHub Actions cron sufficient for scheduled ingest, or do we need Airflow near beta?
4. Where should production MLflow tracking metadata live if multiple people need shared run history?
5. What is the minimum deployment target for beta: one VM, container platform, or managed app service?
6. What data-quality checks should gate S3 uploads and DB upserts?
7. Should `off_trans_pct`/`def_trans_pct` (now in `hoop_explorer_team_stats`) be added to the M2 team style vector in the next feature_eng re-run?
