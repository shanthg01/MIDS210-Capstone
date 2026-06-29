# Playing Time / Rotation Model Plan
## Roster-Aware Opportunity Projection System

**Planned notebook:** `notebooks/models/playing_time_rotation_model.ipynb`
**Planned script:** `scripts/run_playing_time.py`
**Primary module:** `src/portalpoint/modeling/playing_time.py`
**Model family:** Roster simulation + constrained minutes model + calibrated uncertainty
**Primary output table:** `playing_time_projections`
**Secondary output:** `player_team_fit_scores.role_fit` derived from `playing_time_projections`
**Upstream dependencies:** Live roster ingest, Player Projection system, Gap Matching, Scheme Fit, Player Clustering, Team System Clustering
**Downstream consumers:** Role Fit score, Player Projection destination adapter, Team Rating Projection, Recommendation Engine

---

## 1. Objective

The Playing Time / Rotation model estimates how much opportunity a player would receive at a specific destination.

The core question is:

```text
If this player joins this roster, how many minutes and what kind of offensive role should we expect?
```

The model should not directly optimize the user-facing `role_fit` score. It should estimate basketball opportunity first, then let the product layer translate that opportunity into a fit score.

```text
Playing Time / Rotation model
    -> expected minutes
    -> expected usage
    -> usage role label
    -> displaced minutes
    -> minutes uncertainty

Role Fit score
    -> user-facing 0-100 score derived from the opportunity outputs
```

This distinction matters because the same expected minutes can mean different things depending on player quality and roster need. A projected 18-minute defensive specialist may be a great Role Fit for one program and a mediocre Role Fit for another.

---

## 2. How This Fits With the Other Models

The projection system should operate as a connected stack:

```text
Player Projection
    -> neutral player talent, skill rates, offensive/defensive impact, uncertainty

Gap Matching
    -> roster needs by position, skill, and archetype

Scheme Fit
    -> style compatibility between player and team

Live Roster State
    -> returning players, departures, transfers in, open minutes, class mix

Playing Time / Rotation Model
    -> expected minutes, usage role, displaced minutes, uncertainty

Role Fit Score
    -> product-facing 0-100 opportunity score

Destination-Adjusted Player Projection
    -> per-game stats and destination-adjusted value

Team Rating Projection
    -> roster-level offensive/defensive/net rating impact
```

The Player Projection model owns talent. The Playing Time / Rotation model owns opportunity. The Team Rating Projection model combines talent and opportunity into roster impact.

### Target-season contract

The Playing Time / Rotation model consumes the neutral Player Projection row for the same
target season it is scoring:

```text
neutral player projection for player p, season n
    + destination school/team context for school s, season n
    + roster baseline / snapshot for school s, season n
    -> expected opportunity for player p at school s in season n
```

This model is intentionally destination-aware. It should use team context because minutes and
usage are roster- and staff-dependent outcomes, not generic player traits. The neutral projection
answers "how good is the player?"; Playing Time answers "how much of that player will this roster
actually use, and in what role?"

### Live portal season convention

PortalPoint stores completed CBB stat seasons by season-ending year. For example:

```text
player_season_stats.season = 2026
    -> completed 2025-26 player/team stats

roster_snapshots.season = 2026
transfer_portal_events.season = 2026
    -> June 2026 roster/portal planning context after the 2025-26 season

playing_time_projections.season = 2027
    -> projected opportunity for the next playing season, 2026-27
```

The production live run should score `target_season=2027` while using
`source_season=2026`, `roster_season=2026`, `fit_context_season=2026`, and
`team_context_season=2026`. The output row's `season` is always the projected playing season.
The context seasons should be recorded in `opportunity_drivers` so downstream destination-adjusted
player projections can trace which source stats, roster snapshot, team context, and pairwise fit
context fed the row.

The first production version should remain efficient and sturdy:

- Use one row per `(player_id, school_id, season)` candidate-destination pair.
- Use precomputed neutral projections, `scheme_fit`, `gap_match`, team-style vectors, and roster-baseline features.
- Prefer transparent tabular models plus deterministic rotation constraints before building a full roster optimizer.
- Write first-class opportunity rows to `playing_time_projections`; update `player_team_fit_scores.role_fit` as the product score derived from those rows.

