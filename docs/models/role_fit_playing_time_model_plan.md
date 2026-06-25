# Role Fit / Playing Time Model Plan
## Roster-Aware Opportunity Projection System

**Planned notebook:** `notebooks/models/playing_time_rotation_model.ipynb`  
**Planned script:** `scripts/run_playing_time.py`  
**Primary module:** `src/portalpoint/modeling/playing_time.py`  
**Primary MVP output:** `player_team_fit_scores.role_fit`  
**Future rich output:** `playing_time_projections`  
**Model family:** roster-state simulation + minutes-share model + constrained rotation allocation + calibrated uncertainty  
**Hard dependencies:** transfers, roster snapshots, player-team season history, roster-state features  
**Soft dependencies:** Player Projection, Gap Matching, Scheme Fit, player archetypes, team system profiles  
**Downstream consumers:** Fit Score, Team Rating Projection, Player Projection destination adapter, Recommendation Engine, Compare page

---

## 1. Product Objective

Role Fit answers one coach-facing question:

```text
If this player joins our roster, is there a realistic path to the right minutes and role?
```

The model should separate the basketball estimate from the product score:

```text
Playing Time / Rotation engine
    -> expected minutes
    -> minutes interval
    -> usage role
    -> displaced minutes
    -> roster crowding
    -> data-quality flags

Role Fit score
    -> 0-100 product component derived from those opportunity outputs
```

This distinction is important. A projected 18-minute defensive specialist can be an excellent Role Fit for a team that needs a defensive bench wing, while a projected 18-minute high-usage scorer may be a poor Role Fit if the roster already has several creators.

The first implementation should not pretend to solve player talent. Player Projection owns neutral talent and skill forecasts. Role Fit owns opportunity, roster context, and how a player's likely role maps onto a specific school.

### Target-season contract

Role Fit / Playing Time consumes the neutral Player Projection row for the same target
season it is scoring:

```text
neutral player projection for player p, season n
    + destination roster/team context for school s, season n
    -> expected minutes, usage role, displacement, and uncertainty for p at s in n
```

This model should include team context. Minutes and usage are not destination-neutral
player traits; they depend on open minutes, returning talent, team style, roster needs,
and source/destination level. The destination-adjusted Player Projection then consumes
these opportunity outputs to translate neutral talent into school-specific production.

---

## 2. Model Type Decision

Role Fit should be a calibrated supervised tabular model plus roster constraints. It should not start as a deep-learning model, pure recommender model, or clustering model.

Recommended model type:

```text
two-head supervised tabular model
    + minutes-share regression head
    + usage-role multinomial classification head
    + constrained roster allocation
    + deterministic Role Fit scoring layer
```

The supervised layer predicts two connected outputs:

```text
Head A: minutes opportunity
    target = actual_minutes_share
    outputs = expected_minutes, minutes interval

Head B: role / usage context
    target = usage_role
    output = multinomial probabilities over usage-role classes
```

The roster layer makes the prediction basketball-plausible:

```text
team minutes sum to 200
position coverage remains plausible
displaced minutes cannot be negative
candidate minutes come from open, replacement, or displaced roster minutes
```

The product layer turns the opportunity estimate into the user-facing component:

```text
role_fit = 0-100 score derived from minutes, role need, displacement quality, crowding, and confidence
```

Start with a transparent regularized baseline and a nonlinear tabular challenger:

| Layer | MVP choice | Why |
|---|---|---|
| Baseline minutes model | Ridge or Elastic Net | Interpretable, stable, hard to overfit |
| Nonlinear challenger | HistGradientBoostingRegressor | Captures interactions among open minutes, position, tier, and roster crowding |
| Usage-role classifier | Multinomial logistic regression or gradient-boosted classifier | Produces role probabilities instead of brittle hard labels |
| Uncertainty | Quantile regression, then conformal calibration | Produces calibrated minutes intervals |
| Roster allocation | Deterministic constrained allocator | Keeps outputs plausible and explainable |
| Score layer | Weighted deterministic formula | Makes `role_fit` auditable in the API/UI |

This shape keeps the model contemporary without making the MVP fragile. A richer hierarchical/Bayesian version can come later if validation shows the extra complexity improves minutes calibration. A future version can also replace the separate heads with a joint role-minute model:

```text
P(minutes_bucket, usage_role | player, roster, school)
```

