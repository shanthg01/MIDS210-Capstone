# PortalPoint Model Status

**Last updated:** June 16, 2026  
**Scope:** Model notebooks, model outputs, feature/data dependencies, and next modeling work.

Use this file as the model handoff. Architecture and deployment context live in
[`ARCHITECTURE_STATUS.md`](ARCHITECTURE_STATUS.md); app/API context lives in
[`APPLICATION_STATUS.md`](APPLICATION_STATUS.md).

---

## Per-Model Remaining Work

This is the fastest handoff table for model owners. "MVP" means required before the app should present the score as a real production signal; "v2" means valuable improvement after the baseline is usable.

| Model | Current state | MVP remaining work | v2 / improvement backlog | Primary references |
|---|---|---|---|---|
| M1 Player Clustering | Complete and usable; k=8 offensive archetypes written to `player_archetypes`. | Install notebook-only `mlflow` and re-log if shared run history is required; confirm S3 artifacts after final run. | Revisit with stable Hoop Explorer player play types; test action-level labels such as P&R handler, spot-up shooter, cutter, roller/pop big, transition scorer; consider defensive/matchup context. | [`../../notebooks/models/player_clustering.ipynb`](../../notebooks/models/player_clustering.ipynb); this doc's M1 section |
| M2 Team System Clustering | Complete baseline; ⚠️ re-train needed after feature_eng re-run (HE coverage 19%→98%). | Re-run feature_eng then re-train M2; human review of `SYSTEM_LABELS` after. | Evaluate adding `off_trans_pct`/`def_trans_pct` to style vector; hoopR spatial zones decision. | [`../../notebooks/models/team_clustering.ipynb`](../../notebooks/models/team_clustering.ipynb); this doc's M2 section |
| M3 Scheme Fit | Complete deterministic `scheme-cos-v2`; ⚠️ re-run needed after M2 re-trains. | Re-run after M2; validate score distribution. | Test M3 v3 with hoopR spatial zones and/or HE supplementary scheme fit; improve breakdown explanations for coaches. | [`../../notebooks/models/scheme_fit_scorer.ipynb`](../../notebooks/models/scheme_fit_scorer.ipynb); this doc's M3 section |
| Gap Matching | Not started; next critical path item. | Build notebook, decide position-specific vs school-wide gaps, write `gap_match` to `player_team_fit_scores`, then update `fit_scores.py` to expose real scheme + gap. | Add roster snapshots, portal departure confidence, coach-adjustable needs, and richer position/role buckets. | [`../models/gap_matching_plan.md`](../models/gap_matching_plan.md) |
| M4 Role Fit / Playing Time | Not started. | Build roster-aware opportunity model that produces `role_fit`; decide whether MVP only writes score or also stores opportunity details. | Add scenario controls for minutes/usage/displaced players; add uncertainty intervals and roster snapshot versioning. | [`../models/playing_time_rotation_model_plan.md`](../models/playing_time_rotation_model_plan.md) |
| Program Fit | Not started. | Define MVP proxies/data for NIL, geography, academics, and program constraints; implement MAUT-style calculator for `program_fit`. | Replace proxies with better public/partner data; expose configurable program priorities. | `APPLICATION_STATUS.md`; future program-fit plan needed |
| M5 Transfer Success | Not started. | Define outcome label and historical transfer training set; build first predictor writing to `predictions`. | Add confidence/risk explanations and calibration monitoring. | `notebooks/models/` future notebook |
| M6 Team Rating Projection | Planned, not started. | Wait for player projection + role/minutes outputs; define MVP baseline/candidate roster delta. | Use posterior samples, lineup interactions, and coach scenario overrides. | [`../models/team_rating_projection_roster_tool_plan.md`](../models/team_rating_projection_roster_tool_plan.md) |
| M7 Recommendation Engine | Not started; blocked by full fit stack. | Build once scheme/gap/role/program components are real; rank players per program into `recommendations`. | Add collaborative signals, shortlist feedback loops, and explanation-aware ranking. | `APPLICATION_STATUS.md`; future recommendation plan needed |

Immediate modeling order:

