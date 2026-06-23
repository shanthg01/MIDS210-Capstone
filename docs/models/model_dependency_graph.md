# PortalPoint Model Dependency Graph

**Last updated:** June 23, 2026  
**Scope:** Model inputs, outputs, downstream consumers, and execution order.

This document is the dependency contract for PortalPoint's data science stack. It
answers:

- Which models must run before others?
- Which outputs are model inputs elsewhere?
- Which tables/files are written by each step?
- Which dependencies are hard blockers vs helpful context?

The short version:

```text
Raw data / ingest
  -> Feature engineering
  -> M1 Player Clustering
  -> M2 Team System Clustering
  -> M3 Scheme Fit
  -> Gap Matching
  -> Player Projection
  -> Role Fit / Playing Time
  -> Team Rating Projection
  -> Program Fit
  -> Fit Score Calibration / Overall Fit
  -> Recommendation Engine
  -> API + Frontend
```

## Current Dependency DAG

```text
BartTorvik / Hoop Explorer / hoopR / Transfers / Rosters
    |
    v
Feature Engineering
    |
    +--> M1 Player Clustering ---------------------------+
    |       writes: player_archetypes                    |
    |                                                     |
    +--> M2 Team System Clustering -------------------+   |
    |       writes: team_system_profiles              |   |
    |                                                 |   |
    +--> M3 Scheme Fit -------------------------------+---+
    |       writes: player_team_fit_scores.scheme_fit     |
    |                                                     |
    +--> Gap Matching ------------------------------------+
            writes: player_team_fit_scores.gap_match      |
                                                          |
Player Game Logs + Game Context + HE Impact Labels        |
    |                                                     |
    v                                                     |
Neutral Player Projection <------------------------------+
    writes: neutral player_projections or prediction artifacts
    |
    v
Role Fit / Playing Time <------ Roster Snapshots + Transfers
    writes: player_team_fit_scores.role_fit
    writes: playing_time_projections
    |
    v
Destination-Adjusted Player Projection
    consumes: neutral projection + role/minutes
    writes: destination player projections or predictions
    |
    v
Team Rating Projection
    writes: team_rating_projections

Program Fit
    writes: player_team_fit_scores.program_fit

Fit Score Calibration
    consumes: scheme_fit + gap_match + role_fit + program_fit
    writes: calibrated overall_fit / confidence metadata
    |
    v
Recommendation Engine
    consumes: fit scores + projections + availability + preferences
    writes: recommendations
    |
    v
API + Frontend
```

## Model Inventory

| Component | Inputs | Outputs | Used by |
|---|---|---|---|
| Data Loading | BartTorvik, Hoop Explorer, hoopR, transfer/roster sources | Source DB tables and raw S3/local files | Everything |
| Feature Engineering | `player_season_stats`, `team_season_stats`, Hoop Explorer, hoopR aggregates | `data/features/player_features.parquet`, `data/features/team_style_vectors.parquet` | M1, M2, M3, Gap Matching |
| M1 Player Clustering | Player feature parquet | `player_archetypes`, model artifacts | Gap Matching, Player Projection, Role Fit, Recommendations, UI explanations |
| M2 Team System Clustering | Team style parquet | `team_system_profiles`, model artifacts | Scheme Fit breakdown, Role Fit context, Team Rating Projection, Recommendations |
| M3 Scheme Fit | Player/team shot and style vectors; M2 labels for explanation | `player_team_fit_scores.scheme_fit` and scheme breakdown | Fit Score, Role Fit context, Recommendations |
| Roster Baseline | `player_season_stats`, latest `roster_snapshots`, `transfers`, HE `transfer_dest` | shared roster-outlook frame (code module, not persisted yet) | Gap Matching, Role Fit, Team Rating Projection |
| Gap Matching | Player stat vectors, shared roster baseline, positions, archetypes | `player_team_fit_scores.gap_match` and gap breakdown | Fit Score, Role Fit, Recommendations |
| Neutral Player Projection | Player game logs, season stats, opponent context, HE impact labels, archetypes | **Real — `player_projections` (27,047 rows, 2021-2026, neutral mode), served by `GET /api/players/{id}/projection`.** Phase 0 (`player-projection-shrinkage-v1`): shrinkage + Ridge value model vs. HE RAPM. Phase 1 (single-season Kalman, 2026 only) is a validation step, not a second production path — see `player_projection_kalman.py` | Role Fit, Transfer Success, Recommendations, destination-adjusted projection |
| Role Fit / Playing Time | Shared roster baseline, player projections, scheme/gap context, archetypes | `role_fit`, expected minutes, usage role, displaced minutes | Fit Score, Team Rating Projection, Recommendations |
| Destination-Adjusted Player Projection | Neutral player projection, Role Fit minutes/usage/displacement, team pace, competition context | School-specific projected stats and value | Team Rating Projection, Predictions API, Player Profile, Compare |
| Team Rating Projection | Shared roster baseline, player projections, role/minutes, team ratings | `team_rating_projections` | Fit page, Compare page, Recommendations |
| Program Fit | User preferences, school/player metadata, NIL/geography/academic proxies | `player_team_fit_scores.program_fit` | Fit Score, Recommendations |
| Fit Score Calibration | Scheme, gap, role, program, confidence flags | Calibrated `overall_fit`, component confidence metadata | Recommendations, Fit page, Compare page |
| Transfer Success / Outcome | Historical transfers, pre/post stats, projections | Prediction/risk outputs | Predictions API, Player profile, Compare, Recommendations risk context |
| Recommendation Engine | Calibrated fit scores, projections, team-rating deltas, availability, preferences | `recommendations` | Dashboard, Recommendations API |
| API + Frontend | Output tables from all model layers | User-facing product | Final app workflow |

