# PortalPoint

Data-driven transfer portal scouting platform for college basketball programs. Coaching staffs evaluate 2,500+ portal entrants via multi-dimensional fit scoring — Scheme Fit, Gap Matching, Role Fit, and Program Fit — ranked by a composite score into a per-program recommendation feed.

**Primary user:** Coaching staffs / programs (subscription B2B).  
**Core value:** Quantitative player evaluation across the full portal in the 3–4 week evaluation window.

---

## Current State

| Layer | Status |
|---|---|
| Backend API (FastAPI) | All endpoints live — protected routes require JWT; auth/players/users hit real DB |
| Database (PostgreSQL + Alembic) | Current migration head applied; 2021-2026 barttorvik, Hoop Explorer, and hoopR data loaded locally; hoopR game logs, 247Sports transfers, and barttorvik roster snapshots loaded for season 2026 (full backfills documented, not yet run — see below) |
| Ingest pipeline | barttorvik ✅, Hoop Explorer ✅, hoopR ESPN PBP + game logs ✅, 247Sports transfer portal ✅, barttorvik roster snapshots ✅ (raw/local cache + S3 upload where configured) |
| Feature + model pipeline | Script-backed reruns for M1, M2, M3, and Gap Matching; feature parquet/model artifacts are gitignored and regenerated locally |
| MLflow + S3 artifacts | Wired — local `mlruns.db`, S3 model uploads, and script/notebook MLflow runs |
| Fit components | ✅ Player clustering, team clustering, scheme fit, and gap matching complete; API serves real `scheme_fit` + `gap_match` |
| Role Fit, Program Fit | ❌ Not started — API keeps deterministic 50.0 stubs for these components |
| Recommendation Engine (Model 7) | ❌ Blocked on Role Fit + Program Fit |
| Frontend (React + Vite) | 8 pages implemented against live API |