```text
Re-run feature_eng_m1_m2_m3.ipynb   <- HE team coverage 19%->98% after 5-season load
  -> Re-run M2 (team_clustering)    <- fresh team_style_vectors with full HE coverage
  -> Re-run M3 (scheme_fit_scorer)  <- downstream of M2
  -> Gap Matching
  -> fit_scores.py partial real scoring
  -> Role Fit / Playing Time
  -> Program Fit
  -> fit_scores.py full scoring
  -> Recommendation Engine
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
overall_fit = 0.30 * scheme_fit + 0.70 * 50.0
```

`gap_match`, `role_fit`, and `program_fit` are still stubbed at 50 until their models/calculators are built.

---

## Data Available For Models

| Source | Status | Primary tables/files | Notes |
|---|---|---|---|
| BartTorvik | Complete, multi-season loaded | `player_season_stats`, `team_season_stats`, S3 `raw/barttorvik/` | Normalized Postgres rows plus raw S3 files. 2021-2026 player seasons are available locally. |
| Hoop Explorer | Complete — 5 seasons loaded (2022-2026) | `hoop_explorer_player_stats` (13,993 rows, all D1), `hoop_explorer_team_stats` (1,811 rows), S3 `raw/hoop_explorer/` | Player data includes 15 play-type pcts + `pos_confidence_pg/sg/sf/pf/c`. Team data includes trans/scramble pct+ppp. Feature engineering re-run needed before these flow into model inputs. |
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
| Status | Complete for current offensive archetype version |
| Algorithm | Weighted K-Means |
| Current k | `8` |
| Model version | `wkmeans-k8-v3-2026` |
| Training seasons | 2021-2026 BartTorvik player seasons |
| Scored season | 2026 |
| Output table | `player_archetypes` |
| Local artifacts | `data/models/player_kmeans.pkl`, `player_scaler.pkl`, `player_archetype_labels.pkl`, `player_feature_metadata.pkl`, `centroids_player.csv` |

### Selected Features

Production feature set: `current7_ppg_med_poss_light`

| Feature | Weight | Why included |
|---|---:|---|
| `usage_rate` | 1.00 | Offensive burden / possession share. |
| `true_shooting_pct` | 1.00 | Scoring efficiency. |
| `assist_rate` | 1.00 | Creation burden. |
| `bpm` | 1.00 | Overall box-score impact. |
| `three_point_rate` | 1.00 | Shot profile / spacing. |
| `rim_rate` | 1.00 | Rim pressure / finishing profile. |
| `mid_range_rate` | 1.00 | Mid-range tendency. |
| `points_per_game` | 0.55 | Scoring volume; helps separate primary vs lower-volume creators. |
| `possession_security` | 0.35 | Turnover-aware creation; assist creation minus turnover pressure. |

### Current Labels

These are offensive role archetypes from box-score and shot-profile data. They are intentionally not full scouting labels until Hoop Explorer/play-type data is stable.

| Cluster | Label | Basketball read |
|---:|---|---|
| 0 | Efficient Perimeter Scorer | Higher-volume wing/guard scorers, good TS, moderate creation. |
| 1 | Primary Engine | High scoring volume, high usage, high assist, strong BPM. |
| 2 | Interior Creator | Bigger rim/mid creators with efficient scoring and frontcourt role mix. |
| 3 | High-Touch Playmaker | On-ball creation with lower efficiency and higher turnover pressure. |
| 4 | Developmental Perimeter | Low-volume, perimeter-oriented, inefficient offensive profile. |
| 5 | Rim Finisher | Low-usage frontcourt/rim finishers with strong efficiency. |
| 6 | Floor Spacer | Low-usage, high 3PT-rate spacing wings/guards. |
| 7 | Interior/Mid-Range Forward | Bigger mid-range/rim profile with limited spacing and creation. |

### Recent Validation

The updated notebook was executed through final summary by the user. MLflow logging was skipped because `mlflow` is not installed in the uv environment; the core model run and DB/artifact work completed.

Partial validation before DB writes showed:

| Metric | Value |
|---|---:|
| Train rows | 23,913 |
| Current rows | 4,083 |
| k=8 silhouette | 0.1727 |
| k=8 Davies-Bouldin | 1.4687 |
| Current avg confidence | 0.493 |

### Known Follow-Ups

- Revisit M1 once Hoop Explorer player play-type data is fully ingested and trusted.
- Candidate future features: pick-and-roll handling, spot-up usage, cutting, roll/pop finishing, transition usage, defensive activity/matchup context.
- If MLflow tracking is required, install notebook-only `mlflow` and rerun the final logging cell.