## Hard Dependencies

Hard dependencies must exist before the downstream model can run meaningfully.

| Downstream model | Hard dependencies |
|---|---|
| M1 Player Clustering | Feature parquet with player features |
| M2 Team System Clustering | Feature parquet with team style vectors |
| M3 Scheme Fit | Player features, team style vectors, current team/player IDs |
| Roster Baseline | `player_season_stats` for historical S/S+1 inference; latest `roster_snapshots` when no S+1 exists; expected-departure fallback (`transfers`, HE `transfer_dest='NBA'`, senior/graduate class markers) for schools without a usable latest snapshot. This is the canonical roster-membership layer for roster-aware models. |
| Gap Matching baseline | Player season stats, positions or HE soft positions, and shared roster baseline. **No longer depends on M3's fit-score pairs** — `gap-cos-v4` scores every eligible player×school×season pair independently, same all-pairs scope Scheme Fit moved to. Still must run *after* Scheme Fit, though: Scheme Fit's full-season delete+rebuild would wipe Gap Matching's output if run second. |
| Player Projection | Player game logs (✅ `hoopr_player_game_logs` populated for all 7 seasons 2020-2026 as of 2026-06-23) or season-level fallback (✅ used by Phase 0); player ID joins; opponent/team context for full scope |
| Role Fit / Playing Time | Shared roster baseline, player projections (✅ Phase 0 real, see above), roster-state features (optional explanations) |
| Neutral Player Projection | ✅ Phase 0 real (season-level shrinkage + Ridge). Player game logs now fully backfilled 2020-2026, unblocking Phase 2 (cross-season `rho`/dev-curve) on the data axis — Phase 2 itself not yet built |
| Destination-Adjusted Player Projection | Neutral Player Projection plus Role Fit / Playing Time outputs |
| Team Rating Projection | Shared roster baseline, player projections, expected minutes/displacement from Role Fit |
| Program Fit | User/program preferences and agreed MVP proxy/manual-input contract |
| Fit Score Calibration | Real scheme, gap, role, and program component scores |
| Recommendation Engine | Candidate availability, calibrated fit scores, user preferences |
| API / Frontend real-output integration | Real output records from the relevant model tables |

## Soft Dependencies

Soft dependencies improve quality, explanations, or confidence, but should not
always block an MVP run.

| Model | Soft dependencies |
|---|---|
| Neutral Player Projection | M1 archetype priors; HE impact labels; transfer history; team system context |
| Role Fit | Scheme Fit, Gap Matching, M1 archetypes, M2 team systems, coach/rotation tendencies |
| Destination-Adjusted Player Projection | Scheme/system context, confidence flags, player/team comparable cohorts |
| Team Rating Projection | M2 team systems, Hoop Explorer adjusted team labels, posterior projection samples |
| Program Fit | NIL proxies, academic proxies, geography, user-entered constraints |
| Transfer Success | Player Projection outputs, Role Fit outputs, similar-player cohorts |
| Recommendations | Team Rating Projection delta, Transfer Success risk, data-quality confidence |

