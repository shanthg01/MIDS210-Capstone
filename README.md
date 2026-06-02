# PortalPoint

Data-driven transfer portal recommendation platform for college basketball. Helps players find optimal transfer destinations via multi-dimensional fit scoring across Gap Matching, Scheme Fit, Playing Opportunity, and Personal Fit.

## Status

FastAPI application shell is complete. All endpoints return realistic, deterministic stub data. ML models (Phase 2) are not yet wired in — each router stub body is the replacement target.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/shanthg01/MIDS210-Capstone.git
cd MIDS210-Capstone
cp .env.example .env          # fill in DB/Redis/JWT values
uv sync
```

## Running

```bash
uv run uvicorn portalpoint.main:app --reload
```

- API root: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

## Testing

```bash
uv run --with pytest pytest tests/ -v          # full suite (108 tests)
uv run --with pytest pytest tests/test_fit_scores.py -v   # single file
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/signup` | — | Create account, returns JWT |
| POST | `/api/auth/login` | — | Login, returns JWT |
| POST | `/api/auth/logout` | — | Logout |
| GET | `/api/players/{id}` | — | Player profile + stats + archetype |
| GET | `/api/players/search?name=` | — | Fuzzy player search |
| POST | `/api/players/{id}/claim` | Bearer | Link account to player profile |
| GET | `/api/recommendations?user_id=` | Bearer | Top-10 school recommendations |
| GET | `/api/fit-scores?player_id=&school_id=` | Bearer | 4-component fit score breakdown |
| GET | `/api/predictions?player_id=&school_id=` | Bearer | Transfer outcome prediction + SHAP |
| GET | `/api/projections/team-rating?player_id=&school_id=` | Bearer | Delta-AdjEM projection with CI |
| GET | `/api/users/{id}/preferences` | Bearer | User preference weights and filters |
| PUT | `/api/users/{id}/preferences` | Bearer | Update preference weights and filters |
| GET | `/api/users/{id}/shortlist` | Bearer | Saved school shortlist |
| POST | `/api/users/{id}/shortlist/{school_id}` | Bearer | Add school to shortlist |
| DELETE | `/api/users/{id}/shortlist/{school_id}` | Bearer | Remove school from shortlist |
| POST | `/api/compare` | Bearer | Side-by-side comparison of 2-4 schools |

## Project Structure

```
src/portalpoint/
  main.py              # App factory, CORS, router registration, /health
  core/
    config.py          # Settings via pydantic-settings (.env)
    security.py        # JWT + bcrypt
  api/
    deps.py            # CurrentUser auth dependency
    routers/           # One file per endpoint group (stubs for Phase 2)
    schemas/           # Pydantic request/response models (API contract)
  db/
    models.py          # SQLAlchemy ORM stubs (Phase 2)
    session.py         # Async session factory (Phase 2)
tests/                 # 108 pytest tests across 9 files
```

## Design Documents

| File | Contents |
|------|----------|
| `PortalPoint_Design_Document_MVP.md` | Full product spec, API design, ML pipeline, timeline |
| `diagram_1_three_layer_architecture.md` | Fit scoring methodology detail |
| `diagram_2_solution_architecture.md` | Full AWS infrastructure design |
| `diagram_3_data_science_workflow.md` | End-to-end ML pipeline with code examples |
| `diagram_4_database_architecture.md` | Database schema overview |
