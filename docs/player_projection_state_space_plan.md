# Player Projection Model Plan
## State-Space Player Skill and Value Projection System

**Planned notebook:** `notebooks/models/player_projection_state_space.ipynb`  
**Model family:** Game-level state-space model + hybrid basketball rate model  
**Primary output table:** `predictions` initially, with a future dedicated `player_projections` table  
**Downstream consumers:** Playing Time / Rotation model, Role Fit score, Team Rating Projection, Recommendations, player profile UI  
**Related plan:** `docs/playing_time_rotation_model_plan.md`

---

## 1. Objective

PortalPoint needs a player projection system that estimates both **true player talent** and **future statistical output** in a transfer-portal context.

The core questions are:

1. What is the player's underlying skill level right now?
2. How has that skill evolved across games and seasons?
3. How much should we trust noisy observed stat lines after accounting for sample size, opponent strength, role, and conference level?
4. What would this player project to produce next season in a neutral context?
5. How does that projection change when paired with a specific destination roster and playing-time role?

The final value metric should remain generically named for now, but the first target should be RAPM-style player impact. Conceptually, it should live near public all-in-one basketball impact metrics such as BPR, RAPM, BPM, EPM-style points-per-100 impact, or points above average.

```text
Player value target
= projected offensive and defensive impact per 100 possessions
  above D1 average in a neutralized context
```

For team and roster tools, that neutral value becomes destination-adjusted value after expected minutes, usage, and role are applied.

---

## 2. Architecture Summary

The model should follow a two-stage projection stack.

```text
Raw player-game data
    -> data preparation and standardization
    -> Stage 1: State-space latent skill model
    -> forecasted latent skill states
    -> Stage 2: Hybrid basketball outcome/rate model
    -> projected box-score rates and skill percentiles
    -> value translation layer
    -> neutral player projection
    -> destination context adapter + Playing Time / Rotation model
    -> player-school projection
```

### Stage 1 - Latent Skill State-Space Model

Stage 1 estimates hidden player skill states that evolve over time. Observed box-score stats are treated as noisy measurements of those latent skills.

```text
true skill at game t
= prior skill
  + player development / class-year aging curve
  + persistence from previous games/seasons
  + transfer/context effects
  + random player movement
```

### Stage 2 - Hybrid Basketball Rate Model

Stage 2 maps latent skills into observable basketball outcomes:

- A possession outcome model for shooting, free throws, and turnovers.
- Conditional rate models for assists, rebounds, steals, blocks, and fouls.
- A value translation model that turns projected rates into points-per-100 style player value.

This is analogous to a baseball stack where hidden skills feed a multinomial model for observable rates, but basketball requires a hybrid design because box-score events are not all mutually exclusive.

---

## 3. Projection Modes

The system should produce two related outputs.

### Neutral Talent Projection

Neutral projection answers:

```text
How good is this player, independent of destination?
```

It should include:

- Latent skill means and uncertainty.
- Projected per-possession box-score rates.
- Skill percentiles.
- Generic points-per-100 player value.
- Comparable historical players.

### Destination-Adjusted Projection

Destination projection answers:

```text
What does this player project to do at this specific school?
```

It should additionally include:

- Expected minutes from the separate Playing Time / Rotation model.
- Expected usage in the destination roster.
- Pace-adjusted per-game stat line.
- Conference and strength-of-schedule adjustment.
- Scheme and roster context adjustment.
- Destination-adjusted player value and uncertainty.

The neutral talent model should not own playing-time projection. Playing time remains a separate model, but the destination-adjusted projection depends on it.

---

## 4. Why State Space

Player performance is noisy, especially in college basketball where samples are short, roles change quickly, and competition levels vary dramatically.

A state-space framework is useful because:

- Each player has a hidden talent state that cannot be directly observed.
- Observed game stats can be weighted by sample size, minutes, and possessions.
- Recent performance can matter without overreacting to small samples.
- Different skills can have different stability. Three-point shooting should usually move slower than assist rate or turnover rate.
- Skill states can persist imperfectly across seasons.
- Class-year development curves can be built directly into the state evolution.
- Missing and ragged player histories are natural for Kalman filtering.
- Forecast uncertainty falls out of the model instead of being bolted on later.