## Execution Order

Current completed scripts stop at Gap Matching. Planned scripts should extend the
same local-first pattern.

```text
1.  Apply migrations
2.  Ingest BartTorvik
3.  Ingest Hoop Explorer
4.  Ingest hoopR season aggregates
5.  Ingest remaining data: game logs, game context (✅ done, all 7 seasons 2020-2026 as of 2026-06-23), transfers (✅ done, 2021-2026 — 2020 scraped but 0 matched, needs matcher fix), rosters (✅ done, 357 of ~365 D1 schools)
    — see ARCHITECTURE_STATUS.md "Ingest And Feature Pipeline" for the exact commands, including the full transfer backfill and full roster run
6.  Generate feature parquet
7.  Run M1 Player Clustering
8.  Run M2 Team System Clustering
9.  Run M3 Scheme Fit
10. Run Gap Matching baseline or v2
11. Run Neutral Player Projection — ✅ Phase 0 done (`run_player_projection.py`); Phase 1 (Kalman) is notebook-only validation, no script
12. Run Role Fit / Playing Time
13. Run Destination-Adjusted Player Projection
14. Run Team Rating Projection
15. Run Program Fit
16. Run Fit Score Calibration / Overall Fit refresh
17. Run Recommendation Engine
18. Verify API + frontend outputs
```

## Current vs Planned Outputs

| Output | Current state | Owner |
|---|---|---|
| `player_archetypes` | Real, accepted for MVP | M1 |
| `team_system_profiles` | Real, accepted for MVP | M2 |
| `player_team_fit_scores.scheme_fit` | Real | M3 |
| `player_team_fit_scores.gap_match` | Real code path — `gap-cos-v4`, all-pairs with shared roster baseline (stored DB rows need rerun after merge) | Gap Matching |
| `transfers` / `transfer_portal_events` | Real for 2021-2026 (499/628/774/1,037/1,346/1,251 promoted by season); 2020 scraped, 0 matched — matcher bug, not a backfill gap | Issue #17 item 3 |
| `roster_snapshots` / `roster_snapshot_players` | Real, 357 distinct schools (target ~365) | Issue #17 item 4 |
| `roster_state_features` | Depends on roster_snapshots above — re-run against the full 357-school set if last built against a narrower subset | Issue #17 item 6 |
| `player_team_fit_scores.role_fit` | Placeholder `50.0` | Role Fit |
| `player_team_fit_scores.program_fit` | Placeholder `50.0` | Program Fit |
| `player_team_fit_scores.overall_fit` | Partial; compressed until all components are real | Fit Score Calibration |
| `player_projections` | **Real — 27,047 rows, neutral mode, 2021-2026.** Phase 0 production; Phase 1 (Kalman) is validation-only, doesn't write here | Player Projection |
| `playing_time_projections` | Planned / table decision pending | Role Fit |
| `team_rating_projections` | Table exists; no real rows yet | Team Rating Projection |
| `predictions` | Table/API exists; no real rows yet | Transfer Success |
| `recommendations` | Table/API exists; no real rows yet | Recommendation Engine |

## Canonical Question Chain

The models should read like one product workflow, not isolated experiments:

```text
Who is the player?
  -> What kind of player is he?
  -> What kind of team is this?
  -> Does the style fit?
  -> Does the roster need him?
  -> How good will he be next season?
  -> How many minutes would he get here?
  -> How much would he improve this team?
  -> Does he fit program constraints?
  -> Should we recommend him to this program?
```

## Downstream Consumer Map

### M1 Player Clustering

- Feeds Gap Matching archetype/role gap explanations.
- Feeds Player Projection priors and comparable-player groups.
- Feeds Role Fit roster crowding by archetype.
- Feeds Recommendation explanations.
- Feeds UI player labels.

### M2 Team System Clustering

- Feeds Scheme Fit breakdowns.
- Feeds Role Fit destination style context.
- Feeds Team Rating Projection style features.
- Feeds Recommendation explanations.
- Feeds UI team-system labels.