The same stack should support interactive scenario analysis. A dashboard can let a coach adjust minutes, usage role, or displaced minutes without refitting the talent model:

```text
Precomputed player talent projection
    -> coach adjusts minutes / usage role / displacement assumptions
    -> scenario-specific opportunity output
    -> updated player projection and team projection
```

---

## 3. Core Outputs

### Required outputs

These should be the first-class model outputs:

```text
player_id
school_id
season
expected_minutes
expected_minutes_share
minutes_ci_lower
minutes_ci_upper
expected_usage
usage_role
usage_role_confidence
displaced_minutes
opportunity_drivers
data_quality_flags
role_fit
model_version
```

`expected_minutes`, `expected_minutes_share`, and `expected_usage` are the hard quantitative
outputs. `usage_role` is an interpretability and downstream-adapter label derived from those
quantities plus archetype/system context; it should not replace the numeric usage/minutes outputs.

### Optional derived outputs

Starter probability and rotation slot can be useful, but they should not be required for MVP.

```text
starter_probability = P(minutes >= starter_threshold)
rotation_slot = inferred label from expected minutes + position + usage role
```

Expected minutes and usage role already carry most of the basketball signal. For example:

- `28 mpg + primary_creator` implies a starter-level offensive role.
- `18 mpg + low_usage_defender` implies a rotation specialist.
- `10 mpg + backup_big` implies depth minutes.

If the UI needs a simpler label, derive it from the distribution instead of making it the main prediction target.

---

## 4. Live Roster Dependency

This model is only as good as the roster state it sees.