This direction also matches the public projection landscape. EvanMiya's player skill projection tool uses stat-specific dynamic linear models over game-by-game D1 histories with opponent strength, usage, minutes, priors, recent form, and uncertainty. EvanMiya's BPR framework combines box-score priors with adjusted plus-minus style impact in a Bayesian framework.

---

## 5. Data Preparation Layer

The implementation should have a dedicated data-preparation layer, similar in spirit to a `DatasetView`, that turns raw basketball data into model-ready arrays.

### Required source data

| Data | Current status | Use |
|---|---|---|
| Player season stats | Exists in `player_season_stats` | Priors, historical baseline, and fallback only |
| Player game logs | Available through hoopR loaders, not yet ingested locally | Full state-space observations |
| Team game context | Available through hoopR loaders, not yet ingested locally | Pace, opponent, home/away, team role |
| Player identity | Exists in `players` | Position, class, height, priors |
| Team strength | Exists in `team_season_stats` | Opponent and destination context |
| Team system labels | Exists in `team_system_profiles` | Scheme/style context |
| Player archetypes | Exists in `player_archetypes` | Prior grouping and explanations |
| Current portal / committed transfers | BartTorvik transfer portal page; not yet ingested | Live portal candidates, source school, committed destination |
| Historical transfer events | BartTorvik historical transfer portal pages; infer from player-team-season changes as backfill/cross-check | Transfer-specific effects and validation |
| Hoop Explorer player impact | Current repo has sample CSV; expand coverage | Primary RAPM-style value labels |

Current repo reality: PortalPoint has mostly season-level barttorvik data today. The full game-level state-space model requires new player-game ingest before implementation. Season-level data should not be the target modeling grain; it should only support priors, bootstrapping, and temporary fallback checks.

### hoopR feasibility check

hoopR appears to support the needed game-level data:

- `load_mbb_player_box()` loads men's college basketball player box scores from the hoopR data repository. The docs list seasons with a minimum of 2003 and include game-level fields such as `game_id`, `game_date`, `athlete_id`, `minutes`, shooting makes/attempts, free throws, offensive/defensive rebounds, assists, steals, blocks, turnovers, fouls, points, starter flag, team, opponent, and home/away context.
- `load_mbb_pbp()` loads men's college basketball play-by-play from the hoopR data repository. The docs list seasons with a minimum of 2006 and include play-level fields such as play type, score state, period/clock, scoring flag, shooting flag, participant athlete IDs, team IDs, game ID, coordinates when available, and game context.

Remaining validation task: run a local coverage audit to confirm that hoopR athlete/team IDs can be joined cleanly to the existing PortalPoint `players`, `schools`, and barttorvik IDs across the seasons we need.

### Hoop Explorer impact labels

Hoop Explorer should be treated as the first value-label source, not merely an enrichment source. The current repo sample already includes player-level impact fields:

```text
off_adj_rapm
def_adj_rapm
adj_rapm_margin
off_adj_rapm_prod
def_adj_prod_rapm
adj_rapm_prod_margin
off_adj_rapm_pred
def_adj_rapm_pred
adj_rapm_margin_pred
```

The current file in `data/hoop_explorer/all_player_stats_high_tier.csv` is only the local sample. The modeling plan should assume we can add broader Hoop Explorer exports across more seasons/tiers where available.

Recommended value-label hierarchy:

| Priority | Label source | Use |
|---|---|---|
| 1 | Hoop Explorer adjusted RAPM | Primary MVP offensive, defensive, and total impact target |
| 2 | Hoop Explorer adjusted rating / production | Secondary target and robustness check |
| 3 | PortalPoint-owned RAPM from hoopR PBP + personnel | Long-term owned possession-impact label |
| 4 | BartTorvik/hoopR box-value proxy | Fallback when impact labels are unavailable |

