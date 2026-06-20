# PortalPoint Status Index

**Last updated:** June 20, 2026

The original single status tracker has been split into three focused handoff docs:

| Area | Status doc | Use it for |
|---|---|---|
| Models | [`MODEL_STATUS.md`](MODEL_STATUS.md) | Model notebooks, feature contracts, artifacts, fit-score roadmap, and open modeling questions. |
| Architecture | [`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md) | Local/cloud infrastructure, Postgres/Alembic, S3, ingest, MLflow, and operational runbook context. |
| Application | [`APPLICATION_STATUS.md`](APPLICATION_STATUS.md) | Product direction, backend routers, frontend pages, tests, and app-side blockers. |

Start with `MODEL_STATUS.md` for the current critical path. M1 (re-trained, tuned group-weighted, now writes top-three archetype memberships), M2 (two-layer offense/defense team systems with reviewed labels), M3, and Gap Matching are complete; `fit_scores.py` serves real `scheme_fit` + `gap_match` from `player_team_fit_scores` with dynamic current-season resolution. Role Fit (M4) is the next critical-path model after that.

For the model dependency DAG, input/output contracts, and planned run order, see
[`../models/model_dependency_graph.md`](../models/model_dependency_graph.md).

**2026-06-19/20 local refresh:** M1, M2, M3, and Gap Matching now have repeatable script reruns; local Postgres was refreshed through `player_team_fit_scores` with `1,343,050` rows containing both `scheme_fit` and `gap_match`. M1 had a critical bug (no semantic cluster reordering — labels silently scrambled on every rerun); fixed and verified. 3 player archetype labels renamed based on DB-validated `def_adj_rapm` evidence. A `players.id` portability gotcha was found and documented (see `ARCHITECTURE_STATUS.md`) — pulled `data/features/*.parquet` files can reference player IDs that don't exist in your local DB.
