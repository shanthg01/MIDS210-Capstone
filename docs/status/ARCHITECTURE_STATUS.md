# PortalPoint Architecture Status

**Last updated:** July 21, 2026 (real ElastiCache Redis stood up in production, resolving the Cache row's deferred decision below; news-monitoring agent's `POST /api/agent/news-monitoring/run` fixed end-to-end — `is_admin` grant, `TAVILY_API_KEY`/`GOOGLE_API_KEY` secrets, and a real `errors` vs. `review_needed` split so an unmatched-player outcome no longer looks like a crash). Previously: July 20, 2026 — first production incident, same day as go-live: broken login/signup (12 migrations from the `origin/main` merge had never been applied to RDS) plus 5+ minute dashboard/fit/compare hangs from an unindexed `MAX(season)` scan; both root-caused and fixed same day, alongside frontend going live on S3+CloudFront, a CloudWatch alarm, and backend going live on ECS Fargate with SSH→SSM bastion migration. Earlier still: July 16, 2026 (news-monitoring agent RDS migrations applied; `program_events` write pipeline verified on shared RDS).
**Scope:** Infrastructure, data stores, database schema, ingest, S3/MLflow, and runbook context.

Model-specific context lives in [`MODEL_STATUS.md`](MODEL_STATUS.md). Product/API/frontend context lives in
[`APPLICATION_STATUS.md`](APPLICATION_STATUS.md).

---

## Deployment Stance

PortalPoint moved off "local-first" for both backend and frontend on 2026-07-20 — the API runs on ECS Fargate and the frontend is served from S3+CloudFront. Scheduled jobs remain manual, by explicit decision, not oversight.

