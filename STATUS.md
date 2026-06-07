# PortalPoint — Project Status

**Date:** June 7, 2026  
**Current branch:** `main`  
**Test suite:** 111 tests passing

---

## Strategic Pivot: Player-Facing → Program-Facing

The original design positioned players as the primary user (players discovering programs that fit them). The pivot makes **coaching staffs / programs** the primary user.

### What changed and why

| Dimension | Before | After |
|---|---|---|
| Primary user | Transfer portal player | Coaching staff / program |
| Core question | "Which programs fit me?" | "Which portal players fit our program?" |
| Recommendations | Programs ranked for a player | Players ranked for a program |
| Shortlist | Programs a player saves | Players a program is recruiting |
| User account linked to | `player_id` | `school_id` (program) |
| Fit component 3 | Playing Opportunity (player perspective) | Role Fit (program perspective — will this player fill a role we need?) |
| Fit component 4 | Personal Fit (player's NIL/academics/geography preferences) | Program Fit (NIL budget alignment, geographic recruiting range, academic eligibility from program's perspective) |

**Rationale:** Programs are the paying customer ($5K–15K/year subscriptions), have the acute pain point (2,500+ portal entrants, compressed 3-4 week evaluation window), and are underserved by existing platforms. A player-facing product requires player acquisition at scale before it has value; a program-facing product delivers value to each program independently on day one.

### DB schema changes (migration `4f15ed03ddbf`)

- `player_team_fit_scores`: `opportunity` + `personal_fit` → `role_fit` + `program_fit`; weight columns updated to match
- `user_preferences`: importance weights reframed (playing_time/nil/academics/location → scheme_fit/role_fit/gap_match/program_fit)
- `user_shortlists`: `school_id` FK → `player_id` FK (shortlists now hold players, not schools)
- `users`: `player_id` FK → `school_id` FK (user account linked to program, not player profile)
- `recommendations`: `school_id` dropped (recommendations are players ranked for a program, not schools ranked for a player)

---

## What Is Built

### Infrastructure

| Component | Status | Notes |
|---|---|---|
| PostgreSQL 15 (Docker) | ✅ Running | Port 5433 |
| Redis 7 (Docker) | ✅ Running | Port 6379 |
| Alembic migrations (3) | ✅ Applied | `064d7a23e792` initial schema → `b683e0eae93e` barttorvik_id → `4f15ed03ddbf` program pivot |
| MLflow | ❌ Not started | Needed before Model 4 |
| Airflow DAGs | ❌ Not started | Target: Week 9–10 |
| Redis caching layer | ❌ Not started | Add when first router wired to real fit scores |

### Data Pipeline

| Stage | Status | Output |
|---|---|---|
| Barttorvik ETL (`scripts/ingest_barttorvik.py`) | ✅ Complete | ~4,500 players, 364 schools, 2025 season stats in DB |
| Feature engineering — player (`barttorvik_feature_eng.ipynb`) | ✅ Complete | `data/features/player_features.parquet` |
| Feature engineering — team (`barttorvik_feature_eng.ipynb`) | ✅ Complete | `data/features/team_style_vectors.parquet` |
| Roster gap analysis | ❌ Not started | Required for Gap Matching (Component 1) |

### ML Models

| # | Model | Status | Artifacts | DB Table |
|---|---|---|---|---|
| 1 | Player Clustering (K-Means, K=10) | ✅ Complete | `player_kmeans.pkl`, `player_scaler.pkl`, `player_archetype_labels.pkl` | `player_archetypes` |
| 2 | Team System Clustering (K-Means, K=9) | ✅ Complete | `team_kmeans.pkl`, `team_scaler.pkl`, `team_system_labels.pkl` | `team_system_profiles` |
| 3 | Scheme Fit Scorer (cosine similarity) | ✅ Complete | — (no training, deterministic) | `player_team_fit_scores` (scheme_fit col) |
| — | Gap Matching (cosine similarity) | ❌ Not started | — | `player_team_fit_scores` (gap_match col) |
| 4 | Playing Time / Role Fit Predictor (PyMC3) | ❌ Not started | — | `player_team_fit_scores` (role_fit col) |
| — | Program Fit Calculator (MAUT) | ❌ Not started | — | `player_team_fit_scores` (program_fit col) |
| 5 | Transfer Success Predictor (XGBoost) | ❌ Not started | — | `predictions` |
| 6 | Team Rating Projection (XGBoost) | ❌ Not started | Depends on Model 4 | `team_rating_projections` |
| 7 | Recommendation Engine (SVD + content + fit) | ❌ Not started | Depends on all 4 fit components | `recommendations` |

**Current fit score state:** `overall_fit = 0.30 × scheme_fit + 0.70 × 50.0` (gap/role_fit/program_fit stubbed at 50 until built). Pre-computed top-50 schools per player for 2025 season.

### API Routers