### M3 Scheme Fit

- Feeds Fit Score.
- Feeds Role Fit as context.
- Feeds Recommendation ranking and explanation.

### Gap Matching

- Feeds Fit Score.
- Feeds Role Fit opportunity context.
- Feeds Recommendation ranking and explanation.

### Player Projection

- Feeds Role Fit.
- Feeds Team Rating Projection.
- Feeds Transfer Success / risk context.
- Feeds Recommendations.
- Feeds player profile and compare views.

### Role Fit / Playing Time

- Feeds Fit Score.
- Feeds Team Rating Projection through expected minutes and displaced minutes.
- Feeds Recommendations.
- Feeds Fit page and Compare page.

### Team Rating Projection

- Feeds Fit page.
- Feeds Compare page.
- Feeds Recommendations.

### Program Fit

- Feeds Fit Score.
- Feeds Recommendations.

### Transfer Success / Outcome

- Feeds Predictions API.
- Feeds player risk context.
- Feeds Compare page and Recommendations as secondary context.

## Issue Map

| Issue | Dependency role |
|---|---|
| #17 Remaining Data Loading | Removes source-data blockers for all downstream models. Items 1-2, 3-4, and 6 all done — **items 1-2 (hoopR game logs/context) completed 2026-06-23**, full 2020-2026 backfill, the last open piece. Item 5 (derived `player_team_seasons`) dropped on review — mostly duplicated `player_season_stats`, and its named use cases (inferring transfers/roster history) are now redundant since items 3-4 give real data instead. Items 7-8 (projection tables, Program Fit proxy) reassigned to #18/#25/#20. |
| #18 Player Projection Model | ✅ **Phase 0 done (2026-06-23)** — foundational player-talent model, real output in `player_projections`, served by the API. Phase 1 (single-season Kalman) validated; Phase 2 (cross-season, block covariance) is the open next step, now unblocked on data after item #17. See `docs/models/player_projection_state_space_plan.md` §15/§17 and CLAUDE.md's Process Improvement TODOs #6 for the phase-by-phase record. |
| #19 Team Rating Projection Model | Roster counterfactual model |
| #20 Program Fit Model Decision | Decides MVP program-fit feasibility and proxy contract |
| #21 Fit Score Calibration | Makes component aggregation useful for ranking |
| #22 Recommendation Engine | Final program-facing ranking layer |
| #23 Transfer Success / Outcome Model | Historical outcome/risk model |
| #24 Standardize Downstream Role And System Labels Around Accepted Clusters | Keeps model/UI language consistent |
| #25 Role Fit / Playing Time Model | Opportunity model and real `role_fit` |
| #26 Departure-Aware Gap Matching v2 | ✅ Done (2026-06-21) — `gap-cos-v2`, `gap_matching.filter_departed()`, scoped to confirmed portal transfers (`transfers`), current season only |
| #27 Model Pipeline Orchestration And Runbook | Turns this dependency graph into runnable local process |
| #28 API / Frontend Integration For Real Model Outputs | Replaces stubs with real model outputs in product surfaces |
| PR #33 Gap Matching Coverage + Portal Scope | ✅ Done (2026-06-22) — Gap Matching and Scheme Fit both moved to all-pairs (`gap-cos-v3`/`scheme-cos-v3`); `player_team_fit_scores.is_portal_candidate` added as the recommendation-surface scope flag (kept in sync by `portalpoint.modeling.availability`); `fit_scores.py`/`players.py` API surface updated accordingly — see CLAUDE.md Process Improvement TODO #5 |
| Roster Baseline Follow-up | ✅ Code path on `roster-baseline` branch — `gap-cos-v4` consumes `portalpoint.modeling.roster_baseline`; Role Fit and Team Rating Projection should use the same module instead of re-deriving roster membership |

## Notes For Future Model Work

- Do not treat the models as independent. The product value comes from the chain.
- Player Projection is foundational, not just an API endpoint model.
- Role Fit owns opportunity; Player Projection owns talent.
- Team Rating Projection should consume underlying minutes/displacement, not only the `role_fit` score.
- Transfer Success is useful risk context, but should not block the first real recommendation engine.
- Program Fit should be a deterministic/proxy calculator for MVP unless better data becomes available.
- Score calibration should happen after all four fit components are real.
