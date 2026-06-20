# PortalPoint Model Status

**Last updated:** June 20, 2026 (script-based local refresh through Gap Matching)
**Scope:** Model notebooks, model outputs, feature/data dependencies, and next modeling work.

Use this file as the model handoff. Architecture and deployment context live in
[`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md); app/API context lives in
[`APPLICATION_STATUS.md`](APPLICATION_STATUS.md).

---

## Per-Model Remaining Work

This is the fastest handoff table for model owners. "MVP" means required before the app should present the score as a real production signal; "v2" means valuable improvement after the baseline is usable.

| Model | Current state | MVP remaining work | v2 / improvement backlog | Primary references |
|---|---|---|---|---|
| M1 Player Clustering | ✅ Complete baseline — script-backed tuned group-weighted `k9-tuned-v1-2026`; 18,769 player-seasons (min_pct ≥ 20); 85.4% HE-covered in latest local run. **Critical bugfix:** added semantic cluster reordering (was silently scrambling all 9 labels on every rerun — see Known Follow-Ups). 3 labels renamed based on `def_adj_rapm` DB validation (C4, C6, C7). | Human basketball review of remaining pass-one labels before product copy is frozen. | Add HE `pos_confidence_*` for position-aware archetypes; richer P&R role inference. | [`../../scripts/run_player_clustering.py`](../../scripts/run_player_clustering.py); [`../../notebooks/models/player_clustering.ipynb`](../../notebooks/models/player_clustering.ipynb); this doc's M1 section |
| M2 Team System Clustering | ✅ Complete baseline — script-backed two-layer tuned group-weighted `team-v4-2026`; 2,158 team-seasons. Offense/defense memberships populated. Confidence (~0.2 avg) confirmed structural via 2 ruled-out experiments, not a tuning bug — see Known Follow-Ups. | Continue basketball review of offense/defense names using centroid summaries and representative teams. | Later evaluate hoopR spatial zones and defensive PPP/four-factor quality overlays. | [`../../scripts/run_team_clustering.py`](../../scripts/run_team_clustering.py); [`../../notebooks/models/team_clustering.ipynb`](../../notebooks/models/team_clustering.ipynb); this doc's M2 section |
| M3 Scheme Fit | ✅ Complete baseline — script-backed `scheme-cos-v2`; all 6 seasons (2021-2026); 1,343,050 records in latest local run. `player_team_fit_scores` has `season` column and API reads current-season rows. | Score compression noted (mean ~85.7; overall_fit remains narrow while role/program are stubbed). | M3 v3 with hoopR spatial zones; normalization/rescaling of scheme_fit for UI display. | [`../../scripts/run_scheme_fit.py`](../../scripts/run_scheme_fit.py); [`../../notebooks/models/scheme_fit_scorer.ipynb`](../../notebooks/models/scheme_fit_scorer.ipynb); this doc's M3 section |
| Gap Matching | ✅ Complete baseline — script-backed `gap-cos-v1`; all 6 seasons; 1,343,050 records updated in latest local run. Sparse distribution expected — correct behavior. | Populate transfers table for departure-aware gaps. | Add roster snapshots, portal departure confidence, coach-adjustable needs, hoopR play-type gap features. | [`../../scripts/run_gap_matching.py`](../../scripts/run_gap_matching.py); [`../../notebooks/models/gap_matching.ipynb`](../../notebooks/models/gap_matching.ipynb); this doc's Gap Matching section |
| M4 Role Fit / Playing Time | Not started. | Build roster-aware opportunity model that produces `role_fit`; decide whether MVP only writes score or also stores opportunity details. | Add scenario controls for minutes/usage/displaced players; add uncertainty intervals and roster snapshot versioning. | [`../models/playing_time_rotation_model_plan.md`](../models/playing_time_rotation_model_plan.md) |
| Program Fit | Not started. | Define MVP proxies/data for NIL, geography, academics, and program constraints; implement MAUT-style calculator for `program_fit`. | Replace proxies with better public/partner data; expose configurable program priorities. | `APPLICATION_STATUS.md`; future program-fit plan needed |
| M5 Transfer Success | Not started. | Define outcome label and historical transfer training set; build first predictor writing to `predictions`. | Add confidence/risk explanations and calibration monitoring. | `notebooks/models/` future notebook |
| M6 Team Rating Projection | Planned, not started. | Wait for player projection + role/minutes outputs; define MVP baseline/candidate roster delta. | Use posterior samples, lineup interactions, and coach scenario overrides. | [`../models/team_rating_projection_roster_tool_plan.md`](../models/team_rating_projection_roster_tool_plan.md) |
| M7 Recommendation Engine | Not started; blocked by full fit stack. | Build once scheme/gap/role/program components are real; rank players per program into `recommendations`. | Add collaborative signals, shortlist feedback loops, and explanation-aware ranking. | `APPLICATION_STATUS.md`; future recommendation plan needed |

Immediate modeling order:

```text
✅ feature_eng_m1_m2_m3.ipynb   (min_pct >= 20 filter; HE player enrichment added; 18,769 players)
✅ M1 player_clustering          (tuned group-weighted k9-tuned-v1-2026; top-three memberships populated)
✅ M2 team_clustering            (two-layer team-v4-2026 notebook/DB/artifacts populated)
✅ M3 scheme_fit_scorer          (scheme-cos-v2; all 6 seasons; 1,343,050 rows; migration b5d2e9f4 applied)
✅ Gap Matching                  (gap-cos-v1; all 6 seasons; 1,343,050 rows updated; soft positions via HE)
✅ fit_scores.py partial real scoring (scheme + gap, dynamic current-season resolution)
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
| Hoop Explorer | Complete — 6 seasons loaded (2021-2026) | `hoop_explorer_player_stats` (~16,750 rows, all D1), `hoop_explorer_team_stats` (~2,170 rows), S3 `raw/hoop_explorer/` | Player data includes 15 play-type pcts + `pos_confidence_pg/sg/sf/pf/c`. Team data includes offensive/defensive play-type pct, passing/assist texture, trans/scramble pct+ppp, and defensive four-factor overlays; these now flow into `team_style_vectors.parquet`. |
| hoopR ESPN PBP | Complete — 6 seasons (2021-2026) | `hoopr_team_season_stats`, `hoopr_player_season_stats`, S3 `raw/hoopr/` | Team PBP coverage partial for 2021-2024 (~172-235 teams); near-full for 2025-2026. |
| Feature parquet | Generated by notebooks | `data/features/player_features.parquet`, `data/features/team_style_vectors.parquet` | Gitignored; S3 is source of truth for shared feature files. |
| Model artifacts | Generated by scripts/notebooks | `data/models/*.pkl`, centroid CSVs, S3 `models/` | Local artifacts may differ by branch/run; scripts upload to S3 when credentials are configured. |

Useful docs:

| Doc | Purpose |
|---|---|
| [`../models/gap_matching_plan.md`](../models/gap_matching_plan.md) | Gap Matching model plan and implementation notes. |
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
| Status | ✅ Re-trained — tuned group-weighted architecture |
| Algorithm | K-Means, tuned group-weighted (6 feature groups, weights tuned by random search) + BART-only projection fallback |
| Current k | `9` |
| Model version | `k9-tuned-v1-2026` (was `k9-he-v1-2026`) |
| Training seasons | 2021-2026 pooled (min_pct ≥ 20 filter) |
| Training rows | 18,769 player-seasons |
| Output table | `player_archetypes` (18,769 rows; delete-then-insert on re-run; adds `archetype_memberships` JSONB after migration `9c8b7a6d5e4f`) |
| Local artifacts | `data/models/player_kmeans.pkl`, `player_scaler_base.pkl`, `player_scalers_grouped.pkl`, `player_archetype_labels.pkl`, `centroids_player.csv` — **renamed** from `player_scaler_bart.pkl`/`player_scaler_he.pkl` |

### Architecture (v2 — tuned group-weighted)

Replaces the flat BART-7/HE-15 two-scaler concat with 6 named feature groups, each independently scaled, recombined with **tuned weights** (not naive concatenation):

| Group | Features | Default → Selected weight |
|---|---|---:|
| `base_style_size` | 3pt/rim/mid rate, height | 0.18 → 0.16 |
| `creation_role` | usage_rate, assist_rate | 0.14 → 0.11 |
| `efficiency_impact` | true_shooting_pct, bpm | 0.10 → 0.12 |
| `base_two_way` | steal/block/def_reb/off_reb pct | 0.16 → 0.22 |
| `he_offensive_style` | 15 HE `off_style_*_pct` | 0.32 → 0.31 |
| `he_two_way` | def_adj_rapm, def_orb, def_stl, def_blk | 0.10 → 0.09 |

Weight search: 120 candidates, log-normal jitter on a 5,000-player HE-covered sample, scored on `0.35×silhouette + 0.20×davies_bouldin + 0.20×balance + 0.25×defense_focus` (custom term rewarding at least one defense-distinct cluster). Best trial: `weight_score=0.966`.

| Group | Size | Feature dims | Notes |
|---|---:|---:|---|
| HE two-way covered | 16,009 (85.3%) | 31 (12 base + 15 HE-style + 4 HE-defense) | Trains K-Means; full 31-dim distance for assignment |
| Base fallback | 2,760 (14.7%) | 12 | Projects through base-group dims of 31-dim centroids; confidence × 0.75 |

### Validation (actual run, 2026-06-19, post parquet-regen + semantic-reorder fix)

| Metric | Value |
|---|---:|
| Inertia (fit on extended space) | 7,812.4 |
| Silhouette — base-all weighted (fallback-comparable) | 0.1030 |
| Silhouette — HE style+two-way (fit-space) | 0.1127 |
| Davies-Bouldin — base-all weighted | 2.0272 |
| Avg confidence | 0.427 (median 0.429, p10 0.284) |

### Current Labels (from actual run; renamed labels marked)

| Cluster | Label | n (pooled, 6 seasons) | HE two-way | Avg confidence |
|---:|---|---:|---:|---:|
| C0 | Lead Scoring Playmaker | 1,873 | 1,663 | 0.476 |
| C1 | High-Usage Frontcourt Creator | 1,843 | 1,412 | 0.413 |
| C2 | Skilled Stretch Forward | 1,268 | 1,041 | 0.394 |
| C3 | Post Scoring Big | 949 | 782 | 0.470 |
| C4 | **Two-Way Perimeter Guard** (was `Perimeter Scoring Guard`) | 2,623 | 2,385 | 0.444 |
| C5 | Pressure Connector Guard | 2,519 | 2,125 | 0.442 |
| C6 | **Active Connector Forward** (was `Defensive Connector Forward`) | 2,922 | 2,586 | 0.351 |
| C7 | **Two-Way Spacing Wing** (was `Movement Spacing Wing`) | 3,579 | 2,963 | 0.459 |
| C8 | Interior Star Big | 1,193 | 1,063 | 0.392 |

### Known Follow-Ups

- **Critical bugfix — semantic cluster reordering (2026-06-19):** raw K-Means cluster indices are arbitrary and NOT stable across reruns — refitting on slightly different data silently swapped which index corresponded to which archetype, leaving the old hardcoded `ARCHETYPE_LABELS` dict pointing at the wrong clusters with no error. Confirmed empirically: a rerun after a feature/data refresh scrambled all labels except one that happened to land in the same slot by chance. Fixed by porting `team_clustering.ipynb`'s `_semantic_offense_mapping` pattern into `player_clustering.ipynb` (`_semantic_player_mapping`) — reorders centroids into 9 fixed semantic slots based on distinctive feature combinations (most-distinctive-first, each pick removed from the pool) before labels are applied. Verified bijective (no collisions/gaps) and end-to-end via DB write + test suite. **One residual imperfection:** slots 1 (`High-Usage Frontcourt Creator`) and 8 (`Interior Star Big`) both target "tall + high usage" without a fully clean separating signal in the criteria order — mechanically correct (each cluster placed exactly once) but C1's basketball-sense fit is weaker than the other 8 slots. Candidate follow-up: tighten slot 1's criterion to require above-median usage specifically.
- **Label renames validated against `def_adj_rapm` (2026-06-19):** C4, C6, C7 renamed after a multi-query DB validation (distribution spread, HE coverage %, cross-validation against raw steal/block rates, 6-season stability, representative-player eyeball check). C7 and C4 had consistently positive `def_adj_rapm` across all 6 seasons despite the old labels implying pure offense. C6 had the *highest* steal_pct and 2nd-highest block_pct of any cluster but near-zero/negative `def_adj_rapm` in 5 of 6 seasons — renamed to drop the unearned "Defensive" framing without implying weak defense (event rate ≠ defensive value).
- **Review labels:** Remaining labels (C0, C1, C2, C3, C5, C8) are grounded in centroid summaries; still candidate for basketball review before product copy is frozen.
- **Monitor membership usefulness:** The notebook stores top-three distance-derived archetype memberships; downstream consumers should decide whether to expose the confidence/top-three values directly.
- **Silhouette not comparable to prior (two-scaler) run:** clustering objective changed (31-dim weighted vs 22-dim unweighted) — do not compare silhouette across architectures directly.
- **Candidate v3 features:** HE `pos_confidence_*` for position-aware archetypes; richer P&R role inference from `pnr_passer_pct` vs `big_cut_roll_pct`.

---

## M2 - Team System Clustering

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/team_clustering.ipynb` |
| Status | ✅ Rebuilt and re-run for two-layer tuned group-weighted architecture (`team-v4-2026`). Notebook source, DB rows, and artifacts are aligned. |
| Algorithm | Two K-Means layers: offense identity + defense identity; definitive combined label plus top-three soft memberships |
| Current k | `K_OFFENSE=7`, `K_DEFENSE=5` |
| Model version | `team-v4-2026` (was `team-k9-v3-{season}` / `team-k9-v2-2026`) |
| Training seasons | 2021-2026 pooled (unchanged) |
| Output table | `team_system_profiles` (delete-then-insert on re-run; writes `offense_cluster_id`, `defense_cluster_id`, and membership JSONB columns after migration `9c8b7a6d5e4f`) |
| Artifacts | `team_offense_kmeans.pkl`, `team_defense_kmeans.pkl`, backward-compatible `team_kmeans.pkl`, `team_scaler_base.pkl`, `team_scalers_grouped.pkl`, `team_system_labels.pkl` |

### Architecture (v4 — two-layer tuned system identity)

Replaces the old one-layer offense-only clustering with two small, interpretable K-Means layers:

| Group | Features | Fit on |
|---|---|---|
| Offense `style_shape` | `team_three_rate`, `team_rim_rate`, `team_mid_rate` | All D1 teams |
| Offense `pace` | `adj_tempo` | All D1 teams |
| Offense `off_play_type` | transition, post-up, pick-pop, big-cut-roll, attack-kick, perimeter-cut | HE cluster-covered subset |
| Offense `off_passing` | assisted FG pct, assisted rim/mid/3PT texture | HE cluster-covered subset |
| Defense `def_play_type` | opponent/allowed defensive HE play-type shape | HE cluster-covered subset |
| Defense `def_pressure_shape` | `def_trans_pct`, `def_scramble_pct` | HE cluster-covered subset |

The definitive `system_label` is `{offense_label} / {defense_label}`. `cluster_id` remains the offense cluster for backward compatibility, while `offense_cluster_id` and `defense_cluster_id` carry the two-layer identity explicitly.

**Soft memberships:** K-Means distances are converted into distance-derived affinity scores. The DB stores top-three `offense_memberships`, `defense_memberships`, and combined `system_memberships` JSONB arrays while preserving one definitive label.

Fallback teams receive an offense projection from shot shape + pace. Defense is marked `Defense Unavailable` rather than inferred from unrelated offense-only data.

Reviewed labels written to artifacts/DB:

| Layer | Cluster labels |
|---|---|
| Offense | O0 `Perimeter Creation Offense`; O1 `Rim Pressure Offense`; O2 `Transition Attack`; O3 `Balanced Spread Attack`; O4 `Mid-Range Half-Court Offense`; O5 `Deliberate Half-Court Offense`; O6 `3PT Spacing Offense` |
| Defense | D0 `Scramble-Heavy Set Defense`; D1 `Rim-Exposure Defense`; D2 `Transition-Vulnerable Defense`; D3 `Jump-Shot Funnel Defense`; D4 `Controlled Half-Court Defense` |

### Known Issue This Targets

- **Degenerate C7 (v2/v3):** prior one-layer runs produced undersized/offense-only clusters. v4 separates offense/defense and keeps fallback offense-only to avoid defense labels from sparse data. Current run: offense clusters n=252-365 (12-18% each), defense n=397-460 (18-22% each) — no collapse.

### Validation (actual run, 2026-06-19)

| Offense | n | avg_adj_o | avg_adj_em | avg_confidence |
|---|---:|---:|---:|---:|
| O0 Perimeter Creation Offense | 328 | 104.4 | -1.51 | 0.215 |
| O1 Rim Pressure Offense | 259 | 108.8 | +2.25 | 0.234 |
| O2 Transition Attack | 345 | 106.2 | +1.22 | 0.234 |
| O3 Balanced Spread Attack | 365 | 104.0 | **-3.21** | 0.183 |
| O4 Mid-Range Half-Court Offense | 288 | **101.8** (lowest) | **-3.50** (worst) | 0.272 |
| O5 Deliberate Half-Court Offense | 321 | 107.7 | **+5.35** (best) | 0.177 |
| O6 3PT Spacing Offense | 252 | 106.1 | -0.65 | 0.227 |

| Defense | n | avg_adj_d | avg_def_efg | avg_confidence |
|---|---:|---:|---:|---:|
| D0 Scramble-Heavy Set Defense | 398 | 104.9 | 0.500 | 0.194 |
| D1 Rim-Exposure Defense | 460 | 106.8 | 0.511 | 0.194 |
| D2 Transition-Vulnerable Defense | 397 | 110.0 | 0.519 | 0.247 |
| D3 Jump-Shot Funnel Defense | 420 | 103.7 | 0.504 | 0.185 |
| D4 Controlled Half-Court Defense | 404 | 101.7 | 0.497 | 0.209 |

### Known Follow-Ups

- **Offense/defense confidence (~0.18-0.27) is structural, not a tuning bug.** Investigated with two ruled-out experiments (2026-06-19):
  - *Reweighted search objective* (silhouette 0.45→0.65, balance 0.30→0.15): selected weights came back **identical to many decimal places** — one candidate Pareto-dominates on silhouette, Davies-Bouldin, and balance simultaneously, so reweighting the combination cannot change the winner.
  - *Reduced K_OFFENSE 7→5*: confidence range barely moved (0.186-0.232 vs 0.177-0.272).
  - Conclusion: team offensive styles genuinely overlap across D1 — this ceiling reflects the data, not a misconfigured search. Don't spend further time tuning weights/K to chase this metric. (Compare to M1's player-archetype confidence ~0.43 — team style space has fewer, more overlapping signal dimensions than player style space.)
- **O3/O4 tracking the worst `avg_adj_em`/`avg_adj_o` is real signal, not feature leakage.** `adj_em`/`adj_o` are explicitly excluded from the clustering feature groups (overlay-only) — so this isn't leakage. It likely reflects a real basketball pattern: mid-range-heavy and no-clear-identity ("balanced") offenses correlating with worse efficiency outcomes is a well-established stathead finding. No code fix planned; documenting as expected.
- **Continue label review:** current names are reviewed pass-1 labels grounded in centroids and representative 2026 teams.
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
| Output table | `player_team_fit_scores` (1,343,050 rows in latest local run; `season` column added via migration `b5d2e9f4`) |
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
- **M2/M3 script refresh complete (2026-06-19):** team clustering and scheme fit have both been re-run after the two-layer team-label change, so `player_team_fit_scores.breakdown` is aligned with the current combined `{offense} / {defense}` system labels where those are surfaced.
- **hoopR spatial zones (M3 v3):** 5-zone spatial data available in `team_style_vectors.parquet`. Validate cosine discrimination before replacing stable 3-dim base.
- **Schema change:** `player_team_fit_scores` now has `season` column + `uq_fit_score` on `(player_id, school_id, season)`. API `fit_scores.py` uses season-aware current-season lookup for the live portal use case.

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
✅ feature_eng re-run
✅ M1/M2 script reruns
✅ M3 script rerun
✅ Gap Matching script rerun
✅ fit_scores.py partial real scoring
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

> **Resolved:** `fit_scores.py` uses season-aware lookup and serves real `scheme_fit` + `gap_match`; `role_fit` and `program_fit` remain deterministic 50.0 placeholders until M4/Program Fit exist.



1. ✅ Resolved — Gap Matching uses HE `pos_confidence_*` soft positions when available and falls back to one-hot `players.position`.
2. Do we want to store richer opportunity outputs in a dedicated `playing_time_projections` table, or only write `role_fit` first?
3. What public/proxy data should represent NIL budget and program fit?
4. ✅ Resolved — HE player play-type data (`off_style_*_pct`, 15-dim) is M1's extended feature set. 85.3% HE coverage with min_pct ≥ 20 filter. BART-only players (14.7%) project through BART-7 dims with 0.75 confidence discount.
5. How much score explanation is required for coaches before recommendation ranking feels trustworthy?
6. Should `off_trans_pct`/`def_trans_pct` be added to M2 style vector in next re-train? Data is in DB now.
