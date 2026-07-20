# PortalPoint Model Status

**Last updated:** July 15, 2026 (`/api/recommendations` wired to the real `rec-v1.2` engine; Scheme Fit mislabel/fake-metric fixes + `he_scheme_fit` surfaced in the API/UI — see `../models/recommendation_engine_plan.md` and M3 section below)
**Scope:** Model notebooks, model outputs, feature/data dependencies, and next modeling work.

Use this file as the model handoff. Architecture and deployment context live in
[`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md); app/API context lives in
[`APPLICATION_STATUS.md`](APPLICATION_STATUS.md).

---

## Per-Model Remaining Work

This is the fastest handoff table for model owners. "MVP" means required before the app should present the score as a real production signal; "v2" means valuable improvement after the baseline is usable.

| Model | Current state | MVP remaining work | v2 / improvement backlog | Primary references |
|---|---|---|---|---|
| M1 Player Clustering | ✅ Complete baseline — script-backed tuned group-weighted `k9-tuned-v1-2026`; 18,769 player-seasons (min_pct ≥ 20); 85.4% HE-covered in latest local run. **Critical bugfix:** added semantic cluster reordering (was silently scrambling all 9 labels on every rerun — see Known Follow-Ups). Current archetype labels are accepted for MVP. | None for MVP beyond keeping local artifacts/DB refreshed from the script. | Add HE `pos_confidence_*` for position-aware archetypes; richer P&R role inference; optional product-copy refinement if coaches prefer different wording. | [`../../scripts/run_player_clustering.py`](../../scripts/run_player_clustering.py); [`../../notebooks/models/player_clustering.ipynb`](../../notebooks/models/player_clustering.ipynb); this doc's M1 section |
| M2 Team System Clustering | ✅ Complete baseline — script-backed two-layer tuned group-weighted `team-v4-2026`; 2,158 team-seasons. Offense/defense memberships populated. Current offense/defense labels are accepted for MVP. Confidence (~0.2 avg) confirmed structural via 2 ruled-out experiments, not a tuning bug — see Known Follow-Ups. | None for MVP beyond keeping local artifacts/DB refreshed from the script. | Later evaluate hoopR spatial zones and defensive PPP/four-factor quality overlays; optional product-copy refinement if coaches prefer different wording. | [`../../scripts/run_team_clustering.py`](../../scripts/run_team_clustering.py); [`../../notebooks/models/team_clustering.ipynb`](../../notebooks/models/team_clustering.ipynb); this doc's M2 section |
| M3 Scheme Fit | ✅ `scheme-cos-v3` (2026-06-22) — **all-pairs** (was top-50-per-player): every eligible player×school×season, all 6 seasons (2021-2026), 9,666,119 records. School-chunked score+write loop in both script and notebook (in sync). `player_team_fit_scores` has `season` column and API reads current-season rows. | None for MVP. | M3 v4 with hoopR spatial zones; normalization/rescaling of scheme_fit for UI display. | [`../../scripts/run_scheme_fit.py`](../../scripts/run_scheme_fit.py); [`../../notebooks/models/scheme_fit_scorer.ipynb`](../../notebooks/models/scheme_fit_scorer.ipynb); this doc's M3 section |
| Gap Matching | ✅ `gap-cos-v4` (2026-06-23 refresh) — **all-pairs** scoring remains, but roster gap vectors now consume `portalpoint.modeling.roster_baseline` instead of only subtracting portal departures. Historical seasons use `player_season_stats(S+1)` as the roster-outlook source; latest season uses latest `roster_snapshots` where available, with same-season stats minus expected departures (`transfers`, HE `transfer_dest='NBA'`, senior/graduate class markers) as fallback for schools without usable snapshots. `player_team_fit_scores.is_portal_candidate` still scopes recommendations separately. Local DB now has 9,756,718 `gap-cos-v4` fit-score rows and 16,367 `roster_baseline_members`. | None for MVP after the 2026-06-23 script refresh; rerun only when source data changes. | Add roster-baseline confidence into breakdowns; include unmatched/new snapshot players as depth-only priors; add coach-adjustable needs and hoopR play-type gap features. | [`../../scripts/run_gap_matching.py`](../../scripts/run_gap_matching.py); [`../../src/portalpoint/modeling/roster_baseline.py`](../../src/portalpoint/modeling/roster_baseline.py); [`../../notebooks/models/gap_matching.ipynb`](../../notebooks/models/gap_matching.ipynb); this doc's Gap Matching section |
| Player Projection (Model #8) | ✅ Phase 2a next-season forecast (`player-proj-phase2a-fcast-v1`) is the production API default by product decision. Rows use observed CBB season `S` to write target projected season `S+1`, with `source_observed_season` / `target_projected_season` recorded in explanation JSON. Phase 0 (`player-projection-shrinkage-v2`) remains the simpler baseline comparator, and same-season Phase 2a (`player-projection-phase2a-v2`) remains diagnostic. **Phase 2a implemented + real-data validated (2026-06-25):** beats Phase 0 on held-out offense every rolling-origin fold, ties on defense, and exposes richer `projected_rates`/`projected_box_score`. Forecast value translation now includes source-season internal off/def/total value priors so elite returning players are not over-mean-reverted by skill transitions alone. CI bands vary by player and use rolling conformal scaling on top of propagated skill/source-value variance plus the residual error floor. Final rerun wrote 30,304 forecast rows for target seasons 2022-2027; rate payloads now use `player_season_stats` for source-team pace because `player_school_seasons` is empty locally. Gap B (observation-layer context adjustment) regressed accuracy on real data, so the no-context configuration remains enabled. | None after final forecast rerun/validation. | Context-feature redesign; CI calibration monitoring; continued validation/refinement with the destination-adjusted adapter. | [`../models/player_projection_state_space_plan.md`](../models/player_projection_state_space_plan.md) §22; [`../../scripts/run_player_projection.py`](../../scripts/run_player_projection.py) (`--phase {0,2a,both}`, both phases); [`../../notebooks/models/player_projection_state_space.ipynb`](../../notebooks/models/player_projection_state_space.ipynb) (Phase 0/1/2a, interactive/diagnostic) |
| M4 Role Fit / Playing Time | Complete — `playing-time-rotation-v2` in production. Full all-school 2027 all-pairs write done: 457,345 rows across 365 schools, 1,253 portal candidates. Minutes RMSE=5.58, interval coverage=0.87. Real efficiency bug fixed: default chunk-size 25 was re-evaluating 6 player-side CTEs 15x per chunk (15x redundant CTE work); changed default to 365 (all schools in one query). `role_fit` synced into `player_team_fit_scores` for 2027. | Add scenario controls for minutes/usage/displaced players; improve high-minute/high-usage tail recall and interval calibration; add richer team-style interactions. | [`../models/playing_time_rotation_model_plan.md`](../models/playing_time_rotation_model_plan.md); [`../../scripts/run_playing_time.py`](../../scripts/run_playing_time.py); [`../../src/portalpoint/modeling/playing_time.py`](../../src/portalpoint/modeling/playing_time.py) |
| M9 Destination-Adjusted Projection | Complete baseline — P0-P3 fixes applied + re-run 2026-07-01. 454,790 rows, target_season=2027, 2,420 training rows (↑ from 2,125), CV total_resid_std=2.892 (↓ from 2.967). MLflow v3 Staging (Δ=+2.5% vs @champion v2). **P0 box-score basis fixed:** `*_per_40` fields now scaled by `minutes/40`, not `possessions/100`. Cohort validation (P3) logged: tier_down strongest (Spearman=0.402), tier_same weakest (0.153). Known remaining gap: `estimate_usage_value_coef` returns fallback 1.5 every run (zero-overlap bug). Value deltas production-ready; per-game box stats should be validated against named-player checklist before coach-facing exposure. | Fit style/skill delta empirically (biggest R² gain); fix usage-coef zero-overlap; position-specific competition tier effects; barttorvik as secondary RAPM label; add eligibility/timing features. Full roadmap in `../models/destination_projection_plan.md` §19-20. | [`../models/destination_projection_plan.md`](../models/destination_projection_plan.md); [`../../scripts/run_destination_projection.py`](../../scripts/run_destination_projection.py); [`../../src/portalpoint/modeling/destination_projection.py`](../../src/portalpoint/modeling/destination_projection.py) |
| Program Fit | **Descoped from active roadmap (product decision, 2026-07-11).** Not started, and not currently planned — not blocking Recommendation Engine or fit-score work. | None — deprioritized. If revisited: define MVP proxies/data for NIL, geography, academics, and program constraints; implement MAUT-style calculator for `program_fit`. | Replace proxies with better public/partner data; expose configurable program priorities and learn from feedback. | [`../models/program_fit_model_plan.md`](../models/program_fit_model_plan.md); `APPLICATION_STATUS.md` |
| M5 Transfer Success | Not started. | Define outcome label and historical transfer training set; build first predictor writing to `predictions`. | Add confidence/risk explanations and calibration monitoring. | `notebooks/models/` future notebook |
| M6 Team Rating Projection | ✅ `team-roster-proj-v1` (2026-07-02, rerun 2026-07-11) — **457,345 rows live, PR #49 merged to `main` (2026-07-11)** (1,253 portal candidates × 365 D1 schools, target_season=2027). 3-fold CV: em_rmse 1.760/1.965/1.834, off_r2=0.973/0.970/0.976, def_r2=0.950/0.943/0.947. Two Ridge models (off/def) on 14 ROSTER_FEATURES vs BartTorvik adj_o/adj_d. All follow-up fixes landed (Ajay's PR review): source-season team/roster context, real candidate position/3PT/reb from prior stats, scaled explanation attribution, API season+expiry filtering, CI variance from both off/def residuals. **Freshman Prior v2** (2026-07-11, commit f7d9b6a): tier-calibrated base priors (`FRESHMAN_MIN_PCT_BY_TIER`), elite-recruiting-program flag (~15 schools, 1.5× multiplier), position-aware opportunity weighting (open_min/15 clamped [1/3, 1.5]), CI widening per freshman prior (`+0.4` variance/player), per-school audit dict logged to MLflow as `freshman_prior_audit.json`. MLflow `@champion` alias registered (v1). **Known issues:** CI width is constant per global `off_resid_std` (player-specific CI not yet added); 38% of rows have negative delta (expected from minimal-minutes candidates — documented open item); fold 3 off/def RMSE spike (4.8) from RAPM coverage gaps cancels in AdjEM (1.83). Freshman Prior v2 priors not yet applied to live rows (needs rerun). | Rerun script to apply Freshman Prior v2 to live rows; scale CI by player-specific uncertainty (`n_known_players`, minutes CI); diagnose fold 3 RAPM coverage gap. **Now unblocked for M7 integration — see Recommendation Engine row.** | [`../models/team_rating_projection_plan.md`](../models/team_rating_projection_plan.md) |
| M7 Recommendation Engine | ✅ `rec-v1.2` (2026-07-11), **API wired 2026-07-15.** 2-stage engine (Top-50 vectorized rank → Top-10 preference re-rank, Stage 1 in Python) on real `scheme_fit`/`gap_match`/`role_fit`/`team_impact_fit` (M6 `delta_adj_em`, normalized 0-100). `/api/recommendations` now computes live per request instead of serving `_STUB_SCORES` — confirmed via git history that stub had never been replaced on any branch. `CANDIDATE_SQL`/`MODEL_VERSION` moved from `scripts/run_recommendations.py` into `modeling/recommendations.py` (the packaged module `scripts/` isn't) so the API and batch script share one source. Added ownership check + `FitComponents.program_fit` → `team_impact_fit` in this response (engine has no program_fit signal). | None — done. | Add collaborative signals, shortlist feedback loops, explanation-aware ranking, user-adjustable `weight_team_rating`, CI-aware confidence discount. | [`../models/recommendation_engine_plan.md`](../models/recommendation_engine_plan.md); `APPLICATION_STATUS.md` |

Immediate modeling order:

```text
✅ feature_eng_m1_m2_m3.ipynb   (min_pct >= 20 filter; HE player enrichment added; 18,769 players)
✅ M1 player_clustering          (tuned group-weighted k9-tuned-v1-2026; top-three memberships populated)
✅ M2 team_clustering            (two-layer team-v4-2026 notebook/DB/artifacts populated)
✅ M3 scheme_fit_scorer          (scheme-cos-v3; all-pairs, all 6 seasons; 9,666,119 rows; migration b5d2e9f4 applied)
✅ Gap Matching                  (gap-cos-v4; all-pairs; 9,756,718 rows; shared roster_baseline; is_portal_candidate synced separately)
✅ fit_scores.py partial real scoring (scheme + gap, dynamic current-season resolution)
✅ Neutral Player Projection     (Phase 2a next-season forecast API default 2026-06-25; Phase 0 v2 retained as baseline comparator)
✅ Role Fit / Playing Time       (playing-time-rotation-v2; 457,345 rows, 365 schools, 2027 all-pairs complete; role_fit synced)
✅ Destination-Adjusted Proj     (player-destination-proj-v1; 454,790 rows, target=2027; CV resid_std=2.892; v3 Staging; P0-P3 fixes applied)
✅ Team Rating Projection        (team-roster-proj-v1; 457,345 rows, target=2027; em_rmse 1.76-1.97; PR #49 merged to main 2026-07-11)
✅ Recommendation Engine v1.2    (scheme+gap+role+team_impact_fit ranking; M6 delta_adj_em wired in and run for real, 2026-07-11)
✅ /api/recommendations wiring   (computes live per request now, 2026-07-15 — was a hardcoded stub since the original scaffold)
✳️ Program Fit                    (descoped from active roadmap, 2026-07-11 — not blocking anything below)
✅ fit_scores.py calibrated scoring (`fit-cal-v1` code + migration; shared DB backfill pending) — Scheme 25%, Gap 30%, Role 25%, Team Impact 20%; Program Fit removed
```

---

## Current Model Stack

PortalPoint is program-facing: coaching staffs evaluate transfer players for fit with a program. The fit stack is intended to combine:

1. `scheme_fit` - player shot profile vs team shot profile.
2. `gap_match` - player skills vs roster needs.
3. `role_fit` - projected opportunity / rotation fit.
4. `team_impact_fit` - projected change in team AdjEM from adding the player.

Current composite state:

```text
weighted_fit = 0.25*scheme_fit + 0.30*gap_match + 0.25*role_fit + 0.20*team_impact_fit
overall_fit = school_relative_calibration(weighted_fit)
```

`fit-cal-v1` converts the four raw signals to a comparable school-relative
candidate scale, shrinks low-confidence information toward neutral 50, and
persists canonical Overall Fit. Program Fit remains descoped and excluded.
Code and migration are complete; the shared 2027 database backfill is a
separate operational step. See `../models/fit_score_calibration.md`.

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
| [`../models/model_dependency_graph.md`](../models/model_dependency_graph.md) | Model dependency DAG, input/output contracts, run order, and downstream consumer map. |
| [`../models/gap_matching_plan.md`](../models/gap_matching_plan.md) | Gap Matching model plan and implementation notes. |
| [`../models/role_fit_playing_time_model_plan.md`](../models/role_fit_playing_time_model_plan.md) | Role fit / opportunity model design. |
| [`../models/program_fit_model_plan.md`](../models/program_fit_model_plan.md) | Program Fit manual/proxy scoring contract and implementation plan — **descoped 2026-07-11, not active work.** |
| [`../models/player_projection_state_space_plan.md`](../models/player_projection_state_space_plan.md) | Player talent projection plan. |
| [`../models/team_rating_projection_roster_tool_plan.md`](../models/team_rating_projection_roster_tool_plan.md) | Team rating impact / roster scenario model plan. |
| [`../models/team_rating_projection_plan.md`](../models/team_rating_projection_plan.md) | M6 implementation plan and real-run record (merged to `main` 2026-07-11). |
| [`../models/recommendation_engine_plan.md`](../models/recommendation_engine_plan.md) | M7 plan for wiring M6's `delta_adj_em` in as a macro ranking signal. |
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

### Current Labels (accepted for MVP; latest local DB refresh)

| Cluster | Label | n (pooled, 6 seasons) | HE two-way | Avg confidence |
|---:|---|---:|---:|---:|
| C0 | Lead Scoring Playmaker | 1,792 | 1,595 | 0.449 |
| C1 | High-Usage Frontcourt Creator | 1,697 | 1,546 | 0.346 |
| C2 | Skilled Stretch Forward | 1,261 | 1,074 | 0.389 |
| C3 | Post Scoring Big | 1,986 | 1,576 | 0.445 |
| C4 | Two-Way Perimeter Guard | 2,399 | 2,183 | 0.416 |
| C5 | Pressure Connector Guard | 2,531 | 2,064 | 0.390 |
| C6 | Active Connector Forward | 2,314 | 1,886 | 0.334 |
| C7 | Two-Way Spacing Wing | 3,119 | 2,618 | 0.432 |
| C8 | Interior Star Big | 1,673 | 1,489 | 0.400 |

### Known Follow-Ups

- **Critical bugfix — semantic cluster reordering (2026-06-19):** raw K-Means cluster indices are arbitrary and NOT stable across reruns — refitting on slightly different data silently swapped which index corresponded to which archetype, leaving the old hardcoded `ARCHETYPE_LABELS` dict pointing at the wrong clusters with no error. Confirmed empirically: a rerun after a feature/data refresh scrambled all labels except one that happened to land in the same slot by chance. Fixed by porting `team_clustering.ipynb`'s `_semantic_offense_mapping` pattern into `player_clustering.ipynb` (`_semantic_player_mapping`) — reorders centroids into 9 fixed semantic slots based on distinctive feature combinations (most-distinctive-first, each pick removed from the pool) before labels are applied. Verified bijective (no collisions/gaps) and end-to-end via DB write + test suite. **One residual imperfection:** slots 1 (`High-Usage Frontcourt Creator`) and 8 (`Interior Star Big`) both target "tall + high usage" without a fully clean separating signal in the criteria order — mechanically correct (each cluster placed exactly once) but C1's basketball-sense fit is weaker than the other 8 slots. Candidate follow-up: tighten slot 1's criterion to require above-median usage specifically.
- **Label renames validated against `def_adj_rapm` (2026-06-19):** C4, C6, C7 renamed after a multi-query DB validation (distribution spread, HE coverage %, cross-validation against raw steal/block rates, 6-season stability, representative-player eyeball check). C7 and C4 had consistently positive `def_adj_rapm` across all 6 seasons despite the old labels implying pure offense. C6 had the *highest* steal_pct and 2nd-highest block_pct of any cluster but near-zero/negative `def_adj_rapm` in 5 of 6 seasons — renamed to drop the unearned "Defensive" framing without implying weak defense (event rate ≠ defensive value).
- **Labels accepted for MVP:** Current archetype names are grounded in centroid summaries, representative players, and the 2026-06-19 DB validation pass. Future changes should be treated as product-copy refinement rather than a modeling blocker.
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

Accepted labels written to artifacts/DB:

| Layer | Cluster labels |
|---|---|
| Offense | O0 `Perimeter Creation Offense`; O1 `Rim Pressure Offense`; O2 `Transition Attack`; O3 `Balanced Spread Attack`; O4 `Mid-Range Half-Court Offense`; O5 `Deliberate Half-Court Offense`; O6 `3PT Spacing Offense` |
| Defense | D0 `Scramble-Heavy Set Defense`; D1 `Rim-Exposure Defense`; D2 `Transition-Vulnerable Defense`; D3 `Jump-Shot Funnel Defense`; D4 `Controlled Half-Court Defense` |

### Known Issue This Targets

- **Degenerate C7 (v2/v3):** prior one-layer runs produced undersized/offense-only clusters. v4 separates offense/defense and keeps fallback offense-only to avoid defense labels from sparse data. Current run: offense clusters n=252-365 (12-18% each), defense n=397-460 (18-22% each) — no collapse.

### Validation (latest local DB refresh)

| Offense | n | avg_adj_o | avg_adj_em |
|---|---:|---:|---:|
| O0 Perimeter Creation Offense | 386 | 103.5 | -1.89 |
| O1 Rim Pressure Offense | 409 | 106.2 | +2.17 |
| O2 Transition Attack | 236 | 108.4 | +2.01 |
| O3 Balanced Spread Attack | 334 | 104.8 | -1.43 |
| O4 Mid-Range Half-Court Offense | 242 | 101.8 | -2.28 |
| O5 Deliberate Half-Court Offense | 315 | 105.1 | +1.76 |
| O6 3PT Spacing Offense | 236 | 105.8 | -0.61 |

| Defense | n | avg_adj_d |
|---|---:|---:|
| D0 Scramble-Heavy Set Defense | 352 | 106.1 |
| D1 Rim-Exposure Defense | 466 | 105.3 |
| D2 Transition-Vulnerable Defense | 431 | 108.9 |
| D3 Jump-Shot Funnel Defense | 451 | 102.5 |
| D4 Controlled Half-Court Defense | 379 | 101.5 |
| Defense Unavailable | 79 | 109.1 |

### Known Follow-Ups

- **Offense/defense confidence (~0.18-0.27) is structural, not a tuning bug.** Investigated with two ruled-out experiments (2026-06-19):
  - *Reweighted search objective* (silhouette 0.45→0.65, balance 0.30→0.15): selected weights came back **identical to many decimal places** — one candidate Pareto-dominates on silhouette, Davies-Bouldin, and balance simultaneously, so reweighting the combination cannot change the winner.
  - *Reduced K_OFFENSE 7→5*: confidence range barely moved (0.186-0.232 vs 0.177-0.272).
  - Conclusion: team offensive styles genuinely overlap across D1 — this ceiling reflects the data, not a misconfigured search. Don't spend further time tuning weights/K to chase this metric. (Compare to M1's player-archetype confidence ~0.43 — team style space has fewer, more overlapping signal dimensions than player style space.)
- **O4 tracking the lowest `avg_adj_o`/`avg_adj_em` is real signal, not feature leakage.** `adj_em`/`adj_o` are explicitly excluded from the clustering feature groups (overlay-only) — so this isn't leakage. It likely reflects a real basketball pattern: mid-range-heavy offenses correlating with worse efficiency outcomes is a well-established stathead finding. No code fix planned; documenting as expected.
- **Labels accepted for MVP:** current names are grounded in centroids and representative 2026 teams. Future changes should be treated as product-copy refinement rather than a modeling blocker.
- Keep `adj_em` as overlay/quality indicator, not style feature.

---

## M3 - Scheme Fit Scorer

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/scheme_fit_scorer.ipynb` |
| Status | ✅ Complete — all-pairs, multi-season re-run |
| Model type | Deterministic cosine similarity |
| Model version | `scheme-cos-v3` (note: `player_team_fit_scores.model_version` reflects whichever model wrote/updated the row *last* — Gap Matching's upsert touches every row's `model_version` too since it's also all-pairs now, so after a `gap-cos-v4` rerun the column will read `gap-cos-v4` DB-wide even though `scheme_fit` itself is `scheme-cos-v3`'s output) |
| Seasons scored | 2021-2026 (season-matched: player-season × same-season teams), all eligible pairs — no top-k |
| Output table | `player_team_fit_scores` (9,666,119 rows written by the scheme-cos-v3 rebuild, before Gap Matching layers gap_match on top; `season` column added via migration `b5d2e9f4`) |
| MLflow | `scheme-fit-scorer v6` → Staging |

### Feature Contract

Player vector: `three_point_rate`, `rim_rate`, `mid_range_rate`

Team vector: `team_three_rate`, `team_rim_rate`, `team_mid_rate`

Same-season shot-rate vectors; cosine sim scaled 0-100. M2 system labels enrich UI breakdown but are not required for computation.

HE extended fit (breakdown only): `off_style_transition_pct`, `off_style_post_up_pct`, `off_style_pick_pop_pct`, `off_style_big_cut_roll_pct`, `off_style_attack_kick_pct`, `off_style_perimeter_cut_pct` — 6-dim cosine, added to `breakdown.scheme.he_scheme_fit` where both player and team are HE-covered. As of 2026-07-15, `score_all_seasons` also computes a per-play-type `he_breakdown` (same range-normalized-difference formula `scheme_breakdown()` already uses for the 3 shot-distribution dims, just over `HE_FEATS`) — this and `he_scheme_fit` itself now actually reach the API (`SchemeBreakdown` schema previously had no field for either; the DB had the data, the API silently dropped it). `FitScorePage` shows this as its own "Play Type Match" group, separate from the 3-dim "Shot Distribution Match" group, with a display-only average of the two as the section headline (the stored `scheme_fit` column stays shot-distribution-only everywhere else).

### Run Results (scheme-cos-v3, all-pairs — every player × every school with gap data that season)

| Season | Players | Teams | Mean fit | HE records | Total rows |
|---:|---:|---:|---:|---:|---:|
| 2021 | 4,266 | 346 | 85.2 | 807,176 (54.7%) | 1,476,036 |
| 2022 | 4,538 | 358 | 84.8 | 904,491 (55.7%) | 1,624,604 |
| 2023 | 4,556 | 363 | 85.1 | 935,616 (56.6%) | 1,653,828 |
| 2024 | 4,565 | 362 | 85.1 | 955,328 (57.8%) | 1,652,530 |
| 2025 | 4,571 | 364 | 85.5 | 981,492 (59.0%) | 1,663,844 |
| 2026 | 4,551 | 365 | 85.2 | 999,957 (60.2%) | 1,661,115 |
| **Total** | — | — | **85.2** | **4,584,060 (58.0%)** | **9,731,957** |

(Player/team counts per row are *DISTINCT player_id*/*DISTINCT school_id* across that season's rows post-rebuild, not the raw player_df/team_df load counts — those are slightly higher than the old top-50-only table's counts since this is now the full population, not a 50-school-per-player sample.)

### Known Issues / Follow-Ups

- **All-pairs (2026-06-22, `scheme-cos-v3`):** was top-50-per-player; now scores every eligible player×school pair, matching Gap Matching's scope (PR #33 follow-up — see CLAUDE.md Process Improvement TODO #5). The per-pair breakdown computation was vectorized (numpy broadcasting instead of a python-level `scheme_breakdown()` call per pair) to keep the all-pairs rebuild tractable — ~2-3x faster than the naive port.
- **Score compression:** 3-dim cosine on non-negative proportions that sum to ~1 clusters 70-100 for most pairs. Even worst realistic pairs score 43-72. `scheme_fit`, `gap_match`, and 2027 `role_fit` are now real where rows exist, but `program_fit` remains the 50.0 placeholder and component calibration is incomplete. Do not present `overall_fit` as final ranking truth until all 4 components are real and calibrated.
- **M2/M3 script refresh complete (2026-06-19):** team clustering and scheme fit have both been re-run after the two-layer team-label change, so `player_team_fit_scores.breakdown` is aligned with the current combined `{offense} / {defense}` system labels where those are surfaced.
- **hoopR spatial zones (M3 v4):** 5-zone spatial data available in `team_style_vectors.parquet`. Validate cosine discrimination before replacing stable 3-dim base.
- **Schema change:** `player_team_fit_scores` now has `season` column + `uq_fit_score` on `(player_id, school_id, season)`. API `fit_scores.py` uses season-aware current-season lookup for the live portal use case.
- **Real mislabel + fake sub-metric fixed (2026-07-15):** `breakdown.scheme.ball_movement_match` was never a ball-movement/assist signal — it's literally `mid_range_rate`'s match score, mislabeled since the field was added. Renamed to `mid_range_match` in `score_all_seasons`/`compute_scheme_fit_ondemand`/`SchemeBreakdown` schema/frontend. `usage_match` (always a hardcoded `50.0`, never real) removed entirely rather than flagged, matching Program Fit's "don't fabricate" convention. **Batch re-run to backfill existing rows into the new key/fields is blocked in this environment** — `scripts/run_scheme_fit.py` needs `data/features/team_style_vectors.parquet` (produced by `run_team_clustering.py`), which isn't present in this checkout; re-running that upstream model as a side effect was explicitly out of scope (could shift `system_label` assignments Gap Matching/others depend on). `fit_score_service.py` has a fallback read (new key → old key → `50.0`) so nothing breaks for unmigrated rows in the meantime — run `run_scheme_fit.py` from wherever this pipeline normally executes to complete the backfill.

---

## Gap Matching

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/gap_matching.ipynb` |
| Status | ✅ Complete — `gap-cos-v4`, all-pairs with shared roster baseline; DB refreshed locally 2026-06-23 |
| Model type | Deterministic cosine similarity (player stats vs roster gap vector) + shared roster baseline + reliability calibration |
| Model version | `gap-cos-v4` in `scripts/run_gap_matching.py`; notebook code imports `gap_matching.MODEL_VERSION` |
| Seasons scored | 2021-2026 (season-matched), all eligible player×school pairs — no longer scoped to whatever M3 pre-seeded |
| Output table | `player_team_fit_scores.gap_match` (9,756,718 `gap-cos-v4` rows) |

### Feature Contract

Player vector (14-dim rate/style, from `player_season_stats`): `usage_rate`, `true_shooting_pct`, `assist_rate`, `tov_pct_inverse`, `off_reb_pct`, `def_reb_pct`, `block_pct`, `steal_pct`, `free_throw_rate`, `three_point_rate`, `rim_rate`, `mid_range_rate`, `fg3_pct`, `rim_pct` — replaced the original 8-dim mostly-result-counting vector (`gap-cos-v3`, PR #33).

Position weights: `hoop_explorer_player_stats.pos_confidence_pg/sg/sf/pf/c` (soft, sums to 1.0), falling back to `barttorvik_role` → one-hot `players.position` → height prior, in that order, when HE has no match. Score is shrunk toward a conservative baseline (15.0) by a reliability score (position source quality × sample size × feature completeness) — see `gap_matching.add_gap_reliability()`.

### Run Results

**Last stored `gap-cos-v4` run (2026-06-23, all-pairs; MLflow `gap-matching-scorer` v12 promoted):**

| Season | Rows | Mean | Std | Min | Max |
|---:|---:|---:|---:|---:|---:|
| 2021 | 1,497,366 | 15.30 | 13.34 | 0.00 | 90.89 |
| 2022 | 1,633,680 | 15.39 | 14.27 | 0.00 | 92.81 |
| 2023 | 1,649,272 | 15.35 | 14.34 | 0.00 | 93.99 |
| 2024 | 1,647,965 | 15.77 | 14.83 | 0.00 | 92.86 |
| 2025 | 1,668,415 | 16.25 | 15.00 | 0.00 | 95.43 |
| 2026 | 1,660,020 | 17.03 | 15.89 | 0.00 | 95.03 |

Means are lower than the old `gap-cos-v2` top-50-only numbers (~6-7) for a structural reason, not a regression: all-pairs includes every player against every school, including plenty of genuinely poor-fit pairs that the old top-50-per-player scoping would never have surfaced. Mean naturally drops when the denominator widens to "everyone," not just each player's already-good-fit subset.

**scheme_fit std for comparison:** ~12 (see M3 section) — gap_match still differentiates noticeably better.

### Score Distribution Analysis

Gap_match is **intentionally sparse and right-skewed** — this is correct behavior, not compression:

- Mean ~16 reflects that most player-school pairs have low gap alignment (player offers what the school already has)
- High scores (80+) only appear when a player's stat profile closely matches the specific dimensions a school is deficient in
- std ~13-16 vs scheme_fit's ~12 — gap_match still provides better differentiation between pairs
- Schools with highest mean gap_match = programs with the most roster holes across positions

Contrast with scheme_fit: compressed high (mean ~85) because cosine similarity on non-negative proportions summing to 1 naturally clusters near 1. Gap_match gap vectors are sparse (zeroed out where school is at/above benchmark), so cosine sim is low for most pairs.

**Combined overall_fit impact:** With 2 real components:
```
overall_fit ≈ 0.30 × 85.2 + 0.20 × 15.9 + 0.50 × 50.0 = 25.6 + 3.2 + 25.0 = 53.8
```
Range remains narrow until role_fit and program_fit are real. Do not surface overall_fit to users yet.

### Known Issues / Limitations

- **All-pairs (`gap-cos-v3` -> `gap-cos-v4`):** was scoped to whatever M3 pre-seeded (top-50-per-player); now scores every eligible player×school pair — see CLAUDE.md Process Improvement TODO #5. Two consequences worth knowing: (1) the 2021-2026 mean/std numbers above aren't comparable to the old `gap-cos-v2` table — different denominator (everyone, not each player's already-good-fit top-50) — don't read the drop as a quality regression; (2) the "preserve existing Scheme Fit context" step (`gm.load_existing_scheme_context()`) now queries per school-chunk instead of preloading the whole table — preloading ~9.6M scheme_fit rows into one python dict took ~64 minutes once Scheme Fit went all-pairs too. The script now deletes stale prior `gap-cos-*` rows after a successful rebuild so schools that fall out of the valid roster-baseline scope do not keep old gap scores.
- **Shared roster baseline:** `scripts/run_gap_matching.py` now builds roster gap vectors from `portalpoint.modeling.roster_baseline`. Historical seasons use next-season `player_season_stats` to infer the target roster outlook; the latest season uses latest `roster_snapshots` when available, with expected-departure subtraction as fallback. This separates roster membership from portal availability and gives Role Fit / Team Rating Projection a single baseline contract to consume.
- **player_school_seasons empty:** Roster baseline now uses `player_season_stats` directly rather than waiting on the empty `player_school_seasons` table.
- **is_portal_candidate:** `player_team_fit_scores.is_portal_candidate` flags rows whose player has a matched Entered/Committed `transfer_portal_events` row that season — the recommendation-surface scope decision (keep all-pairs scoring, filter at the query/API layer instead of restricting what gets scored). This is intentionally separate from `is_roster_baseline_member`.

---

## Player Projection (Model #8)

Full design, every real bug found and fixed, and the complete real-data record live in [`../models/player_projection_state_space_plan.md`](../models/player_projection_state_space_plan.md) §22 — this section is the fast-handoff summary only.

| Item | Current state |
|---|---|
| Phase 0 | ✅ Baseline comparator. `player-projection-shrinkage-v2` after defensive-sign fix. Empirical-Bayes shrinkage + Ridge value model vs. Hoop Explorer RAPM. Still written by the pipeline and useful for model comparison. |
| Phase 1 | ✅ Validated, not in production. Single-season scalar Kalman filter/smoother per skill — calibration check only, no DB write. |
| Phase 2a | ✅ Production API default is the next-season forecast version, `player-proj-phase2a-fcast-v1`. It advances each observed season `S` to target projected season `S+1`, records source/target season metadata, and carries the source-season internal value prior in the explanation payload. Same-season `player-projection-phase2a-v2` rows remain diagnostic state estimates. Two-level Kalman (intra-season + cross-season persistence/drift) plus source-value persistence in the final value layer. **Beats Phase 0 on held-out offense every fold; ties on defense.** Writes projected rates/box-score payloads under a separate model version. |
| Gap B (context adjustment) | ⚠️ Coded, real-data tested, **regresses accuracy** (worse than even Phase 0 on offense). Root-cause analysis found the current team-level context signals explain too little skill variance; not enabled. Revisiting context should use stronger, skill-specific opponent signals rather than these blunt proxies. |
| Gap C (rate projections) | ✅ Real per-40/per-100 attempt-rate + direct-readoff rates, feeding `projected_rates`/`projected_box_score` (previously empty `{}` placeholders for Phase 2a rows). |
| `foul_discipline` (11th skill) | ✅ Added 2026-06-24 — `hoopr_player_game_logs.fouls` was the one real, previously-unused offensive/defensive metric. Phase 1/2-only (Phase 0 has no season-grain fouls column — intentional asymmetry). |
| Offense/defense feature-set split | ✅ Added 2026-06-25 — `off_adj_rapm` regressed on offense-only skills, `def_adj_rapm` on defense-only skills (+ position, shared). **Real, accepted tradeoff:** offense barely moved, defense R² dropped ~30% relative (0.119→0.083) for both Phase 0 and Phase 2a — kept anyway for interpretability. |
| Defensive value sign convention | ✅ Fixed 2026-06-25 — Hoop Explorer raw `def_adj_rapm` is lower-is-better, and the source identity is `adj_rapm_margin = off_adj_rapm - def_adj_rapm`. `value_per_100` now subtracts the raw defensive prediction instead of adding it. |
| Production integration | ✅ **Updated 2026-06-25.** API's hardcoded `model_version` default now serves `player-proj-phase2a-fcast-v1` by product decision. Phase 2a did not clear the automatic MLflow 5% promotion gate, but its architecture is the intended production direction and fresh validation is effectively tied with Phase 0. |
| Test coverage | 199 tests passing (`uv run pytest -q`, 2026-06-25). |

**Known real bugs found and fixed this work (full detail in the plan doc §22), for anyone touching this code next:** a `BrokenProcessPool` from an eager full-frame-copy memory blowup in the parallelized Kalman fit; a near-zero-minutes division blowup in Gap C's attempt-rate targets; a `CardinalityViolation` from a small join-fan-out duplicate-row issue in the season-recovery step (now structurally fixed, not just band-aided, by threading the real `season` value through instead of relying on a positionally-reconstructed `season_rank`); a stale-cache risk from cache filenames that didn't vary by the requested `seasons` list; and a test-fixture `expires_at` staleness bug in `scripts/seed_test_data.py` that was silently masked locally by real pipeline writes refreshing the same row.

---

## M6 - Team Rating Projection

| Item | Current state |
|---|---|
| Script | `scripts/run_team_rating_projection.py` |
| Module | `src/portalpoint/modeling/team_rating_projection.py` |
| Notebook | `notebooks/models/team_rating_projection_roster_tool.ipynb` |
| Status | ✅ Rerun complete (2026-07-11); Freshman Prior v2 implemented; **PR #49 merged to `main` (2026-07-11)** |
| Algorithm | Two Ridge models (off/def) trained on 14 ROSTER_FEATURES vs BartTorvik adj_o/adj_d; counterfactual diff = delta_adjEM |
| Model version | `team-roster-proj-v1` |
| Training seasons | 2021–2026 (2,158 school-seasons) |
| Inference | 1,253 portal candidates × 365 D1 schools = 457,345 rows, target_season=2027 |
| Output table | `team_rating_projections` (migration `c3a9e1f5b847` adds `season`, breakdown columns, extended unique constraint) |
| MLflow | `team-rating-scorer`, Staging; `@champion` alias registered (v1) |
| run_id (latest) | `8a33bdc29e334c62b8bc860e2a270afb` (2026-07-11 rerun) |

### CV Metrics (3-fold rolling-origin)

| Fold | val | off_rmse | def_rmse | em_rmse | off_R² | def_R² |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2024 | 2.577 | 2.568 | 1.760 | 0.973 | 0.950 |
| 2 | 2025 | 2.927 | 2.960 | 1.965 | 0.970 | 0.943 |
| 3 | 2026 | 4.769 | 4.847 | 1.834 | 0.976 | 0.947 |

- off_resid_std=2.008, def_resid_std=2.057
- Gate metric: `fold3_em_rmse=1.834` (lower is better; no prior champion to compare)

### Architecture

**14 ROSTER_FEATURES:** `weighted_off_impact`, `weighted_def_impact`, `top1_off_impact`, `top2_impact`, `bench_depth_impact`, `three_pt_coverage`, `rim_protection`, `pg_creation`, `rebounding_coverage`, `usage_concentration`, `returning_minutes_pct`, `n_known_players`, `conference_tier`, `adj_tempo_prior`

Player quality signal: HE `off_adj_rapm`/`def_adj_rapm` (RAPM-based). Candidate inserted via `playing_time_projections.expected_minutes` + `displaced_minutes` JSONB. Slot baselines fill missing players by `(conference_tier, position)` mean RAPM.

Incoming freshmen from roster snapshots receive conservative depth priors via `build_freshman_prior_rows`. **Freshman Prior v2** (2026-07-11): tier-keyed base min_pct (`FRESHMAN_MIN_PCT_BY_TIER={1:10,2:8,3:7,4:6}`), elite-recruiting-program flag (~15 schools with 1.5× multiplier), per-position opportunity weighting (`open_min/15` clamped [1/3,1.5]), per-school audit dict logged to MLflow as `freshman_prior_audit.json`. CI widens by `+0.4` variance per freshman prior (`analytical_ci(n_freshman_priors)`). Freshman prior rows excluded from `n_known_players`. Rerun needed to apply v2 priors to live rows.

CI: analytical Gaussian approximation (`delta ± 1.28×sqrt(2×(off_resid_std² + def_resid_std²))`) replacing 200-sample bootstrap — 200× faster, still a global-width approximation until player/school-specific uncertainty is wired in.

### Known Issues / Improvement Roadmap

- **Fold 3 off/def RMSE spike (4.8 vs 2.6 in folds 1-2):** individual adj_o/adj_d predictions degrade in 2026, but errors are correlated (cancel in AdjEM → em_rmse=1.83). Likely cause: RAPM coverage gaps in 2026 force more slot-baseline fills, degrading individual components while leaving net impact roughly correct. Investigate `n_known_players` distribution for 2026 vs prior seasons.
- **Candidate profile fields:** now use prior-season candidate position, 3PT rate, and offensive rebounding when `prior_stats` has the player; neutral placeholders remain only as missing-data fallbacks.
- **Incoming freshman prior:** now covers true freshmen present in roster snapshots but missing from player-season tables; future improvement is to replace the conservative slot prior with recruiting ratings or team-specific freshman development priors.
- **CI width is constant per model run** (global off/def residual variance). Scale by `playing_time_projections.minutes_ci_upper - minutes_ci_lower` and `n_known_players` for player/school-specific uncertainty.
- **Continuity proxy:** now uses source-season `roster_state_features` returning/departing/open minutes JSON when present; still falls back to 1.0 when roster-state rows are missing.
- **MLflow `@champion` alias:** registered (v1, 2026-07-11). Future promotions gate against it normally via `maybe_promote`.
- **Performance:** Step 6 (counterfactuals) now ~28 min (was hours). Remaining bottleneck: `build_roster_features` creates a pandas DataFrame per player-school pair (~457K calls). Further speedup possible by replacing the DataFrame-based `build_roster_features` with vectorized numpy operations across all schools for a given player.

---

## Planned Models And Calculators

| # | Model / Calculator | Status | Depends on | Output |
|---|---|---|---|---|
| 4 | Playing Time / Rotation -> Role Fit | ✅ Complete baseline; `playing-time-rotation-v2` full 2027 all-school write completed and synced to fit scores | shared roster baseline, 2027 neutral player projection, 2026 fit/roster context | `playing_time_projections`, `player_team_fit_scores.role_fit` |
| 4a | Neutral Player Projection | ✅ Phase 2a next-season forecast production API default; Phase 0 v2 retained as baseline comparator | player game logs or season-level fallback, HE impact labels | `player_projections` (`player-proj-phase2a-fcast-v1` production rows; `player-projection-shrinkage-v2` / `player-projection-phase2a-v2` comparators) |
| 4b | Destination-Adjusted Player Projection | ✅ Complete baseline; `player-destination-proj-v1` writes destination-mode `player_projections` rows | neutral player projection + role/minutes outputs | destination projection rows/artifacts |
| - | Program Fit Calculator | **Descoped from active roadmap (2026-07-11)** | user preferences, NIL/location/academic proxies | `player_team_fit_scores.program_fit` |
| 5 | Transfer Success Predictor | Not started | historical transfers/outcomes (`transfers` now populated for season 2026 — full 2020-2026 backfill pending) | `predictions` |
| 6 | Team Rating Projection | ✅ `team-roster-proj-v1`; PR #49 merged to `main` (2026-07-11) | shared roster baseline + player projection + role/minutes | `team_rating_projections` |
| 7 | Recommendation Engine | ✅ `rec-v1.2`; M6 macro signal wired in and run for real (2026-07-11); API endpoint still stubbed | scheme/gap/role/team_impact_fit all real (plan: `../models/recommendation_engine_plan.md`); program/projection deferred (program_fit descoped) | `recommendations` |

Critical path:

```text
✅ feature_eng re-run
✅ M1/M2 script reruns
✅ M3 script rerun
✅ Gap Matching script rerun
✅ fit_scores.py partial real scoring
✅ Neutral Player Projection (Phase 2a next-season forecast production API default; Phase 0 baseline retained)
✅ Role Fit / Playing Time full 2027 production write
✅ Destination-Adjusted Player Projection baseline
✅ Recommendation Engine v1 script
✅ Team Rating Projection PR #49 merged to main
✅ Recommendation Engine v1.2 (M6 team_rating_delta macro signal wired in + run for real, 2026-07-11, see ../models/recommendation_engine_plan.md)
✳️ Program Fit — descoped, not on this path
  -> fit_scores.py full scoring (reweight decision pending, program_fit descoped)
  -> Recommendation/API v2 (wire /api/recommendations to real recommendations rows)
```

Parallel work:

- Transfer Success Predictor can start once historical transfer/outcome labels are ready.
- Team Rating Projection is merged and ready for M7 to consume — see `../models/recommendation_engine_plan.md`.

---

## Model Open Questions

> **Updated:** `fit_scores.py` uses season-aware lookup and serves real `scheme_fit` + `gap_match` + `role_fit`. `program_fit` remains a deterministic 50.0 placeholder — **descoped as a product decision (2026-07-11)**, not pending a model anymore.



1. ✅ Resolved — Gap Matching uses HE `pos_confidence_*` soft positions when available and falls back to one-hot `players.position`.
2. ✅ Resolved — store first-class opportunity outputs in `playing_time_projections`, then sync/upsert Role Fit into `player_team_fit_scores`.
3. ✳️ Descoped (2026-07-11) — Program Fit is out of scope for now; question moot until revisited.
4. ✅ Resolved — HE player play-type data (`off_style_*_pct`, 15-dim) is M1's extended feature set. 85.3% HE coverage with min_pct ≥ 20 filter. BART-only players (14.7%) project through BART-7 dims with 0.75 confidence discount.
5. How much score explanation is required for coaches before recommendation ranking feels trustworthy? Partial answer landed 2026-07-15 (per-metric hover tooltips, key-insight summaries, Glossary page) — still open whether it's sufficient.
6. Should `off_trans_pct`/`def_trans_pct` be added to M2 style vector in next re-train? Data is in DB now.
7. Should `he_scheme_fit` (6-dim HoopExplorer play-type cosine, surfaced in the API/UI 2026-07-15) be folded into how `scheme_fit` itself is computed/weighted everywhere it feeds other models (`overall_fit`, M7 ranking), not just shown as a display-only average on `FitScorePage`? Same class of open decision as Program Fit's reweighting question — needs its own validation pass, not a UI-only change.