That joint multinomial approach may better capture that "starter minutes as a primary creator" and "starter minutes as a defensive specialist" are different roster outcomes. Keep it as a v2 candidate unless separate minutes and role heads produce inconsistent outputs.

---

## 3. Current Repo Reality

The local stack already has enough to design the contract, but not enough to run the real model:

| Data / output | Current state | Role Fit implication |
|---|---|---|
| `player_season_stats` | Populated 2021-2026 | Historical player priors and fallback training labels |
| `team_season_stats` | Populated 2021-2026 | Team tier, tempo, quality context |
| `hoop_explorer_player_stats` | Populated 2021-2026 | Soft position, play style, RAPM-style impact context |
| `hoopr_player_season_stats` | Populated 2021-2026 aggregate PBP | Supplemental player style and coverage flags |
| `player_archetypes` | Populated | Canonical player-role vocabulary |
| `team_system_profiles` | Populated | Destination system context |
| `player_team_fit_scores.scheme_fit` | Real | Helpful context, not a hard dependency |
| `player_team_fit_scores.gap_match` | Real baseline | Helpful context, v2 improves after roster data |
| `transfers` | Empty | Hard blocker for departure-aware opportunity |
| `player_school_seasons` | Empty | Hard blocker unless replaced by derived `player_team_seasons` |
| roster snapshots | Not implemented | Hard blocker for current-roster Role Fit |
| `role_fit` | Flat `50.0` | Placeholder to replace |

Decision: wait for data ingestion before presenting Role Fit as real. It is acceptable to implement a dry-run dataset audit before full modeling, but any baseline before roster snapshots should be labeled `playing-time-baseline-v0` and should not overwrite production-facing `role_fit`.

---

## 4. Research Basis And Feasibility

The recommended framework is deliberately practical: regularized supervised learning for opportunity, constrained post-processing for roster realism, and calibrated intervals for uncertainty.

Public-methods check:

