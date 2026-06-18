# PortalPoint Application Status

**Last updated:** June 18, 2026  
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
| Scheme Fit | Real | `player_team_fit_scores.scheme_fit`, served via `fit_scores.py`. |
| Gap Match | Real | `player_team_fit_scores.gap_match` (`gap-cos-v1`); sparse/right-skewed by design — most pairs score low, high scores indicate genuine roster need. Served via `fit_scores.py`. |
| Role Fit | Not built | Requires playing time / rotation model (M4). Scalar stubbed at 50.0; breakdown is seeded-random placeholder. |
| Program Fit | Not built | Requires preference/proxy data for NIL, geography, academics, and program constraints. Scalar stubbed at 50.0; breakdown is seeded-random placeholder. |

Current state:

```text
overall_fit = 0.30 * scheme_fit + 0.20 * gap_match + 0.50 * 50.0
```

`overall_fit` is still partial — narrow effective range until role_fit and program_fit are real. Do not present `overall_fit` as a trustworthy ranking signal yet; `gap_match` and `scheme_fit` individually are meaningful now.

---

## Backend API

Backend framework: FastAPI  
Runtime package: `src/portalpoint`  
Interactive docs: `http://localhost:8000/docs`

| Router | Status | Notes |
|---|---|---|
| `auth.py` | Real DB | Signup/login/logout; signup creates `UserPreference`; duplicate email returns 409. |
| `players.py` | Real DB | Player get/search/claim; latest-season stats join; TS normalized for API. |
| `users.py` | Real DB | Preferences and shortlist CRUD; shortlists store `player_id`; user isolation enforced. |
| `fit_scores.py` | Partial real | Queries `player_team_fit_scores` by `(player_id, school_id, season)`, default season 2026. Real `scheme_fit` + `gap_match`; `role_fit`/`program_fit` stubbed at 50.0. Falls back to full stub when no row exists for the triple (pair outside M3/Gap Matching scope). |
| `recommendations.py` | Stub | Program-facing recommendation shape exists; blocked on Model 7 and complete fit scores. |
| `predictions.py` | Stub | Blocked on transfer success model. |
| `projections.py` | Stub | Blocked on team rating projection model. |
| `comparison.py` | Stub/partial | Side-by-side comparison shape exists; richer comparison blocked on full fit scores/projections. |

Important backend modules:

| Path | Purpose |
|---|---|
| `src/portalpoint/main.py` | App factory/router registration. |
| `src/portalpoint/api/deps.py` | DB/auth dependencies. |
| `src/portalpoint/api/schemas/` | Pydantic request/response schemas. |
| `src/portalpoint/core/security.py` | Password hashing/JWT helpers. |
| `src/portalpoint/db/models.py` | SQLAlchemy ORM models. |
| `src/portalpoint/db/session.py` | Async DB session setup. |

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
| Dashboard | `pages/DashboardPage.tsx` | Program landing surface. |
| Player search | `pages/PlayerSearchPage.tsx` | Search/browse players. |
| Player profile | `pages/PlayerProfilePage.tsx` | Player detail surface. |
| Fit score | `pages/FitScorePage.tsx` | Fit score visualization; still depends on partial backend data. |
| Compare | `pages/ComparePage.tsx` | Player comparison shell. |
| Pipeline | `pages/PipelinePage.tsx` | Build/status style page. |
| Settings | `pages/SettingsPage.tsx` | Preferences/account settings. |

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
- Any page that assumes complete fit scores must communicate partial/stub state until all components are real.
- `fit_scores.py` hardcodes `CURRENT_SEASON = 2026` — no season config exists yet. Add a `season` query param override if the frontend needs historical seasons.
- `gap.uniqueness_bonus` / `redundancy_penalty` in the breakdown are hardcoded 0.0 — not yet computed by `gap-cos-v1`.

---

## Tests

Current known state from the prior tracker:

```text
111 tests passing across 8 modules
```

Test areas:

| Test file | Coverage |
|---|---|
| `tests/test_auth.py` | Signup/login/auth edge cases. |
| `tests/test_players.py` | Player endpoints and real DB data shape. |
| `tests/test_users.py` | Preferences/shortlist behavior. |
| `tests/test_fit_scores.py` | Fit score router behavior. |
| `tests/test_recommendations.py` | Recommendation endpoint shape. |
| `tests/test_predictions.py` | Prediction endpoint shape. |
| `tests/test_projections.py` | Projection endpoint shape. |
| `tests/test_comparison.py` | Comparison endpoint shape. |
| `tests/test_health.py` | App health. |

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
-> expose partial but meaningful fit breakdown in UI
-> build Role Fit / Playing Time
-> build Program Fit
-> wire complete fit_scores.py
-> build Recommendation Engine
-> replace recommendation stubs with ranked program-specific players
```

Recommended app-side order:

1. Update the Fit Score page to distinguish real (scheme, gap) from placeholder (role, program) components — `fit_scores.py` now returns real values for the first two.
2. Add clearer empty/loading/error states around missing scores.
3. Make player shortlist actions prominent from search/profile/fit pages.
4. Once recommendations are real, make Dashboard the coach's daily recruiting queue.
5. Keep Compare page focused on decision support: fit breakdown, role, risk, and roster impact.

---

## Application Open Questions

1. How should the UI label partial fit scores while gap/role/program are stubs?
2. Should the Dashboard prioritize recommendations, shortlists, or roster gaps first?
3. What is the minimum explanation required beside each fit score for a coach to trust it?
4. Should coaches be able to override preference weights before Program Fit is fully modeled?
5. Which comparison dimensions matter most for MVP: fit components, archetype, projected minutes, projected impact, or risk?
6. Should the Pipeline page remain a user-facing screen, or move to internal/admin status only?
