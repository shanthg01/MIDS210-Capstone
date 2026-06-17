# PortalPoint Architecture Status

**Last updated:** June 16, 2026  
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
| MLflow artifacts | Local/S3 depending notebook setup | `s3://portalpoint-data/mlflow/` |

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
| Notebook sync access | notebooks often replace `+asyncpg` with sync driver URL where needed |

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

Important tables:

| Table | Purpose |
|---|---|
| `players`, `schools` | Core entities |
| `player_school_seasons` | Player-team-season linkage |
| `player_season_stats` | BartTorvik normalized player stats |
| `team_season_stats` | BartTorvik normalized team stats |
| `hoop_explorer_player_stats` | HE player exports — 13,993 rows (5 seasons 2022-2026, all D1); includes RAPM, 15 play-type pcts, `pos_confidence_pg/sg/sf/pf/c` |
| `hoop_explorer_team_stats` | HE team exports — 1,811 rows (5 seasons 2022-2026); includes 12 off/def play-type pcts, `off/def_trans_pct/ppp`, `off/def_scramble_pct/ppp` |
| `hoopr_team_season_stats` | ESPN PBP-derived team features |
| `hoopr_player_season_stats` | ESPN PBP-derived player features (87-92% crosswalk per season, 2021-2026) |
| `player_archetypes` | M1 cluster assignments |
| `team_system_profiles` | M2 cluster assignments |
| `player_team_fit_scores` | Scheme/gap/role/program/overall fit scores |
| `predictions` | Future transfer success outputs |
| `team_rating_projections` | Future team impact outputs |
| `recommendations` | Future ranked player recommendations |
| `users`, `user_preferences`, `user_shortlists` | Program-facing app state |

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
| BartTorvik ingest | `scripts/ingest_barttorvik.py` | Complete | Player/team stats in Postgres; raw CSVs in S3 |
| Hoop Explorer ingest | `scripts/ingest_hoop_explorer.py --all-seasons` | Complete — 5 seasons 2022-2026 | `hoop_explorer_team_stats` (1,811 rows) + `hoop_explorer_player_stats` (13,993 rows, all D1 tiers); new `pos_confidence_*` + trans/scramble cols populated; raw files in S3 |
| hoopR PBP ingest | `scripts/ingest_hoopr.py` | Complete — 6 seasons 2021-2026 | `hoopr_team_season_stats` + `hoopr_player_season_stats`; raw parquet in S3 |
| Feature engineering | `notebooks/features/feature_eng_m1_m2_m3.ipynb` | ⚠️ Re-run needed | HE team coverage was 19% (single season); after 5-season load it will reach ~98%. Re-run before M2/M3 re-train. |
| Model notebooks | `notebooks/models/*.ipynb` | M1-M3 complete | DB outputs and model artifacts |

Suggested rebuild order from a fresh DB:

```text
1. alembic upgrade head
2. ingest_barttorvik.py
3. ingest_hoop_explorer.py --all-seasons
4. ingest_hoopr.py --season <year>  (repeat per season or iterate 2021-2026)
5. feature_eng_m1_m2_m3.ipynb
6. team_clustering.ipynb
7. player_clustering.ipynb
8. scheme_fit_scorer.ipynb
```

Gitignored local data:

```text
data/hoopr/
notebooks/data/
data/features/
mlruns/
mlruns.db
```

---

## MLflow Architecture

MLflow is notebook-only right now.

| Store | Current state |
|---|---|
| Tracking metadata | `mlruns.db` SQLite at repo root |
| Artifacts | S3 `s3://portalpoint-data/mlflow/` when configured |
| Helper | `notebooks/utils/mlflow_helpers.py` |

Do not set `MLFLOW_TRACKING_URI` to S3. S3 is for artifacts, not tracking metadata.

Known issue:

- `mlflow` is not part of the main uv dependency set because of dependency conflicts. Install it as a notebook-only dependency when needed:

```bash
uv pip install mlflow
```

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
2. When should Redis caching be enabled in `fit_scores.py`?
3. Is GitHub Actions cron sufficient for scheduled ingest, or do we need Airflow near beta?
4. Where should production MLflow tracking metadata live if multiple people need shared run history?
5. What is the minimum deployment target for beta: one VM, container platform, or managed app service?
6. What data-quality checks should gate S3 uploads and DB upserts?
7. Should `off_trans_pct`/`def_trans_pct` (now in `hoop_explorer_team_stats`) be added to the M2 team style vector in the next feature_eng re-run?
