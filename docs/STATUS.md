# PortalPoint — Project Status

**Date:** June 15, 2026  
**Current branch:** `feature/integrate-sources` → PR #10 open to `main`  
**Test suite:** 111 tests passing

---

## Team Roles & Infrastructure (current)

**Deployment stance:** Local-first — Docker Compose for Postgres + Redis; **no EC2/ECS images until beta.** AWS used for S3 (and credits) only for now.

**S3 onboarding:** [`docs/aws_s3_setup.md`](aws_s3_setup.md) — classmates copy `.env.example` → `.env`, get IAM keys from Justin, run `aws s3 ls s3://portalpoint-data/`.

| Owner | Scope |
|---|---|
| **Justin** | **M1** (player clustering — dataset feature review, archetype labels, MLflow re-log); **M3** (scheme fit — joint dataset review with Shanth); **AWS S3** bucket layout (bronze raw + model artifacts); **Supabase** project (team-shared Postgres and/or storage — see below) |
| **Shanth** | **M2** (team system clustering — dataset feature review, system labels, MLflow re-log); **M3** (scheme fit — joint dataset review with Justin); **MLflow** setup (tracking URI, S3 artifact backend, Colab + local workflow) |

### Local vs cloud data stores

| Layer | Now (local) | Cloud (Justin provisions) |
|---|---|---|
| App DB (dev) | Docker Postgres `:5433` | Supabase Postgres — optional shared dev/staging URL for team |
| Cache | Docker Redis | Defer |
| Raw data + models | `data/`, `.torvik_cache/` | S3 `s3://portalpoint-data/raw/`, `s3://portalpoint-data/models/` |
| MLflow artifacts | `mlruns/` (gitignored) | S3 `s3://portalpoint-data/mlflow/` (when wired) |
| API | `uvicorn` locally | Defer (no EC2) |

**Supabase note:** PortalPoint already uses FastAPI + SQLAlchemy + Alembic. Use Supabase as **hosted Postgres** (paste connection string into `.env`) unless the team explicitly wants Supabase Auth/Storage later. S3 remains primary for parquet/model blobs; Supabase Storage is optional duplicate.

### S3 bucket (`portalpoint-data`)

**Status:** Live in `us-east-1`. Block public access; default SSE-S3 encryption.

**Access:** IAM group `PortalPoint-Dev` in bucket owner account — one programmatic user per teammate. AWS Organization links member accounts for credit sharing; S3 keys are issued by Justin (not cross-account). See [`aws_s3_setup.md`](aws_s3_setup.md).

```
s3://portalpoint-data/
  raw/barttorvik/YYYY-MM-DD/
  raw/hoop_explorer/YYYY-MM-DD/
  raw/hoopr/YYYY-MM-DD/          # raw PBP parquets (~120MB/season); not in git
  raw/features/                  # player_features.parquet, team_style_vectors.parquet
  models/player_clustering/
  models/team_clustering/
  models/transfer_success/
  mlflow/                        # MLflow artifacts (tracking stays sqlite:///mlruns.db)
```

### Handoff: MLflow + M1–M3 re-log ✅ Complete

MLflow is wired to S3 (`s3://portalpoint-data/mlflow`) via `notebooks/utils/mlflow_helpers.py`. Run metadata in `mlruns.db` (SQLite, local). Existing experiments patched to S3 artifact root via `client.update_experiment()` — no data loss.

- M1 (`player_clusterer`) — pkl artifacts in S3 + MLflow run logged ✅
- M2 (`team_system_clusterer`) — pkl artifacts in S3 + MLflow run logged ✅
- M3 — no pkl artifact (deterministic cosine); MLflow run logged with scheme-cos-v2 metrics ✅

---

## Model dataset evaluation — M1, M2, M3

**Goal:** Before building M4+ or expanding ingest, Justin and Shanth document which **datasets and columns** each built model actually needs, what coverage exists in Postgres/S3 today, and what gaps require new ingest (multi-season barttorvik, Hoop Explorer, etc.).