BartTorvik remains essential for features and priors: usage, efficiency, shot profile, BPM-like statistics, team strength, schedule context, and transfer history. It should not be treated as the primary RAPM label source unless a separate verified feed exposes that metric.

### Transfer data strategy

Transfer data should come from three complementary paths:

```text
Current portal / commitment status
    -> BartTorvik transfer portal page
    -> player name, source school, destination school nullable

Historical transfer training data, primary path
    -> BartTorvik transfer portal pages by season
    -> player name, source school, destination school, status flag

Historical transfer training data, backfill/cross-check
    -> infer from player-team-season histories
    -> player appears for School A in season Y and School B in season Y+1
```

The BartTorvik transfer portal page embeds a transfer array with fields equivalent to:

```text
player_name
transfer_source_school
transfer_destination_school nullable
status_flag
```

This is useful for live portal candidates and current roster updates. It should not be the only historical source because name-only rows still need ID resolution.

Historical year pages appear available and should be ingested across seasons where coverage is strong. In a local probe, the transfer array changed by year and returned season-specific rows for 2020-2026. Example coverage checks:

| Season | Rows | Destination populated |
|---|---:|---:|
| 2026 | 3,820 | 1,878 |
| 2025 | 2,419 | 1,769 |
| 2024 | 2,924 | 1,970 |
| 2023 | 1,815 | 1,388 |
| 2022 | 1,718 | 1,718 |
| 2021 | 1,563 | 1,563 |
| 2020 | 906 | 906 |

For model training, create an inferred `player_team_seasons` view:

```text
player_id
season
school_id
games_played
minutes
primary_team_flag
first_game_date
last_game_date
```

Then infer transfer events when a stable player ID changes primary team across seasons:

```text
source_school_id
destination_school_id
source_season
destination_season
pre_transfer_minutes
post_transfer_minutes
confidence
evidence_type
```

Confidence should be based on ID quality, source-school match, name ambiguity, roster presence, and pre/post playing time. BartTorvik transfer rows, Hoop Explorer `transfer_src` / `transfer_dest`, and inferred player-team histories should cross-check each other where possible.

### Explicit tensor and table shapes

Use clear names so the notebook implementation stays readable.

| Object | Shape | Meaning |
|---|---|---|
| `obs_TPS` | time x player x skill | Standardized observed skill measurements |
| `ss_TPS` | time x player x skill | Sample sizes or observation weights for each skill |
| `ctx_TPC` | time x player x context | Game-level context features |
| `ages_TP` | time x player | Standardized player age or class-year index |
| `level_TP` | time x player | Conference/competition tier |
| `counts_TPO` | time x player x outcome | Possession outcome counts for Stage 2 |
| `rates_TPS` | time x player x skill | Raw or adjusted rate observations |
| `mask_TPS` | time x player x skill | Missing observation mask |
| `state_TPK` | time x player x latent skill | Filtered/smoothed latent skill states |

Where:

```text
T = time index, preferably player-game
P = player
S = observed skill/stat
C = context feature
O = outcome class
K = latent skill
```

### Context adjustments

The data layer should separate broad level effects from game-specific strength effects:

```text
level effect = conference tier / competition tier
schedule strength effect = opponent AdjEM, AdjO, AdjD, and quality within tier
```

This lets the model account for both "SEC vs MAAC" and "played elite teams vs weak teams inside the same conference."

---

## 6. Latent Skill State Vector

Stage 1 should use a multivariate latent skill state, not fully independent stat-by-stat models.

Recommended first state vector:

```text
alpha[t, player] = [
    shooting_3p,
    shooting_2p_finishing,
    free_throw_touch,
    shot_creation_usage,
    passing_creation,
    turnover_avoidance,
    offensive_rebounding,
    defensive_rebounding,
    steal_disruption,
    block_rim_protection,
    foul_discipline
]
```

### Cross-skill information

Some skills should inform other skills. The model can support this in three levels of complexity:

| Option | Description | Recommendation |
|---|---|---|
| Shared priors only | Position, class, height, FT%, archetype, and prior stats inform multiple skill priors | MVP-friendly |
| Block covariance | Correlated state noise within skill groups like shooting, creation, rebounding, defense | Preferred full v1 |
| Full covariance | Every latent skill can correlate with every other skill | Powerful but harder to fit |

