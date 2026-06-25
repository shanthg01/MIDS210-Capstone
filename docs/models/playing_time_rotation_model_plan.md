# Playing Time / Rotation Model Plan
## Roster-Aware Opportunity Projection System

**Planned notebook:** `notebooks/models/playing_time_rotation_model.ipynb`  
**Model family:** Roster simulation + constrained minutes model + calibrated uncertainty  
**Primary output table:** `player_team_fit_scores` initially, with a future dedicated `playing_time_projections` table  
**Upstream dependencies:** Live roster ingest, Player Projection system, Gap Matching, Scheme Fit  
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
    -> usage role
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

The first production version should remain efficient and sturdy:

- Use one row per `(player_id, school_id, season)` candidate-destination pair.
- Use precomputed neutral projections, `scheme_fit`, `gap_match`, team-style vectors, and roster-baseline features.
- Prefer transparent tabular models plus deterministic rotation constraints before building a full roster optimizer.
- Keep the output contract rich enough for downstream models even if the first implementation writes only `role_fit` plus JSON breakdowns.

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
minutes_ci_lower
minutes_ci_upper
usage_role
usage_role_confidence
displaced_minutes
opportunity_explanation
model_version
```

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
actual_minutes_per_game
actual_minutes_share
actual_usage_rate
games_played
```

Minutes share is often cleaner than raw minutes:

```text
actual_minutes_share = player_minutes / team_available_minutes
```

Raw minutes are still the product output because coaches think in minutes per game.

### Usage role target

Usage role should be a coarse, interpretable label or distribution, not a fragile exact usage-rate forecast.

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

Usage role can be predicted from projected usage, player skill state, team needs, and destination roster context.

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

---

## 7. Recommended Model Shape

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

### Stage D - Usage role

Predict usage role from candidate skills and destination context:

```text
usage_role = g(
    projected_usage,
    creation_skill,
    shooting_gravity,
    passing_creation,
    turnover_risk,
    team_available_usage,
    current_ball_handlers,
    scheme_fit
)
```

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

## 8. Interactive Scenario Mode

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

## 9. Model Family

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

## 10. Role Fit Score

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

The API can continue storing this in `player_team_fit_scores.role_fit`, while richer opportunity fields move into a future table.

---

## 11. Data Contracts

### Current compatibility

Current table:

```text
player_team_fit_scores
```

Current MVP fields:

| Field | Source |
|---|---|
| `player_id` | Candidate |
| `school_id` | Destination |
| `role_fit` | Derived score from opportunity outputs |
| `model_version` | `playing-time-rotation-v1` |

### Future table

Future table:

```text
playing_time_projections
```

Suggested columns:

```text
player_id
school_id
season
roster_snapshot_id
expected_minutes
minutes_ci_lower
minutes_ci_upper
expected_usage
usage_role
usage_role_confidence
displaced_minutes jsonb
opportunity_drivers jsonb
data_quality_flags jsonb
scenario_overrides jsonb nullable
role_fit
model_version
computed_at
expires_at
```

---

## 12. Notebook Structure

### Cell 0 - Setup

```python
MODEL_VERSION = "playing-time-rotation-v1"
TRAIN_SEASONS = range(2020, 2026)
PROJECTION_SEASON = 2026
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

### Cell 4 - Train Minutes Model

Predict expected minutes or minutes share.

### Cell 5 - Calibrate Uncertainty

Fit prediction intervals with quantile regression, conformal calibration, bootstrap simulation, or Bayesian posterior draws.

### Cell 6 - Predict Usage Role

Train or derive usage role labels from projected usage, role stats, and destination roster context.

### Cell 7 - Constrained Rotation Allocation

Apply roster constraints and produce displaced-minute estimates.

### Cell 8 - Build Role Fit Score

Convert opportunity outputs into the 0-100 `role_fit` component.

### Cell 9 - Build Scenario Adapter

Apply optional coach/dashboard overrides for minutes, usage role, usage rate, and displaced-minute assumptions.

### Cell 10 - Validation

Temporal validation and cohort diagnostics.

### Cell 11 - DB Write

Write `role_fit` to `player_team_fit_scores`; future migration writes full opportunity projections.

---

## 13. Validation Strategy

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

## 14. Open Questions

1. ~~What is the best live roster source and refresh cadence during portal season?~~ — Resolved: barttorvik `rostercast.php` via `ingest_roster_snapshots.py` (Issue #17 item 4). Refresh cadence (daily during portal window, per the original ask) not yet automated — manual `uv run` only so far.
2. Can we reliably distinguish returning, departing, transfer-in, transfer-out, and unknown roster statuses? — Partially: `returning`/`transfer_in`/`new` are computed from a single snapshot (see §4 update above). `departing`/`transfer_out` need day-over-day snapshot diffing — still open, deferred to Issue #17 items 5-6.
3. Should usage role be learned as labels or derived from projected usage/skill thresholds for MVP?
4. Do we need starter probability in the product, or is expected minutes plus usage role enough?
5. How should coach-entered roster overrides affect projections and cache invalidation?
6. How much should a player's stated preference for role/minutes influence the score versus the basketball projection?
7. Which scenario controls should be MVP: minutes, usage role, usage rate, displaced player/group, or all of them?

---

## 15. MVP vs Full Version

### MVP

- Live roster snapshot schema and coverage audit.
- Expected minutes / minutes share model.
- Calibrated uncertainty interval.
- Coarse usage role.
- Simple displaced-minute allocation.
- Scenario override layer for minutes and usage role.
- Derived `role_fit` score written to `player_team_fit_scores`.
- Consumed by Player Projection destination adapter and Team Rating Projection.

### Full version

- Multi-snapshot roster history.
- Coach/team random effects.
- User-editable depth chart overrides.
- Scenario simulation for injuries, late portal additions, and player withdrawals.
- Full `playing_time_projections` table.
- Joint minutes and usage allocation across all candidate roster scenarios.
