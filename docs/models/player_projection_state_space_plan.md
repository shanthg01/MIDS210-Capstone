# Player Projection Model Plan
## State-Space Player Skill and Value Projection System

**Status (2026-06-23): Phase 0 in production. Phase 1 validated and calibrated. Phase 2 unblocked, not started.**
Phase 0 (`player-projection-shrinkage-v1`) writes real rows to `player_projections`, served by
`GET /api/players/{id}/projection`. Phase 1 (single-season Kalman, `player_projection_kalman.py`)
is implemented, its `R_t`-scaling bug found and fixed, and its defense-label sign question
investigated and resolved as a real (non-bug) finding — see §15 and the notebook's §13 for the
full record. Phase 2 (cross-season persistence, block covariance) is the open next step: the
2020-2025 game-log backfill that gated it is complete, and nothing in §15's Phase 0/1 checklist
remains open.

**Notebook:** `notebooks/models/player_projection_state_space.ipynb` (built, executed, both phases)  
**Script:** `scripts/run_player_projection.py` (Phase 0 only — Phase 1 has no production script, it's notebook-only validation)  
**Module:** `src/portalpoint/modeling/player_projection.py` (Phase 0), `player_projection_kalman.py` (Phase 1)  
**Model family:** Game-level state-space model + hybrid basketball rate model  
**Primary output table:** `player_projections` (migration `e6a2c8f1b734`) — the original plan to stage through `predictions` was dropped, see §10  
**Downstream consumers:** Playing Time / Rotation model, Role Fit score, Team Rating Projection, Recommendations, player profile UI  
**Related plan:** `docs/models/role_fit_playing_time_model_plan.md`

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

Key dependency decision:

```text
Neutral Player Projection does NOT depend on Role Fit.
Destination-adjusted Player Projection DOES depend on Role Fit / Playing Time.
```

The first implementation can and should build the neutral player projection before Role Fit is complete. Role Fit then consumes that neutral projection to estimate opportunity. After Role Fit exists, a destination adapter converts neutral talent into school-specific projected production.

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

The system should produce two related outputs. Treat these as two pipeline stages, not one model that always requires a destination school.

```text
Stage A: Neutral player projection
    player only
    context-neutral rate/value outputs
    can run before Role Fit

Stage B: Destination-adjusted projection
    player + school + roster snapshot
    consumes Role Fit / Playing Time outputs
    runs after expected minutes and role are known
```

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

It should not include a destination-specific minutes projection. If a display surface needs a temporary minutes value before Role Fit exists, label it as a fallback estimate rather than as neutral player talent.

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

Canonical flow:

```text
Neutral Player Projection
    -> Role Fit / Playing Time
        -> expected minutes, expected usage, usage role, displacement
            -> Destination-adjusted Player Projection
```

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
| Player game logs | ✅ Ingested 2026 via `hoopr_player_game_logs` (`ingest_hoopr.py --game-logs`, Issue #17 item 1); 2020-2025 backfill not yet run | Full state-space observations |
| Team game context | ✅ Ingested 2026 via `hoopr_games`/`hoopr_team_game_logs` (Issue #17 item 2); 2020-2025 backfill not yet run | Pace, opponent, home/away, team role |
| Player identity | Exists in `players` | Position, class, height, priors |
| Team strength | Exists in `team_season_stats` | Opponent and destination context |
| Team system labels | Exists in `team_system_profiles` | Scheme/style context |
| Player archetypes | Exists in `player_archetypes` | Prior grouping and explanations |
| Current portal / committed transfers | ✅ Ingested 2026 via `transfer_portal_events`/`transfers` — **source is 247Sports, not BartTorvik** (BartTorvik's transfer JSON is `robots.txt`-disallowed; see §"BartTorvik transfer portal page" note below) | Live portal candidates, source school, committed destination |
| Historical transfer events | ✅ Ingested 2026 via the same 247Sports pipeline; 2020-2025 backfill documented in `ARCHITECTURE_STATUS.md` but not yet run; infer from player-team-season changes as backfill/cross-check | Transfer-specific effects and validation |
| Hoop Explorer player impact | Current repo has sample CSV; expand coverage | Primary RAPM-style value labels |

Current repo reality (updated 2026-06-21): game-level player/team data and transfer events now exist in Postgres for season 2026 (Issue #17 items 1-3); full multi-season backfills are documented (see `ARCHITECTURE_STATUS.md`) but not yet run. Season-level data should not be the target modeling grain; it should only support priors, bootstrapping, and temporary fallback checks.

**This is a hard blocker for the full game-level state-space path, not just a fallback nicety.** The state evolution equation in §7 fits `rho` (season-to-season persistence) and the class-year development curve from cross-season movement in latent skill. With only one season (2026) of game-level data, there is no cross-season game-level signal to fit either term against — every player has exactly one season of observations. Concretely: **`scripts/ingest_hoopr.py --game-logs --skip-season-stats` must be run for seasons 2020-2025 before Cell 5 (state-space fit) can do anything beyond a single-season smoother.** Treat this backfill as step 0 of the implementation plan (§17), not as an optional data-quality nice-to-have discovered during the Cell 1 coverage audit.

### hoopR feasibility check

hoopR appears to support the needed game-level data:

- `load_mbb_player_box()` loads men's college basketball player box scores from the hoopR data repository. The docs list seasons with a minimum of 2003 and include game-level fields such as `game_id`, `game_date`, `athlete_id`, `minutes`, shooting makes/attempts, free throws, offensive/defensive rebounds, assists, steals, blocks, turnovers, fouls, points, starter flag, team, opponent, and home/away context.
- `load_mbb_pbp()` loads men's college basketball play-by-play from the hoopR data repository. The docs list seasons with a minimum of 2006 and include play-level fields such as play type, score state, period/clock, scoring flag, shooting flag, participant athlete IDs, team IDs, game ID, coordinates when available, and game context.

Remaining validation task: run a local coverage audit to confirm that hoopR athlete/team IDs can be joined cleanly to the existing PortalPoint `players`, `schools`, and barttorvik IDs across the seasons we need.

### Hoop Explorer impact labels

Hoop Explorer should be treated as the first value-label source, not merely an enrichment source. This section previously listed a 9-field RAPM set assumed from the local sample CSV (`data/hoop_explorer/all_player_stats_high_tier.csv`). That CSV is stale — `hoop_explorer_player_stats` is already ingested in Postgres (~16,750 rows, 6 seasons, all D1 tiers; see `ingest_hoop_explorer.py --all-seasons` in ARCHITECTURE_STATUS.md) and its actual impact columns (`src/portalpoint/db/models.py` `HoopExplorerPlayerStats`) are:

```text
adj_rtg_margin       # on-court net efficiency, unadjusted
adj_rapm_margin      # RAPM-isolated total impact
off_adj_rapm
def_adj_rapm
adj_rapm_margin_pred # projection to NCAAT-bound high-major context, total only — no off/def split
```

**Correction (2026-06-23):** these fields are not aspirational — they genuinely exist in the raw Hoop Explorer source CSVs (`data/hoop_explorer/all_player_stats_*.csv` has `off_adj_rapm_prod`, `def_adj_prod_rapm`, `adj_rapm_prod_margin`, `off_adj_rapm_pred`, `def_adj_rapm_pred`, plus a full `rank_*`/`pctile_*` set HE computes server-side). `ingest_hoop_explorer.py`'s player-row mapping (around line 486) simply never selects them — only `off_adj_rapm`, `def_adj_rapm`, `adj_rtg_margin`, `adj_rapm_margin`, and `adj_rapm_margin_pred` are mapped into `hoop_explorer_player_stats`. This is a real ingestion gap (missing columns + a migration), not a documentation error — see §19 for the fix.

Recommended value-label hierarchy (corrected):

| Priority | Label source | Use |
|---|---|---|
| 1 | `off_adj_rapm`, `def_adj_rapm`, `adj_rapm_margin` | Primary MVP offensive, defensive, and total impact target |
| 2 | `adj_rtg_margin` | Secondary/robustness target — same on-court signal pre-RAPM-adjustment, useful as a sanity check when RAPM sample is thin |
| 3 | `adj_rapm_margin_pred` | Tertiary — already-projected total margin in a high-major context; useful as a validation comparator for the model's own projected value, not a training target (it's downstream of the same kind of projection this model is building) |
| 4 | PortalPoint-owned RAPM from hoopR PBP + personnel | Long-term owned possession-impact label |
| 5 | BartTorvik/hoopR box-value proxy | Fallback when impact labels are unavailable |

BartTorvik remains essential for features and priors: usage, efficiency, shot profile, BPM-like statistics, team strength, schedule context, and transfer history. It should not be treated as the primary RAPM label source unless a separate verified feed exposes that metric.

### Transfer data strategy

**Superseded (2026-06-21): BartTorvik is not the implemented source — robots.txt blocks it.** The probe described below (confirming a season-partitioned transfer array for 2020-2026) was accurate — BartTorvik's `{season}_transfer_stats.json` is real, and its `player_id` field matches `players.barttorvik_id` exactly, no fuzzy matching needed. But `robots.txt` disallows `/*.json` and `/playerstat.php` (the only two real transfer pages on that domain) and explicitly disallows `ClaudeBot`/`anthropic-ai` site-wide. **247Sports' transfer-portal pages are the actual implemented source** (`scripts/ingest_transfers_247sports.py` → `transfer_portal_events`/`transfers`, not robots.txt-disallowed; season 2026 done, 2020-2026 backfill documented in `ARCHITECTURE_STATUS.md` but not yet run). Player resolution there is fuzzy name+roster matching against `player_season_stats` (~83% match rate verified), not a clean ID join — so the backfill/cross-check path below is still valuable, not just a fallback. The rest of this section is kept for the data-shape reasoning, with "BartTorvik" read as "247Sports" for the actual source.

Transfer data should come from three complementary paths:

```text
Current portal / commitment status
    -> 247Sports transfer portal pages
    -> player name, source school, destination school nullable

Historical transfer training data, primary path
    -> 247Sports transfer portal pages by season
    -> player name, source school, destination school, status flag

Historical transfer training data, backfill/cross-check
    -> infer from player-team-season histories
    -> player appears for School A in season Y and School B in season Y+1
```

The transfer-portal page embeds a transfer array with fields equivalent to:

```text
player_name
transfer_source_school
transfer_destination_school nullable
status_flag
```

This is useful for live portal candidates and current roster updates. It should not be the only historical source because name-only rows still need ID resolution.

Historical year pages appear available and should be ingested across seasons where coverage is strong. In a local probe, the transfer array changed by year and returned season-specific rows for 2020-2026 (confirmed live on 247Sports too — 1,193 entries for 2021 vs. 2,739 for 2026). Example coverage checks:

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

Confidence should be based on ID quality, source-school match, name ambiguity, roster presence, and pre/post playing time. 247Sports transfer events (`transfer_portal_events`), Hoop Explorer `transfer_src` / `transfer_dest`, and inferred player-team histories should cross-check each other where possible.

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

**No competition-tier column exists today.** `schools.conference` (raw conference name string) and `schools.nil_tier` (NIL budget tier, an unrelated concept) are the only categorical fields on `schools`; there is no major/mid-major/low-major tier anywhere in the schema. `level_TP` must be derived at feature-build time, not read from a column: bucket each school-season into 4 tiers from `team_season_stats.adj_em` national-percentile rank for that season (recompute per season, since conference strength shifts year to year — a fixed conference-to-tier lookup table would drift). This derivation belongs in the same data-prep layer that builds `obs_TPS`/`ctx_TPC`, not in a migration.

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

### Primary fitting path — staged, not one shot

Fitting a full multivariate Kalman model with block covariance, hierarchical priors, and MLE in one pass, on a ragged single-season-deep panel, is a high non-convergence-risk first deliverable. Stage the build instead:

```text
Phase 0: per-skill empirical-Bayes shrinkage estimator
    no Kalman filter at all — shrink observed rate toward position/class/archetype
    prior in proportion to sample size (attempts/possessions/minutes)
    ships fastest, gives every downstream model (Role Fit, Team Rating Projection)
    something real to consume while Phase 1/2 are built

Phase 1: univariate linear Gaussian state-space per skill
    diagonal Q, no cross-skill covariance, fit via statsmodels/pykalman MLE
    this is the first "real" state-space model — validate convergence and
    calibration here before adding covariance structure

Phase 2: block-covariance multivariate state-space
    correlated process noise within the shooting/creation/rebounding/defense
    blocks from §6 — only attempt once Phase 1 is calibrated and the
    2020-2025 game-log backfill has landed
```

Use Kalman filtering/smoothing with maximum likelihood estimation as the primary implementation path for Phase 1 and Phase 2.

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

`transfers.pre_usage_rate`, `transfers.post_usage_rate`, and `transfers.per_change` (`src/portalpoint/db/models.py` `Transfer`) are real, already-populated historical role-change observations — every confirmed transfer has a pre/post usage delta and a PER delta tied to it. This is the actual training signal for Stage 2C, not just an archetype-prior fallback: regress post-transfer rate/value change on pre-transfer skill state, usage delta, and archetype, using the transfer population as the fit set. Archetype-level priors should only fill in where a given archetype/usage-delta bucket has too few transfer rows to fit directly.

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

`adj_rtg_margin` (unadjusted on-court net efficiency) is the secondary/robustness label — see corrected hierarchy in §5. The production-weighted variant (`off_adj_rapm_prod`/`adj_rapm_prod_margin`) is now ingested (§19) and wired in as a robustness check in `scripts/run_player_projection.py` and the notebook — correlated against, not retrained on (corr 0.643/0.433 against `off_value_per_100`/`value_per_100`, sane for a playing-time-weighted secondary label). `def_adj_prod_rapm` has no def-side equivalent currently available — empty in the raw HE export (§19). `off_team_poss_pct` (fraction of team possessions played) is the closest existing ingested column to a playing-time-share weight, and should be used directly as the observation-weight (`ss_TPS`) input at season grain.

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

Neutral output basis:

```text
per 100 possessions
per 40 minutes
usage-normalized rates
context-neutral value
```

No destination school is required for this mode.

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

Destination output basis:

```text
neutral projection
+ Role Fit expected minutes
+ Role Fit usage role / expected usage
+ team pace
+ competition/tier adjustment
+ scheme/roster context
= school-specific projected stats and value
```

### Current API compatibility — corrected

The original idea of staging into `predictions` and migrating to `player_projections` later does not work and should be dropped. `predictions` (`src/portalpoint/db/models.py` `Prediction`) has `school_id: nullable=False` and a unique constraint on only `(player_id, school_id)` — no `season` column at all. That means:

- Neutral-mode rows (no destination school) cannot be inserted — `school_id` is required.
- Destination-mode rows can only ever hold one row per `(player_id, school_id)` pair, ever — no season versioning, every re-run overwrites the same row regardless of season.
- `predictions` is also already conceptually owned by Model 5 (Transfer Success / Outcome, XGBoost) per CLAUDE.md's ML Models table. Writing player-talent projections into it would conflate two different models under one table.

Go straight to a dedicated `player_projections` table instead of staging through `predictions`. See §18 for the migration.

```text
player_id
school_id nullable
season
projection_mode              # 'neutral' or 'destination'
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
| Primary game-log source | hoopR (`ingest_hoopr.py --game-logs`) — ✅ ingested for 2026 (Issue #17 item 1); 2020-2025 backfill not yet run. `player_id` resolved via `players.espn_id` first (~90% pre-backfilled by the season-level hoopR ingest), fuzzy roster match second. |
| Stage 2 model shape | Use the hybrid basketball rate model: possession outcomes plus conditional contribution rates. |
| Observed skills | Directly observe 3P, 2P, FT, usage, assists, turnovers, offensive/defensive rebounds, steals, blocks, and fouls where data supports it. |
| Latent/proxy-heavy skills | Defensive impact, rim-protection quality, off-ball value, and spacing gravity remain latent/proxy-driven until richer data is available. |
| Competition adjustment | Start with four broad conference/competition tiers, plus within-tier opponent strength and schedule-strength adjustments. |
| Value target | Use Hoop Explorer adjusted RAPM as the primary MVP target: `off_adj_rapm`, `def_adj_rapm`, and `adj_rapm_margin`. Use adjusted rating/production as secondary labels, and BartTorvik/hoopR box-value proxy only as fallback. |
| No-history players | Use priors from position, class, height, recruiting/JUCO/international profile where available; otherwise use replacement-level priors. |
| Playing time | Keep playing time as a separate Playing Time / Rotation model. Role Fit is the user-facing score derived from that model; destination-adjusted player projections require the underlying minutes, usage-role, displacement, and uncertainty outputs. |

---

## 15. MVP vs Full Version

Three tiers, matching the Phase 0/1/2 staging in §7, not two.

### Phase 0 (MVP — ships first) — ✅ done, in production (2026-06-23)

- Preserve the two-stage architecture conceptually, but Stage 1 is a per-skill empirical-Bayes shrinkage estimator, not a Kalman filter.
- Use season-level data (`player_season_stats`, `hoop_explorer_player_stats`) as the only input — 2026 game logs are too thin for a single-season state-space fit to beat a shrinkage estimator anyway.
- Fit interpretable RAPM-style value model against the corrected Hoop Explorer label set (§5/§8: `off_adj_rapm`, `def_adj_rapm`, `adj_rapm_margin`).
- Consume heuristic or future Playing Time / Rotation minutes for destination-adjusted outputs.
- Write to the new `player_projections` table (§18) from day one — never stage through `predictions`.
- **Result:** 27,047 player-seasons (2021-2026) scored and written, served by `GET /api/players/{id}/projection`. Two real upstream data bugs found and fixed along the way (`players.position` hardcoded `'G'`; Hoop Explorer ingest dropping `off_adj_rapm_prod`/`adj_rapm_prod_margin`) — neither was a Phase 0 logic bug, both were pre-existing ingestion gaps Phase 0's data audit surfaced.

### Phase 1 (state-space, single-season) — ✅ done, validated and calibrated (2026-06-23)

- Univariate (diagonal-covariance) linear Gaussian state-space per skill, fit via Kalman MLE, still on 2026 game logs only.
- Validates the state-space machinery (filtering, smoothing, forecast uncertainty) without betting the whole MVP on multivariate convergence.
- Requires `hoopr_player_game_logs`/`hoopr_games` for 2026 (already ingested) — does not require the 2020-2025 backfill.
- **Result:** machinery works; first run surfaced a real bug (`R_t` mis-scaled for count-rate skills — Bernoulli-shaped noise applied to Poisson-shaped data, pinning their fitted `Q` at its search bound). Fixed via `_r_numerator()` (Bernoulli `p(1-p)` vs. Poisson `mean_rate*40`, full derivation in the function docstring) plus widening `Q_BOUNDS` to `(1e-6, 100.0)`. Post-fix: zero skills at-bound, Phase 0/Phase 1 correlation jumped from 0.15-0.39 to 0.50-0.81 for every affected skill. Also investigated (separately, not a Phase 1 bug) a negative correlation between block/steal/def-rebound box stats and Hoop Explorer's `def_adj_rapm` in Phase 0's value model — ruled out competition tier, position, and sample-size as confounds; concluded it's a real, robust property of the RAPM label, not a pipeline artifact.

### Phase 2 (full version) — unblocked, not started

- 2020-2026 game-log backfill complete (2026-06-23, all 7 seasons) — cross-season `rho` and class-year development curve are now fittable.
- Block-correlated latent skill states (§6).
- Hybrid possession outcome and conditional rate model (Stage 2A/2B).
- Dedicated Playing Time / Rotation model integration.
- PortalPoint-owned RAPM-style possession-impact target from hoopR play-by-play and play personnel.
- Team projection consumes projection distributions, not just means.

---

## 16. Remaining Open Questions

1. **Hoop Explorer coverage and joins — resolved (2026-06-23):** 16,568 player rows, 16,206 matched to PortalPoint players (97.8%), RAPM columns 100% populated for matched rows. Not a blocker.
2. **hoopR play-personnel coverage:** Is `espn_mbb_game_play_personnel()` populated broadly enough to support a PortalPoint-owned RAPM-style possession model?
3. **UI uncertainty surface:** How much uncertainty should surface in the product: intervals, risk tags, percentile bands, or all three?
4. **No-history player priors:** §14 assumes recruiting/JUCO/international profile data is available for no-history priors, but no such source is ingested anywhere in the current schema (§19). Either find a source or fall back to replacement-level-only priors for that cohort.

---

## 17. Implementation Plan

Sequenced to match `docs/models/model_dependency_graph.md`'s execution order, with the game-log backfill pulled forward as step 0 since it gates the full game-level path.

```text
0.  ✅ DONE (2026-06-23). Backfill 2020-2025 hoopR game logs:
    uv run python scripts/ingest_hoopr.py --season 2020 --game-logs --skip-season-stats
    (repeated for 2021-2025; 2026 already done) — all 7 seasons now in hoopr_player_game_logs.
    Was blocking for Phase 2 only — Phase 0/1 didn't need it and shipped first as planned.

1.  ✅ DONE. Migration: add `player_projections` table (§18) — e6a2c8f1b734.

2.  ✅ DONE. src/portalpoint/modeling/player_projection.py
    - Phase 0: shrinkage fit/score/write functions, mirroring the pure-function
      pattern already used by player_clustering.py / scheme_fit.py / gap_matching.py.
    - level_TP (competition tier) derivation was scoped out of Phase 0 — Phase 0
      doesn't use a level/tier feature at all (§5's "Context adjustments" framing
      applies to Phase 1/2's game-level context, not Phase 0's season-grain shrinkage).
      Still open whenever Phase 2 needs it.
    - Pull value-label targets from the corrected Hoop Explorer column set (§5/§8).

3.  ✅ DONE. notebooks/models/player_projection_state_space.ipynb
    - Built with 13 sections covering both Phase 0 and Phase 1 (not just 0-4 as
      originally scoped — Phase 1 landed in the same notebook once it was ready).
    - Cell 1 coverage audit reports per-season game-log row counts; confirmed full
      2020-2026 coverage as of the step-0 backfill above.

4.  ✅ DONE. scripts/run_player_projection.py — Phase 0 only, no production script
    for Phase 1 (notebook-only validation, by design — see §15).

5.  ✅ DONE. Validation against the corrected RAPM labels (off_adj_rapm_prod/
    adj_rapm_prod_margin robustness check, corr 0.643/0.433). Role Fit's hard
    dependency on player projections is now unblocked — Role Fit itself not yet built.

6.  Phase 2 (block covariance, multivariate Kalman, cross-season rho) — data and
    calibration blockers both cleared (step 0 above; Phase 1's R_t bug fixed,
    see §15). Not yet started. Next real step on this model.
```

## 18. Additional Database Changes

### `player_projections` (new table, replaces the `predictions`-staging idea)

```text
id               bigint PK
player_id        FK players.id, not null
school_id        FK schools.id, nullable        -- null for neutral mode
season           smallint, not null
projection_mode  varchar(20), not null           -- 'neutral' | 'destination'
value_per_100    float, not null
value_ci_lower   float
value_ci_upper   float
projected_minutes  float, nullable
projected_usage    float, nullable
projected_box_score  jsonb
projected_rates      jsonb
skill_states         jsonb
skill_percentiles    jsonb
uncertainty          jsonb
explanation          jsonb
model_version    varchar(20), not null
computed_at      timestamptz, server_default now()
expires_at       timestamptz, not null
```

Constraint design needs care because `school_id` is nullable but still needs to dedupe on re-run. A plain `UniqueConstraint("player_id", "school_id", "season", "model_version")` will not work — Postgres treats every `NULL` as distinct, so neutral-mode reruns would insert a fresh row every time instead of upserting. Use two partial unique indexes instead:

```sql
CREATE UNIQUE INDEX uq_player_projections_neutral
  ON player_projections (player_id, season, model_version)
  WHERE school_id IS NULL;

CREATE UNIQUE INDEX uq_player_projections_destination
  ON player_projections (player_id, school_id, season, model_version)
  WHERE school_id IS NOT NULL;
```

Plus a lookup index for the common read path: `Index("ix_player_projections_player_season", "player_id", "season")`.

### One additional schema change landed alongside this: `hoop_explorer_player_stats` RAPM columns

Not originally scoped under "Additional Database Changes" (it surfaced from the §5/§19 ingestion-gap investigation, not from `player_projections`' own design) — migration `f1c4a8d3e570` added `off_adj_rapm_prod`/`def_adj_prod_rapm`/`adj_rapm_prod_margin`/`off_adj_rapm_pred`/`def_adj_rapm_pred` to `hoop_explorer_player_stats`. Only 2 of the 5 actually populate (the other 3 are empty in HE's raw export itself, not an ingest bug — see §19).

`hoopr_player_game_logs`, `hoopr_games`, `transfers`, `player_season_stats`, and `team_season_stats` carry everything else this plan needs. Competition tier (`level_TP`) is a derived feature, not a column (§5) — do not add a `schools.tier` column for this; it would need season-aware recomputation and would just go stale like a cached value.

## 19. Additional Source Data Needed

| Need | Status | Action |
|---|---|---|
| 2020-2025 game-level player/team logs | Not ingested (2026 only) | Run `ingest_hoopr.py --game-logs --skip-season-stats` per season 2020-2025 (§17 step 0) — this is the only genuinely *new* source-data work; everything else below is reuse of existing tables |
| Hoop Explorer RAPM labels, multi-season | **Done (2026-06-23).** Core 5 fields plus `off_adj_rapm_prod`/`adj_rapm_prod_margin` now ingested (migration `f1c4a8d3e570`). `def_adj_prod_rapm`/`off_adj_rapm_pred`/`def_adj_rapm_pred` are mapped but 100% NULL — confirmed empty in the raw source CSV across all 6 seasons, an HE export-configuration limit (their `_pred` fields need a "for transfers" leaderboard view we don't currently export with), not a PortalPoint ingestion bug. `rank_*`/`pctile_*` (~80 cols) intentionally not ingested — no concrete use case yet. | None remaining for the 5 originally-targeted fields; getting `_pred` populated needs a different manual HE export, out of scope here |
| Historical transfer pre/post role-change data | Already populated (`transfers.pre_usage_rate`/`post_usage_rate`/`per_change`) | None — wire into Stage 2C (§8) instead of building a separate role-change dataset |
| Competition/conference tier labels | Does not exist as a column anywhere | Derive at feature-build time from `team_season_stats.adj_em` percentile-per-season; no ingest needed |
| hoopR play-personnel (`espn_mbb_game_play_personnel()`) | Not ingested, needed only for the long-term owned-RAPM target (Stage 2D priority 4) | Out of scope for Phase 0-2; revisit when building the owned possession-impact model |
| Recruiting/JUCO/international profile for no-history priors (§14) | Not ingested anywhere in current schema | Open gap — no source identified yet; flag as a real open question rather than assuming it exists |

---

## 20. Cross-Document Notes Found During This Review

Not part of the modeling plan itself, but surfaced while grounding this doc against the live schema and sibling plans:

- `CLAUDE.md`'s "ML Models (7 total)" table has no row for Player Projection. It is a distinct model from #4 Playing Time Predictor (PyMC3) and #5 Transfer Success Predictor (XGBoost) — both of which depend on it per `model_dependency_graph.md`. Worth adding a row once this plan starts shipping real output, so the model count and dependency graph stay in sync.
- `docs/models/role_fit_playing_time_model_plan.md` and `docs/models/playing_time_rotation_model_plan.md` both target the same notebook (`notebooks/models/playing_time_rotation_model.ipynb`) and the same model. They look like two drafts of the same plan, not two different models — worth consolidating or marking one superseded before that model's implementation starts, so a future contributor doesn't build against the stale one.

---

## 21. Research Context

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

---

## 22. Phase 2 Progress and TODOs (2026-06-23)

**Status: 2a implemented and validated-with-caveats, then paused for consolidation. 2b-2e not started.** This section is the resume-here record — read it before picking Phase 2 back up.

### 2a — Cross-season, block-aware state-space

Files: `src/portalpoint/modeling/player_projection_phase2.py`, `scripts/validate_phase2_season_model.py`, `tests/test_player_projection_phase2.py` (7 tests, all passing).

**Design:** two-level hierarchical Kalman, not one combined model. Phase 1's intra-season filter (`player_projection_kalman.py`, unchanged) runs once per season across 2020-2026, producing one end-of-season smoothed state per player per season per skill. A new season-grain Kalman layer fits cross-season persistence (`rho`) and development-curve/transfer/level-change drift on top of those season-ending estimates, per §7's state evolution equation. `career_season_index` (rank among a player's own observed seasons) replaces literal `class_year` as the drift covariate — `players.class_year` only holds the most-recently-ingested value, not a per-season history (see the module docstring for the full reasoning).

**Real finding: joint MLE of `rho` and the drift terms (beta_1/beta_2) is not identifiable on this data.** Confirmed three ways, in order:
1. Naive joint fit on short sequences (n=2-4, the real-data median) lands on a wrong-but-genuinely-better-likelihood optimum than the true generating params — confirmed via multi-start from 4 different initial `rho` values, all converging to the same degenerate point (`rho≈0.06`, wrong-signed drift).
2. Pooling `rho` from only the long-career subset (n≥5 seasons) didn't fix it — even our longest real sequences (capped at 7 seasons by the game-log backfill) aren't long enough to separate persistence from trend.
3. Adding a Gaussian MAP-style prior penalty on `rho` didn't fix it either — the likelihood's pull toward the degenerate `rho→0` region was large enough (hundreds of NLL units) to swamp even a fairly tight prior.

**Fix:** estimate `rho` via simple pooled lag-1 Pearson autocorrelation of consecutive-season smoothed estimates instead of MLE — a single, well-identified statistic with no competing parameter to be traded against. Fix it, then fit the drift terms by MLE on the full population. All three failed attempts are preserved in `tests/test_player_projection_phase2.py`, not deleted, so this doesn't get rediscovered the hard way next time.

**Real-data results (full 2020-2026 run, completed 2026-06-23, ~2h15min):**

| Skill | rho | beta_1 | beta_2 | Q | Notes |
|---|---|---|---|---|---|
| shooting_3p | 0.200 | 0.011 | -0.001 | 0.0005 | rho at clip floor — see caveat below |
| shooting_2p_finishing | 0.216 | -0.000 | 0.001 | 0.003 | |
| free_throw_touch | 0.307 | 0.024 | -0.003 | 0.006 | |
| shot_creation_usage | 0.491 | -2.997 | 0.600 | 11.0 | large-magnitude params are this skill's natural scale (per-40 attempt rate ~20s), not necessarily alarming |
| passing_creation | 0.563 | 0.219 | -0.027 | 0.65 | |
| turnover_avoidance | 0.219 | -0.092 | 0.006 | 0.38 | negative beta_1 = improves with experience (correct direction — this skill is inverted) |
| offensive_rebounding | 0.530 | -0.049 | 0.007 | 0.48 | |
| defensive_rebounding | 0.416 | 1.455 | -0.222 | 1.24 | |
| steal_disruption | 0.234 | 0.007 | -0.001 | 0.12 | |
| block_rim_protection | 0.601 | -0.017 | 0.003 | 0.15 | |

**Within-block residual correlations — validates 2 of 4 blocks cleanly, 1 weakly, 1 not at all:**
- **Creation block: validates §6's own hypothesis.** `passing_creation` vs `turnover_avoidance` residual corr = **0.35** — unexpectedly good passing correlates with unexpectedly higher turnovers, exactly matching §6's "usage/creation can inform turnover risk."
- **Rebounding block: validates.** `offensive_rebounding` vs `defensive_rebounding` = **0.41** — correlated but not collinear, matching §6's "shared athleticism/size prior but still diverge."
- **Defensive playmaking: near-zero (-0.10).** Steals and blocks are genuinely different skillsets (perimeter gambling vs. rim presence) — sensible, just not the "shared block" signal §6 hoped for.
- **Shooting touch: weak/mixed, does not cleanly validate.** 3P vs FT = 0.20 (sensible), 2P-finishing vs FT = -0.15 (counterintuitive), 3P vs 2P-finishing ≈ 0 (-0.02).

**Known caveats, not yet resolved:**
- `shooting_3p`'s `rho` landed at exactly the clip floor (0.2). The raw unclipped value wasn't logged in this run — logging was added afterward (see below) but never re-verified against real data, since re-running costs ~2h15min and nothing yet requires it.
- No proper held-out validation anywhere in Player Projection yet — Phase 0 shipped on in-sample residual std only (no train/test split, no R², no cross-validation); 2a has nothing better. This is exactly what §12/2e is for, not yet built.
- Shooting-touch block doesn't validate as cleanly as creation/rebounding — worth a closer look before trusting any cross-skill prior-blending built on top of it.

**Performance + tooling added after the first real run:**
- `load_or_build_season_skill_states()` / `load_or_build_season_covariates()` — parquet-cached wrappers (gitignored under `data/features/player_projection_phase2/`, same convention as `feature_eng_m1_m2_m3.ipynb`'s caches) so 2b/2c/2d don't re-pay the ~2h intra-season filtering cost.
- `estimate_rho_autocorrelation()` now logs the raw pre-clip value whenever clipping changes it (or confirms it didn't), closing the `shooting_3p` transparency gap for future runs.

### TODO before resuming Phase 2

1. **Move `scripts/validate_phase2_season_model.py` into the notebook**, matching Phase 1's precedent exactly: `validate_phase1_kalman.py` was built standalone first, then folded into `notebooks/models/player_projection_state_space.ipynb` once it was stable, and the standalone script deleted (see this notebook's own intro markdown, which already documents that supersession for Phase 1). **Not yet done for 2a** — the script still exists standalone and should not be treated as the long-term home for this validation.
2. Re-confirm `shooting_3p`'s raw autocorrelation once the cache is warm and a future run actually executes the new logging — current real-data results predate that fix.
3. Investigate the shooting-touch block's weak correlation result specifically before relying on cross-skill prior-blending for those 3 skills.
4. **2b-2e not started.** Hybrid possession-outcome + conditional rate model (Stage 2A/2B), role/usage adapter (Stage 2C), Stage 2D value-model refit, and the full §12 validation suite (2e) remain exactly as scoped when Phase 2 was approved. Re-scope/re-confirm sequencing when resuming — 2a's real findings here (the rho-identifiability trap, the cost of a full real-data run) should inform how 2b is built, particularly: budget for an identifiability check early, and validate on a smaller sample before committing to a multi-hour full run, rather than discovering problems after a 2+ hour fit the way 2a did.
5. Phase 2a's code (`player_projection_phase2.py`, its tests, the validation script) is implemented and passing but **not yet committed** — exists only in the working tree as of this pause point.