Recommended skill blocks:

| Block | Skills |
|---|---|
| Shooting touch | `shooting_3p`, `shooting_2p_finishing`, `free_throw_touch` |
| Creation | `shot_creation_usage`, `passing_creation`, `turnover_avoidance` |
| Rebounding | `offensive_rebounding`, `defensive_rebounding` |
| Defensive playmaking | `steal_disruption`, `block_rim_protection`, `foul_discipline` |

Examples:

- Free-throw touch can inform shooting priors, especially for small 3P samples.
- Usage/creation can inform turnover risk.
- Blocks directly inform rim-protection skill.
- Offensive and defensive rebounding can share an athleticism/size prior but still diverge.
- Steals, blocks, and foul discipline can jointly inform defensive playmaking without collapsing into one defensive stat.

---

## 7. State-Space Specification

For player `p`, game/time `t`, latent skill vector `alpha[p,t]`, and observed skill vector `y[p,t]`:

### State evolution

```text
alpha[p,t+1]
= rho * alpha[p,t]
  + development_curve[class_year[p,t]]
  + transfer_context_effect[p,t]
  + level_transition_effect[p,t]
  + process_noise[p,t]
```

A more explicit form:

```text
mu[p,t] = beta_0
          + beta_1 * class_year[p,t]
          + beta_2 * class_year[p,t]^2
          + beta_3 * transfer_flag[p,t]
          + beta_4 * level_change[p,t]

alpha[p,t+1] = rho * alpha[p,t] + mu[p,t] + epsilon[p,t]
epsilon[p,t] ~ Normal(0, Q)
```

### Observation model

```text
y[p,t] = Z * alpha[p,t]
         + level_intercept[level[p,t]]
         + opponent_adjustment[opponent_strength[p,t]]
         + role_adjustment[usage[p,t], minutes[p,t]]
         + observation_noise[p,t]
```

Observation variance should depend on sample size:

```text
Var(observation_noise[p,t,k])
proportional to 1 / sample_size[p,t,k]
```

For basketball:

- Shooting observations use attempts as sample size.
- Assist/turnover observations use possessions or offensive possessions.
- Rebounding observations use available rebound chances when available, otherwise possessions/minutes.
- Steal/block/foul observations use defensive possessions or minutes.

### Primary fitting path

Use Kalman filtering/smoothing with maximum likelihood estimation as the primary implementation path.

Pros:

- Fast enough for repeated temporal cross-validation.
- Natural fit for ragged player-game panels.
- Operationally simpler than full MCMC.
- Produces filtered, smoothed, and forecast states.
- Matches the intended production-style projection stack.

Cons:

- Complex hierarchical priors require careful design.
- Full posterior uncertainty is less expressive than Bayesian MCMC.
- Full multivariate covariance can be numerically fragile.

### Bayesian extension

Use Bayesian/PyMC-style models as a research extension, not the first implementation path.

Pros:

- Richer hierarchical priors and uncertainty.
- Cleaner conceptual partial pooling across positions, classes, and archetypes.
- Useful for prior calibration or smaller submodels.

Cons:

- Slower for many players and CV folds.
- More convergence/debugging risk.
- Heavier operational footprint for the capstone timeline.

Recommended wording for implementation:

```text
Primary engine: linear Gaussian state-space model fit by maximum likelihood
with Kalman filtering and smoothing.

Uncertainty: filtered/smoothed covariance, forecast covariance, residual
calibration, and bootstrap checks.

Extension: hierarchical Bayesian model for richer priors if runtime permits.
```

---

## 8. Stage 2: Hybrid Basketball Outcome and Rate Model

Basketball does not map cleanly to one multinomial outcome because assists, rebounds, steals, and blocks overlap with possession outcomes. Use a hybrid Stage 2.

### Stage 2A - Possession outcome model

Model offensive possession-ending outcomes:

```text
2PA made
2PA missed
3PA made
3PA missed
free-throw trip / shooting foul drawn
turnover
other / low-involvement possession
```

Inputs:

```text
latent shooting and creation states
usage role
opponent defense
team pace/style
conference tier
```

### Stage 2B - Conditional contribution rates

Model overlapping box-score events separately:

| Rate | Inputs |
|---|---|
| Assist rate | passing creation, usage, teammate shooting context |
| Offensive rebound rate | offensive rebounding, position, team shot profile |
| Defensive rebound rate | defensive rebounding, position, opponent shot profile |
| Steal rate | steal disruption, opponent turnover context |
| Block rate | block rim protection, position, opponent rim rate |
| Foul rate | foul discipline, defensive role, opponent rim pressure |

### Stage 2C - Role / Usage Sensitivity Adapter

The player projection should support role-conditional outputs, not just one fixed stat line.

This is especially important for transfer evaluation. A high-usage mid-major guard may be more valuable at a high-major in a scaled-down role if his shooting, decision-making, and defensive traits translate better than his on-ball volume.

Important MVP constraint: this should be treated as a conservative scenario adjustment layer, not a fully causal estimate of what usage does to player value.

Usage is not randomly assigned. Players change usage because of talent, teammates, coaching, scheme, injuries, and roster quality. The first version should therefore estimate role-conditioned scenarios from historical role-change patterns and archetype-level priors, with wide uncertainty where evidence is thin.

Recommended MVP framing:

```text
role / usage sensitivity = structured scenario adjustment
not a standalone causal truth engine
```

The model should estimate:

```text
projected_rate_or_value
= base_talent_projection
  + usage_role_adjustment
  + minutes_context_adjustment
  + teammate_context_adjustment
  + scenario_uncertainty
```

For MVP, prefer coarse usage roles over a precise continuous usage curve:

Example scenario outputs:

| Scenario | Question |
|---|---|
| `primary_creator` | What if this player carries first-option usage? |
| `secondary_creator` | What if usage scales down but creation remains meaningful? |
| `connector` | What if the player plays lower usage with more spacing/decision value? |
| `specialist` | What if the player is mainly a shooter, defender, rebounder, or rim protector? |

The adjustment should be constrained by the player's projected skill state. For example:

- Good shooting, low turnovers, and useful defense may translate well into a scaled-down role.
- A high-turnover creator may become more efficient with less on-ball burden, but with less creation value.
- A player whose value depends mostly on high-volume self-created scoring may not scale down as cleanly.
- Low-usage specialists should carry high uncertainty when forced into primary usage scenarios.

This enables an interactive dashboard to keep the player's underlying talent fixed while changing role assumptions:

```text
same latent skill projection
    + adjusted usage role
    + adjusted minutes
    -> updated box rates, efficiency, and impact
```

The UI should present these as scenarios:

```text
Base projection
vs.
If used as secondary creator
vs.
If used as connector / specialist
```

Avoid overclaiming precision. The right interpretation is:

```text
Players with this profile have historically translated better/worse in this role,
so this scenario moves projected efficiency and value with added uncertainty.
```

### Stage 2D - RAPM-Style Value Translation

Translate projected rates into generic player value trained against RAPM-style impact labels:

```text
player_value_per_100
= offensive_value_per_100
  + defensive_value_per_100
```

Primary MVP labels:

```text
offensive_value_per_100 target = Hoop Explorer off_adj_rapm
defensive_value_per_100 target = Hoop Explorer def_adj_rapm
total_value_per_100 target     = Hoop Explorer adj_rapm_margin
```

Production-weighted variants such as `off_adj_rapm_prod`, `def_adj_prod_rapm`, and `adj_rapm_prod_margin` should be secondary labels because they mix per-possession impact with playing-time share. They are useful for season value, but the neutral talent model should first learn per-possession impact.

The value model should start as an interpretable regularized additive model:

```text
value = shooting_value
        + creation_value
        + turnover_value
        + rebounding_value
        + defensive_playmaking_value
        + foul_value
        + position/archetype effects
        + competition adjustment
```

