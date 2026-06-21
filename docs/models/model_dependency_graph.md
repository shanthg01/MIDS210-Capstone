# PortalPoint Model Dependency Graph

**Last updated:** June 21, 2026  
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
Player Projection <---------------------------------------+
    writes: player_projections or prediction artifacts
    |
    v
Role Fit / Playing Time <------ Roster Snapshots + Transfers
    writes: player_team_fit_scores.role_fit
    writes: playing_time_projections
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
| Gap Matching | Player stat vectors, roster state, positions, archetypes | `player_team_fit_scores.gap_match` and gap breakdown | Fit Score, Role Fit, Recommendations |
| Player Projection | Player game logs, season stats, opponent context, HE impact labels, archetypes | Neutral player projection: impact, usage, box rates, uncertainty | Role Fit, Team Rating Projection, Transfer Success, Recommendations |
| Role Fit / Playing Time | Roster snapshots, transfers, player projections, scheme/gap context, archetypes | `role_fit`, expected minutes, usage role, displaced minutes | Fit Score, Team Rating Projection, Recommendations |
| Team Rating Projection | Player projections, role/minutes, roster state, team ratings | `team_rating_projections` | Fit page, Compare page, Recommendations |
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
| Gap Matching baseline | Player season stats, positions or HE soft positions, fit-score pairs from M3 |
| Gap Matching v2 | Transfers (✅ season 2026 populated, full 2020-2026 backfill pending), roster snapshots (✅ table populated, full ~365-school run pending), derived roster-state features (not yet built — Issue #17 items 5-6) |
| Player Projection | Player game logs (✅ `hoopr_player_game_logs` populated for 2026) or season-level fallback; player ID joins; opponent/team context for full scope |
| Role Fit / Playing Time | Roster snapshots (✅ populated), transfers/departures (✅ populated), player projections (not yet built), roster-state features (not yet built) |
| Team Rating Projection | Player projections, expected minutes/displacement from Role Fit, roster state |
| Program Fit | User/program preferences and agreed MVP proxy/manual-input contract |
| Fit Score Calibration | Real scheme, gap, role, and program component scores |
| Recommendation Engine | Candidate availability, calibrated fit scores, user preferences |
| API / Frontend real-output integration | Real output records from the relevant model tables |

## Soft Dependencies

Soft dependencies improve quality, explanations, or confidence, but should not
always block an MVP run.

| Model | Soft dependencies |
|---|---|
| Player Projection | M1 archetype priors; HE impact labels; transfer history; team system context |
| Role Fit | Scheme Fit, Gap Matching, M1 archetypes, M2 team systems, coach/rotation tendencies |
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
5.  Ingest remaining data: game logs, game context (✅ done, 2026), transfers (✅ done, 2026 — 2020-2026 backfill pending), rosters (✅ table populated, one school verified — full ~365-school run pending)
    — see ARCHITECTURE_STATUS.md "Ingest And Feature Pipeline" for the exact commands, including the full transfer backfill and full roster run
6.  Generate feature parquet
7.  Run M1 Player Clustering
8.  Run M2 Team System Clustering
9.  Run M3 Scheme Fit
10. Run Gap Matching baseline or v2
11. Run Player Projection
12. Run Role Fit / Playing Time
13. Run Team Rating Projection
14. Run Program Fit
15. Run Fit Score Calibration / Overall Fit refresh
16. Run Recommendation Engine
17. Verify API + frontend outputs
```

## Current vs Planned Outputs

| Output | Current state | Owner |
|---|---|---|
| `player_archetypes` | Real, accepted for MVP | M1 |
| `team_system_profiles` | Real, accepted for MVP | M2 |
| `player_team_fit_scores.scheme_fit` | Real | M3 |
| `player_team_fit_scores.gap_match` | Real baseline; v2 data dependency now satisfied (`transfers`/`roster_snapshots` populated for 2026) but the model itself (Issue #26) isn't wired yet | Gap Matching |
| `transfers` / `transfer_portal_events` | Real for season 2026 (1,251 promoted); 2020-2026 backfill pending | Issue #17 item 3 |
| `roster_snapshots` / `roster_snapshot_players` | Real, one school verified (Duke); full ~365-school run pending | Issue #17 item 4 |
| `player_team_fit_scores.role_fit` | Placeholder `50.0` | Role Fit |
| `player_team_fit_scores.program_fit` | Placeholder `50.0` | Program Fit |
| `player_team_fit_scores.overall_fit` | Partial; compressed until all components are real | Fit Score Calibration |
| `player_projections` | Planned / table decision pending | Player Projection |
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
| #17 Remaining Data Loading | Removes source-data blockers for all downstream models. Items 1-2 (hoopR game logs/context) and 3-4 (transfers, roster snapshots) done for 2026/one school; full backfills documented but not run. Items 5-8 (derived `player_team_seasons`, roster-state features, projection tables, Program Fit proxy) open. |
| #18 Player Projection Model | Foundational player-talent model |
| #19 Team Rating Projection Model | Roster counterfactual model |
| #20 Program Fit Model Decision | Decides MVP program-fit feasibility and proxy contract |
| #21 Fit Score Calibration | Makes component aggregation useful for ranking |
| #22 Recommendation Engine | Final program-facing ranking layer |
| #23 Transfer Success / Outcome Model | Historical outcome/risk model |
| #24 Standardize Downstream Role And System Labels Around Accepted Clusters | Keeps model/UI language consistent |
| #25 Role Fit / Playing Time Model | Opportunity model and real `role_fit` |
| #26 Departure-Aware Gap Matching v2 | Upgrades gap matching once roster/departure data exists |
| #27 Model Pipeline Orchestration And Runbook | Turns this dependency graph into runnable local process |
| #28 API / Frontend Integration For Real Model Outputs | Replaces stubs with real model outputs in product surfaces |

## Notes For Future Model Work

- Do not treat the models as independent. The product value comes from the chain.
- Player Projection is foundational, not just an API endpoint model.
- Role Fit owns opportunity; Player Projection owns talent.
- Team Rating Projection should consume underlying minutes/displacement, not only the `role_fit` score.
- Transfer Success is useful risk context, but should not block the first real recommendation engine.
- Program Fit should be a deterministic/proxy calculator for MVP unless better data becomes available.
- Score calibration should happen after all four fit components are real.