**Update (2026-06-21, Issue #17 items 3-4):** Live roster snapshots and transfer events are now real, not sample/historical-only. `scripts/ingest_roster_snapshots.py` scrapes barttorvik's `rostercast.php` per school (one school verified; full ~365-school run documented but not yet run) into `roster_snapshots`/`roster_snapshot_players`, with `returning_status` (`returning`/`transfer_in`/`new`) computed by diffing against `player_season_stats` — `departing`/`transfer_out` are intentionally not yet derivable from a single snapshot (needs day-over-day snapshot diffing, still open). `scripts/ingest_transfers_247sports.py` populates `transfers`/`transfer_portal_events` (season 2026 done; 2020-2026 backfill documented but not yet run) — see below, this supersedes the BartTorvik-as-primary-source assumption in §7.

### Required roster fields

At minimum, each roster snapshot should include:

```text
school_id
season
snapshot_date
player_id
player_name
year_class
position
origin
height
returning_status          # returning, departing, transfer_in, transfer_out, unknown
transfer_source_school
transfer_destination_school
scholarship_status nullable
eligibility_remaining nullable
injury_status nullable
```

The screenshot-style roster fields are useful and should be standardized:

```text
roster.year_class
roster.pos
roster.origin
tier
transfer_src
transfer_dest
team/opponent strength fields
```

The roster table should support multiple snapshots because portal rosters change daily. A candidate projection should always record the roster snapshot it was computed against.

```text
roster_snapshot_id
computed_at
```

### Portal transfer source

BartTorvik's transfer portal pages should be treated as useful current and historical transfer sources. The page exposes transfer rows equivalent to:

```text
player_name
transfer_source_school
transfer_destination_school nullable
status_flag
```

Use this feed to identify:

- Current portal candidates.
- Source schools losing players.
- Committed destinations where available.
- Uncommitted players where destination is null.
- Historical transfer source/destination pairs for past seasons.

Because this source is name/school based, the ingest should resolve rows to PortalPoint IDs with a confidence score:

```text
player_name + transfer_source_school + transfer_season
    -> player_id / barttorvik_id
```

High-confidence matches can update roster snapshots automatically. Ambiguous matches should be flagged for review or excluded from model training.

**Superseded (2026-06-21):** BartTorvik's transfer-event JSON (`{season}_transfer_stats.json`) would have been the cleanest source — its `player_id` field matches `players.barttorvik_id` exactly, no fuzzy matching needed — but barttorvik's `robots.txt` disallows `/*.json` and `/playerstat.php` (the only two real transfer pages on that domain), and explicitly disallows `ClaudeBot`/`anthropic-ai` site-wide. **247Sports' transfer-portal pages are the actual primary transfer-event source** (`scripts/ingest_transfers_247sports.py`, not robots.txt-disallowed) — player resolution there is fuzzy name+roster matching against `player_season_stats` (~83% match rate verified for season 2026), not a clean ID join, so the inferred player-team-season backfill/validation layer below is still valuable as a cross-check, not just a fallback for missing destinations.

### Roster state features

For each school-season snapshot, build:

- Returning minutes by position.
- Departing minutes by position.
- Known transfer-in minutes and projected impact.
- Open minutes by position band.
- Skill gaps by role: creator, shooter, rim protector, rebounder, defensive playmaker.
- Class balance and eligibility risk.
- Existing usage distribution.
- Historical coach/team rotation tendencies where available.
- Conference tier and team quality tier.

---

## 5. Modeling Target

### Primary targets

Train on historical player-school-season outcomes:

```text
actual_minutes_share
derived_actual_minutes_per_game
actual_usage_rate
games_played
```

Minutes share is the canonical target. Use `player_season_stats.min_pct` as the
source of truth; older DBs may have legacy/mis-mapped
`player_season_stats.minutes_per_game` values even though the current ingest
derives MPG from `min_pct`:

```text
actual_minutes_share = player_season_stats.min_pct / 100
derived_actual_minutes_per_game = player_season_stats.min_pct * 0.4
```

Derived raw minutes are still the product output because coaches think in
minutes per game. If exact player-game total minutes are needed later, prefer
`hoopr_player_game_logs.minutes` aggregates over trusting legacy stored MPG.

### Usage role label

Usage role is a coarse, interpretable label on top of projected minutes and usage. It is useful for
explanation, downstream destination-adjusted stat translation, and sanity checks, but it is not the
main supervised target. The main numeric targets remain `actual_minutes_share` and
`actual_usage_rate`.

Suggested labels:

| Usage role | Meaning |
|---|---|
| `primary_creator` | High usage, high on-ball creation burden |
| `secondary_creator` | Meaningful handling/creation without carrying the offense |
| `connector` | Moderate usage, passing/spacing/decision-making role |
| `play_finisher` | Low-to-moderate usage, finishes advantages |
| `spacing_specialist` | Low usage, shooting gravity |
| `rim_runner_rebounder` | Big role centered on rim finishing/rebounding |
| `defensive_specialist` | Value driven mostly by defense |
| `depth` | Limited or uncertain offensive role |

MVP usage-role assignment should be deterministic and archetype-informed:

```text
base_role_prior = mapping(player_archetypes.archetype_label / memberships)
role_adjustments = projected usage + expected minutes + skill percentiles
                 + destination roster needs + team_system_profiles.system_label / memberships
usage_role = highest scoring role
usage_role_confidence = margin between the top two roles, adjusted for data quality
```

Examples:

- `Lead Scoring Playmaker` starts with a high prior for `primary_creator` or `secondary_creator`.
- `Two-Way Spacing Wing` starts with a high prior for `spacing_specialist` or `connector`.
- `Pressure Connector Guard` starts with a high prior for `connector` or defensive guard roles.
- `Post Scoring Big` and `Interior Star Big` split between high-usage big, rim runner, rebounder,
  and defensive anchor roles based on projected usage, minutes, and destination frontcourt need.

Team clustering should adjust the label and confidence, not overwrite the player archetype:

- `3PT Spacing Offense` improves confidence for spacing wings and stretch forwards.
- `Rim Pressure Offense` improves confidence for rim finishers, screeners, and interior bigs.
- `Perimeter Creation Offense` increases the value of on-ball creators and passing guards.
- `Transition Attack` can lift transition guards/wings and widen intervals for poor-fit half-court players.

A learned usage-role classifier is a future challenger. It should only become production if it beats
the deterministic/archetype-informed labeler on role sanity, calibration, and downstream validation.

---

## 6. Feature Design

### Candidate features

- Neutral player projected impact per 100 for the target season.
- Offensive and defensive impact split.
- Projected skill states, percentiles, usage texture, and box-score rates.
- Position and archetype.
- Class year and eligibility.
- Prior minutes and games played.
- Transfer direction: up-transfer, lateral, down-transfer.
- Conference tier change.
- Uncertainty in player projection.

### Destination roster features

- Open minutes at candidate's position band.
- Returning player strength at same/similar roles.
- Departing production and usage.
- Gap Match components.
- Scheme Fit components.
- Team quality tier.
- Conference tier.
- Team system/style cluster.
- Team pace and shot profile.
- Team play-style frequencies where Hoop Explorer coverage exists.
- Coach/team historical rotation size if available.

### Interaction features

- Candidate impact minus likely displaced player impact.
- Candidate usage vs available usage.
- Candidate role scarcity on roster.
- Position-band crowding.
- Defensive role need: rim protection, rebounding, point-of-attack defense.
- Offensive role need: creation, spacing, rim pressure.
- Player skill × team style interactions:
  - shooting skill × team 3PA rate / spacing need
  - passing creation × open ball-handler minutes
  - shot creation usage × available usage
  - rim finishing / offensive rebounding × rim-pressure style
  - rim protection / defensive rebounding × frontcourt defensive need

These interaction features are the bridge to destination-adjusted projection. Playing Time uses
them to estimate role and opportunity; the destination adapter later uses the resulting role/minutes
to translate neutral rates into school-specific production.

### Concrete data sources

| Feature group | Source table/artifact | Key fields / metrics |
|---|---|---|
| Neutral talent | `player_projections` | Production neutral rows where `model_version="player-proj-phase2a-fcast-v1"` and `school_id IS NULL`; use `value_per_100`, CI bounds, `skill_states`, `skill_percentiles`, `projected_rates`, `projected_box_score`, `uncertainty` |
| Prior role / historical minutes | `player_season_stats` | `school_id`, `season`, `games_played`, `min_pct`, derived MPG (`min_pct * 0.4`), `usage_rate`, `position`, shooting/rebounding/assist/turnover rates. Do not use legacy `minutes_per_game` as true MPG. |
| Historical actual playing-time labels | `player_season_stats.min_pct`; optional `hoopr_player_game_logs.minutes` aggregate | Canonical label: `actual_minutes_share = min_pct / 100`; display MPG: `min_pct * 0.4`; derive minutes buckets and starter/rotation flags from derived MPG |
| RAPM / soft position / HE style | `hoop_explorer_player_stats` | `off_adj_rapm`, `def_adj_rapm`, `pos_confidence_*`, `off_style_*_pct`, `transfer_dest` |
| Archetype / player role texture | `player_archetypes` | `archetype_label`, `confidence`, membership JSON where available |
| Destination quality / pace | `team_season_stats` | `adj_em`, `adj_o`, `adj_d`, `adj_tempo`, shot profile/style fields where populated |
| Team systems / style clusters | `team_system_profiles`; `data/features/team_style_vectors.parquet` | offense/defense labels, memberships, style vectors, pace/shot/play-style dimensions |
| Scheme and gap context | `player_team_fit_scores` | `scheme_fit`, `gap_match`, breakdown JSON, `is_portal_candidate`, `is_roster_baseline_member` |
| Program Fit (not a feature) | `player_team_fit_scores.program_fit` | Carried forward unchanged into the sync step's `overall_fit` recompute only (see §12 Fit-score compatibility) — never selected as a Playing Time training/inference feature. Distinct from `scheme_fit`/`gap_match` above, which *are* pulled in as feature candidates and then deliberately excluded from `NUMERIC_FEATURES` (`PLAYING_TIME_EXCLUDED_FEATURES`) to avoid circularity with the composite score. Will automatically reflect real values once Program Fit (#25) ships, with no Playing Time code change needed. |
| Current roster state | `roster_snapshots`, `roster_snapshot_players`, `roster_state_features` | roster membership, returning status, transfer-in/new flags, snapshot date, open/departing/returning minutes and usage by position |
| Transfer context | `transfers`, `transfer_portal_events` | `from_school_id`, `to_school_id`, `season`, `status`, `pre_usage_rate`, `post_usage_rate`; use transfer MPG fields only after they are derived from `player_season_stats.min_pct` or hoopR minutes |

Do not use `player_school_seasons` as a required dependency for this model; it is empty in the
current local stack. Use `player_season_stats`, transfer events, and roster snapshots for
player-team-season membership.

---

## 7. Training and Inference Sets

### Training set

The supervised training set should contain observed player-school-season outcomes only:

```text
row grain:
    player_id, actual_school_id, target_season

label source:
    player_season_stats for player_id at actual_school_id in target_season

labels:
    actual_minutes_share = min_pct / 100
    actual_minutes_per_game = min_pct * 0.4
    actual_usage_rate = usage_rate
    games_played
```

Feature construction must respect time:

```text
target season n label
    uses features known before or at the roster-planning point for season n
```

For returning players, candidate history generally comes from season `n-1` at the same school. For
transfers, candidate history comes from the source school in season `n-1` or the latest available
observed season. Neutral Player Projection input should be the production neutral projection for
target season `n`, not the realized target-season RAPM.

Historical destination context should be built as if the school were evaluating the player before
season `n`:

- neutral projection for player `p`, season `n`,
- source-season player stats and archetype,
- destination `team_system_profiles` for school `s`, season `n` or most recent prior system label,
- `scheme_fit` / `gap_match` for `(p, s, n)` where available,
- roster baseline/snapshot features for school `s`, season `n`,
- returning/open minutes and usage features that do not leak the player's realized target-season role.

Do not create negative training examples by treating unchosen schools as zero-minute outcomes. Those
counterfactuals are unobserved, not observed benchings. Historical validation can evaluate ranking
for actual destinations and compare plausible alternatives, but the supervised minutes/usage labels
come only from the school where the player actually played.

Recommended training seasons:

```text
train:      2021-2024 target seasons where source-season features exist
validation: 2025
test:       2026, when enough completed actuals are available
```

Adjust the split based on the completed seasons in the local DB. Keep the split temporal.

### Inference set

Production inference scores candidate-destination pairs:

```text
row grain:
    player_id, school_id, target_season
```

Candidate pool:

- current portal candidates from `transfer_portal_events` / `transfers`,
- current roster baseline members who may return,
- optional coach-selected watchlist players,
- no projection for players with no usable neutral Player Projection or no usable historical/source profile.

Destination pool:

- every active D1 school with a usable roster snapshot or roster baseline for the target season,
- current school rows for returners,
- committed destination rows for known commits,
- all-pairs rows where `scheme_fit` / `gap_match` already exist for broad recommendation workflows.

Inference output:

```text
playing_time_projections:
    expected_minutes
    expected_minutes_share
    minutes interval
    expected_usage
    usage_role
    usage_role_confidence
    displaced_minutes
    role_fit
    data_quality_flags
```

The script should also update `player_team_fit_scores.role_fit` and `overall_fit` for matching
candidate-destination rows, preserving existing `scheme_fit`, `gap_match`, and `program_fit`.

---

## 8. Recommended Model Shape

MVP should be interpretable and constrained.

### Stage A - Baseline rotation state

Build the roster before adding the candidate:

```text
returning players
- known departures
+ known transfer-ins
+ average replacement assumptions for unresolved openings
```

Estimate baseline minutes by position band, ensuring:

```text
sum(team_minutes) = 200 per game
minutes >= 0
position coverage remains plausible
```

### Stage B - Candidate insertion

Insert the candidate and estimate how much opportunity they earn:

```text
candidate_minutes = f(
    candidate_talent,
    prior_minutes,
    open_minutes,
    roster_depth,
    gap_match,
    scheme_fit,
    team_tier,
    conference_transition
)

candidate_usage = h(
    source_usage,
    projected skill states,
    expected minutes,
    available destination usage,
    returning creator quality,
    team system profile,
    conference_transition
)
```

For MVP, this can be a regularized tabular model or gradient-boosted model trained on historical
player-school-season outcomes, followed by simple roster constraints. Do not start with a slow
simulation over every possible lineup unless the simpler constrained model fails validation.

### Stage C - Displacement

Allocate lost minutes to the most plausible displaced sources:

```text
displaced_minutes
    = replacement_slot_minutes
    + same_position_depth_minutes
    + flexible_bench_minutes
```

This displacement is essential for team projection. A 24-minute candidate is not adding 24 new minutes to the roster; he is replacing someone else's minutes.

### Stage D - Usage role label

Derive usage role from the numeric opportunity outputs plus archetype/system context:

```text
usage_role = g(
    player_archetype_label,
    archetype_memberships,
    expected_usage,
    expected_minutes,
    neutral skill percentiles,
    team_system_profiles.system_label,
    team_system_profiles.system_memberships,
    roster need and crowding
)
```

The role label should explain the opportunity estimate. It should never be the only input to
downstream models when numeric `expected_usage` and `expected_minutes` are available.

Usage role should also be coach-adjustable in scenario mode. This directly supports questions like:

```text
How effective is this player as a first option?
How effective is he if scaled down to a second or third option?
Does the rest of the roster improve if his usage drops and teammates keep more creation?
```

For MVP, the model can provide a recommended usage role while the UI allows an override. The override should be stored as scenario metadata rather than replacing the model's base projection.

### Stage E - Uncertainty

Return a minutes distribution, not just a point estimate.

Key uncertainty sources:

- Incomplete or stale roster data.
- Unknown eligibility / injury / scholarship status.
- Player projection uncertainty.
- Transfer level change.
- Crowded position groups.
- New coach or major system change.

---

## 9. Interactive Scenario Mode

The model should support a dashboard where coaches can test role assumptions with sliders or controls.

### Adjustable inputs

```text
minutes_override
usage_role_override
usage_rate_override
displaced_player_or_group_override
replacement_baseline_override
```

### Scenario output

```text
base_expected_minutes
scenario_expected_minutes
base_usage_role
scenario_usage_role
base_displaced_minutes
scenario_displaced_minutes
scenario_confidence
```

This turns the model from a single answer into a roster planning tool. The base model gives the recommended projection, and the scenario layer answers "what if we use him differently?"

Important implementation rule:

```text
Base projection != scenario override
```

The base projection should remain model-driven. Scenario overrides should be stored separately so the dashboard can compare:

```text
model recommendation
vs.
coach-adjusted assumption
```

---

## 10. Model Family

Recommended first implementation:

```text
calibrated hierarchical regression + constrained roster allocation
```

This can be implemented with:

- Ridge/elastic-net or gradient-boosted regression for candidate minutes.
- Quantile regression or conformal intervals for uncertainty.
- Team/coach/conference random effects if enough history exists.
- A constrained post-processing step that keeps roster minutes plausible.

A full Bayesian hierarchical model is still attractive, but it should not block MVP. The MVP can approximate posterior uncertainty with calibrated prediction intervals and scenario simulation.

### Why not model only minutes directly?

Raw expected minutes alone misses the roster mechanics. The model needs to know what those minutes replace.

```text
candidate: 24 mpg
displaced: 14 mpg replacement slot + 10 mpg returning bench guard
```

That is more actionable than:

```text
candidate: 24 mpg
```

---

## 11. Role Fit Score

Role Fit should be a derived product score.

Example scoring components:

```text
role_fit
= expected_minutes_score
 + role_need_score
 + usage_role_match_score
 - uncertainty_penalty
 - roster_crowding_penalty
```

Possible interpretation:

| Role Fit range | Meaning |
|---|---|
| 85-100 | Clear path to major role |
| 70-84 | Strong rotation opportunity |
| 50-69 | Plausible role but competition exists |
| 30-49 | Crowded or uncertain path |
| 0-29 | Limited opportunity without roster changes |

The API can continue reading `player_team_fit_scores.role_fit` for fit-score responses, but the
source of truth should be the matching `playing_time_projections` row.

---

## 12. Data Contracts

### Primary table

Add and write a dedicated table:

```text
playing_time_projections
```

Required columns:

```text
id
player_id
school_id
season
roster_snapshot_id nullable
expected_minutes
expected_minutes_share
minutes_ci_lower
minutes_ci_upper
expected_usage
usage_role
usage_role_confidence
starter_probability nullable
rotation_probability nullable
displaced_minutes jsonb
opportunity_drivers jsonb
data_quality_flags jsonb
scenario_overrides jsonb nullable
role_fit
model_version
computed_at
expires_at
```

Recommended uniqueness:

```sql
UNIQUE (player_id, school_id, season, model_version)
```

`opportunity_drivers` should carry the traceable downstream contract for destination-adjusted
player projections:

```text
target_season
source_stat_season
roster_context_season
fit_context_season
team_context_season
neutral_projection_model_version
roster_open_minutes
returning_minutes_position
same_position_prior_minutes
position_crowding_ratio
opportunity_to_prior_minutes_ratio
prior_minutes
rotation_probability_model
starter_probability_model
heavy_minutes_probability
high_usage_probability
candidate_changes_school
is_portal_candidate
```

### Fit-score compatibility

Also update the existing fit-score table:

```text
player_team_fit_scores
```

Fields to update:

| Field | Source |
|---|---|
| `player_id` | Candidate |
| `school_id` | Destination |
| `role_fit` | Derived from `playing_time_projections.role_fit` |
| `overall_fit` | Recomputed using existing component weights |
| `breakdown.role_fit` | Summary JSON copied from `playing_time_projections` |
| `model_version` | `playing-time-rotation-v2` |

For next-season live scoring, `player_team_fit_scores` may not already have rows for the target
season. The sync step should upsert target-season fit rows by copying `scheme_fit`, `gap_match`,
`program_fit`, weights, `breakdown`, and `is_portal_candidate` from the fit context season, then
replace only the Role Fit component with the current Playing Time output. This keeps all-pairs
scoring available while preserving the upstream component model ownership boundaries.

---

## 13. Script Contract

Create:

```text
src/portalpoint/modeling/playing_time.py
scripts/run_playing_time.py
```

Public functions in `playing_time.py` should be pure and testable:

```python
build_training_examples(...)
build_inference_pairs(...)
build_roster_context(...)
fit_minutes_usage_models(...)
predict_minutes_usage(...)
calibrate_minutes_intervals(...)
derive_usage_role(...)
allocate_displaced_minutes(...)
compute_role_fit_score(...)
build_playing_time_records(...)
upsert_playing_time_projections(...)
sync_role_fit_scores(...)
```

`scripts/run_playing_time.py` should:

1. Load training examples at observed player-school-season grain.
2. Resolve the season contract (`target_season`, `source_season`, `roster_season`,
   `fit_context_season`, `team_context_season`).
3. Fit or load the minutes-share and usage models using only completed seasons through the source
   season.
4. Calibrate minutes intervals on temporal holdout seasons.
5. Build the production inference grid for the target season from fit-context all-pairs rows.
6. Score expected minutes, minutes share, usage, intervals, and data-quality flags.
7. Derive usage role from player archetype, skill state, team system, expected usage, and roster need.
8. Allocate displaced minutes with roster constraints.
9. Compute `role_fit`.
10. Upsert `playing_time_projections`.
11. Sync/upsert `player_team_fit_scores.role_fit`, `overall_fit`, and `breakdown.role_fit`.
12. Log MLflow metrics, feature config, coverage counts, interval widths, and score distributions.

---

## 14. Notebook Structure

### Cell 0 - Setup

```python
MODEL_VERSION = "playing-time-rotation-v2"
TRAIN_SEASONS = range(2021, 2027)
SOURCE_SEASON = 2026
ROSTER_SEASON = 2026
FIT_CONTEXT_SEASON = 2026
PROJECTION_SEASON = 2027
```

### Cell 1 - Roster Coverage Audit

Audit live and historical roster coverage, transfer source/destination fields, returning/departing status, and player ID joins.

### Cell 2 - Build Roster Snapshots

Construct historical roster states from pre-season-available information only.

### Cell 3 - Build Candidate / Destination Examples

Create player-school-season examples with:

```text
candidate features
destination roster features
gap/scheme features
actual next-season minutes and usage labels
```

### Cell 4 - Train Minutes / Usage Models

Predict expected minutes share and expected usage.

### Cell 5 - Calibrate Uncertainty

Fit prediction intervals with quantile regression, conformal calibration, bootstrap simulation, or Bayesian posterior draws.

### Cell 6 - Derive Usage Role

Derive usage role from player archetype/memberships, projected skills, expected usage, expected
minutes, team system labels/memberships, and destination roster context.

### Cell 7 - Constrained Rotation Allocation

Apply roster constraints and produce displaced-minute estimates.

### Cell 8 - Build Role Fit Score

Convert opportunity outputs into the 0-100 `role_fit` component.

### Cell 9 - Build Scenario Adapter

Apply optional coach/dashboard overrides for minutes, usage role, usage rate, and displaced-minute assumptions.

### Cell 10 - Validation

Temporal validation and cohort diagnostics.

### Cell 11 - DB Write

Write `playing_time_projections`, then sync `role_fit` to `player_team_fit_scores`.

---

## 15. Validation Strategy

### Metrics

| Metric | Use |
|---|---|
| Minutes RMSE / MAE | Main point-estimate accuracy |
| Minutes share RMSE | Normalized opportunity accuracy |
| Interval coverage | Whether 80% intervals contain actual minutes |
| Usage role accuracy | Whether role labels are useful |
| Rank correlation | Whether the model ranks better opportunities correctly |
| Calibration by bucket | Whether 20 mpg projections behave like 20 mpg players |

### Required slices

- Up-transfers.
- Down-transfers.
- High-major to high-major transfers.
- Freshmen/sophomores vs seniors.
- Guards, wings, forwards, bigs.
- Teams with high roster churn.
- Teams with incomplete live roster status.

---

## 16. Future Decisions

1. ~~What is the best live roster source and refresh cadence during portal season?~~ — Resolved: barttorvik `rostercast.php` via `ingest_roster_snapshots.py` (Issue #17 item 4). Refresh cadence (daily during portal window, per the original ask) not yet automated — manual `uv run` only so far.
2. Can we reliably distinguish returning, departing, transfer-in, transfer-out, and unknown roster statuses? — Partially: `returning`/`transfer_in`/`new` are computed from a single snapshot (see §4 update above). `departing`/`transfer_out` need day-over-day snapshot diffing — still open, deferred to Issue #17 items 5-6.
3. Should a learned usage-role classifier replace the deterministic/archetype-informed MVP labeler after enough validation data exists?
4. Do we need starter probability in the product, or is expected minutes plus usage role enough?
5. How should coach-entered roster overrides affect projections and cache invalidation?
6. How much should a player's stated preference for role/minutes influence the score versus the basketball projection?
7. Which scenario controls should be MVP: minutes, usage role, usage rate, displaced player/group, or all of them?

---

## 17. MVP vs Full Version

### MVP

- Live roster snapshot schema and coverage audit.
- `playing_time_projections` table and upsert path.
- Expected minutes / minutes share model.
- Expected usage model.
- Calibrated uncertainty interval.
- Deterministic, archetype-informed usage role label.
- Simple displaced-minute allocation.
- Scenario override layer for minutes and usage role.
- Derived `role_fit` score synced to `player_team_fit_scores`.
- Consumed by Player Projection destination adapter and Team Rating Projection.

### Full version

- Multi-snapshot roster history.
- Coach/team random effects.
- User-editable depth chart overrides.
- Scenario simulation for injuries, late portal additions, and player withdrawals.
- Learned usage-role classifier if it beats the deterministic labeler.
- Joint minutes and usage allocation across all candidate roster scenarios.