Long-term, PortalPoint should own this target by fitting a RAPM-style possession-impact model from hoopR play-by-play plus on-court personnel. Hoop Explorer labels provide the first practical training target and validation layer while that owned target is built.

---

## 9. Playing Time, Rotation, and Role Fit Dependency

Playing time should remain a separate model, but it is not optional for destination-adjusted projections.

The architecture should distinguish the underlying model from the product score:

```text
Playing Time / Rotation model
    -> expected minutes, usage role, displaced minutes, minutes uncertainty

Role Fit score
    -> user-facing 0-100 opportunity/role match derived from the model outputs
```

### Separate Playing Time / Rotation model owns

- Expected minutes.
- Usage role in destination context.
- Displaced teammate minutes.
- Minutes uncertainty.
- Optional derived starter probability or rotation label if useful for the UI.

### Player Projection model owns

- Neutral latent skill and value projection.
- Per-possession skill rates.
- Skill uncertainty.
- Destination-adjusted stat/value projection after receiving minutes and usage context from the Playing Time / Rotation model.

Data flow:

```text
Neutral player projection
    -> Playing Time / Rotation model
    -> expected minutes + usage role + displaced minutes + uncertainty
    -> Role Fit score for user-facing fit explanation
    -> destination-adjusted player projection
    -> team rating projection
```

This keeps talent and opportunity conceptually separate while still tying them together for roster decisions.

---

## 10. Outputs

### Neutral player projection

```text
player_id
season
projected_skill_states
projected_box_rates
skill_percentiles
value_per_100
value_interval
projection_confidence
model_version
```

### Destination-adjusted projection

```text
player_id
school_id
season
expected_minutes
expected_usage
projected_per_game_box_score
projected_per_possession_rates
destination_adjusted_value_per_100
destination_adjusted_value_interval
role
confidence
explanation
model_version
```

### Current API compatibility

The current `predictions` table can hold an MVP projection:

| Existing column | New interpretation |
|---|---|
| `predicted_per_change` | Compatibility field; derive from value/PER bridge until API evolves |
| `predicted_minutes` | From Playing Time / Rotation model |
| `predicted_role` | starter/rotation/bench/reserve |
| `confidence` | Projection confidence from data quality and interval width |
| `shap_explanations` | Projection decomposition JSON, not necessarily SHAP |

Future table: `player_projections`.

```text
player_id
school_id nullable
season
projection_mode              # neutral or destination
value_per_100
value_ci_lower
value_ci_upper
projected_minutes nullable
projected_usage nullable
projected_box_score jsonb
projected_rates jsonb
skill_states jsonb
skill_percentiles jsonb
uncertainty jsonb
explanation jsonb
model_version
computed_at
expires_at
```

---

## 11. Interpretability Contract

The model should explain projections through model-native components rather than relying only on generic feature attribution.

### Coach-facing explanation fields

| Field | Meaning |
|---|---|
| `prior_skill_estimate` | Where the player started before current observations |
| `observed_performance_signal` | How recent games moved the estimate |
| `sample_size_weight` | How strongly the observed sample was trusted |
| `development_curve_effect` | Class-year/age movement |
| `level_adjustment` | Conference or competition-tier adjustment |
| `schedule_strength_adjustment` | Opponent quality adjustment inside level |
| `usage_role_adjustment` | Effect of offensive load on projected rates |
| `transfer_context_adjustment` | Adjustment for changing teams/contexts |
| `skill_strengths` | Top projected skill percentiles |
| `skill_risks` | Weakest skills or widest uncertainty bands |
| `comparable_players` | Historical players with similar latent state and role transition |

For tree or nonlinear challenger models, SHAP can be added later. For the core projection model, the preferred explanation is the actual state-space and value decomposition.

---

## 12. Validation Strategy

### Temporal cross-validation

Use rolling-origin validation:

```text
train through game/date T
forecast next N games or rest of season
compare projected rates and values to observed outcomes
```

### Skill/rate metrics