**Shared deliverable:** One table per model in `docs/data_eval/` (or a section in this file) with columns: `feature | source | table/file | join key | seasons | coverage % | blocker?`

**Sync:** 30-min joint review after individual drafts — especially for **M3**, where player-side features (Justin) must align with team-side features (Shanth).

### M1 — Player clustering (**Justin**)

| Item | Detail |
|---|---|
| **Model** | K-Means (k=10) on 7 player style features; outputs `player_archetypes` |
| **Notebook** | `notebooks/models/player_clustering.ipynb` |
| **Feature file** | `data/features/player_features.parquet` (from `barttorvik_feature_eng.ipynb`) |
| **Features to verify** | `usage_rate`, `true_shooting_pct`, `assist_rate`, `bpm`, `three_point_rate`, `rim_rate`, `mid_range_rate` |
| **Primary source** | barttorvik → `player_season_stats` |
| **Join keys** | `player_id`, `season`; `players.barttorvik_id` for ingest linkage |
| **Justin’s eval tasks** | (1) Map each feature to exact DB column and derivation (e.g. ts% 0–100 vs 0–1). (2) Run coverage query: % non-null per feature for 2025 and after multi-season ingest 2020–2025. (3) Confirm `minutes_threshold_met` / games played filter used in notebook. (4) Finalize `ARCHETYPE_LABELS` from centroid heatmap — Gap Matching and M7 consume these labels. (5) Note optional enrichments **not** in M1 MVP (Hoop Explorer RAPM, height) and whether to add later. |
| **MLflow** | Re-run training with Shanth’s URI; register `player_clusterer`; sync `data/models/player_*.pkl` → S3 |

### M2 — Team system clustering (**Shanth**)

| Item | Detail |
|---|---|
| **Model** | K-Means (k=9) on 4 team style features; outputs `team_system_profiles` |
| **Notebook** | `notebooks/models/team_clustering.ipynb` |
| **Feature file** | `data/features/team_style_vectors.parquet` |
| **Features to verify** | `team_three_rate`, `team_rim_rate`, `team_mid_rate`, `adj_tempo` |
| **Excluded by design** | `adj_em` — team quality, not style (used as overlay only) |
| **Primary source** | barttorvik → `team_season_stats` (pace, shot distribution, adj tempo) |
| **Join keys** | `school_id`, `season`; school name aliases via `ingest_barttorvik.TEAM_NAME_ALIASES` |
| **Shanth’s eval tasks** | (1) Map each style feature to `team_season_stats` columns and feature-engineering logic. (2) Coverage: schools × seasons with complete style vector (target: all D1 in ingest). (3) Review `SYSTEM_LABELS` / auto-label taxonomy — M3 breakdown UI references system cluster. (4) Assess bias from Hoop Explorer team CSV (`data/hoop_explorer/all_team_explorer_stats_power_6.csv`) — Power 6 only; document as post-MVP enrichment, not M2 input. (5) Confirm `team_system_profiles` join rate to `team_style_vectors.parquet` for M3. |
| **MLflow** | Re-run training; register `team_system_clusterer`; sync `data/models/team_*.pkl` → S3 |

### M3 — Scheme fit scorer (**Justin + Shanth**, joint)

| Item | Detail |
|---|---|
| **Model** | Deterministic cosine similarity — **not trained**, no MLflow model artifact |
| **Notebook** | `notebooks/models/scheme_fit_scorer.ipynb` |
| **Player vector (3-dim)** | `three_point_rate`, `rim_rate`, `mid_range_rate` from `player_season_stats` |
| **Team vector (3-dim)** | `team_three_rate`, `team_rim_rate`, `team_mid_rate` from `team_style_vectors.parquet` / team features |
| **Output** | `player_team_fit_scores.scheme_fit` (0–100); `model_version` = `scheme-cos-v2` |
| **Depends on** | M1 not required for score; M2 labels used for breakdown heatmaps / UI context |