| Layer | Current approach | Cloud / shared path |
|---|---|---|
| App database | **AWS RDS PostgreSQL 15** (`portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com:5432`) — shared team DB, migrated 2026-06-29 | ✅ Done |
| Cache | **Real ElastiCache Redis (`portalpoint-cache`, cache.t3.micro, single node), live 2026-07-21** — `portalpoint-cache-subnets` subnet group (same private subnets as ECS), `portalpoint-cache-sg` allowing 6379 from both the ECS task SG (app traffic) and the bastion SG (debugging via SSM tunnel — this second rule was missed initially and had to be added after a tunnel attempt failed). `REDIS_URL` added to `task-def.json` as a plain env var (no auth token on this cluster, not a secret). Resolves both failure modes found in production on 2026-07-20: `fit_score_service`'s cache-aside layer (was failing open to raw DB queries — already separately fixed via the `ix_fit_scores_season` index, this now additionally restores real caching) and `api/routers/agent.py`'s news-monitoring run-tracking (was failing closed with a 503, PR #65 review — now actually works, not just fails gracefully). | ✅ Done |
| API | **ECS Fargate** (`portalpoint-prod` cluster, `portalpoint-backend` service, behind an ALB) — deployed 2026-07-20, image built from the repo-root `Dockerfile`, deployed via GitHub Actions OIDC on merge to `main` | ✅ Done |
| Frontend | **Live: https://d331zwrxbrp79d.cloudfront.net** — S3 (`portalpoint-frontend`) + CloudFront (`E2HF7HKH8Y1FKD`), deployed 2026-07-20; bucket has no public access, read-only via Origin Access Control scoped to this distribution's ARN; `/api/*` routed to the ALB at the CDN layer, so the SPA still calls relative `/api/*` paths, no separate API base URL needed. Deploy is manual (`npm run build` → `aws s3 sync` → `aws cloudfront create-invalidation`), no CI step yet | ✅ Done (deploy automation not done) |
| Raw/model storage | Local gitignored `data/` plus S3 | `s3://portalpoint-data/` |
| MLflow tracking metadata | Local `mlruns.db` SQLite | Could move to hosted DB or MLflow server later |
| MLflow artifacts | Local/S3 depending script/notebook setup | `s3://portalpoint-data/mlflow/` |

ECS/Fargate backend and S3+CloudFront frontend deployment both landed ahead of beta (`docs/production_db_connectivity_plan.md`, `docs/road_to_production.md` Phases 3-4) — backend not yet autoscaled (fixed task count) and CORS still points at local dev origins (frontend is live, but the CORS finalization sub-item wasn't revisited). One CloudWatch alarm exists (`portalpoint-unhealthy-targets` on ALB `UnHealthyHostCount` → SNS `portalpoint-alerts`) — the rest of observability (Sentry, Prometheus/Grafana, drift detection) is explicitly deferred. Scheduled jobs (GitHub Actions cron vs. Airflow) remain unimplemented by explicit decision — no automated freshness need exists yet; see `docs/road_to_production.md` Phase 5 for the revisit conditions.

---

## Local Runbook

Start local infrastructure (Redis only — Postgres is now shared RDS):

```bash
docker compose up -d redis
```

Install and migrate:

```bash
uv sync
# Schema is managed on RDS — run alembic only when applying new migrations:
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
| `DATABASE_URL` | Async SQLAlchemy URL for app/runtime. Now points to shared RDS — use `?ssl=require` suffix. See `docs/aws_rds_setup.md`. |
| `REDIS_URL` | Redis URL for future caching. |
| `JWT_SECRET` | Required for auth token signing. |
| `JWT_EXPIRY_SECONDS` | Increase locally if frequent re-login is annoying. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Shared S3 access keys. Never commit. |
| `AWS_DEFAULT_REGION` | `us-east-1`. |
| `S3_BUCKET` | `portalpoint-data`. |
| `MLFLOW_TRACKING_URI` | SQLite or MLflow server URL; do not use an `s3://` URI. |
| `TAVILY_API_KEY` / `GOOGLE_API_KEY` | News-monitoring agent (`scripts/run_news_monitoring.py`). Documented in `.env.example`. |

S3 onboarding guide: [`../aws_s3_setup.md`](../aws_s3_setup.md).

---

## Database

| Component | State |
|---|---|
| Database | **AWS RDS PostgreSQL 15** — migrated 2026-06-29; endpoint `portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com:5432` |
| ORM | SQLAlchemy |
| Migrations | Alembic — run `alembic upgrade head` against RDS when landing new migrations; `alembic stamp head` was run post-restore to sync the version table |
| Async app access | `postgresql+asyncpg://...?ssl=require` — `ssl=require` required (RDS enforces TLS) |
| Modeling sync access | `src/portalpoint/modeling/io.py` converts the async app URL to a sync psycopg2 URL (`ssl=require` → `sslmode=require` translation added 2026-06-29) |
| App user | `portalpoint_app` — scoped runtime user; master user (`portalpoint_master`) reserved for admin ops only |
| Security group | RDS SG `sg-0ec78cb4f641ee901` — port 5432 only from bastion SG `sg-06d79bdd59fea641a` (source-group rule, not per-IP). No public access. Connect via **AWS SSM Session Manager port-forwarding** through the bastion (`i-0a6e1bafc1cb6f379`) — see `docs/aws_rds_setup.md`. Replaced an earlier per-teammate static-IP allowlist (broke on network changes), then (2026-07-20) the bastion's SSH port itself was closed (`0.0.0.0/0` ingress on port 22 revoked — had been open to the entire internet, not just the team) in favor of IAM-authenticated SSM access, logged per-identity in CloudTrail |

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
| `2547054ae5cb` | News agent: `program_events`, `program_events_review_queue` (idempotent create-if-missing on shared RDS) |
| `b1d3f5a7c9e2` | `team_system_profiles.stale_flag` / `stale_reason` (Gate 7 coaching-change signal) |
| `c9e2a1f4b8d3` | News agent catch-up: partial unique indexes on `program_events` for idempotent `ON CONFLICT DO NOTHING` (transfer + coach events) |
| `d7f3b2e1a904` | Merge head for news-agent catch-up branch |
| `d2f6a8c1b3e7` | Adds `users.is_admin` |
| `a6c1f9e2d4b8` | Adds calibrated fit-score columns to `player_team_fit_scores` (`calibrated_scheme_fit`/`calibrated_gap_match`/`calibrated_role_fit`/`calibrated_program_fit`, `overall_confidence`, `component_confidences`, `data_quality_flags`, `calibration_version`) — all nullable, no default, so this one is a fast metadata-only `ADD COLUMN` on the ~10M-row table, not a rewrite |
| `d7f54d0a43bb` | Merge head: `d2f6a8c1b3e7` + `a6c1f9e2d4b8` |
| `e9f2a7b3c4d5` | Adds `transfer_success_scores` table |
| `e5a8c2d4f901` | Adds playing-time explanation payload column |
| `e11d8f65109c` | Merge head: news-agent branch + main |
| `f8db53b163a8` | Merge head: news-agent branch + team-rating-v2 branch |
| `b3f8e21a6c94` | `CREATE INDEX CONCURRENTLY ix_fit_scores_season ON player_team_fit_scores (season)` — real production fix, 2026-07-20; see the incident note below and `docs/status/STATUS.md`'s dated entry for the full story |

**Real production incident, 2026-07-20 — 12 migrations (everything from `d2f6a8c1b3e7` through `f8db53b163a8` above) had never been applied to the shared RDS after the `origin/main` merge landed in code.** Broke login/signup (`column users.is_admin does not exist`) until `alembic upgrade head` was run for real. That run hit the same table-ownership gotcha the 2026-07-16 note below already flagged, just on a different table (`playing_time_projections`, not `coaches`) — confirms this is a recurring pattern, not a one-off: **some tables in this DB are owned by a user other than `portalpoint_app`, so any DDL touching them must run as `portalpoint_master`.** Separately, the missing `ix_fit_scores_season` index (now fixed by `b3f8e21a6c94`) was letting `SELECT MAX(season) FROM player_team_fit_scores` — called on every Dashboard/FitScorePage/Compare request via `fit_score_service.get_current_season()` — run as a genuine ~200-300s full sequential scan each time, because the query's documented Redis cache was silently never wired into production (`REDIS_URL` missing from `task-def.json`; the cache fails open to the DB on any Redis error, including "can't connect at all"). Full trail in `docs/status/STATUS.md`.

**Alembic note (2026-07-16):** `alembic/env.py` passes `settings.database_url` directly to SQLAlchemy (bypasses ConfigParser — URL-encoded passwords with `%` break `set_main_option`). `coaches.tenure_end`/`departure_date` DDL from `2547054ae5cb` still requires RDS table-owner; runtime migrations skip it for `portalpoint_app`.

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
| `player_team_fit_scores` | Scheme/gap/role/program/overall fit scores; multi-season (`season` col). `scheme_fit`+`gap_match` are real, and `playing-time-rotation-v2` has written/synced 2027 `role_fit` rows by copying 2026 fit context into 2027 rows, then replacing `role_fit`/`breakdown.role_fit`. `program_fit` remains stubbed, so `overall_fit` is still partial |
| `transfer_portal_events` | Raw 247Sports scrape staging — every scraped row, matched or not; `player_id` nullable; `portal_entry_date`/`commitment_date` fill in incrementally across repeated scrapes |
| `transfers` | Promoted transfer records (matched rows only) — `(player_id, season)` unique, supports upsert; backfills `pre_per`/`pre_usage_rate` from `player_season_stats`; `pre_minutes_per_game` should be derived from `player_season_stats.min_pct * 0.4`, not copied from legacy `minutes_per_game` |
| `roster_snapshots` / `roster_snapshot_players` | Point-in-time roster composition per school per scrape date (barttorvik `rostercast.php`); `returning_status` (`returning`/`transfer_in`/`new`) computed by diffing against `player_season_stats`, not given by the source |
| `roster_state_features` | One row per `roster_snapshots` row — derived facts (counts/sums, not gap scores): returning/departing/incoming minutes+usage by position (as min_pct share — see note below), returning production/impact, class balance, archetype counts. Built by `scripts/build_roster_state_features.py` (plain script, not a model — no MLflow) |
| `roster_baseline_members` | Shared roster-membership snapshot consumed by Gap Matching (`gap-cos-v4`) and intended for Role Fit/Team Rating Projection too — see `portalpoint.modeling.roster_baseline` |
| `player_projections` | **Real neutral and destination player projections.** The production neutral API default is the Phase 2a next-season forecast model (`player-proj-phase2a-fcast-v1`): observed season `S` writes target projected season `S+1`. Phase 0 v2 and same-season Phase 2a v2 remain baseline/diagnostic comparators. Destination mode (`school_id` set) is written by `player-destination-proj-v1` after Role Fit. Served by `GET /api/players/{id}/projection` |
| `playing_time_projections` | First-class Role Fit / opportunity output table. `playing-time-rotation-v2` writes target playing season rows; for the live 2026 portal cycle that means `season=2027`, with 2026 source/roster/fit context recorded in `opportunity_drivers`. Required by destination-adjusted player projections |
| `predictions` | Future transfer success outputs |
| `team_rating_projections` | Team impact outputs; implementation is in open PR #49 and needs merge/rerun before current rows should be trusted |
| `recommendations` | Batch-job cache table (`scripts/run_recommendations.py`, precomputed, no scheduler keeps it fresh — checked, none exists). `GET /api/recommendations` does **not** read this table — as of 2026-07-15 it computes live per request straight from `player_team_fit_scores`/`team_rating_projections` instead, since nothing schedules the batch job. This table is still written by the standalone script for offline/MLflow-tracked runs, just not the API's data source. |
| `program_events` / `program_events_review_queue` | News-monitoring agent event log (migration `2547054ae5cb` + dedup indexes `c9e2a1f4b8d3`). `transfer_player` writes `transfer_entry` rows; `coach_departure` writes `coach_departed` + flags `team_system_profiles.stale_flag`. Source=`news-agent`. |
| `users`, `user_preferences`, `user_shortlists` | Program-facing app state |

**Known data gaps:** `player_school_seasons` is still empty (0 rows) — no VerbalCommits ingest yet. `transfers` backfill is done for 2021-2026 (499/628/774/1,037/1,346/1,251 rows respectively, confirmed 2026-06-23). Season 2020 was scraped (371 raw rows in `transfer_portal_events`) but produced zero matches — all rows landed `match_status='no_school'`; `transfers` has no 2020 rows and this needs separate investigation, not a rerun of the same command. **Game-level data backfill is done (2026-06-23):** `hoopr_player_game_logs`/`hoopr_games`/`hoopr_team_game_logs` now cover all 7 seasons 2020-2026 (player-game row counts: 162,813 / 121,547 / 171,896 / 176,962 / 180,527 / 184,504 / 178,108 — 1,176,237 total). This was blocked mid-backfill by an unrelated stray Postgres session that had been `idle in transaction` since before the backfill even started, silently holding a lock on `hoopr_games` inserts — `pg_terminate_backend()` on that session unblocked it; not a backfill-script bug.

**Minutes convention:** `player_season_stats.min_pct` is the canonical
historical playing-time field (0-100 share of team minutes). Derive
coach-facing MPG as `min_pct * 0.4`. The BartTorvik ingest now writes derived
MPG into `player_season_stats.minutes_per_game`, and the current local DB was
backfilled the same way. Older DBs may still contain legacy/mis-mapped
`minutes_per_game` values, so modeling contracts should continue to treat
`min_pct` as the source of truth.

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
| Player Projection Phase 0 | `scripts/run_player_projection.py` / `notebooks/models/player_projection_state_space.ipynb` | Complete — `player-projection-shrinkage-v2` after defensive-sign fix | `player_projections` (neutral mode); retained as baseline comparator |
| Player Projection Phase 1 | `notebooks/models/player_projection_state_space.ipynb` Cells 8-12 | Validation only, no production script — single-season (2026) Kalman filter/smoother per skill; `R_t` scaling bug fixed 2026-06-23 | No DB write; validates Phase 2 readiness |
| Player Projection Phase 2a | `scripts/run_player_projection.py --phase 2a` / `notebooks/models/player_projection_state_space.ipynb` Cells 14-18 | **Complete + real-data validated 2026-06-25**, reconciled against [Issue #37](https://github.com/shanthg01/MIDS210-Capstone/issues/37). Beats Phase 0 on held-out offense every fold, ties on defense. Production path now forecasts target season `S+1` from observed season `S`; same-season Phase 2a rows are diagnostic only. 11th skill (`foul_discipline`) + offense/defense feature-set split for the value model both landed. Gap B (context adjustment) regresses accuracy and is not enabled after root-cause analysis. | `player_projections` (`model_version="player-proj-phase2a-fcast-v1"`, neutral mode, with projected rates/box scores and source/target season metadata). API default serves this forecast version; Phase 0 remains the baseline comparator. |
| 247Sports transfer ingest | `scripts/ingest_transfers_247sports.py` | Complete for 2021-2026 (499/628/774/1,037/1,346/1,251 promoted by season); 2020 scraped but 0 matched, needs investigation | `transfer_portal_events` (raw) + `transfers` (promoted) |
| News monitoring agent | `scripts/run_news_monitoring.py` | **Manual live runs verified 2026-07-16** — Tavily + Gemini + RDS writes; no scheduler yet | `program_events` (`news-agent` source), `transfer_portal_events`, `transfers`, `team_system_profiles.stale_flag` on coach departures |
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
14. run_player_projection.py                        # player-projection-shrinkage-v2 + player-proj-phase2a-fcast-v1 — writes neutral player_projections
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
| [`../aws_rds_setup.md`](../aws_rds_setup.md) | RDS PostgreSQL setup and teammate onboarding. |

---

## Architecture Open Questions

1. ✅ Resolved — migrated to AWS RDS PostgreSQL 15 (shared team DB) on 2026-06-29. Local Docker Postgres no longer needed; only Redis remains in `docker compose`. Access pattern further hardened the same day: per-teammate static-IP security group rules (broke whenever someone changed networks) replaced with a bastion EC2 host + SSH tunnel — RDS SG now only allows port 5432 from the bastion's SG (source-group rule), no public/per-IP access at all. **Further hardened 2026-07-20** (start of production-DB-connectivity work, `docs/production_db_connectivity_plan.md`): the bastion's SSH port was found open to `0.0.0.0/0` (the whole internet, not just the team — a real exposure, not just a hygiene nit) and closed entirely; access moved to AWS SSM Session Manager port-forwarding, authenticated via IAM instead of a shared `.pem` key, with per-session CloudTrail logging. Same session also stood up private subnets + NAT gateway + S3 gateway endpoint, an ECR repo, an ECS task security group scoped to RDS, RDS Multi-AZ, and a GitHub Actions OIDC deploy role — see `docs/production_deployment_commands.md` for the full command log. Backend ECS Fargate hosting itself (Phase 3) has not started yet. See `docs/aws_rds_setup.md` for the current teammate access flow.
2. ✅ Resolved — Redis caching is enabled in `fit_scores.py` (cache-aside, 30min TTL, fails open on Redis errors; see `src/portalpoint/db/redis_client.py`).
3. Is GitHub Actions cron sufficient for scheduled ingest, or do we need Airflow near beta?
4. Where should production MLflow tracking metadata live if multiple people need shared run history?
5. What is the minimum deployment target for beta: one VM, container platform, or managed app service?
6. What data-quality checks should gate S3 uploads and DB upserts?
7. Should `off_trans_pct`/`def_trans_pct` (now in `hoop_explorer_team_stats`) be added to the M2 team style vector in the next feature_eng re-run?