| Router | Status | Notes |
|---|---|---|
| `auth.py` (signup, login, logout) | ✅ Real DB | Signup creates `UserPreference` row atomically; duplicate email → 409 |
| `players.py` (get, search, claim) | ✅ Real DB | `true_shooting_pct` normalized from barttorvik's 0-100 → 0-1; latest-season subquery join |
| `users.py` (preferences, shortlist CRUD) | ✅ Real DB | Shortlist now tracks player_ids (post-pivot); `_check_auth` enforces user isolation |
| `fit_scores.py` | ⚠️ Stub | Reads from `player_team_fit_scores` blocked on gap + role_fit + program_fit components |
| `recommendations.py` | ⚠️ Stub | Returns players ranked for program; blocked on Model 7 |
| `predictions.py` | ⚠️ Stub | XGBoost transfer success; blocked on Model 5 |
| `projections.py` | ⚠️ Stub | Team rating delta AdjEM; blocked on Model 6 |
| `comparison.py` | ⚠️ Stub | Side-by-side player comparison; blocked on full fit scores |

### Tests

111 tests passing across 8 modules. Fixed after real DB wiring:
- Test user seeded idempotently in `client` fixture (signup → login to get actual `user_id`)
- Signup tests use `uuid`-prefixed unique emails to avoid cross-run 409 conflicts
- `test_users.py` uses dynamic `user_id` fixture (not hardcoded `1001`)
- Stats assertions relaxed to match real data (`per >= 0`, `true_shooting_pct < 1`)

---

## What Is Next

### Critical path (blocks full fit score + recommendations)

```
Gap Matching notebook          ← next immediate step
        ↓
wire fit_scores.py (partial)   ← scheme_fit + gap_match real, role/program stubbed
        ↓
Model 4: Role Fit (PyMC3)      ← playing time → role_fit score
        ↓
Program Fit calculator         ← MAUT on user_preferences.importance_weights
        ↓
wire fit_scores.py (full)      ← all 4 components live
        ↓
Model 7: Recommendation Engine ← unblocks recommendations.py
```

### Off critical path (can parallelize)

- **Model 5: Transfer Success** (XGBoost) → unblocks `predictions.py`
- **Model 6: Team Rating Projection** (needs Model 4 output) → unblocks `projections.py`

### Infrastructure triggers

| Task | Trigger |
|---|---|
| Add MLflow to Docker Compose + instrument M1–M3 retroactively | **Now** — before Model 4 (M6 depends on M4 artifact) |
| Redis caching layer in `fit_scores.py` | When `fit_scores.py` wired to real DB |
| Airflow DAGs + Docker Compose airflow service | After all critical-path models built (~Week 9–10) |

---

## Gap Matching — Build Plan

Next model to build. No new external data required; derivable from existing DB.

**Feature space:** `[ppg, rpg, apg, spg, bpg, ts_pct, usage_rate, three_point_rate]`

**Steps:**
1. Check position coverage in `player_season_stats` — if >70% populated, compute per-position gaps; else school-wide aggregate
2. Compute league position benchmarks (mean stats per position across all players)
3. Compute post-departure roster per school: exclude players in `transfers` table who have `from_school_id = school` with `to_school_id IS NOT NULL`
4. Gap vector per school = `max(0, benchmark[pos] - roster_current[school, pos])` — flattened across positions
5. Player stat vectors — normalize to same scale as gap vectors
6. Batch cosine similarity (`sklearn.metrics.pairwise.cosine_similarity`, same pattern as Model 3)
7. Scale ×100 → [0, 100]; write `gap_match` to `player_team_fit_scores` via `execute_values`

---

## Open Design Questions

1. **Gap matching — position handling:** Per-position (more accurate, needs clean position data) vs. school-wide aggregate (simpler). Decide after checking position coverage in DB.
2. **Player archetype labels (Model 1):** `ARCHETYPE_LABELS` dict in `player_clustering.ipynb` still uses auto-generated candidates — review centroid heatmap and fill in final labels before using in Gap Matching.
3. **Team system labels (Model 2):** Auto-generated via taxonomy distance matching — review `SYSTEM_LABELS` dict for accuracy.
4. **MLflow retroactive instrumentation:** M1 and M2 notebooks have no MLflow tracking yet. Add before M4 so full model lineage exists.
5. **Program Fit data gaps:** `nil_valuations` and `schools.nil_estimated_budget_usd` are not populated from barttorvik (no public NIL data). NIL fit score will require either manual data entry, third-party source, or a proxy (conference tier, market size). Decide before building Program Fit calculator.
6. **NCAA/FERPA compliance:** Legal review required before public launch. Use only public data; document data sources clearly.

---

## MVP Success Criteria (Week 12 target)

- [ ] 2,500+ portal players in DB with complete stats *(2025 season loaded; portal subset to verify)*
- [ ] All core endpoints < 500ms
- [ ] All 4 fit components computing (not stubbed)
- [ ] 10+ beta programs complete full workflow
- [ ] 99% uptime during beta
- [ ] Recommendation hit rate > 40% on 2024 holdout