**Why joint:** M3 is the first **cross-entity** model — player shot mix vs team shot mix. Both sides must use the **same season**, **same scale** (rates sum ~1), and **same school universe** or fits will be misleading.

| Owner | M3 dataset review focus |
|---|---|
| **Justin** | Player shot rates: null %, whether barttorvik rim/mid/3PT columns match notebook; player-season row count for 2025 portal subset; edge cases (low minutes, missing shot dist). |
| **Shanth** | Team shot rates: parity with player definitions (are team rates computed the same way as player rates?); `adj_tempo` — in design doc but **not** in current 3-dim scheme vector; decide if M3 v2 adds pace dimension. |
| **Together** | (1) Write signed **feature contract** — exact column list for v1. (2) Spot-check 5 player–school pairs manually (eyeball style fit vs basketball intuition). (3) Review score distribution (notebook reports mean ~86 — is compression a problem?). (4) Confirm `team_system_profiles` enriches breakdown JSON without changing core cosine. (5) Document whether user preference weights (`scheme_fit` importance) multiply dimensions later — data vs product decision. |

**M3 exit criteria:** Both approve a short `M3_scheme_fit_data_contract.md` listing player columns, team columns, season scope, and known limitations before Gap Matching (reuses same cosine pattern).

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
| Alembic migrations (5) | ✅ Applied | `064d7a23e792` → `b683e0eae93e` barttorvik_id → `4f15ed03ddbf` program pivot → `4d2553a387cc` expanded barttorvik fields → `a3f7b2c9e1d0` HE tables → `c1e8f4a2b5d3` hoopR table |
| MLflow | ✅ Complete | S3 artifact backend wired; run metadata in `mlruns.db`; M1–M3 logged |
| AWS S3 | ✅ Live | `portalpoint-data`; all ingest scripts write raw data + features; MLflow artifacts |
| Supabase | 🔄 In progress | **Justin** — shared Postgres (optional replace local Docker for team) |
| EC2 / ECS | ⏸️ Deferred | Local API + Docker only until beta |
| Airflow DAGs | ❌ Not started | GitHub Actions cron first; Airflow Week 9–10 if needed |
| Redis caching layer | ❌ Not started | Add when first router wired to real fit scores |

### Data Pipeline

| Stage | Status | Output |
|---|---|---|
| Barttorvik ETL (`scripts/ingest_barttorvik.py`) | ✅ Complete | ~4,548 players, 365 schools, 2026 season; expanded advanced fields; S3 upload |
| Hoop Explorer ETL (`scripts/ingest_hoop_explorer.py`) | ✅ Complete | `hoop_explorer_team_stats` (356 teams), `hoop_explorer_player_stats`; S3 upload |
| hoopR PBP ETL (`scripts/ingest_hoopr.py`) | ✅ Complete | `hoopr_team_season_stats` (365 D1 teams, 11 PBP features); raw parquet → S3 |
| Feature engineering (`feature_eng_m1_m2_m3.ipynb`) | ✅ Complete | `player_features.parquet` (4,083 players); `team_style_vectors.parquet` (365 teams, barttorvik + HE + hoopR cols); S3 upload |
| Roster gap analysis | ❌ Not started | Required for Gap Matching (Component 1) |

**gitignore:** `data/hoopr/`, `notebooks/data/`, `data/features/` — all large data files excluded; S3 is source of truth.

### ML Models

