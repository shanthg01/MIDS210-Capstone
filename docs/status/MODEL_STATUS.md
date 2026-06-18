# PortalPoint Model Status

**Last updated:** June 18, 2026 (Gap Matching complete)  
**Scope:** Model notebooks, model outputs, feature/data dependencies, and next modeling work.

Use this file as the model handoff. Architecture and deployment context live in
[`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md); app/API context lives in
[`APPLICATION_STATUS.md`](APPLICATION_STATUS.md).

---

## Per-Model Remaining Work

This is the fastest handoff table for model owners. "MVP" means required before the app should present the score as a real production signal; "v2" means valuable improvement after the baseline is usable.

| Model | Current state | MVP remaining work | v2 / improvement backlog | Primary references |
|---|---|---|---|---|
| M1 Player Clustering | ✅ Re-trained — HE two-scaler `k9-he-v1-2026`; 18,769 player-seasons (min_pct ≥ 20); 85.3% HE-covered. DB write uses delete-then-insert (stale-row guard). | Review provisional `ARCHETYPE_LABELS` against centroid table — especially C1 vs C5 (both interior) and C6 (n=3,111, oversized). Confirm S3 artifacts. | Bump C6 if catch-all confirmed; add action-level HE labels (P&R handler, cutter, spot-up shooter); consider defensive context. | [`../../notebooks/models/player_clustering.ipynb`](../../notebooks/models/player_clustering.ipynb); this doc's M1 section |
| M2 Team System Clustering | ✅ Re-trained — `team-k9-v2-2026`; 2,158 team-seasons (2021-2026); 96.7% HE-covered. DB write uses delete-then-insert. ⚠️ C7 degenerate (n=20, HE=5). | Decide: keep k=9 with C7 flagged, or reduce to k=8. Human review of `SYSTEM_LABELS` — auto-labels are candidate only. | Evaluate `off_trans_pct`/`def_trans_pct` addition to style vector; hoopR spatial zones decision. | [`../../notebooks/models/team_clustering.ipynb`](../../notebooks/models/team_clustering.ipynb); this doc's M2 section |
| M3 Scheme Fit | ✅ Re-run — `scheme-cos-v2`; all 6 seasons (2021-2026); 1,343,150 records; 57.5% HE-extended. `player_team_fit_scores` now has `season` column. | ⚠️ API `fit_scores.py` must add `season` filter before wiring real scheme_fit. Score compression noted (mean 85.7; range 55-65 for overall_fit while 3 stubs remain). | M3 v3 with hoopR spatial zones; normalization/rescaling of scheme_fit for UI display. | [`../../notebooks/models/scheme_fit_scorer.ipynb`](../../notebooks/models/scheme_fit_scorer.ipynb); this doc's M3 section |
| Gap Matching | ✅ Complete — `gap-cos-v1`; all 6 seasons; 1,343,050 records updated. Mean 6.1, std 16.8. Sparse distribution expected — correct behavior. | Wire `fit_scores.py` (add `season` filter, return real `gap_match` + `scheme_fit`). Populate transfers table for departure-aware gaps. | Add roster snapshots, portal departure confidence, coach-adjustable needs, hoopR play-type gap features. | [`../../notebooks/models/gap_matching.ipynb`](../../notebooks/models/gap_matching.ipynb); this doc's Gap Matching section |
| M4 Role Fit / Playing Time | Not started. | Build roster-aware opportunity model that produces `role_fit`; decide whether MVP only writes score or also stores opportunity details. | Add scenario controls for minutes/usage/displaced players; add uncertainty intervals and roster snapshot versioning. | [`../models/playing_time_rotation_model_plan.md`](../models/playing_time_rotation_model_plan.md) |
| Program Fit | Not started. | Define MVP proxies/data for NIL, geography, academics, and program constraints; implement MAUT-style calculator for `program_fit`. | Replace proxies with better public/partner data; expose configurable program priorities. | `APPLICATION_STATUS.md`; future program-fit plan needed |
| M5 Transfer Success | Not started. | Define outcome label and historical transfer training set; build first predictor writing to `predictions`. | Add confidence/risk explanations and calibration monitoring. | `notebooks/models/` future notebook |
| M6 Team Rating Projection | Planned, not started. | Wait for player projection + role/minutes outputs; define MVP baseline/candidate roster delta. | Use posterior samples, lineup interactions, and coach scenario overrides. | [`../models/team_rating_projection_roster_tool_plan.md`](../models/team_rating_projection_roster_tool_plan.md) |
| M7 Recommendation Engine | Not started; blocked by full fit stack. | Build once scheme/gap/role/program components are real; rank players per program into `recommendations`. | Add collaborative signals, shortlist feedback loops, and explanation-aware ranking. | `APPLICATION_STATUS.md`; future recommendation plan needed |

Immediate modeling order:

```text
✅ feature_eng_m1_m2_m3.ipynb   (min_pct >= 20 filter; HE player enrichment added; 18,769 players)
✅ M1 player_clustering          (HE two-scaler k9-he-v1-2026; 85.3% HE-covered; 18,769 rows in DB)
✅ M2 team_clustering            (team-k9-v2-2026; 96.7% HE-covered; 2,158 rows in DB; C7 degenerate)
✅ M3 scheme_fit_scorer          (scheme-cos-v2; all 6 seasons; 1,343,150 rows; migration b5d2e9f4 applied)
✅ Gap Matching                  (gap-cos-v1; all 6 seasons; 1,343,050 rows updated; soft positions via HE)
→  fit_scores.py partial real scoring (scheme + gap)
→  Role Fit / Playing Time
→  Program Fit
→  fit_scores.py full scoring
→  Recommendation Engine
```

---

## Current Model Stack

PortalPoint is program-facing: coaching staffs evaluate transfer players for fit with a program. The fit stack is intended to combine:

1. `scheme_fit` - player shot profile vs team shot profile.
2. `gap_match` - player skills vs roster needs.
3. `role_fit` - projected opportunity / rotation fit.
4. `program_fit` - program constraints and preferences.

Current composite state:

```text
overall_fit = 0.30 * scheme_fit + 0.20 * gap_match + 0.50 * 50.0
```

`role_fit` and `program_fit` are still stubbed at 50. `scheme_fit` and `gap_match` are real.

---

## Data Available For Models

| Source | Status | Primary tables/files | Notes |
|---|---|---|---|
| BartTorvik | Complete, multi-season loaded | `player_season_stats`, `team_season_stats`, S3 `raw/barttorvik/` | Normalized Postgres rows plus raw S3 files. 2021-2026 player seasons are available locally. |
| Hoop Explorer | Complete — 6 seasons loaded (2021-2026) | `hoop_explorer_player_stats` (~16,750 rows, all D1), `hoop_explorer_team_stats` (~2,170 rows), S3 `raw/hoop_explorer/` | Player data includes 15 play-type pcts + `pos_confidence_pg/sg/sf/pf/c`. Team data includes trans/scramble pct+ppp. Feature engineering re-run needed before these flow into model inputs. |
| hoopR ESPN PBP | Complete — 6 seasons (2021-2026) | `hoopr_team_season_stats`, `hoopr_player_season_stats`, S3 `raw/hoopr/` | Team PBP coverage partial for 2021-2024 (~172-235 teams); near-full for 2025-2026. |
| Feature parquet | Generated by notebooks | `data/features/player_features.parquet`, `data/features/team_style_vectors.parquet` | Gitignored; S3 is source of truth for shared feature files. |
| Model artifacts | Generated by notebooks | `data/models/*.pkl`, centroid CSVs, S3 `models/` | Local artifacts may differ by branch/run; upload to S3 when sharing. |

Useful docs:

| Doc | Purpose |
|---|---|
| [`../models/gap_matching_plan.md`](../models/gap_matching_plan.md) | Next critical-path fit component. |
| [`../models/playing_time_rotation_model_plan.md`](../models/playing_time_rotation_model_plan.md) | Role fit / opportunity model design. |
| [`../models/player_projection_state_space_plan.md`](../models/player_projection_state_space_plan.md) | Player talent projection plan. |
| [`../models/team_rating_projection_roster_tool_plan.md`](../models/team_rating_projection_roster_tool_plan.md) | Team rating impact / roster scenario model plan. |
| [`../models/hoopr_integration_plan.md`](../models/hoopr_integration_plan.md) | hoopR spatial feature integration notes. |
| [`../diagram_3_data_science_workflow.md`](../diagram_3_data_science_workflow.md) | End-to-end data science workflow reference. |

---

## M1 - Player Clustering

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/player_clustering.ipynb` |
| Status | ✅ Complete — HE two-scaler architecture |
| Algorithm | K-Means, two-scaler (HE majority + BART-only projection) |
| Current k | `9` |
| Model version | `k9-he-v1-2026` |
| Training seasons | 2021-2026 pooled (min_pct ≥ 20 filter) |
| Training rows | 18,769 player-seasons |
| Output table | `player_archetypes` (18,769 rows; delete-then-insert on re-run) |
| Local artifacts | `data/models/player_kmeans.pkl`, `player_scaler_bart.pkl`, `player_scaler_he.pkl`, `player_archetype_labels.pkl`, `centroids_player.csv` |

### Architecture

| Group | Size | Feature dims | Notes |
|---|---:|---:|---|
| HE-covered | 16,011 (85.3%) | 22 (BART-7 + HE-15) | Trains K-Means; full 22-dim distance for assignment |
| BART-only | 2,758 (14.7%) | 7 | Projects through BART-7 dims of 22-dim centroids; confidence × 0.75 |

### Features

**BART-7 (all players):** `usage_rate`, `true_shooting_pct`, `assist_rate`, `bpm`, `three_point_rate`, `rim_rate`, `mid_range_rate`

**HE-15 (HE-covered players):** all `off_style_*_pct` columns from `hoop_explorer_player_stats` — rim_attack, attack_kick, perimeter_sniper, dribble_jumper, mid_range, hits_cutter, perimeter_cut, pnr_passer, big_cut_roll, post_up, post_kick, pick_pop, high_low, reb_scramble, transition

### Validation

| Metric | Value | Notes |
|---|---:|---|
| Silhouette — BART-all 7-dim | 0.0637 | Lower than hoopR run (0.099) — expected; centroids optimized for 22-dim, not 7-dim |
| Silhouette — HE-ext 22-dim | 0.1282 | Better than hoopR 19-dim (0.119) — HE features improve cluster separation |
| Davies-Bouldin — BART-all | 2.2642 | — |
| Avg confidence | 0.444 | — |

> **Note on silhouette comparison:** BART-7 silhouette is not comparable across hoopR and HE runs — the clustering objective changed (22-dim vs 19-dim). Do not use BART-7 silhouette to judge improvement between architectures. HE-ext 22-dim silhouette is the correct within-run quality signal.

### Current Labels (provisional — review against centroid table)

| Cluster | Label | n | HE-ext | Flag |
|---:|---|---:|---:|---|
| 0 | Versatile Scorer | 1,825 | 1,607 | — |
| 1 | Interior Wing | 2,315 | 2,107 | — |
| 2 | Wing Creator | 2,161 | 1,912 | — |
| 3 | Perimeter Shooter | 1,677 | 1,487 | — |
| 4 | Floor Spacer | 2,295 | 1,923 | — |
| 5 | Interior Finisher | 2,109 | 1,729 | — |
| 6 | Primary Playmaker | 3,111 | 2,590 | ⚠️ oversized (16.6%); may be catch-all |
| 7 | Shooting Playmaker | 1,779 | 1,373 | — |
| 8 | Paint Scorer | 1,497 | 1,283 | — |

### Known Follow-Ups

- **Review labels:** Confirm centroid table for C1 (Interior Wing) vs C5 (Interior Finisher) — both interior, need distinct BART-7 + HE-15 profile. Confirm C6 (Primary Playmaker, n=3,111) — check HE centroid dims for `pnr_passer_pct` / `hits_cutter_pct`; if no strong HE signal, consider k-bump.
- **MLflow promotion:** BART-7 silhouette dropped (architecture change); model staged as Staging. Do not force-promote — metric comparison is invalid cross-architecture.
- **Candidate v2 features:** HE `pos_confidence_*` for position-aware archetypes; defensive activity; P&R role inference from `pnr_passer_pct` vs `big_cut_roll_pct`.

---

## M2 - Team System Clustering

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/team_clustering.ipynb` |
| Status | ✅ Complete — HE two-scaler architecture |
| Algorithm | K-Means, two-scaler (HE majority + BART-only projection) |
| Current k | `9` |
| Model version | `team-k9-v2-2026` |
| Training seasons | 2021-2026 pooled |
| Training rows | 2,158 team-seasons |
| Output table | `team_system_profiles` (2,158 rows; delete-then-insert on re-run) |
| Artifacts | `team_kmeans.pkl`, `team_bart_scaler.pkl`, `team_he_scaler.pkl`, `team_system_labels.pkl` |

### Architecture

| Group | Size | Feature dims | Notes |
|---|---:|---:|---|
| HE-covered | 2,087 (96.7%) | Variable (BART-4 + HE dims) | Trains K-Means |
| BART-only fallback | 71 (3.3%) | 4 | Projects through BART-4 dims; confidence × 0.75 |

### Features

**BART-4 (all teams):** `team_three_rate`, `team_rim_rate`, `team_mid_rate`, `adj_tempo`

**HE extensions (HE-covered teams):** transition/scramble play-type frequencies from `hoop_explorer_team_stats`

> **Imputation note:** 326 null `adj_tempo` values in 2021 team-seasons (sparse data). Imputed with population median (67.3) before building `X_he`. Assertion guard confirms no NaN in training matrix.

### Validation

| Metric | Value | Notes |
|---|---:|---|
| HE coverage | 96.7% | vs player M1's 85.3% — teams much better covered |
| C7 cluster n | 20 | ⚠️ degenerate; only 5 HE-covered teams; centroid defined by 5 |

### Current Labels (provisional — human basketball review needed)

| Cluster | n | HE-ext | Flag |
|---:|---:|---:|---|
| C0 | — | — | Auto-label; review |
| C1 | — | — | Auto-label; review |
| C2 | — | — | Auto-label; review |
| C3 | — | — | Auto-label; review |
| C4 | — | — | Auto-label; review |
| C5 | — | — | Auto-label; review |
| C6 | — | — | Auto-label; review |
| C7 | 20 | 5 | ⚠️ degenerate cluster; n=20 total, HE=5 only |
| C8 | — | — | Auto-label; review |

### Known Follow-Ups

- **Degenerate C7:** Decide k=8 (merge C7) vs k=9-with-flag. C7 not basketball-coherent; 15 of 20 teams assigned via BART-only projection through a centroid defined by 5 HE teams. Recommend k=8 before wiring M3.
- **Human label review:** `SYSTEM_LABELS` are candidate auto-labels only — need basketball review for named system archetypes.
- **Candidate v2 features:** `off_trans_pct`/`def_trans_pct` now in DB — evaluate adding before next re-train. hoopR spatial zones available but validate cosine discrimination first.
- Keep `adj_em` as overlay/quality indicator, not style feature.

---

## M3 - Scheme Fit Scorer

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/scheme_fit_scorer.ipynb` |
| Status | ✅ Complete — multi-season re-run |
| Model type | Deterministic cosine similarity |
| Model version | `scheme-cos-v2` |
| Seasons scored | 2021-2026 (season-matched: player-season × same-season teams) |
| Output table | `player_team_fit_scores` (1,343,150 rows; `season` column added via migration `b5d2e9f4`) |
| MLflow | `scheme-fit-scorer v1` → Production |

### Feature Contract

Player vector: `three_point_rate`, `rim_rate`, `mid_range_rate`

Team vector: `team_three_rate`, `team_rim_rate`, `team_mid_rate`

Same-season shot-rate vectors; cosine sim scaled 0-100. M2 system labels enrich UI breakdown but are not required for computation.

HE extended fit (breakdown only): `off_style_transition_pct`, `off_style_post_up_pct`, `off_style_pick_pop_pct`, `off_style_big_cut_roll_pct`, `off_style_attack_kick_pct`, `off_style_perimeter_cut_pct` — 6-dim cosine, added to `breakdown.scheme.he_scheme_fit` where both player and team are HE-covered.

### Run Results

| Season | Players | Teams | Mean fit | HE records |
|---:|---:|---:|---:|---:|
| 2021 | 4,240 | 346 | 85.8 | 116,168 (54.8%) |
| 2022 | 4,504 | 358 | 85.5 | 124,248 (55.2%) |
| 2023 | 4,521 | 363 | 85.7 | 128,019 (56.6%) |
| 2024 | 4,532 | 362 | 85.8 | 131,547 (58.1%) |
| 2025 | 4,541 | 364 | 86.0 | 135,511 (59.7%) |
| 2026 | 4,525 | 365 | 85.6 | 136,988 (60.6%) |
| **Total** | **26,863** | — | **85.7** | **772,481 (57.5%)** |

### Score Distribution (2026 snapshot)

| Stat | Value |
|---|---:|
| Mean | 84.9 |
| Std | 12.2 |
| p10 | 67.9 |
| p90 | 98.1 |
| Min | 10.4 |

### Known Issues / Follow-Ups

- **Score compression:** 3-dim cosine on non-negative proportions that sum to ~1 clusters 70-100 for most pairs. Even worst realistic pairs score 43-72. With only `scheme_fit` real (30% weight) and other 3 components stubbed at 50, `overall_fit` range ≈ **[55, 65]** — insufficient for ranking. Do not surface overall_fit to users until all 4 components are real.
- **M2 C7 still degenerate:** k=8 vs k=9 decision pending. Does not affect M3 computation — only affects system_label field in breakdown JSON.
- **hoopR spatial zones (M3 v3):** 5-zone spatial data available in `team_style_vectors.parquet`. Validate cosine discrimination before replacing stable 3-dim base.
- **Schema change:** `player_team_fit_scores` now has `season` column + `uq_fit_score` on `(player_id, school_id, season)`. API `fit_scores.py` router query must add `season` filter (use `CURRENT_SEASON` for live portal use case).

---

## Gap Matching

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/gap_matching.ipynb` |
| Status | ✅ Complete — `gap-cos-v1` |
| Model type | Deterministic cosine similarity (player stats vs roster gap vector) |
| Model version | `gap-cos-v1` |
| Seasons scored | 2021-2026 (season-matched) |
| Output table | `player_team_fit_scores.gap_match` (1,343,050 rows updated) |

### Feature Contract

Player vector (8-dim, from `player_season_stats`): `points_per_game`, `rebounds_per_game`, `assists_per_game`, `steals_per_game`, `blocks_per_game`, `true_shooting_pct`, `usage_rate`, `three_point_rate`

Position weights: `hoop_explorer_player_stats.pos_confidence_pg/sg/sf/pf/c` (soft, sums to 1.0). 59.3% of player-season rows HE-matched; remaining 40.7% use one-hot fallback from `players.position`.

### Run Results

| Season | Mean | Std | Min | Max |
|---:|---:|---:|---:|---:|
| 2021 | 7.23 | 18.34 | 0.0 | 98.2 |
| 2022 | 6.60 | 17.26 | 0.0 | 98.1 |
| 2023 | 6.55 | 16.83 | 0.0 | 94.9 |
| 2024 | 6.29 | 16.80 | 0.0 | 98.1 |
| 2025 | 5.86 | 15.98 | 0.0 | 96.2 |
| 2026 | 5.82 | 15.51 | 0.0 | 94.2 |

**scheme_fit std for comparison:** 7.22 — gap_match (std 16.8) differentiates 2.3× better.

### Score Distribution Analysis

Gap_match is **intentionally sparse and right-skewed** — this is correct behavior, not compression:

- Mean ~6 reflects that most player-school pairs have low gap alignment (player offers what the school already has)
- High scores (80+) only appear when a player's stat profile closely matches the specific dimensions a school is deficient in
- std 16.8 vs scheme_fit's 7.22 — gap_match provides far better differentiation between pairs
- Schools with highest mean gap_match = programs with the most roster holes across positions

Contrast with scheme_fit: compressed high (mean 85.7) because cosine similarity on non-negative proportions summing to 1 naturally clusters near 1. Gap_match gap vectors are sparse (zeroed out where school is at/above benchmark), so cosine sim is low for most pairs.

**Combined overall_fit impact:** With 2 real components:
```
overall_fit ≈ 0.30 × 85.7 + 0.20 × 6.1 + 0.50 × 50.0 = 25.7 + 1.2 + 25.0 = 51.9
```
Range remains narrow until role_fit and program_fit are real. Do not surface overall_fit to users yet.

### Known Issues / Limitations

- **transfers table empty:** No departure filter applied. Gap computed against full-season roster (players who appeared in `player_season_stats`). Will overestimate roster depth for schools that lost players mid-season or after. Accuracy improves when VerbalCommits data populates `transfers`.
- **player_school_seasons empty:** Roster source is `player_season_stats.school_id` per season. Same limitation as above.
- **Declining mean 2021→2026** (7.23 → 5.82): Likely reflects improving HE coverage (better soft position assignments reduce position mismatch noise) and possible real trend toward roster balance over time. Monitor post-transfers population.
- **Scope matches M3:** Only pairs in `player_team_fit_scores` (from M3 run) are scored. Players in `player_season_stats` not in M3 parquet are excluded.

---

## Planned Models And Calculators

| # | Model / Calculator | Status | Depends on | Output |
|---|---|---|---|---|
| 4 | Playing Time / Rotation -> Role Fit | Not started | roster state, M1/M3 helpful | `player_team_fit_scores.role_fit` |
| - | Program Fit Calculator | Not started | user preferences, NIL/location/academic proxies | `player_team_fit_scores.program_fit` |
| 5 | Transfer Success Predictor | Not started | historical transfers/outcomes | `predictions` |
| 6 | Team Rating Projection | Not started | player projection + role/minutes | `team_rating_projections` |
| 7 | Recommendation Engine | Not started | all fit components | `recommendations` |

Critical path:

```text
feature_eng re-run -> M2 re-train -> M3 re-run
  -> Gap Matching
  -> fit_scores.py partial real scoring
  -> Role Fit / Playing Time
  -> Program Fit
  -> fit_scores.py full scoring
  -> Recommendation Engine
```

Parallel work:

- Transfer Success Predictor can start once historical transfer/outcome labels are ready.
- Team Rating Projection should wait for player projection and role/minutes outputs.

---

## Model Open Questions

> **Immediate before wiring fit_scores.py:** `player_team_fit_scores` now has `season` column. The `fit_scores.py` router currently queries without season filter — will return multiple rows per player×school. Must add `AND season = :current_season` (or take season as query param) before exposing real scheme_fit through the API.



1. Gap Matching position handling: HE `pos_confidence_*` data now available (13,993 rows). Per-position gaps are now feasible — recommend this path. Confirm join rate to BART player-seasons before committing.
2. Do we want to store richer opportunity outputs in a dedicated `playing_time_projections` table, or only write `role_fit` first?
3. What public/proxy data should represent NIL budget and program fit?
4. ✅ Resolved — HE player play-type data (`off_style_*_pct`, 15-dim) is M1's extended feature set. 85.3% HE coverage with min_pct ≥ 20 filter. BART-only players (14.7%) project through BART-7 dims with 0.75 confidence discount.
5. How much score explanation is required for coaches before recommendation ranking feels trustworthy?
6. Should `off_trans_pct`/`def_trans_pct` be added to M2 style vector in next re-train? Data is in DB now.