- Basketball lineup and rotation data are sparse because teams use many lineup combinations. Recent L-RAPM work addresses that problem with regression, opponent controls, and informed player priors, which supports using player priors and regularization rather than trying to learn every lineup combination directly: [Lineup Regularized Adjusted Plus-Minus, 2026](https://arxiv.org/abs/2601.15000).
- Quantile regression and gradient boosting can produce prediction intervals; scikit-learn's histogram gradient boosting regressor supports `loss="quantile"` and `quantile` parameters, so this is feasible with the existing Python stack: [HistGradientBoostingRegressor docs](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html), [prediction interval example](https://scikit-learn.org/stable/auto_examples/ensemble/plot_gradient_boosting_quantile.html).
- Conformalized quantile regression is a contemporary uncertainty framework that combines quantile models with finite-sample coverage guarantees without distributional assumptions. This is a good fit for player-minute intervals after temporal validation: [Romano, Patterson, Candes 2019](https://arxiv.org/abs/1905.03222).

Implementation implication:

```text
MVP
    regularized minutes-share model
    + quantile/conformal intervals
    + constrained 200-minute roster allocation
    + interpretable Role Fit formula

Full version
    richer hierarchical/Bayesian model
    + multi-snapshot roster history
    + coach-editable scenario simulation
```

---

## 5. Modeling Question Chain

Role Fit should follow this chain:

```text
Who is the candidate?
  -> What position/role/archetype can he credibly play?
  -> What does the destination roster currently have?
  -> Which minutes are open or weakly held?
  -> What role would the player earn?
  -> Whose minutes would he displace?
  -> How uncertain is the opportunity?
  -> What 0-100 Role Fit score should be written?
```

The model should produce both the underlying opportunity estimate and the score. The score alone is not enough for Team Rating Projection, because Team Rating Projection needs expected minutes and displaced-minute assumptions.

---

## 6. Required Source Data

### Roster Snapshots

Minimum fields:

```text
roster_snapshot_id
school_id
season
snapshot_date
source_name
source_url nullable
created_at
```

Snapshot player rows:

```text
roster_snapshot_player_id
roster_snapshot_id
player_id nullable
raw_player_name
school_id
season
position
height
class_year
origin nullable
returning_status       # returning, departing, transfer_in, transfer_out, freshman_in, juco_in, walk_on, unknown
transfer_source_school_id nullable
transfer_destination_school_id nullable
scholarship_status nullable
eligibility_remaining nullable
injury_status nullable
match_confidence
match_status           # matched, unmatched, ambiguous
```

Roster snapshots must include incoming freshmen/JUCOs/non-transfer additions when available. Otherwise the model will overstate open minutes.

### Transfers

Minimum fields:

```text
player_id nullable
from_school_id nullable
to_school_id nullable
season
portal_entry_date nullable
commitment_date nullable
portal_status          # uncommitted, committed, withdrawn, unknown
transfer_type nullable
raw_player_name
raw_from_school
raw_to_school nullable
source_name
source_url nullable
match_confidence
match_status
```

### Derived Player-Team Season History

Create a derived table or materialized view:

```text
player_team_seasons
```

Minimum fields:

```text
player_id
school_id
season
games_played
minutes_per_game
minutes_share
usage_rate
primary_team_flag
first_game_date nullable
last_game_date nullable
source_confidence
```

This view supports transfer inference, historical roster reconstruction, and training labels.

---

## 7. Feature Contract

### Candidate Features

| Group | Features |
|---|---|
| Identity | position, soft position probabilities, height, class_year, archetype_id |
| Prior role | prior minutes, minutes share, games played, prior usage |
| Talent | player projection outputs when available; fallback to BPM/RAPM-style fields |
| Skill texture | neutral projected skills/rates, shooting profile, assist rate, usage, rebounding, blocks, steals, HE play-style frequencies |
| Transfer context | source conference tier, destination conference tier, up/lateral/down transfer flag |
| Data quality | player projection confidence, HE/hoopR availability, match confidence |

### Destination Roster Features

| Group | Features |
|---|---|
| Open minutes | departing minutes by position and archetype; returning minutes by position |
| Crowding | returning players above candidate at same position/role; soft-position depth |
| Need | gap_match, archetype deficit, role scarcity, skill deficits |
| System | scheme_fit, team offense label, team defense label, pace, shot profile |
| Program tier | AdjEM tier, conference tier, recent wins, rotation size |
| Volatility | roster snapshot age, unknown-status players, unmatched roster rows |

### Interaction Features

These are the most important Role Fit features:

```text
candidate_minutes_prior - same_position_returning_minutes
candidate_projected_impact - projected_displaced_impact
candidate_usage - available_usage
candidate_archetype in destination_archetype_deficits
candidate_soft_position dot open_minutes_by_position
scheme_fit * role_need_score
gap_match * candidate_position_confidence
shooting_skill * team_3pa_rate_or_spacing_need
passing_creation * open_ball_handler_minutes
shot_creation_usage * available_usage
rim_protection * frontcourt_defensive_need
rebounding_skill * destination_rebounding_gap
```

These interactions should remain opportunity features here. They help predict whether a player
earns minutes or a usage role at a school. The destination-adjusted Player Projection owns the
next step: converting those minutes/roles into school-specific stats and value.

---

## 8. Training Targets

Primary target:

```text
actual_minutes_share
```

Secondary targets:

```text
actual_minutes_per_game
actual_usage_rate
usage_role
starter_flag
rotation_flag
```

Minutes share is cleaner than raw minutes because team pace, overtime, injuries, and game availability vary. Raw minutes remain the coach-facing output.

Suggested derived labels:

```text
actual_minutes_share = player_total_minutes / team_total_available_minutes
starter_flag = actual_minutes_per_game >= 24
rotation_flag = actual_minutes_per_game >= 10
deep_bench_flag = actual_minutes_per_game < 10
```

Temporal rule: when training historical examples, only use roster/player information that would have been knowable before or early in that season. Do not leak end-of-season role outcomes into pre-season roster features.

---

## 9. Usage Role Vocabulary

Use a small usage-role vocabulary that does not conflict with M1 player archetypes.

| Usage role | Definition |
|---|---|
| `primary_creator` | High usage and high on-ball creation burden |
| `secondary_creator` | Meaningful creation without carrying the offense |
| `connector` | Moderate usage; passing, spacing, and decisions |
| `spacing_specialist` | Low-to-medium usage, shooting gravity |
| `play_finisher` | Finishes advantages without heavy creation |
| `rim_runner_rebounder` | Big role centered on rim finishing/rebounding |
| `defensive_specialist` | Value driven mostly by defense and low usage |
| `depth` | Limited, uncertain, or matchup-only role |

Usage role should be a multinomial classification target when historical labels can be derived cleanly. MVP can also keep a deterministic threshold-derived fallback for thin or noisy labels.

Suggested derived labels:

```text
primary_creator        = high usage + high assist/creation signal
secondary_creator      = medium-high usage + creation signal
connector              = medium usage + assist/spacing/low-turnover signal
spacing_specialist     = strong 3PT profile + lower creation burden
play_finisher          = lower creation + rim/efficiency finishing signal
rim_runner_rebounder   = big/forward profile + rim/rebounding signal
defensive_specialist   = defense-forward profile + low usage
depth                  = low projected minutes or unclear role
```

Role classifier candidates:

| Classifier | Use |
|---|---|
| Multinomial logistic regression | Transparent baseline and calibrated class probabilities |
| Gradient-boosted classifier | Nonlinear challenger for role/roster interactions |
| Threshold-derived fallback | Deterministic fallback when role labels are too noisy |

Inputs for either learned or threshold-derived role assignment:

```text
projected_usage
assist_rate
three_point_rate
rim_rate
off_reb_pct
def_reb_pct
block_pct
steal_pct
archetype_label
expected_minutes
```

---

## 10. Recommended MVP Model

### Stage A - Roster State Builder

Build a school-season-snapshot roster state:

```text
returning players
- departing players
+ known transfer-ins
+ freshman/JUCO/other additions
+ replacement assumptions for unknown slots
```

Output:

```text
returning_minutes_by_position
departing_minutes_by_position
incoming_minutes_by_position
open_minutes_by_position
returning_usage_by_position
role_depth_by_archetype
unknown_minutes_risk
snapshot_confidence
```

### Stage B - Candidate Opportunity Model

Fit a two-head model to estimate minutes share and usage role:

```text
expected_minutes_share = f(candidate, destination_roster, interactions)
P(usage_role) = g(candidate, destination_roster, interactions)
```

MVP estimators to compare:

| Estimator | Use |
|---|---|
| Ridge / Elastic Net | Transparent minutes baseline |
| HistGradientBoostingRegressor | Nonlinear minutes challenger; native missing-value handling |
| Quantile models | Lower/upper minutes interval |
| Multinomial logistic regression | Transparent usage-role baseline |
| Gradient-boosted classifier | Nonlinear usage-role challenger |

Use the simplest estimator that wins temporal validation without losing interpretability.

### Stage C - Constrained Rotation Allocation

Convert expected minutes into a plausible roster allocation:

```text
sum(projected_team_minutes) = 200
0 <= player_minutes <= 34
bench depth remains non-negative
position coverage remains plausible
candidate displaces same-position/replacement/FLEX minutes first
```

The allocation does not need full integer programming for MVP. A deterministic greedy allocator is fine if it is tested and explainable.

Recommended displacement order:

1. Replacement-slot minutes.
2. Open/departing minutes at candidate's soft position.
3. Low-projected-impact returning bench minutes at same position band.
4. FLEX bench minutes.
5. High-minute returning players only if candidate projection clearly justifies it.

### Stage D - Usage Role Assignment

Assign `usage_role` and `usage_role_confidence` from the multinomial role probabilities when the classifier is available. If the learned role labels are not yet reliable, fall back to the deterministic threshold rules in Section 9.

```text
usage_role = argmax(P(usage_role))
usage_role_confidence = max(P(usage_role)) adjusted for data quality
```

The confidence should drop when:

- projected usage is near a role boundary,
- top-two role probabilities are close,
- roster usage is crowded,
- candidate has low sample size,
- player projection uncertainty is high,
- position match is ambiguous.

### Stage E - Role Fit Score

Compute `role_fit` from opportunity components. MVP formula:

```text
role_fit =
    0.35 * minutes_opportunity_score
  + 0.25 * role_need_score
  + 0.15 * usage_role_match_score
  + 0.15 * displacement_quality_score
  + 0.10 * confidence_score
  - crowding_penalty
  - stale_roster_penalty
```

All components should be clipped to `[0, 100]`, and penalties should be capped so a player with incomplete data can still receive a neutral but low-confidence score rather than crashing the run.

Interpretation:

| Role Fit | Meaning |
|---:|---|
| 85-100 | Clear path to major role |
| 70-84 | Strong rotation opportunity |
| 50-69 | Plausible role but competition exists |
| 30-49 | Crowded or uncertain path |
| 0-29 | Limited opportunity without roster changes |

---

## 11. Output Contract

### `player_team_fit_scores`

MVP writes:

```text
role_fit
overall_fit
breakdown.role_fit
model_version
computed_at
expires_at
```

`breakdown.role_fit` should include:

```json
{
  "expected_minutes": 22.4,
  "minutes_ci_lower": 16.1,
  "minutes_ci_upper": 27.8,
  "usage_role": "secondary_creator",
  "usage_role_confidence": 0.72,
  "minutes_opportunity_score": 81.3,
  "role_need_score": 74.2,
  "usage_role_match_score": 68.5,
  "displacement_quality_score": 70.0,
  "roster_crowding_penalty": 6.0,
  "stale_roster_penalty": 0.0,
  "open_minutes_by_position": {
    "PG": 12.0,
    "SG": 18.5,
    "SF": 8.0,
    "PF": 4.0,
    "C": 0.0
  },
  "displaced_minutes": [
    {
      "source": "replacement_slot",
      "minutes": 12.0
    },
    {
      "source": "same_position_depth",
      "minutes": 10.4
    }
  ],
  "data_quality_flags": [
    "player_projection_missing_used_season_fallback"
  ]
}
```

### Future `playing_time_projections`

If/when the richer table is added:

```text
id
player_id
school_id
season
roster_snapshot_id
expected_minutes
minutes_ci_lower
minutes_ci_upper
expected_minutes_share
expected_usage
usage_role
usage_role_confidence
starter_probability
rotation_probability
displaced_minutes jsonb
opportunity_drivers jsonb
data_quality_flags jsonb
scenario_overrides jsonb nullable
role_fit
model_version
computed_at
expires_at
```

Decision: MVP can write rich JSON into `player_team_fit_scores.breakdown` first. Add `playing_time_projections` when Team Rating Projection needs queryable expected minutes/displacement at scale.

---

## 12. Script Contract

Create:

```text
src/portalpoint/modeling/playing_time.py
scripts/run_playing_time.py
notebooks/models/playing_time_rotation_model.ipynb
```

Public functions in `playing_time.py` should be pure and testable:

```python
build_roster_state(...)
build_training_examples(...)
fit_minutes_model(...)
predict_minutes(...)
calibrate_minutes_intervals(...)
allocate_displaced_minutes(...)
assign_usage_role(...)
compute_role_fit_score(...)
score_role_fit_pairs(...)
upsert_role_fit_scores(...)
```

`scripts/run_playing_time.py` should:

1. Load scored `(player_id, school_id, season)` pairs from `player_team_fit_scores`.
2. Load the latest valid roster snapshot per school-season.
3. Build roster-state features.
4. Load player/candidate features and projection fallback fields.
5. Score expected minutes and usage role.
6. Allocate displacement.
7. Compute `role_fit`.
8. Merge Role Fit JSON into `breakdown`.
9. Recompute `overall_fit` using existing component weights.
10. Log coverage, interval width, score distributions, and data-quality counts.

---

## 13. Notebook Structure

### Cell 0 - Setup

```python
MODEL_VERSION = "playing-time-v1"
TRAIN_SEASONS = [2021, 2022, 2023, 2024, 2025]
SCORING_SEASON = 2026
INTERVAL_ALPHA = 0.20
```

### Cell 1 - Data Coverage Audit

Audit:

- roster snapshot coverage by school,
- transfer match rate,
- returning/departing status coverage,
- player ID match rate,
- missing candidate features,
- missing destination roster features.

### Cell 2 - Build Historical Roster States

Create training roster snapshots using only pre-season-available status.

### Cell 3 - Build Candidate-Destination Examples

Construct historical candidate-school examples and labels.

### Cell 4 - Baseline Minutes Model

Train ridge/elastic-net baseline.

### Cell 5 - Nonlinear Challenger

Train gradient boosting challenger and compare against baseline.

### Cell 6 - Quantile / Conformal Intervals

Fit lower/upper interval models and calibrate on holdout seasons.

### Cell 7 - Rotation Allocation

Convert candidate minutes into constrained displacement output.

### Cell 8 - Multinomial Usage Role

Train or derive usage-role probabilities, selected role, and confidence.

### Cell 9 - Role Fit Score

Compute score components, score distribution, and examples.

### Cell 10 - Validation

Temporal and cohort validation.

### Cell 11 - DB Write Dry Run

Preview rows and breakdown JSON.

### Cell 12 - MLflow / Artifact Logging

Log model, metrics, feature config, and score distributions.

---

## 14. Validation Strategy

### Primary Metrics

| Metric | Target |
|---|---|
| Minutes MAE / RMSE | Point-estimate accuracy |
| Minutes-share MAE / RMSE | Normalized opportunity accuracy |
| 80% interval coverage | Calibration of uncertainty |
| Mean interval width | Sharpness |
| Usage-role accuracy / macro F1 | Multinomial role quality |
| Usage-role log loss | Probability calibration and sharpness |
| Starter/rotation AUC | Coarse role/minutes classification utility |
| Spearman rank correlation | Whether opportunity ranking is useful |
| Role Fit distribution spread | Product ranking usefulness |

### Consistency Checks

Because minutes and usage role are predicted by separate heads in MVP, validate that the combined outputs make basketball sense:

- `depth` role should rarely pair with high expected minutes.
- `primary_creator` should rarely pair with very low expected usage.
- High expected minutes plus low role confidence should create a data-quality flag.
- The constrained allocator should reduce or flag role/minutes combinations that cannot fit the destination roster.
- If inconsistency is frequent, evaluate a joint multinomial role-minute model for v2.

### Required Slices

- Up-transfers.
- Down-transfers.
- Lateral high-major transfers.
- Freshmen/sophomores vs juniors/seniors.
- Guards, wings, forwards, bigs.
- High-usage creators.
- Low-minute upside players.
- Teams with high roster churn.
- Teams with stale or incomplete roster snapshots.

### Sanity Checks

- A player should not project for more than 34 mpg in baseline mode.
- Team minutes should sum to 200 after candidate insertion.
- Adding a candidate should not create negative minutes for displaced groups.
- Role Fit should fall when the roster is crowded at the player's position.
- Role Fit should rise when open minutes and role need match the player.
- Stale or low-confidence roster data should lower confidence, not silently produce confident scores.

---

## 15. Integration Contract

### Fit Score

After Role Fit runs:

```text
overall_fit =
    weight_gap * gap_match
  + weight_scheme * scheme_fit
  + weight_role * role_fit
  + weight_program * program_fit
```

The Role Fit script should preserve existing scheme/gap/program values and only update:

```text
role_fit
overall_fit
breakdown.role_fit
model_version or component_model_versions
computed_at
expires_at
```

### Team Rating Projection

Team Rating Projection should consume:

```text
expected_minutes
minutes_ci_lower
minutes_ci_upper
usage_role
displaced_minutes
data_quality_flags
```

It should not consume only the 0-100 `role_fit`.

### API / Frontend

The Fit Score API should expose whether Role Fit is:

```text
real
baseline
placeholder
missing
stale
```

The frontend should not render seeded-random Role Fit breakdowns once this model exists.

---

## 16. Implementation Order

1. Ingest transfers.
2. Add roster snapshot schema and ingest.
3. Build `player_team_seasons`.
4. Build roster-state feature view/table.
5. Add dry-run coverage notebook section.
6. Implement `playing_time.py` pure functions.
7. Train baseline minutes-share model.
8. Add quantile/conformal intervals.
9. Add constrained displacement allocator.
10. Add usage-role assignment.
11. Compute `role_fit` and breakdown.
12. Write `scripts/run_playing_time.py`.
13. Update Fit Score API to treat role breakdown as real.
14. Feed expected minutes/displacement into Team Rating Projection.

---

## 17. MVP vs Full Version

### MVP

- Latest roster snapshot per school.
- Transfer-aware roster state.
- Minutes-share model.
- Quantile or conformal minutes interval.
- Coarse usage role.
- Deterministic displaced-minute allocation.
- Derived `role_fit` written to `player_team_fit_scores`.
- Rich Role Fit breakdown JSON.
- Explicit data-quality flags.

### Full Version

- Multi-snapshot roster history.
- Coach/team random effects.
- User-editable depth chart overrides.
- Scenario simulation for injuries, late portal additions, and player withdrawals.
- Dedicated `playing_time_projections` table.
- Joint minutes and usage allocation across full roster scenarios.
- Posterior samples consumed by Team Rating Projection.

---

## 18. Open Questions

1. What roster source has the best coverage for incoming freshmen/JUCOs/non-transfer additions?
2. Should `playing_time_projections` be added before Team Rating Projection, or can JSON in `player_team_fit_scores.breakdown` carry MVP?
3. Should the first production model score all D1 players or only current portal candidates?
4. Should usage role be learned, derived, or hybrid for MVP?
5. Should coach-entered overrides affect the stored baseline row, or only create scenario rows?
6. How stale can a roster snapshot be before Role Fit should degrade or hide the score?
7. Should `model_version` remain one field or evolve into component-specific versions inside `breakdown`?