| # | Model | Status | Artifacts | DB Table |
|---|---|---|---|---|
| 1 | Player Clustering (K-Means, K=9) | ✅ Complete | `player_kmeans.pkl`, `player_scaler.pkl`, `player_archetype_labels.pkl` → S3 + MLflow | `player_archetypes` |
| 2 | Team System Clustering (K-Means) | ✅ Complete | `team_kmeans.pkl`, `team_bart_scaler.pkl`, `team_he_scaler.pkl`, `team_system_labels.pkl` → S3 + MLflow | `team_system_profiles` |
| 3 | Scheme Fit Scorer (cosine similarity) | ✅ Complete | Deterministic; `scheme-cos-v2`; MLflow run logged | `player_team_fit_scores` (scheme_fit col) |
| — | Gap Matching (cosine similarity) | ❌ Not started | — | `player_team_fit_scores` (gap_match col) |
| 4 | Playing Time / Rotation Model → Role Fit Score | ❌ Not started | — | `player_team_fit_scores` (role_fit col) |
| — | Program Fit Calculator (MAUT) | ❌ Not started | — | `player_team_fit_scores` (program_fit col) |
| 5 | Transfer Success Predictor (XGBoost) | ❌ Not started | — | `predictions` |
| 6 | Team Rating Projection (XGBoost) | ❌ Not started | Depends on Model 4 | `team_rating_projections` |
| 7 | Recommendation Engine (SVD + content + fit) | ❌ Not started | Depends on all 4 fit components | `recommendations` |

**Current fit score state:** `overall_fit = 0.30 × scheme_fit + 0.70 × 50.0` (gap/role_fit/program_fit stubbed at 50 until built).

**M2 feature vector (two-scaler approach):**
- All 365 D1 teams: 4-dim barttorvik vector (`team_three_rate`, `team_rim_rate`, `team_mid_rate`, `adj_tempo`)
- 356/365 HE-covered teams: +12 play-type frequency dimensions (second scaler)
- 365/365 hoopR-covered teams: `pbp_*` spatial zone + tempo columns available in `team_style_vectors.parquet` for M3 enrichment
- Non-HE teams assigned via 4-dim BART centroid projection (confidence discounted 25%)

**M3 scheme vector (v2):** 3-dim base cosine (shot rates) always computed; `he_scheme_fit` supplementary in breakdown JSON for HE-covered pairs; hoopR spatial zones available for v3 expansion.

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
Model 4: Playing Time / Rotation model      ← opportunity outputs → role_fit score
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
| ~~MLflow S3 tracking + instrument M1–M3~~ | ✅ Complete — `mlruns.db` + S3 artifact backend; M1–M3 runs logged |
| ~~S3 bucket + sync ingest cache / model artifacts~~ | ✅ Complete — `portalpoint-data`; all three ingest scripts upload |
| ~~M3 joint dataset feature contract~~ | ✅ Complete — `scheme-cos-v2` vector locked (3-dim BART shot rates) |
| Supabase project + share `DATABASE_URL` with team | **When ready** — Justin; keep local Docker for offline dev |
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
2. **Player archetype labels (M1 — Justin):** `ARCHETYPE_LABELS` in `player_clustering.ipynb` still uses auto-generated candidates — finalize before Gap Matching.
3. **Team system labels (M2 — Shanth):** Auto-generated via taxonomy distance matching — review `SYSTEM_LABELS` for accuracy.
4. **Program Fit data gaps:** `nil_valuations` and `schools.nil_estimated_budget_usd` are not populated from barttorvik (no public NIL data). NIL fit score will require either manual data entry, third-party source, or a proxy (conference tier, market size). Decide before building Program Fit calculator.
5. **NCAA/FERPA compliance:** Legal review required before public launch. Use only public data; document data sources clearly.
6. **hoopR spatial zones in M3 v3:** hoopR 5-zone data now in `team_style_vectors.parquet`. Adding to scheme vector increases cosine dim from 3→8 — validate discrimination before wiring.

---

## MVP Success Criteria (Week 12 target)

- [ ] 2,500+ portal players in DB with complete stats *(2025 season loaded; portal subset to verify)*
- [ ] All core endpoints < 500ms
- [ ] All 4 fit components computing (not stubbed)
- [ ] 10+ beta programs complete full workflow
- [ ] 99% uptime during beta
- [ ] Recommendation hit rate > 40% on 2024 holdout
