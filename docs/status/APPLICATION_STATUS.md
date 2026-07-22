# PortalPoint Application Status

**Last updated:** July 21, 2026 (M5 Transfer Success v2 — inference live, `/api/predictions` wired to `transfer_success_scores`)
**Scope:** Product direction, backend API, frontend, tests, and app-side blockers.

Model context lives in [`MODEL_STATUS.md`](MODEL_STATUS.md). Infrastructure/data-store context lives in
[`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md).

---

## Product Direction

PortalPoint is a program-facing transfer portal scouting platform.

| Dimension | Current direction |
|---|---|
| Primary user | Coaching staffs / programs |
| Core question | Which portal players fit our program? |
| Business model | B2B subscription for programs |
| Core workflow | Search/evaluate players, inspect fit, shortlist, compare, and eventually rank recommendations |
| Main value | Quantitative full-portal evaluation during compressed transfer windows |

This is a pivot from the original player-facing design. User accounts now attach to `school_id`, and shortlists store players rather than programs.

---

## Fit Score Product Model

User-facing fit should eventually expose four components:

| Component | Status | App implication |
|---|---|---|
| Scheme Fit | Real | `player_team_fit_scores.scheme_fit` (shot-distribution cosine), served via `fit_scores.py`. Breakdown fixed 2026-07-15: mislabeled `ball_movement_match` (was actually `mid_range_rate`'s match score) renamed to `mid_range_match`; fake `usage_match` (always a `50.0` stub) removed; real `he_scheme_fit`/`he_breakdown` (6-dim HoopExplorer play-type cosine, computed but never reached the API before) now surfaced. `FitScorePage` shows a display-only average of shot-distribution + play-type match as the headline — the stored `scheme_fit` column and everything that reads it (Overall Fit, Compare, recommendation engine) is untouched. |
| Gap Match | Real | `player_team_fit_scores.gap_match` (`gap-cos-v4` code path, all-pairs, shared roster baseline); sparse/right-skewed by design — most pairs score low, high scores indicate genuine roster need. Served via `fit_scores.py`. |
| Role Fit | Real per-row | `playing-time-rotation-v2` writes `playing_time_projections` and syncs `player_team_fit_scores.role_fit` / `breakdown.role_fit`. `FitScorePage`'s Live/Placeholder indicator now checks `fit.model_version === 'playing-time-rotation-v2'` per response (fixed 2026-07-15 — was previously a blanket "always Placeholder" flag regardless of whether the row was actually synced). Dashboard/Compare still show the blanket flag — no per-player `model_version` in those response shapes yet. |
| Program Fit | Not built, descoped | Requires preference/proxy data for NIL, geography, academics, and program constraints. Scalar hardcoded `50.0` on every real row (confirmed via DB query 2026-07-15 — zero variation across 12M+ rows, not just "pending a model"). UI honesty pass (2026-07-15): fake sub-metric bars removed from `FitScorePage`, replaced everywhere Program Fit appears (FitScorePage, Compare tooltip, Settings caption) with one shared "not live yet, here's what it represents" note. Settings weight sliders stay live/adjustable by product choice even though they currently have no real effect. |

Current state:

```text
overall_fit = weighted(scheme_fit, gap_match, role_fit, program_fit)
```

`overall_fit` is still partial because `program_fit` remains the 50.0 placeholder and component calibration is not final. `scheme_fit`, `gap_match`, and model-written `role_fit` are meaningful individually where rows exist.

---

## Backend API

Backend framework: FastAPI  
Runtime package: `src/portalpoint`  
Interactive docs: `http://localhost:8000/docs`

| Router | Status | Notes |
|---|---|---|
| `auth.py` | Real DB | Signup/login/logout; signup creates `UserPreference`; duplicate email returns 409. |
| `players.py` | Real DB | Player get/search/claim; latest-season stats join; TS normalized for API. `/search` takes `available_only` to restrict to matched Entered/Committed `transfer_portal_events` rows for the player's latest season. `GET /{id}/projection` serves neutral Phase 2a next-season forecasts (`player-proj-phase2a-fcast-v1`) by default, or destination-adjusted projections when `?school_id=X` is passed — the destination branch existed and was tested but was never called from the frontend until 2026-07-15, when `FitScorePage` (school-specific everywhere else) was switched to it. Destination mode's `projected_box_score` uses `_per_game` keys scaled to real expected minutes, not neutral's `_per_40` keys — frontend picks the right label map by `projection_mode`. See `MODEL_STATUS.md`'s Player Projection section. |
| `users.py` | Real DB | Preferences and shortlist CRUD; shortlists store `player_id`; user isolation enforced. |
| `fit_scores.py` | Partial real | Queries `player_team_fit_scores` by `(player_id, school_id, season)`, dynamic current-season default. Real `scheme_fit` + `gap_match` (both all-pairs now — `scheme-cos-v3`/`gap-cos-v4` code path) and 2027 `role_fit` where `playing-time-rotation-v2` has synced rows. `program_fit` remains stubbed at 50.0. Response includes `is_portal_candidate` (player has a matched Entered/Committed portal event this season), `is_current_school` (raw player_season_stats row for this school/season), `is_roster_baseline_member` (player counts in the shared roster baseline used by roster-aware models), and (Gate 7) `scheme_fit_stale`/`scheme_fit_stale_reason` — set when the news-monitoring agent has detected a coaching change at that school and M2 `team_system_profiles` has not yet been re-run. Falls back to full stub only when the pair predates model scope. |
| `recommendations.py` | Real, live | Wired 2026-07-15 — was a hardcoded stub list since the original scaffold, on every branch, despite the real 2-stage engine (`src/portalpoint/modeling/recommendations.py`, `rec-v1.2`) existing and being run for real by the batch script. Router now resolves the caller's `school_id`/saved weights, builds the candidate pool live via `CANDIDATE_SQL` (moved into `modeling/recommendations.py` — `scripts/` isn't a packaged module, can't be imported from the API), and runs `generate_top_50_candidates()`/`refine_to_top_10()` in-process per request. `FitComponents.program_fit` → `team_impact_fit` in this response only (engine has no program_fit signal). |
| `predictions.py` | Real, live | Wired 2026-07-21 — reads `transfer_success_scores` via `transfer_success_service.py` (`transfer-success-eb-v2`, season default from latest non-expired rows). Returns `success_probability`, `success_tier`, `explanation`, and `similar_transfers` (M5-native schema; dropped legacy PER/minutes/SHAP stub fields). Redis-cached 30 min. 404 when pair missing or expired. |
| `projections.py` | Real, live | `GET /api/projections/team-rating` reads `team_rating_projections` (`team-roster-proj-v1`); top-roster-impact endpoint ranks portal candidates by `delta_adj_em`. |
| `comparison.py` | Partial real | Fit scores are real; `prediction` field now uses `transfer_success_service` (falls back to `model_version="unavailable"` when no score row). Richer comparison still blocked on full fit calibration and frontend surfacing of transfer success. |

Important backend modules:

| Path | Purpose |
|---|---|
| `src/portalpoint/main.py` | App factory/router registration. |
| `src/portalpoint/api/services/fit_score_service.py` | Shared `player_team_fit_scores` → `FitScoreResponse` mapping (used by fit scores + compare). |
| `src/portalpoint/api/services/transfer_success_service.py` | Shared `transfer_success_scores` → `PredictionResponse` mapping (used by predictions + compare). |
| `src/portalpoint/api/schemas/` | Pydantic request/response schemas. |
| `src/portalpoint/core/security.py` | Password hashing/JWT helpers. |
| `src/portalpoint/db/models.py` | SQLAlchemy ORM models. |
| `src/portalpoint/db/session.py` | Async DB session setup. |
| `src/portalpoint/modeling/entity_resolution.py` | Shared player/school fuzzy-match module — used by both `ingest_transfers_247sports.py` and the news-monitoring agent. |
| `src/portalpoint/agents/news_monitoring/` | LangGraph ReAct news-monitoring agent (`state.py`, `config.py`, `extract.py`, `resolve.py`, `graph.py`, `sources/tavily.py`). Entrypoint: `scripts/run_news_monitoring.py` — loads `.env`, supports `--dry-run` / `--season` / `--window-days`. Live run path: Tavily search → Gemini classify → `transfer_player` / `coach_departure` DB writes. `collect_results` node parses tool outputs into run summary. **Verified 2026-07-16:** live CLI run succeeds end-to-end against RDS (0 events in a 1-day window is expected off-peak); 40 unit/integration tests green. Still manual-only — no scheduler/Airflow yet. |

---

## Frontend

Frontend framework: React + Vite + TypeScript  
Frontend root: `frontend/`  
Local URL: `http://localhost:5173`

Implemented pages:

| Page | File | Current role |
|---|---|---|
| App shell | `frontend/src/App.tsx`, `components/AppLayout.tsx` | Routing/layout. |
| Login/signup | `pages/LoginPage.tsx`, `pages/SignupPage.tsx` | Auth flow. |
| Overview | `pages/OverviewPage.tsx` | New 2026-07-15 — plain-language landing page (goal, qualitative "what this does" tiles), first nav item. |
| Dashboard | `pages/DashboardPage.tsx` | Program landing surface. Recommendation cards now show real, varied component scores (was a fixed 10-row stub sequence before the recommendations router wiring) with a tiered-verdict reasoning line. |
| Player search | `pages/PlayerSearchPage.tsx` | Search/browse players. |
| Player profile | `pages/PlayerProfilePage.tsx` | Player detail surface; neutral (context-free) Player Projection, unchanged. |
| Fit score | `pages/FitScorePage.tsx` | Fit score visualization. Destination-adjusted Player Projection (was neutral), per-row Role Fit Live/Placeholder accuracy, restructured Scheme Fit section (Shot Distribution + Play Type groups), key-insight summary strip, Program Fit honesty note — all 2026-07-15. |
| Compare | `pages/ComparePage.tsx` | Player comparison; header-row CSS bug fixed, verdict summary added above the matrix, Program Fit tooltip. |
| Pipeline | `pages/PipelinePage.tsx` | Build/status style page. |
| Settings | `pages/SettingsPage.tsx` | Preferences/account settings; Program Fit weight slider now has an inline "not live yet" caption. |
| Glossary | `pages/GlossaryPage.tsx` | New 2026-07-15 — definitions for every fit component/metric, last nav item. |

API clients:

| File | Purpose |
|---|---|
| `frontend/src/api/client.ts` | Shared API client setup. |
| `frontend/src/api/auth.ts` | Login/signup/logout calls. |
| `frontend/src/api/players.ts` | Player search/detail calls. |
| `frontend/src/api/users.ts` | Preferences/shortlist calls. |
| `frontend/src/api/fitScores.ts` | Fit score calls. |
| `frontend/src/api/recommendations.ts` | Recommendation calls. |
| `frontend/src/api/compare.ts` | Comparison calls. |

Design reference:

| Doc | Purpose |
|---|---|
| [`../PORTALPOINT_DESIGN_PALETTE.md`](../PORTALPOINT_DESIGN_PALETTE.md) | Visual tokens, colors, typography, component guidance. |
| [`../PortalPoint_Design_Document_MVP.md`](../PortalPoint_Design_Document_MVP.md) | MVP product/design narrative. |

---

## Auth And User State

Current app assumptions:

- Protected routes require JWT auth.
- Signup immediately creates a user and default preferences.
- User accounts are linked to programs via `school_id`.
- Shortlists are player lists for a program, not school lists for a player.
- The app should avoid presenting player-facing language such as "programs that fit me."

Watch-outs:

- Default JWT expiry is one hour; set `JWT_EXPIRY_SECONDS=86400` locally if helpful.
- Any page that assumes complete fit scores must communicate partial state until Program Fit and final calibration are real.
- `fit_scores.py` resolves current season dynamically (`fit_score_service.get_current_season()` — max season in `player_team_fit_scores`, Redis-cached); `season` query param overrides it for historical seasons.
- `gap.uniqueness_bonus` / `redundancy_penalty` in the breakdown are hardcoded 0.0 — not yet computed by `gap-cos-v4`.

---

## Tests

Current state (2026-07-21):

```text
660 passed, 10 skipped repo-wide (uv run pytest -q) — with RDS tunnel up.
tests/test_transfer_success.py (28) + tests/test_transfer_success_service.py (3) added for M5 v2.
```

Test areas:

| Test file | Coverage |
|---|---|
| `tests/test_auth.py` | Signup/login/auth edge cases. |
| `tests/test_players.py` | Player endpoints and real DB data shape. |
| `tests/test_users.py` | Preferences/shortlist behavior. |
| `tests/test_fit_scores.py` | Fit score router behavior — includes Scheme Fit breakdown field checks (`mid_range_match`, not `usage_match`/`ball_movement_match`, 2026-07-15). |
| `tests/test_recommendations.py` | Recommendation endpoint — now exercises the real engine end-to-end (real `user_id` fixture, `season` pinned explicitly, `team_impact_fit` field, ownership-check 403 case), not just response shape. |
| `tests/test_recommendation_engine.py` | Pure-unit tests for `modeling/recommendations.py` (no DB) — 46 tests. |
| `tests/test_predictions.py` | Transfer success endpoint — real DB rows, M5 response shape, expiry 404. |
| `tests/test_transfer_success.py` | M5 modeling unit tests (hierarchy, covariates, calibration, inference explanations). |
| `tests/test_transfer_success_service.py` | API service mapping for `similar_transfers` JSONB → response schema. |
| `tests/test_projections.py` | Projection endpoint shape. |
| `tests/test_comparison.py` | Comparison endpoint shape. |
| `tests/test_health.py` | App health. |
| `tests/test_news_monitoring.py` | News agent classifiers, dedup, graph routing, `collect_results` parsing (36 tests). |
| `tests/test_news_monitoring_integration.py` | Live RDS write pipeline for `transfer_player` + `coach_departure` (4 tests; skips without tunnel/seed data). |

Run tests:

```bash
uv run pytest
```

Recent fixture/testing notes:

- Test user setup signs up/logs in to get dynamic `user_id`.
- Signup tests use unique emails to avoid cross-run 409 conflicts.
- Stats assertions are relaxed to match real loaded data.

---

## App Critical Path

The app becomes truly useful when the fit stack is no longer mostly stubbed.

```text
✅ Build Gap Matching
✅ wire fit_scores.py to real scheme_fit + gap_match
✅ run Role Fit / Playing Time full 2027 write
✅ build Recommendation Engine v1 script
✅ expose partial but meaningful fit breakdown in UI (frontend explainability pass, 2026-07-15)
✅ merge/rerun Team Rating Projection PR #49
✅ replace recommendation stubs with ranked program-specific players (2026-07-15)
✳️ build Program Fit — descoped, not on this path
-> wire complete fit_scores.py (reweight decision pending, program_fit descoped)
✅ news-monitoring agent manual live runs (2026-07-16)
✅ M5 Transfer Success v2 inference + `/api/predictions` wiring (2026-07-21)
-> schedule news agent (GitHub Actions cron or Airflow)
-> surface transfer success on Fit Score / Compare UI (API ready, no frontend consumer yet)
```

Recommended app-side order:

1. Update the Fit Score page to distinguish real (scheme, gap, role where synced) from placeholder/incomplete Program Fit and calibration state.
2. Add clearer empty/loading/error states around missing scores.
3. Make player shortlist actions prominent from search/profile/fit pages.
4. Once recommendations are real, make Dashboard the coach's daily recruiting queue.
5. Keep Compare page focused on decision support: fit breakdown, role, risk, and roster impact.

---

## Application Open Questions

1. ✅ Partially resolved (2026-07-15) — Program Fit now has a consistent "not live yet, here's what it represents" note everywhere it appears (FitScorePage, Compare, Settings), sourced from one shared string. Open remainder: whether/how to communicate final-calibration status once `overall_fit`'s weighting question (3 vs. 4 components) is decided.
2. Should the Dashboard prioritize recommendations, shortlists, or roster gaps first?
3. What is the minimum explanation required beside each fit score for a coach to trust it? Partial answer landed 2026-07-15 (key-insight summary strips, hover tooltips on every component/sub-metric, Glossary page) — still open whether this is *sufficient*, not just present.
4. Should coaches be able to override preference weights before Program Fit is fully modeled? (Settings sliders are live/adjustable already, per 2026-07-15 product decision, even though Program Fit's slider has no real effect yet.)
5. Which comparison dimensions matter most for MVP: fit components, archetype, projected minutes, projected impact, or risk? Compare page gained a plain-language verdict summary 2026-07-15 (which player is the stronger fit and why) — doesn't resolve which raw dimensions to prioritize.
6. Should the Pipeline page remain a user-facing screen, or move to internal/admin status only?
7. Should `he_scheme_fit` (now surfaced 2026-07-15) actually feed `scheme_fit`'s weight in `overall_fit`/ranking, not just this page's own display average? Same class of open question as #4 for Program Fit.
