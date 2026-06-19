# PortalPoint Status Index

**Last updated:** June 18, 2026

The original single status tracker has been split into three focused handoff docs:

| Area | Status doc | Use it for |
|---|---|---|
| Models | [`MODEL_STATUS.md`](MODEL_STATUS.md) | Model notebooks, feature contracts, artifacts, fit-score roadmap, and open modeling questions. |
| Architecture | [`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md) | Local/cloud infrastructure, Postgres/Alembic, S3, ingest, MLflow, and operational runbook context. |
| Application | [`APPLICATION_STATUS.md`](APPLICATION_STATUS.md) | Product direction, backend routers, frontend pages, tests, and app-side blockers. |

Start with `MODEL_STATUS.md` for the current critical path. M1 (re-trained, tuned group-weighted, now writes top-three archetype memberships), M2 (two-layer offense/defense team systems with reviewed labels), M3, and Gap Matching are complete; `fit_scores.py` serves real `scheme_fit` + `gap_match` from `player_team_fit_scores` with dynamic current-season resolution. Role Fit (M4) is the next critical-path model after that.
