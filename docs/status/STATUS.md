# PortalPoint Status Index

**Last updated:** June 22, 2026

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

**2026-06-20/21 — Issue #17 (Remaining Data Loading), items 1-4:** hoopR game-level logs (`hoopr_games`/`hoopr_team_game_logs`/`hoopr_player_game_logs`, via `ingest_hoopr.py --game-logs`), 247Sports transfer-portal ingest (`transfer_portal_events` + `transfers`, via `ingest_transfers_247sports.py` — season 2026 done, 2020-2026 backfill command documented in `ARCHITECTURE_STATUS.md` but not yet run), and barttorvik roster snapshots (`roster_snapshots`/`roster_snapshot_players`, via `ingest_roster_snapshots.py` — verified on one school, full ~365-school run not yet done). Items 5-8 (derived `player_team_seasons`, roster-state features, projection output tables, Program Fit proxy) remain open. See `ARCHITECTURE_STATUS.md` for full detail and backfill commands.

**2026-06-22 — Scheme Fit + Gap Matching unified all-pairs (PR #33 portal-scope follow-ups):** Scheme Fit (`scheme-cos-v3`) and Gap Matching (`gap-cos-v3`) both rebuilt to score every eligible player×school×season pair — Scheme Fit was top-50-per-player, Gap Matching was scoped to whatever Scheme Fit pre-seeded. `player_team_fit_scores.is_portal_candidate` added (migration `c7f1a9d3e652`) as the recommendation-surface scope flag, kept in sync by `portalpoint.modeling.availability` from both the 247Sports ingest and the Gap Matching rerun — no standalone backfill script. `fit_scores.py` responses now include `is_portal_candidate`/`is_current_school`; `players.py /search` gained `available_only`. Two real bugs fixed at the new scale: Gap Matching wrote `role_fit`/`program_fit` as `0.0` instead of the `50.0` stub `overall_fit` actually assumes (wrong `overall_fit` on ~8.4M rows), and Gap Matching's "preserve existing Scheme Fit" step preloaded the whole table into one python dict (~64min at ~9.6M rows) — replaced with a per-chunk query. See `MODEL_STATUS.md` and CLAUDE.md Process Improvement TODO #5 for full detail.

**2026-06-22 — Roster baseline branch:** `roster-baseline` introduces `portalpoint.modeling.roster_baseline` as the shared roster-outlook contract for Gap Matching, Role Fit, and Team Rating Projection. Gap Matching moves to `gap-cos-v4` code semantics: historical seasons infer target roster membership from `player_season_stats(S+1)`, while the latest season uses latest `roster_snapshots` with expected-departure fallback for schools without usable snapshots. Availability remains separate via `is_portal_candidate`; fit-score responses now also expose `is_roster_baseline_member`.
