# PortalPoint Model Status

**Last updated:** June 25, 2026 (Player Projection production semantics updated — API serves the Phase 2a next-season forecast model, with Phase 0 v2 and same-season Phase 2a v2 retained as baseline/diagnostic comparators — see Player Projection section below and `../models/player_projection_state_space_plan.md` §22 for the full record)
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
| Player Projection (Model #8) | ✅ Phase 2a next-season forecast (`player-proj-phase2a-fcast-v1`) is the production API default by product decision. Rows use observed CBB season `S` to write target projected season `S+1`, with `source_observed_season` / `target_projected_season` recorded in explanation JSON. Phase 0 (`player-projection-shrinkage-v2`) remains the simpler baseline comparator, and same-season Phase 2a (`player-projection-phase2a-v2`) remains diagnostic. **Phase 2a implemented + real-data validated (2026-06-25):** beats Phase 0 on held-out offense every rolling-origin fold, ties on defense, and exposes richer `projected_rates`/`projected_box_score`. Forecast value translation now includes source-season internal off/def/total value priors so elite returning players are not over-mean-reverted by skill transitions alone. CI bands vary by player and use rolling conformal scaling on top of propagated skill/source-value variance plus the residual error floor. Final rerun wrote 30,304 forecast rows for target seasons 2022-2027; rate payloads now use `player_season_stats` for source-team pace because `player_school_seasons` is empty locally. Gap B (observation-layer context adjustment) regressed accuracy on real data, so the no-context configuration remains enabled. | None after final forecast rerun/validation. | Context-feature redesign; CI calibration monitoring; eventual destination-adjusted projection once Role Fit exists. | [`../models/player_projection_state_space_plan.md`](../models/player_projection_state_space_plan.md) §22; [`../../scripts/run_player_projection.py`](../../scripts/run_player_projection.py) (`--phase {0,2a,both}`, both phases); [`../../notebooks/models/player_projection_state_space.ipynb`](../../notebooks/models/player_projection_state_space.ipynb) (Phase 0/1/2a, interactive/diagnostic) |
| M4 Role Fit / Playing Time | Not started. | Build roster-aware opportunity model that produces `role_fit`; consume `portalpoint.modeling.roster_baseline` for current roster/outlook membership before estimating minutes or displaced usage. | Add scenario controls for minutes/usage/displaced players; add uncertainty intervals and roster snapshot versioning. | [`../models/playing_time_rotation_model_plan.md`](../models/playing_time_rotation_model_plan.md); [`../../src/portalpoint/modeling/roster_baseline.py`](../../src/portalpoint/modeling/roster_baseline.py) |
| Program Fit | Not started. | Define MVP proxies/data for NIL, geography, academics, and program constraints; implement MAUT-style calculator for `program_fit`. | Replace proxies with better public/partner data; expose configurable program priorities. | `APPLICATION_STATUS.md`; future program-fit plan needed |
| Replace proxies with better public/partner data; expose configurable program priorities and learn from feedback. | [`../models/program_fit_model_plan.md`](../models/program_fit_model_plan.md); `APPLICATION_STATUS.md` |
| M5 Transfer Success | Not started. | Define outcome label and historical transfer training set; build first predictor writing to `predictions`. | Add confidence/risk explanations and calibration monitoring. | `notebooks/models/` future notebook |
| M6 Team Rating Projection | Planned, not started. | Wait for player projection + role/minutes outputs; define MVP baseline/candidate roster delta. | Use posterior samples, lineup interactions, and coach scenario overrides. | [`../models/team_rating_projection_roster_tool_plan.md`](../models/team_rating_projection_roster_tool_plan.md) |
| M7 Recommendation Engine | Not started; blocked by full fit stack. | Build once scheme/gap/role/program components are real; rank players per program into `recommendations`. | Add collaborative signals, shortlist feedback loops, and explanation-aware ranking. | `APPLICATION_STATUS.md`; future recommendation plan needed |

Immediate modeling order:

```text
✅ feature_eng_m1_m2_m3.ipynb   (min_pct >= 20 filter; HE player enrichment added; 18,769 players)
✅ M1 player_clustering          (tuned group-weighted k9-tuned-v1-2026; top-three memberships populated)
✅ M2 team_clustering            (two-layer team-v4-2026 notebook/DB/artifacts populated)
✅ M3 scheme_fit_scorer          (scheme-cos-v3; all-pairs, all 6 seasons; 9,666,119 rows; migration b5d2e9f4 applied)
✅ Gap Matching                  (gap-cos-v4; all-pairs; 9,756,718 rows; shared roster_baseline; is_portal_candidate synced separately)
✅ fit_scores.py partial real scoring (scheme + gap, dynamic current-season resolution)
✅ Neutral Player Projection     (Phase 2a next-season forecast API default 2026-06-25; Phase 0 v2 retained as baseline comparator)
→  Role Fit / Playing Time
→  Destination-Adjusted Player Projection
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
| [`../models/model_dependency_graph.md`](../models/model_dependency_graph.md) | Model dependency DAG, input/output contracts, run order, and downstream consumer map. |
| [`../models/gap_matching_plan.md`](../models/gap_matching_plan.md) | Gap Matching model plan and implementation notes. |
| [`../models/role_fit_playing_time_model_plan.md`](../models/role_fit_playing_time_model_plan.md) | Role fit / opportunity model design. |
| [`../models/program_fit_model_plan.md`](../models/program_fit_model_plan.md) | Program Fit manual/proxy scoring contract and implementation plan. |
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

HE extended fit (breakdown only): `off_style_transition_pct`, `off_style_post_up_pct`, `off_style_pick_pop_pct`, `off_style_big_cut_roll_pct`, `off_style_attack_kick_pct`, `off_style_perimeter_cut_pct` — 6-dim cosine, added to `breakdown.scheme.he_scheme_fit` where both player and team are HE-covered.

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
- **Score compression:** 3-dim cosine on non-negative proportions that sum to ~1 clusters 70-100 for most pairs. Even worst realistic pairs score 43-72. With only `scheme_fit` real (30% weight) and other 3 components stubbed at 50, `overall_fit` range ≈ **[55, 65]** — insufficient for ranking. Do not surface overall_fit to users until all 4 components are real.
- **M2/M3 script refresh complete (2026-06-19):** team clustering and scheme fit have both been re-run after the two-layer team-label change, so `player_team_fit_scores.breakdown` is aligned with the current combined `{offense} / {defense}` system labels where those are surfaced.
- **hoopR spatial zones (M3 v4):** 5-zone spatial data available in `team_style_vectors.parquet`. Validate cosine discrimination before replacing stable 3-dim base.
- **Schema change:** `player_team_fit_scores` now has `season` column + `uq_fit_score` on `(player_id, school_id, season)`. API `fit_scores.py` uses season-aware current-season lookup for the live portal use case.

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

## Planned Models And Calculators

| # | Model / Calculator | Status | Depends on | Output |
|---|---|---|---|---|
| 4 | Playing Time / Rotation -> Role Fit | Not started | shared roster baseline, neutral player projection, M1/M3 helpful | `player_team_fit_scores.role_fit` |
| 4a | Neutral Player Projection | ✅ Phase 2a next-season forecast production API default; Phase 0 v2 retained as baseline comparator | player game logs or season-level fallback, HE impact labels | `player_projections` (`player-proj-phase2a-fcast-v1` production rows; `player-projection-shrinkage-v2` / `player-projection-phase2a-v2` comparators) |
| 4b | Playing Time / Rotation -> Role Fit | Not started | shared roster baseline, neutral player projection, M1/M3 helpful | `player_team_fit_scores.role_fit` |
| 4c | Destination-Adjusted Player Projection | Not started | neutral player projection + role/minutes outputs | destination projection rows/artifacts |
| - | Program Fit Calculator | Not started | user preferences, NIL/location/academic proxies | `player_team_fit_scores.program_fit` |
| 5 | Transfer Success Predictor | Not started | historical transfers/outcomes (`transfers` now populated for season 2026 — full 2020-2026 backfill pending) | `predictions` |
| 6 | Team Rating Projection | Not started | shared roster baseline + player projection + role/minutes | `team_rating_projections` |
| 7 | Recommendation Engine | Not started | all fit components | `recommendations` |

Critical path:

```text
✅ feature_eng re-run
✅ M1/M2 script reruns
✅ M3 script rerun
✅ Gap Matching script rerun
✅ fit_scores.py partial real scoring
✅ Neutral Player Projection (Phase 2a next-season forecast production API default; Phase 0 baseline retained)
  -> Role Fit / Playing Time
  -> Destination-Adjusted Player Projection
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