---

## M2 - Team System Clustering

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/team_clustering.ipynb` |
| Status | Complete, but labels should still be reviewed after new data refreshes |
| Algorithm | K-Means |
| Output table | `team_system_profiles` |
| Artifacts | `team_kmeans.pkl`, `team_bart_scaler.pkl`, `team_he_scaler.pkl`, `team_system_labels.pkl` |

### Feature Shape

- All D1 teams receive the BartTorvik style vector:
  - `team_three_rate`
  - `team_rim_rate`
  - `team_mid_rate`
  - `adj_tempo`
- Hoop Explorer-covered teams receive additional play-type frequency dimensions through a second scaler.
- Non-Hoop Explorer teams are assigned via the BartTorvik centroid projection with discounted confidence.
- hoopR team spatial/tempo columns are available for future enrichment.

### Follow-Ups

- **Feature engineering re-run needed:** HE team coverage was ~19% (single season, 356 rows). Now 1,811 team-seasons (5 seasons); re-run `feature_eng_m1_m2_m3.ipynb` to pick up expanded HE data, then re-train M2.
- Evaluate adding `off_trans_pct`/`def_trans_pct` (now in `hoop_explorer_team_stats`) to team style vector before re-training.
- Review `SYSTEM_LABELS` for basketball readability after re-train.
- Decide whether hoopR spatial zones should alter M2 or only feed M3/team projection.
- Keep `adj_em` as an overlay/quality indicator, not a style feature.

---

## M3 - Scheme Fit Scorer

| Item | Current state |
|---|---|
| Notebook | `notebooks/models/scheme_fit_scorer.ipynb` |
| Status | Complete |
| Model type | Deterministic cosine similarity |
| Model version | `scheme-cos-v2` |
| Output table | `player_team_fit_scores.scheme_fit` |
| MLflow | Run logged when MLflow dependency is present |

### Feature Contract

Player vector:

```text
three_point_rate, rim_rate, mid_range_rate
```

Team vector:

```text
team_three_rate, team_rim_rate, team_mid_rate
```

Both vectors should be same-season shot-rate vectors on the same scale. M2 labels enrich the UI/breakdown but are not required to compute `scheme_fit`.

### Follow-Ups

- Re-run after M2 re-trains on expanded HE data.
- Validate whether score compression remains acceptable.
- hoopR spatial zones can support an M3 v3 vector, but should be validated before replacing the stable 3-dim base.
- HE supplementary scheme fit can appear in breakdown JSON; HE team coverage is now 1,811 rows (5 seasons) — viable after feature_eng re-run.

---

## Gap Matching - Next Critical Model

| Item | Current state |
|---|---|
| Status | Not started |
| Primary doc | [`../models/gap_matching_plan.md`](../models/gap_matching_plan.md) |
| Expected output | `player_team_fit_scores.gap_match` |
| External data needed | None for MVP |

Planned feature space:

```text
ppg, rpg, apg, spg, bpg, ts_pct, usage_rate, three_point_rate
```

Immediate decisions:

1. Position inference: `hoop_explorer_player_stats.pos_confidence_pg/sg/sf/pf/c` now populated for 13,993 player-seasons — use these rather than relying on BART's sparse position column.
2. Use per-position roster gaps if HE position confidence is reliable (recommend this path now that data exists).
3. Fall back to school-wide aggregate gaps if position coverage is weak after join.
4. Write a small feature contract before scoring all player-team pairs.

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

1. Gap Matching position handling: HE `pos_confidence_*` data now available (13,993 rows). Per-position gaps are now feasible — recommend this path. Confirm join rate to BART player-seasons before committing.
2. Do we want to store richer opportunity outputs in a dedicated `playing_time_projections` table, or only write `role_fit` first?
3. What public/proxy data should represent NIL budget and program fit?
4. Should Hoop Explorer player play-type data (`off_style_*_pct`, 15 types, 13,993 rows) become part of M1 feature vector? Coverage ~60% vs. hoopR's ~20% — higher coverage and semantically richer. Requires feature_eng update + new M1 experiment.
5. How much score explanation is required for coaches before recommendation ranking feels trustworthy?
6. Should `off_trans_pct`/`def_trans_pct` be added to M2 style vector in next re-train? Data is in DB now.