See [`docs/status/STATUS.md`](docs/status/STATUS.md) for the status index, or jump directly to
[`docs/status/MODEL_STATUS.md`](docs/status/MODEL_STATUS.md),
[`docs/status/ARCHITECTURE_STATUS.md`](docs/status/ARCHITECTURE_STATUS.md), and
[`docs/status/APPLICATION_STATUS.md`](docs/status/APPLICATION_STATUS.md).

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Backend runtime |
| [uv](https://docs.astral.sh/uv/) | latest | Python package/venv manager |
| Node.js | 18+ | Frontend build |
| npm | 9+ | Frontend package manager |
| Docker Desktop | latest | Redis (PostgreSQL is now shared AWS RDS — see [Team RDS access](#team-rds-access-aws)) |

---

## Quick Start (Full Stack)

### 1. Clone and configure

```bash
git clone https://github.com/shanthg01/MIDS210-Capstone.git
cd MIDS210-Capstone
cp .env.example .env
```

RDS sits behind a bastion host — open the SSH tunnel first (leave it running), then edit `.env` and replace `<password>` in `DATABASE_URL` with the `portalpoint_app` password from Justin. See [Team RDS access](#team-rds-access-aws) for the tunnel command.

**Teammates doing notebook / S3 work:** also add AWS keys from Justin (see [Team S3 access](#team-s3-access-aws) below).

### 2. Start infrastructure (Redis only — PostgreSQL is shared RDS)

```bash
docker compose up -d redis
```

This starts:
- Redis 7 on port **6379**

PostgreSQL is hosted on AWS RDS — no local Postgres container needed. See [Team RDS access](#team-rds-access-aws) to get connected.

### 3. Install Python dependencies and apply migrations

```bash
uv sync
uv run alembic upgrade head
```

### 4. Load data and refresh local model outputs

```bash
uv run python scripts/ingest_barttorvik.py --seasons 2021 2022 2023 2024 2025 2026
uv run python scripts/ingest_hoop_explorer.py --all-seasons
uv run python scripts/ingest_hoopr.py --season 2021 --season 2022 --season 2023 --season 2024 --season 2025 --season 2026
```

Raw PBP parquets (~120MB/season) land in `data/hoopr/` (gitignored) and `s3://portalpoint-data/raw/hoopr/`.

AWS keys required for S3 upload — see [Team S3 access](#team-s3-access-aws). Ingest writes to DB regardless of S3 availability (upload failure is logged, not fatal).

**Game-level grain, transfers, and roster snapshots (Issue #17 items 1-4):**

```bash
# hoopR game logs — one season already loaded (2026); repeat per season for a full backfill
uv run python scripts/ingest_hoopr.py --season 2026 --game-logs --skip-season-stats

# 247Sports transfer-portal events — one season already loaded (2026)
uv run python scripts/ingest_transfers_247sports.py --seasons 2026

# Full transfer-portal backfill, 2020-2026 (not yet run — ~2 min/season scraped, ~15 min total)
uv run python scripts/ingest_transfers_247sports.py --seasons 2020 2021 2022 2023 2024 2025 2026

# barttorvik roster snapshots — verified on one school (Duke) only so far
uv run python scripts/ingest_roster_snapshots.py --schools Duke

# Full roster snapshot run, all ~365 D1 schools (not yet run — ~3 min, 0.5s delay/request)
uv run python scripts/ingest_roster_snapshots.py
```

Idempotent — safe to re-run any of the above. `--dry-run` is available on all three for a no-write match-rate check first.

Feature parquet and most model artifacts are intentionally not tracked in git. Regenerate them against your local DB before running the model scripts:

```bash
uv run jupyter nbconvert --to notebook --execute notebooks/features/feature_eng_m1_m2_m3.ipynb \
  --output /tmp/feature_eng_m1_m2_m3.executed.ipynb \
  --ExecutePreprocessor.kernel_name=portalpoint \
  --ExecutePreprocessor.timeout=1800

uv run python scripts/run_player_clustering.py
uv run python scripts/run_team_clustering.py
uv run python scripts/run_scheme_fit.py
uv run python scripts/run_gap_matching.py
```

Use the notebooks in `notebooks/models/` for retuning, visual review, and validation plots. Use the scripts above for repeatable local refreshes that keep Postgres, local artifacts, MLflow, and S3 model uploads in sync.

### 5. Start the backend

```bash
uv run uvicorn portalpoint.main:app --reload
```

- API root: http://localhost:8000
- Interactive docs (Swagger UI): http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

### 6. Start the frontend

In a second terminal:

```bash
cd frontend
npm install          # first time only
npm run dev
```

- Frontend: http://localhost:5173
- All `/api/*` requests are proxied to the backend — no CORS config needed in dev

### 7. Create an account and log in

Navigate to http://localhost:5173, click **Create one**, fill in name / email / password (min 8 chars). You are redirected to the dashboard on signup.

---

## Environment Variables

Copy `.env.example` to `.env`. All variables have sane defaults for local Docker:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://portalpoint_app:<password>@127.0.0.1:5433/portalpoint?ssl=require` | SSH tunnel to shared AWS RDS — get password from Justin, see [Team RDS access](#team-rds-access-aws); use `127.0.0.1` not `localhost` (avoids IPv6 bind issue) |
| `REDIS_URL` | `redis://localhost:6379` | |
| `JWT_SECRET` | `change-me-in-production-use-a-long-random-string` | Any string works locally; use a random 32+ char string in production |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_EXPIRY_SECONDS` | `3600` | Set to `86400` in local dev to avoid hourly re-login |
| `ENVIRONMENT` | `development` | |
| `MLFLOW_TRACKING_URI` | `sqlite:///mlruns.db` | SQLite at repo root for local dev; do **not** use an `s3://` URI here |
| `AWS_ACCESS_KEY_ID` | *(from Justin)* | Programmatic S3 access — see [Team S3 access](#team-s3-access-aws) |
| `AWS_SECRET_ACCESS_KEY` | *(from Justin)* | Never commit; `.env` is gitignored |
| `AWS_DEFAULT_REGION` | `us-east-1` | Must match bucket region |
| `S3_BUCKET` | `portalpoint-data` | Shared team bucket |

**Token expiry note:** The default 1-hour expiry means you'll be logged out after 60 minutes. For active development, set `JWT_EXPIRY_SECONDS=86400` in `.env`.

---

## Team RDS access (AWS)

Shared PostgreSQL 15 database on AWS RDS. All teammates connect to the same instance — through a bastion SSH tunnel, since RDS has no public access and no per-IP allowlist.

**Full guide:** [`docs/aws_rds_setup.md`](docs/aws_rds_setup.md)

### Classmate quick start

1. Get `portalpoint-bastion.pem`, bastion public IP, and `portalpoint_app` password from Justin (DM — never commit)
2. Open the tunnel, leave it running:
   ```powershell
   ssh -i portalpoint-bastion.pem -L 5433:portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com:5432 ec2-user@<bastion-public-ip> -N -o ServerAliveInterval=60 -o ServerAliveCountMax=3
   ```
3. Set `DATABASE_URL` in `.env` to `127.0.0.1:5433` with the real password replacing `<password>` (see `.env.example`)
4. Verify access: `uv run python -c "from portalpoint.modeling.io import get_sync_engine; get_sync_engine().connect().close(); print('OK')"`

| Item | Value |
|---|---|
| RDS host (behind bastion) | `portalpoint-db.con8amymqi1e.us-east-1.rds.amazonaws.com` |
| Local tunnel address (what you actually connect to) | `127.0.0.1:5433` |
| Database | `portalpoint` |
| Runtime user | `portalpoint_app` |
| SSL | Required (`?ssl=require` in URL) |

---

## Team S3 access (AWS)

Shared bucket for raw data, model artifacts, and MLflow artifacts. **Justin provisions IAM users** — teammates only need keys in `.env`.

**Full guide:** [`docs/aws_s3_setup.md`](docs/aws_s3_setup.md)

### Classmate quick start

```powershell
Copy-Item .env.example .env
# Add AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY from Justin (DM)
uv pip install -r notebooks/requirements-notebooks.txt
aws s3 ls s3://portalpoint-data/    # smoke test
```

| Item | Value |
|---|---|
| Bucket | `portalpoint-data` |
| Region | `us-east-1` |
| Access | IAM user per teammate (programmatic keys only) |
| Billing | Teammate AWS accounts linked to org for credit sharing; S3 keys come from bucket owner account |

---

## ML Model Tracking (MLflow)

MLflow tracks model runs, parameters, metrics, and artifacts for player clustering, team clustering, scheme fit, and gap matching. It is included in the project dependencies and used by both notebooks and the non-interactive model scripts.

### Tracking backend

All runs write to `mlruns.db` (SQLite) at the repo root by default. The path is resolved automatically by `src/portalpoint/modeling/mlflow_helpers.py` — no CWD dependency. If `MLFLOW_TRACKING_URI` is set in `.env`, that URI is used instead (must be `sqlite:///` or an MLflow server URL — **not** `s3://`; `file:` paths are rejected by mlflow 3.x).

MLflow artifacts (`.pkl`, plots) can be stored in `s3://portalpoint-data/mlflow/` when wired; deploy bundles live under `s3://portalpoint-data/models/<model>/`. See [`docs/aws_s3_setup.md`](docs/aws_s3_setup.md).

### Launch the MLflow UI

```bash
# From repo root
mlflow ui --backend-store-uri sqlite:///mlruns.db
```

Open http://127.0.0.1:5000. Current experiments include:

| Experiment | Model | Key metric | Registry name |
|---|---|---|---|
| `player-clustering` | K-Means player archetypes (k=9) | `silhouette_score` | `player-clustering` |
| `team-clustering` | Two-layer K-Means team system profiles | `silhouette_score` | `team-clustering` |
| `scheme-fit-scorer` | Cosine similarity `scheme-cos-v2` | `n_records_written` | `scheme-fit-scorer` |
| `gap-matching` | Cosine gap matching `gap-cos-v2` (departure-aware) | `std_gap_match` | `gap-matching-scorer` |

Artifacts (pkl files) in `s3://portalpoint-data/models/`; MLflow artifact store in `s3://portalpoint-data/mlflow/`.

### Auto-promotion logic

Each notebook/script run registers a new model version. `maybe_promote()` in `mlflow_helpers.py` compares the new run's key metric against the current Production version:

- **First run ever** → automatically promoted to `Production`
- **Improvement > 5%** → promoted to `Production`
- **Improvement ≤ 5%** → sent to `Staging`

### Shared helper

`src/portalpoint/modeling/mlflow_helpers.py` provides three functions used by model notebooks and scripts:

| Function | Purpose |
|---|---|
| `setup_mlflow(experiment_name)` | Sets tracking URI + experiment, returns `MlflowClient` |
| `maybe_promote(client, model_name, run_id, ...)` | Registers version, promotes or stages based on metric delta |
| `get_tracking_uri()` | Reads `.env`; falls back to `sqlite:///mlruns.db` at repo root |

### Legacy file-store artifacts

Earlier runs created `notebooks/models/mlruns/` and `notebooks/mlruns/` directories (file-store format, incompatible with mlflow 3.x). These can be deleted — all current runs are in `mlruns.db`.

```bash
# Optional cleanup
Remove-Item -Recurse -Force notebooks/models/mlruns, notebooks/mlruns
```

---

## Running Tests

```bash
# Full suite (111 tests)
uv run --with pytest pytest tests/ -v

# Single file
uv run --with pytest pytest tests/test_fit_scores.py -v

# Single test
uv run --with pytest pytest tests/test_fit_scores.py::test_overall_matches_weighted_sum -v
```

---

## API Endpoints

All endpoints under `/api`. Public endpoints require no auth; protected endpoints require `Authorization: Bearer <token>` header (token returned on login/signup).

### Auth (public)

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/signup` | Create program account → returns JWT + user_id |
| POST | `/api/auth/login` | Login → returns JWT + user_id |
| POST | `/api/auth/logout` | Logout (client-side token discard) |

### Players (public)

| Method | Path | Description |
|---|---|---|
| GET | `/api/players/{id}` | Full player profile + current season stats + archetype |
| GET | `/api/players/search?name=` | Fuzzy player name search |

### Portal Intelligence (Bearer required)

| Method | Path | Description |
|---|---|---|
| GET | `/api/recommendations?user_id=` | Top-10 portal players ranked for program (stub → Model 7) |
| GET | `/api/fit-scores?player_id=&school_id=` | 4-component fit score breakdown (scheme + gap real; role/program stubbed) |
| GET | `/api/predictions?player_id=&school_id=` | Transfer outcome prediction + SHAP explanations (stub → Model 5) |
| GET | `/api/projections/team-rating?player_id=&school_id=` | Delta-AdjEM projection with 80% CI (stub → Model 6) |
| POST | `/api/compare` | Side-by-side comparison for 2–4 players |

### User / Pipeline (Bearer required)

| Method | Path | Description |
|---|---|---|
| GET | `/api/users/{id}/preferences` | Program preference weights and recruiting filters |
| PUT | `/api/users/{id}/preferences` | Update fit weights (gap/scheme/role/program) + importance weights |
| GET | `/api/users/{id}/shortlist` | Recruiting pipeline — saved players |
| POST | `/api/users/{id}/shortlist/{player_id}` | Add player to pipeline (409 if duplicate) |
| DELETE | `/api/users/{id}/shortlist/{player_id}` | Remove player from pipeline |

---

## Frontend Pages

| Route | Page | Notes |
|---|---|---|
| `/login` | Login | Public |
| `/signup` | Signup | Public |
| `/dashboard` | Recommendations feed | Top-10 players, fit score cards |
| `/players/search` | Player search | Debounced name search, 4,500+ players |
| `/players/:id` | Player profile | Full stats + add to pipeline |
| `/pipeline` | Recruiting pipeline | Shortlisted players, remove, sort by fit |
| `/fit/:player_id` | Fit score detail | All 4 components + team rating projection |
| `/compare` | Side-by-side comparison | 2–4 players from pipeline |
| `/settings` | Program settings | Fit weight sliders (must sum to 100%) + priority weights |

---

## Project Structure

```
MIDS210-Capstone/
├── src/portalpoint/
│   ├── main.py                  # FastAPI app, CORS, router registration, /health
│   ├── core/
│   │   ├── config.py            # pydantic-settings — loads .env
│   │   └── security.py          # JWT encode/decode, bcrypt
│   ├── api/
│   │   ├── deps.py              # CurrentUser + DbSession dependencies
│   │   ├── routers/             # One file per endpoint group
│   │   └── schemas/             # Pydantic request/response models (API contract)
│   └── db/
│       ├── models.py            # SQLAlchemy ORM models
│       └── session.py           # Async session factory
├── frontend/
│   ├── src/
│   │   ├── api/                 # Axios API functions (auth, players, users, fitScores, compare)
│   │   ├── components/          # Shared UI components (AppLayout, FitScoreBar, RecommendationCard)
│   │   ├── context/             # AuthContext (JWT + userId in localStorage)
│   │   ├── pages/               # One file per route
│   │   └── types/api.ts         # TypeScript types mirroring all backend schemas
│   ├── public/                  # Static assets (logos, favicon)
│   └── vite.config.ts           # Dev server + /api proxy to localhost:8000
├── scripts/
│   ├── ingest_barttorvik.py     # barttorvik ETL — loads players/schools/stats
│   ├── ingest_hoop_explorer.py  # Hoop Explorer ETL — team + player play-type stats
│   ├── ingest_hoopr.py          # hoopR ESPN PBP — 5 spatial zones → hoopr_team_season_stats; --game-logs → hoopr_games/team/player_game_logs
│   ├── ingest_transfers_247sports.py  # 247Sports transfer portal → transfer_portal_events (raw) + transfers (promoted)
│   ├── ingest_roster_snapshots.py     # barttorvik rostercast.php → roster_snapshots + roster_snapshot_players
│   ├── run_player_clustering.py # Script rerun for M1 player archetypes
│   ├── run_team_clustering.py   # Script rerun for M2 team systems
│   ├── run_scheme_fit.py        # Script rerun for M3 scheme fit
│   └── run_gap_matching.py      # Script rerun for gap matching
├── notebooks/
│   ├── eda/
│   │   └── eda_hoopr.ipynb            # ESPN coordinate system validation (source of truth)
│   ├── features/
│   │   └── feature_eng_m1_m2_m3.ipynb # BART + HE + hoopR → player_features + team_style_vectors
│   ├── models/
│   │   ├── player_clustering.ipynb    # Model 1 ✅ — K-Means player archetypes (k=9)
│   │   ├── team_clustering.ipynb      # Model 2 ✅ — two-layer K-Means team system profiles
│   │   ├── scheme_fit_scorer.ipynb    # Model 3 ✅ — cosine similarity scheme-cos-v2
│   │   └── gap_matching.ipynb         # Gap Matching ✅ — cosine gap-cos-v2, departure-aware
│   └── requirements-notebooks.txt     # Notebook-only deps — install via uv pip
├── mlruns.db                          # MLflow SQLite tracking store (created on first run)
├── alembic/                     # Database migrations
├── tests/                       # 111 pytest tests across 9 modules
├── .env.example                 # Environment variable template
├── docker-compose.yml           # Redis 7 (PostgreSQL migrated to AWS RDS — only Redis needed locally)
├── docs/                        # Project documentation, diagrams, and model plans
│   ├── status/
│   │   ├── STATUS.md                # Status index
│   │   ├── MODEL_STATUS.md          # Models, feature contracts, artifacts, critical path
│   │   ├── ARCHITECTURE_STATUS.md   # Infrastructure, DB, S3, ingest, MLflow
│   │   └── APPLICATION_STATUS.md    # Product, API, frontend, tests
│   ├── models/                  # Model plans, handoffs, and model data-source notes
│   ├── aws_s3_setup.md          # Team S3 onboarding (keys, smoke test, layout)
│   └── PORTALPOINT_DESIGN_PALETTE.md  # Design token reference
└── README.md                    # Repo overview and setup guide
```

---

## Design Documents

| File | Contents |
|---|---|
| `docs/status/STATUS.md` | Index for the split status docs |
| `docs/status/MODEL_STATUS.md` | Model notebooks, feature contracts, artifacts, critical path, open model questions |
| `docs/status/ARCHITECTURE_STATUS.md` | Local/cloud infrastructure, database, S3, ingest, MLflow |
| `docs/status/APPLICATION_STATUS.md` | Product direction, API routers, frontend pages, tests, app blockers |
| `docs/dataflow_diagram.mmd` | Mermaid: sources → ingest → DB → features → models (all 5 sources) |
| `docs/models/model_dependency_graph.md` | Model input/output contracts, dependency DAG, and Issue #17-28 dependency map |
| `docs/models/gap_matching_plan.md` | Gap Matching model plan and implementation handoff |
| `docs/models/hoopr_integration_plan.md` | hoopR zone geometry, ESPN coordinate system, join strategy, execution order |
| `docs/aws_s3_setup.md` | Team S3 onboarding — keys, bucket layout, smoke test |
| `docs/aws_rds_setup.md` | Team RDS onboarding — password, IP allowlist, connection string, troubleshooting |
| `docs/PortalPoint_Design_Document_MVP.md` | Full product spec, API design, ML pipeline, timeline |
| `docs/PORTALPOINT_DESIGN_PALETTE.md` | Color tokens, typography, spacing — single source of truth for UI |
| `docs/models/player_projection_state_space_plan.md` | State-space player projection system plan |
| `docs/models/role_fit_playing_time_model_plan.md` | Roster-aware playing time, usage role, and Role Fit plan |
| `docs/models/program_fit_model_plan.md` | Manual/proxy Program Fit scoring contract and implementation plan |
| `docs/models/team_rating_projection_roster_tool_plan.md` | Roster-based team rating projection plan |
| `docs/diagram_1_three_layer_architecture.md` | Fit scoring methodology detail |
| `docs/diagram_2_solution_architecture.md` | Full AWS infrastructure design |
| `docs/diagram_3_data_science_workflow.md` | End-to-end ML pipeline with code examples |
| `docs/diagram_4_database_architecture.md` | Database schema overview |
