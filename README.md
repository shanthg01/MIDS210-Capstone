# PortalPoint

Data-driven transfer portal scouting platform for college basketball programs. Coaching staffs evaluate 2,500+ portal entrants via multi-dimensional fit scoring — Scheme Fit, Gap Matching, Role Fit, and Program Fit — ranked by a composite score into a per-program recommendation feed.

**Primary user:** Coaching staffs / programs (subscription B2B).  
**Core value:** Quantitative player evaluation across the full portal in the 3–4 week evaluation window.

---

## Current State

| Layer | Status |
|---|---|
| Backend API (FastAPI) | All endpoints live — protected routes require JWT |
| Database (PostgreSQL + Alembic) | 3 migrations applied; ~4,500 players + 364 schools loaded from barttorvik |
| ML Models 1–2 (clustering) | Complete — artifacts on disk, results in DB |
| ML Model 3 (Scheme Fit scorer) | Complete — cosine similarity, pre-computed scores in DB |
| Gap Matching, Role Fit, Program Fit | In progress — routers return deterministic stubs |
| Recommendation Engine (Model 7) | Blocked on remaining fit components |
| Frontend (React + Vite) | 8 pages implemented against live API |

---

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Backend runtime |
| [uv](https://docs.astral.sh/uv/) | latest | Python package/venv manager |
| Node.js | 18+ | Frontend build |
| npm | 9+ | Frontend package manager |
| Docker Desktop | latest | PostgreSQL + Redis |

---

## Quick Start (Full Stack)

### 1. Clone and configure

```bash
git clone https://github.com/shanthg01/MIDS210-Capstone.git
cd MIDS210-Capstone
cp .env.example .env
```

Edit `.env` — the defaults work for local Docker. At minimum verify `JWT_SECRET` is set to any non-empty string for local dev.

### 2. Start infrastructure (PostgreSQL + Redis)

```bash
docker compose up -d
```

This starts:
- PostgreSQL 15 on port **5433** (mapped to avoid conflict with local Postgres)
- Redis 7 on port **6379**

### 3. Install Python dependencies and apply migrations

```bash
uv sync
uv run alembic upgrade head
```

### 4. Load barttorvik data (2025 season)

```bash
uv run python scripts/ingest_barttorvik.py
```

Takes 2–3 minutes. Loads ~4,500 players and 364 schools into the DB.

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
| `DATABASE_URL` | `postgresql+asyncpg://postgres:password@localhost:5433/portalpoint` | Port 5433 = Docker-mapped Postgres |
| `REDIS_URL` | `redis://localhost:6379` | |
| `JWT_SECRET` | `change-me-in-production-use-a-long-random-string` | Any string works locally; use a random 32+ char string in production |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_EXPIRY_SECONDS` | `3600` | Set to `86400` in local dev to avoid hourly re-login |
| `ENVIRONMENT` | `development` | |
| `MLFLOW_TRACKING_URI` | `sqlite:///mlruns.db` | SQLite at repo root for local dev; set to S3 URI for prod |

**Token expiry note:** The default 1-hour expiry means you'll be logged out after 60 minutes. For active development, set `JWT_EXPIRY_SECONDS=86400` in `.env`.

---

## ML Model Tracking (MLflow)

MLflow tracks all model runs, parameters, metrics, and artifacts for Models 1–3. It is a **notebook-only dependency** — not in `pyproject.toml` because mlflow's metadata pins `pandas<3`, which conflicts with `uv sync` resolution against pandas 3. Install it separately:

```bash
# Install once from repo root (uv pip is lenient; does NOT downgrade pandas)
uv pip install mlflow
```

### Tracking backend

All runs write to `mlruns.db` (SQLite) at the repo root. The path is resolved automatically by `notebooks/utils/mlflow_helpers.py` — no CWD dependency. If `MLFLOW_TRACKING_URI` is set in `.env`, that URI is used instead (must be a `sqlite:///` or remote URI — `file:` paths are rejected by mlflow 3.x).

### Launch the MLflow UI

```bash
# From repo root
mlflow ui --backend-store-uri sqlite:///mlruns.db
```

Open http://127.0.0.1:5000. Three experiments are visible:

| Experiment | Model | Key metric | Registry name |
|---|---|---|---|
| `player-clustering` | K-Means player archetypes (k=10) | `silhouette_score` | `player-clustering` |
| `team-clustering` | K-Means team system profiles (k=9) | `silhouette_score` | `team-clustering` |
| `scheme-fit-scorer` | Cosine similarity scheme fit | `n_records_written` | `scheme-fit-scorer` |

### Auto-promotion logic

Each notebook run registers a new model version. `maybe_promote()` in `mlflow_helpers.py` compares the new run's key metric against the current Production version:

- **First run ever** → automatically promoted to `Production`
- **Improvement > 5%** → promoted to `Production`
- **Improvement ≤ 5%** → sent to `Staging`

### Shared helper

`notebooks/utils/mlflow_helpers.py` provides three functions used by all model notebooks:

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
| GET | `/api/fit-scores?player_id=&school_id=` | 4-component fit score breakdown (scheme real, others stub) |
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
│   └── ingest_barttorvik.py     # Barttorvik ETL — loads players/schools/stats
├── notebooks/
│   ├── models/
│   │   ├── player_clustering.ipynb    # Model 1 — K-Means player archetypes
│   │   ├── team_clustering.ipynb      # Model 2 — K-Means team system profiles
│   │   └── scheme_fit_scorer.ipynb    # Model 3 — cosine similarity scheme fit
│   ├── utils/
│   │   └── mlflow_helpers.py          # Shared MLflow helpers (setup, auto-promote)
│   └── requirements-notebooks.txt     # Notebook-only deps — install via uv pip
├── mlruns.db                          # MLflow SQLite tracking store (created on first run)
├── alembic/                     # Database migrations
├── tests/                       # 111 pytest tests across 9 modules
├── .env.example                 # Environment variable template
├── docker-compose.yml           # PostgreSQL 15 + Redis 7
├── docs/                        # Project documentation, diagrams, and model plans
│   └── PORTALPOINT_DESIGN_PALETTE.md  # Design token reference
└── README.md                    # Repo overview and setup guide
```

---

## Design Documents

| File | Contents |
|---|---|
| `docs/PortalPoint_Design_Document_MVP.md` | Full product spec, API design, ML pipeline, timeline |
| `docs/PORTALPOINT_DESIGN_PALETTE.md` | Color tokens, typography, spacing — single source of truth for UI |
| `docs/player_projection_state_space_plan.md` | State-space player projection system plan |
| `docs/playing_time_rotation_model_plan.md` | Roster-aware playing time, usage role, and Role Fit plan |
| `docs/team_rating_projection_roster_tool_plan.md` | Roster-based team rating projection plan |
| `docs/diagram_1_three_layer_architecture.md` | Fit scoring methodology detail |
| `docs/diagram_2_solution_architecture.md` | Full AWS infrastructure design |
| `docs/diagram_3_data_science_workflow.md` | End-to-end ML pipeline with code examples |
| `docs/diagram_4_database_architecture.md` | Database schema overview |