| Metric | Use |
|---|---|
| Cross-entropy / log loss | Possession outcome model |
| RMSE/MAE | Continuous rate forecasts |
| Brier score | Probabilistic rate events |
| Calibration | Whether 80% intervals cover outcomes about 80% of the time |
| Rank correlation | Whether leaderboards are useful |
| Cohort error | Freshmen, transfers, low-minute players, high-usage players |

### Value metrics

| Metric | Use |
|---|---|
| RAPM target RMSE | Accuracy against Hoop Explorer `off_adj_rapm`, `def_adj_rapm`, and `adj_rapm_margin` |
| Impact rank correlation | Whether projected player value orders players similarly to RAPM-style labels |
| Team-level lift | Whether player values improve team AdjEM projection |
| Transfer holdout error | Performance on players changing teams |
| Directional accuracy | Whether the model gets improvement/decline direction right |

### Required slices

Validate separately for:

- Low-major to high-major transfers.
- High-major to mid-major transfers.
- High-usage guards.
- Low-minute upside players.
- Bigs / rim protectors.
- Elite shooters with small 3P samples.
- Returning players vs transfers.

---

## 13. Notebook Structure

### Cell 0 - Setup

```python
MODEL_VERSION = "player-state-space-v1"
TRAIN_SEASONS = range(2020, 2026)
PROJECTION_SEASON = 2026
TIME_GRAIN = "game"
```

### Cell 1 - Data Coverage Audit

Audit game-log availability. If game logs are insufficient, run the MVP season-level fallback while preserving the full output contract.

### Cell 2 - Build Dataset View

Create a dataset-prep object that outputs:

```text
obs_TPS
ss_TPS
ctx_TPC
ages_TP
level_TP
counts_TPO
mask_TPS
```

### Cell 3 - Standardize Observations

Standardize skill observations and store means/scales for inverse transforms.

### Cell 4 - Build Priors and Level Effects

Estimate:

- Player priors from prior seasons, position, class, height, archetype, and recruiting profile if available.
- Conference-tier intercepts.
- Opponent/SOS adjustments.
- Transfer and level-change effects.

### Cell 5 - Fit State-Space Model

Fit the latent skill model using Kalman filtering/smoothing and maximum likelihood.

### Cell 6 - Forecast Latent Skill States

Generate next-season neutral skill forecasts and uncertainty intervals.

### Cell 7 - Fit Hybrid Outcome / Rate Model

Map latent skill states to possession outcomes and conditional contribution rates.

### Cell 8 - Fit Role / Usage Sensitivity Layer

Estimate how projected rates and value change across usage roles, especially for players scaling up or down after transferring.

### Cell 9 - Fit RAPM-Style Value Translation Layer

Translate projected rates into offensive, defensive, and total points-per-100 player value using Hoop Explorer adjusted RAPM labels where available.

### Cell 10 - Destination Context Adapter

Join Playing Time / Rotation outputs when available:

```text
expected_minutes
expected_usage
usage_role
minutes_ci_lower
minutes_ci_upper
displaced_minutes
```

Produce destination-adjusted projections for player-school pairs.

### Cell 11 - Scenario Outputs

Generate player-level scenario outputs for dashboard sliders:

```text
usage_role_override
minutes_override
projected_box_rates
projected_value_per_100
projected_per_game_stats
```

### Cell 12 - Validation

Run temporal CV and cohort diagnostics.

### Cell 13 - Explanation Payloads

Build projection decomposition JSON and comparable-player outputs.

### Cell 14 - DB Write

Write MVP-compatible rows to `predictions`; future migration writes to `player_projections`.

---

## 14. Architecture Decisions

These questions are settled for the first version of the player projection plan.

| Question | Decision |
|---|---|
| Modeling grain | Game-level is the target grain. Season-level data is only a fallback/prior source. |
| Primary game-log source | hoopR is the planned source for player game logs and play-by-play, pending local coverage and ID-join audit. |
| Stage 2 model shape | Use the hybrid basketball rate model: possession outcomes plus conditional contribution rates. |
| Observed skills | Directly observe 3P, 2P, FT, usage, assists, turnovers, offensive/defensive rebounds, steals, blocks, and fouls where data supports it. |
| Latent/proxy-heavy skills | Defensive impact, rim-protection quality, off-ball value, and spacing gravity remain latent/proxy-driven until richer data is available. |
| Competition adjustment | Start with four broad conference/competition tiers, plus within-tier opponent strength and schedule-strength adjustments. |
| Value target | Use Hoop Explorer adjusted RAPM as the primary MVP target: `off_adj_rapm`, `def_adj_rapm`, and `adj_rapm_margin`. Use adjusted rating/production as secondary labels, and BartTorvik/hoopR box-value proxy only as fallback. |
| No-history players | Use priors from position, class, height, recruiting/JUCO/international profile where available; otherwise use replacement-level priors. |
| Playing time | Keep playing time as a separate Playing Time / Rotation model. Role Fit is the user-facing score derived from that model; destination-adjusted player projections require the underlying minutes, usage-role, displacement, and uncertainty outputs. |

---

## 15. MVP vs Full Version

### MVP

- Preserve the two-stage architecture.
- Use season-level data only for priors and temporary fallback checks if player-game ingest is not ready.
- Use shared priors and simple block-level correlations.
- Fit interpretable RAPM-style value model against Hoop Explorer adjusted RAPM labels where coverage exists.
- Consume heuristic or future Playing Time / Rotation minutes for destination-adjusted outputs.
- Write to existing `predictions` table.

### Full version

- Game-level state-space model.
- Kalman filtering/smoothing with MLE.
- Block-correlated latent skill states.
- Hybrid possession outcome and conditional rate model.
- Dedicated Playing Time / Rotation model integration.
- PortalPoint-owned RAPM-style possession-impact target from hoopR play-by-play and play personnel.
- Dedicated `player_projections` table.
- Team projection consumes projection distributions, not just means.

---

## 16. Remaining Open Questions

1. **Hoop Explorer coverage and joins:** Can we export enough Hoop Explorer seasons/tiers, and can those rows join cleanly to PortalPoint player/school IDs?
2. **hoopR play-personnel coverage:** Is `espn_mbb_game_play_personnel()` populated broadly enough to support a PortalPoint-owned RAPM-style possession model?
3. **UI uncertainty surface:** How much uncertainty should surface in the product: intervals, risk tags, percentile bands, or all three?

---

## 17. Research Context

- EvanMiya Player Skill Projections: dynamic linear models over game-by-game data, context adjustments, priors, recent form, and uncertainty.  
  https://blog.evanmiya.com/p/new-tool-player-skill-projections
- EvanMiya Bayesian Performance Rating: Bayesian player value metric combining RAPM-style impact, box-score priors, preseason projections, and uncertainty.  
  https://blog.evanmiya.com/p/bayesian-performance-rating
- EvanMiya glossary: BPR/OBPR/DBPR interpretation, player projections, and roster strength concepts.  
  https://evanmiya.com/
- BartTorvik: source of existing PortalPoint team/player data concepts such as adjusted efficiency, tempo, team strength, and player stat inputs.  
  https://barttorvik.com/
- Hoop Explorer local notes and exports: current repo data includes player-level adjusted rating, adjusted production, adjusted RAPM, offensive/defensive RAPM, production-weighted RAPM, and predicted RAPM-style fields.  
  `notebooks/eda/justin/Hoop Explorer.md`, `data/hoop_explorer/all_player_stats_high_tier.csv`
- hoopR `load_mbb_player_box()` official docs: men's college basketball player box-score loader with player-game fields and seasons from 2003 onward.  
  https://hoopr.sportsdataverse.org/reference/load_mbb_player_box.html
- hoopR `load_mbb_pbp()` official docs: men's college basketball play-by-play loader with seasons from 2006 onward.  
  https://hoopr.sportsdataverse.org/reference/load_mbb_pbp.html
- hoopR function index: lists `espn_mbb_game_play_personnel()` for MBB event play personnel / on-court lineup at play, which is the key data dependency for a PortalPoint-owned RAPM target.  
  https://hoopr.sportsdataverse.org/reference/index.html
