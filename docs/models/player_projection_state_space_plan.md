# Player Projection Model Plan
## State-Space Player Skill and Value Projection System

**Status (2026-06-25): Phase 2a next-season forecast is the API default by product decision. Phase 1 validated and calibrated. Phase 0 remains the baseline comparator.**
Phase 2a production rows use observed college season `S` to forecast target projected season `S+1`
under `model_version="player-proj-phase2a-fcast-v1"`, served by
`GET /api/players/{id}/projection`. Same-season Phase 2a rows (`player-projection-phase2a-v2`) remain
diagnostic state estimates, not the production row consumed by downstream Role Fit / destination
projection work. Phase 1 (single-season Kalman, `player_projection_kalman.py`)
is implemented, its `R_t`-scaling bug found and fixed, and its defense-label sign question
investigated and resolved as a real (non-bug) finding — see §15 and the notebook's §13 for the
full record. **Phase 2a (cross-season persistence, block covariance) is implemented and reconciled
against [Issue #37](https://github.com/shanthg01/MIDS210-Capstone/issues/37)** — Gaps A/D/E/G coded,
tested, and validated against real data (Phase 2a beats Phase 0 on offense every fold, ties on
defense); Gap B (context adjustment) is coded but found to *regress* accuracy on real data, with
root cause documented as weak current context signals, so it is not enabled; Gap C (rate projections)
and Gap F (real DB write; now productionized as the `S -> S+1` forecast model version above) are both done. Two follow-on additions landed
2026-06-24/25: an 11th skill (`foul_discipline`, Phase 1/2-only) and an offense/defense
feature-set split for the value-translation model (kept despite a real, measured defense-accuracy
cost — see §22). Production integration decision: `scripts/run_player_projection.py --phase both`
writes Phase 0 plus the Phase 2a next-season forecast version, and
`GET /api/players/{id}/projection` serves that forecast version by explicit
product/architecture preference despite the automatic MLflow gate keeping Phase 0 as champion.
Confidence intervals are no longer static across every player in Phase 2a forecasts: the value
translation adds a player-specific skill/source-value variance component to the residual error floor
and applies rolling conformal scaling for the nominal 80% target. The production forecast value
layer also includes source-season internal off/def/total value priors so elite returning players are
not over-mean-reverted by skill transitions alone. Final script rerun wrote 30,304 forecast rows
for target seasons 2022-2027; every row carries projected rates/box-score payloads, with per-100
rates using source-season team pace from `player_season_stats` because `player_school_seasons` is
empty in the current local data stack.
Full real-data record, all real bugs found and fixed, and the teammate-review response are all in §22.

**Notebook:** `notebooks/models/player_projection_state_space.ipynb` (built, executed, both phases)
**Script:** `scripts/run_player_projection.py` (`--phase {0,2a,both}`; Phase 1 remains notebook-only validation)
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

It is a downstream adapter over the neutral projection, not a replacement for it. For target season
`n`, it should consume:

- Neutral player projection for player `p`, season `n`.
- Playing Time / Rotation outputs for `(player p, school s, season n)`.
- Destination team style, pace, roster baseline, and level/tier context for school `s`, season `n`.

It should output:

- Expected minutes and usage copied from the Playing Time / Rotation model.
- Pace-adjusted per-game stat line.
- Destination-adjusted per-40/per-100 rates.
- Destination-adjusted player value and uncertainty.
- Explanation of what moved relative to neutral projection.

The neutral talent model should not own playing-time projection. Playing time remains a separate model, but the destination-adjusted projection depends on it.

Canonical flow:

```text
Neutral Player Projection
    -> Playing Time / Rotation
        -> expected minutes, expected usage, usage role, displacement
            -> Destination-adjusted Player Projection
```

Production destination-adjusted projections are blocked until `playing_time_projections` exists.
Dry-run notebooks may use heuristic minutes for exploration, but production rows should not be
written without model-produced playing-time outputs.

MVP destination formula:

```text
destination_adjusted_value_per_100
    = neutral_value_per_100
    + role_usage_delta
    + style_skill_fit_delta
    + roster_context_delta
    + competition_level_delta
```

The per-100 value is not the same thing as total roster value. Also compute:

```text
projected_possessions_played
projected_total_value
destination_adjusted_interval
```

Where:

```text
projected_total_value =
    destination_adjusted_value_per_100
    * projected_possessions_played
    / 100
```

The interval should start from the neutral projection interval and widen for:

- playing-time interval width,
- usage-role confidence,
- roster snapshot staleness,
- large source/destination level changes,
- sparse player history or wide neutral uncertainty.

Guardrails:

- Keep `neutral_value_per_100` as the anchor; contextual deltas should be modest until validated.
- Suggested MVP caps:
  - `abs(role_usage_delta) <= 0.75`
  - `abs(style_skill_fit_delta) <= 0.50`
  - `abs(roster_context_delta) <= 0.50`
  - `abs(competition_level_delta) <= 0.75`
  - `abs(total_context_delta) <= 1.50`
- Do not let `scheme_fit` become value by itself. Scheme Fit is compatibility; destination projection
  translates compatibility into expected production only through role, usage, and style/skill
  interactions.
- Playing-time uncertainty should widen destination value and box-score intervals.
- Destination rows should be separate `player_projections` records with `school_id` set and a
  distinct destination-adjusted model version.

Efficient first version:

1. Use Playing Time outputs for expected minutes, usage role, expected usage, displacement, and uncertainty.
2. Translate neutral projected rates to per-game stats using destination pace and minutes.
3. Apply conservative rules or a small regularized residual model for role/style/context deltas.
4. Validate against historical player-school-season outcomes before increasing delta magnitudes.

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
| Shared priors only | Position, class, height, FT%, and prior stats inform multiple skill priors | MVP-friendly |
| Block covariance | Correlated state noise within skill groups like shooting, creation, rebounding, defense | Preferred full v1 |
| Full covariance | Every latent skill can correlate with every other skill | Powerful but harder to fit |

**Correction (Issue #37, 2026-06-23): `archetype` removed from the shared-priors list above.** The original row listed it alongside position/class/height/FT% as a prior input — never actually implemented that way, and Issue #37 now makes it an explicit constraint: `player_archetypes` is evaluation/explanation/comparable-player metadata only, never a model feature, prior, or input to any state equation or value head. Verified compliant as of this correction — zero archetype references anywhere in `player_projection*.py`.

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
  - defensive_value_per_100_raw
```

Primary MVP labels:

```text
offensive_value_per_100 target = Hoop Explorer off_adj_rapm
defensive_value_per_100_raw target = Hoop Explorer def_adj_rapm  # lower is better
total_value_per_100 target     = Hoop Explorer adj_rapm_margin
```

**Sign convention correction (2026-06-25):** Hoop Explorer's raw defensive
adjusted RAPM is lower-is-better. The raw source and local DB both satisfy
`adj_rapm_margin = off_adj_rapm - def_adj_rapm`, so `value_per_100` must
subtract the defensive model prediction unless the defensive training target is
explicitly flipped to a positive defensive-value scale.

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
expected_minutes_share
expected_usage
usage_role
usage_role_confidence
minutes_uncertainty
displaced_minutes
projected_per_game_box_score
projected_per_possession_rates
destination_adjusted_value_per_100
destination_adjusted_value_interval
projected_possessions_played
projected_total_value
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

The first adapter should compute at least these explanation fields:

```text
neutral_value_per_100
role_usage_delta
style_skill_fit_delta
roster_context_delta
competition_level_delta
total_context_delta
uncertainty_adjustment
minutes_source_model_version
neutral_projection_model_version
team_style_features_used
```

Suggested style/skill translation examples:

- Strong projected 3P shooting on a high-3PA roster can raise projected 3PA and spacing value.
- High passing creation with open ball-handler minutes can raise assist rate and usage role.
- High shot-creation usage on a crowded backcourt should lower expected usage and raise uncertainty.
- Rim protection / defensive rebounding should translate more at destinations with frontcourt need.
- Destination pace changes per-game box score more than per-100 value.

Concrete data sources for the first destination adapter:

| Input | Source table/artifact | Key fields / metrics |
|---|---|---|
| Neutral projection | `player_projections` | Neutral rows with `school_id IS NULL`, `model_version="player-proj-phase2a-fcast-v1"`, target `season`; use `value_per_100`, CI bounds, `projected_rates`, `projected_box_score`, `skill_states`, `skill_percentiles`, `uncertainty`, `explanation.source_observed_season` |
| Playing time / role | `playing_time_projections` | Required production dependency: `expected_minutes`, `expected_minutes_share`, `minutes_ci_lower`, `minutes_ci_upper`, `expected_usage`, `usage_role`, `usage_role_confidence`, `displaced_minutes`, `role_fit`, `model_version`, `data_quality_flags` |
| Destination roster context | `roster_snapshots`, `roster_snapshot_players`, `roster_state_features` | current roster membership, returning/transfer-in/new flags, snapshot freshness, open/departing/returning minutes and usage by position |
| Destination team quality / pace | `team_season_stats` | `adj_em`, `adj_o`, `adj_d`, `adj_tempo`; use `adj_tempo` for per-game stat translation and `adj_em` percentile for level/tier |
| Destination style / system | `team_system_profiles`; `data/features/team_style_vectors.parquet`; `hoop_explorer_team_stats` | offense/defense labels, style memberships, team 3PA/rim/mid profile, HE `off_style_*_pct` / `def_style_*_pct` where covered |
| Player style context | `hoop_explorer_player_stats`, `player_archetypes` | `pos_confidence_*`, `off_style_*_pct`, archetype label/confidence; use as explanation/context, not as a replacement for neutral projection |
| Pairwise fit context | `player_team_fit_scores` | `scheme_fit`, `gap_match`, `role_fit`, breakdown JSON; use as interaction/explanation inputs, not direct value by itself |
| Transfer/source context | `transfers`, `transfer_portal_events`, source-season `player_season_stats` | source/destination school IDs, status, pre/post usage, derived MPG from `player_season_stats.min_pct * 0.4` where available, source school/tier. Treat `min_pct` as the source of truth because older DBs may contain legacy stored MPG. |

Destination rows should be written back to `player_projections` with `school_id` populated,
`projection_mode="destination"`, and a distinct destination-adjusted `model_version`. The partial
unique index on `(player_id, school_id, season, model_version)` handles reruns separately from
neutral rows.

#### Destination training set

Train and validate destination adjustments at observed player-school-season grain:

```text
row grain:
    player_id, actual_school_id, target_season

required inputs:
    neutral player projection for player_id, target_season
    playing_time_projections-like opportunity features for actual_school_id, target_season
    team_system_profiles for actual_school_id, target_season
    team_season_stats for actual_school_id, target_season
    source-season player context

labels:
    actual per-game box stats from player_season_stats / hoopR aggregates
    actual usage_rate
    actual value target from Hoop Explorer RAPM where available
```

For historical validation, the playing-time features should come from the Playing Time / Rotation
model's out-of-fold predictions where feasible, not from actual target-season minutes copied from
the label row. This avoids validating the destination adapter with leaked opportunity.

Do not train on unchosen schools as negative outcomes. A player not attending a school is an
unobserved counterfactual, not an observed zero-production season.

#### Destination inference set

Production inference should be the successful `playing_time_projections` rows for the target season:

```text
row grain:
    player_id, school_id, target_season

filter:
    playing_time_projections.model_version = active/champion playing-time model
    player_projections has neutral target-season row
    destination team context is available
```

The destination adapter should skip or flag rows when any of these are missing:

- neutral projection,
- playing-time projection,
- destination team pace/quality,
- usable player archetype/skill context.

No production destination row should be written when the playing-time projection is missing.

#### Destination script contract

Create:

```text
src/portalpoint/modeling/destination_projection.py
scripts/run_destination_projection.py
```

Public functions in `destination_projection.py` should be pure and testable:

```python
load_destination_inputs(...)
build_destination_training_examples(...)
build_destination_inference_frame(...)
translate_neutral_rates_to_destination_stats(...)
compute_role_usage_delta(...)
compute_style_skill_fit_delta(...)
compute_roster_context_delta(...)
compute_competition_level_delta(...)
apply_delta_caps(...)
propagate_destination_uncertainty(...)
build_destination_projection_records(...)
upsert_destination_projections(...)
```

`scripts/run_destination_projection.py` should:

1. Load neutral production projections for the target season.
2. Load active `playing_time_projections` for the target season.
3. Inner join to destination team context and pairwise fit context.
4. Skip rows missing playing-time projections.
5. Translate neutral per-40/per-possession rates to destination per-game stats using expected minutes and destination pace.
6. Apply conservative role/style/roster/competition deltas with MVP caps.
7. Propagate uncertainty from neutral projection and playing-time intervals.
8. Build explanation JSON with component deltas and source model versions.
9. Upsert destination rows to `player_projections`.
10. Log coverage, skipped-row reasons, delta distributions, interval widths, and validation metrics.

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

**Status (2026-06-23): implemented for Phase 0's value model, with real results.** `src/portalpoint/modeling/player_projection_eval.py` + a new notebook section ("7a. Phase 0 — Formal Rolling-Origin Evaluation") replace what used to be in-sample-only metrics (`off_resid_std`/`def_resid_std` computed on the same rows used to fit the model) with real rolling-origin temporal CV, hyperparameter tuning, baseline comparison, and partial cohort slicing. This closes the gap called out earlier in this doc and in CLAUDE.md — production had shipped with zero held-out validation.

### Temporal cross-validation — done

3 rolling-origin folds (not one static split — cheap to run, more robust, and what this section already specified):

| Fold | Train | Val (hyperparameter selection only) | Test | k | alpha | off_rmse | off_r² | off_spearman | def_rmse | def_r² | def_spearman | calibration |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2021-2022 | 2023 | 2024 | 8.0 | 1.0 | 1.544 | 0.491 | 0.716 | 1.446 | 0.122 | 0.347 | 90.9% |
| 2 | 2021-2023 | 2024 | 2025 | 8.0 | 0.1 | 1.631 | 0.453 | 0.676 | 1.535 | 0.117 | 0.366 | 89.2% |
| 3 (headline) | 2021-2024 | 2025 | **2026** | 4.0 | 0.1 | 1.714 | 0.451 | 0.702 | 1.559 | 0.129 | 0.365 | 88.2% |

**Real findings:**
- **The model clearly beats baselines** — fold 3's Ridge `off_rmse`=1.714 vs. predict-train-mean's 2.315 and predict-position-mean's 2.300. Ridge is earning its complexity, not just memorizing the mean.
- **Offense predicts much better than defense, out-of-sample too** — off_r²≈0.45-0.49 vs. def_r²≈0.12-0.13 in every fold. This is the held-out, quantitative confirmation of the qualitative finding already documented below (§13's "investigated, not a bug" note on `def_adj_rapm` vs. box-score defense) — defense is a genuinely harder target for this feature set, not an artifact of in-sample overfitting.
- **Calibration runs slightly high** (88-91% vs. the 80% nominal target of `project_value`'s `CI_Z=1.2816`) — the model is mildly conservative (CI bands a bit too wide), not overconfident. Safer failure direction for a product surface, not urgent to fix.
- **Hyperparameter tuning: `alpha` barely matters.** Grid-searched `RIDGE_ALPHA ∈ [0.1, 1, 5, 10, 20]` — validation RMSE was flat to the 3rd decimal place across that entire range (confirmed by extending the grid down to 0.05 before finalizing — not a boundary artifact, genuinely flat). At ~13 coefficients (10 skills + position dummies) against thousands of training rows, the model is heavily overdetermined; Ridge's regularization strength isn't the binding constraint. Production's `RIDGE_ALPHA=5.0` default is fine as-is — this is a real result (checked), not an assumption. `SHRINKAGE_K` showed mild sensitivity (selected 4.0-8.0 across folds vs. production's hardcoded 8.0) but differences were small enough not to warrant a production change without more evidence.
- **Production unchanged.** This evaluation used a smaller, fold-specific train set on purpose (to keep honest holdouts) — `run_player_projection.py` keeps fitting on the full pooled 2021-2026 population, which is the right call for the actual product surface.

### Skill/rate metrics

| Metric | Use | Status |
|---|---|---|
| Cross-entropy / log loss | Possession outcome model | N/A — no possession outcome model exists (Stage 2A, never built) |
| RMSE/MAE | Continuous rate forecasts | ✅ done, value-model RMSE above |
| Brier score | Probabilistic rate events | N/A — no probabilistic rate model exists |
| Calibration | Whether 80% intervals cover outcomes about 80% of the time | ✅ done, 88-91% — see above |
| Rank correlation | Whether leaderboards are useful | ✅ done, Spearman 0.68-0.72 (offense) — leaderboards are reasonably useful; 0.35-0.37 (defense) — much less so |
| Cohort error | Freshmen, transfers, low-minute players, high-usage players | Partial — see Required slices below |

### Value metrics

| Metric | Use | Status |
|---|---|---|
| RAPM target RMSE | Accuracy against Hoop Explorer `off_adj_rapm`, `def_adj_rapm`, and `adj_rapm_margin` | ✅ done for `off_adj_rapm`/`def_adj_rapm`; total value now follows Hoop Explorer's sign identity (`off_adj_rapm - def_adj_rapm`) and robustness-checks against `adj_rapm_prod_margin` |
| Impact rank correlation | Whether projected player value orders players similarly to RAPM-style labels | ✅ done — Spearman, see above |
| Team-level lift | Whether player values improve team AdjEM projection | ❌ not done — needs Team Rating Projection (Model 6), not built |
| Transfer holdout error | Performance on players changing teams | ✅ done — see cohort slice below (transfers: rmse=1.596, r²=0.370 vs. returning: rmse=1.763, r²=0.474 — transfers have lower error but the model explains less of their variance, a mixed signal worth a closer look, not yet investigated further) |
| Directional accuracy | Whether the model gets improvement/decline direction right | ❌ not done — needs a player-level season-over-season comparison, doesn't apply to a single rolling-origin test fold the way it's currently built |

### Required slices

| Slice | Status |
|---|---|
| Low-major to high-major transfers | ❌ not done — needs competition tier, which only exists in the Phase 2 branch's `player_projection_phase2.compute_level_tier`, out of scope here |
| High-major to mid-major transfers | ❌ not done — same reason |
| High-usage guards | Partial — guards evaluated as a slice (rmse=1.747, r²=0.424), not cross-cut with usage specifically |
| Low-minute upside players | ✅ done as "small_sample (<15 games)" — rmse=1.839, r²=0.127, n=17. Small n, high noise, exactly as expected — not a red flag, a sample-size reality |
| Bigs / rim protectors | ✅ done as "bigs (C/PF-ish)" — rmse=1.618, r²=0.500 (offense) |
| Elite shooters with small 3P samples | ❌ not done — not yet sliced this specifically |
| Returning players vs transfers | ✅ done — see Transfer holdout error above |

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

Join required Playing Time / Rotation outputs:

```text
expected_minutes
expected_minutes_share
expected_usage
usage_role
usage_role_confidence
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

Write destination rows directly to `player_projections` with `school_id` populated and
`projection_mode="destination"`. Do not write destination-adjusted player projections to
`predictions`.

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
- Neutral projections ship without destination-specific minutes. Production destination-adjusted rows are blocked until the Playing Time / Rotation model writes `playing_time_projections`; heuristic minutes are dry-run only.
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

4.  ✅ DONE. scripts/run_player_projection.py — Phase 0 and Phase 2a both
    have scriptable rerun paths via `--phase {0,2a,both}`. Phase 1 remains
    notebook-only validation by design — see §15 and §22.

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

**Reconciled against Issue #37 (2026-06-23):** [GitHub Issue #37](https://github.com/shanthg01/MIDS210-Capstone/issues/37), written after #36 (Phase 0/1) merged, is the authoritative scope document for the rest of Phase 2 — it supersedes the informal 2b-2e staging below in several concrete ways (most importantly: `player_archetypes` is explicit evaluation/explanation metadata only, never a model feature — see §6's correction above). The 2a work recorded below is reconciled against Issue #37's 8 scope items as real gaps (A-G), sequenced and tracked separately — see the implementation-time plan, not duplicated here. `scripts/validate_phase2_season_model.py` referenced below has been folded into the notebook and deleted as part of that reconciliation — do not look for it standalone going forward.

### 2a — Cross-season, block-aware state-space

Files: `src/portalpoint/modeling/player_projection_phase2.py`, `tests/test_player_projection_phase2.py` (7 tests, all passing). Validation now lives in `notebooks/models/player_projection_state_space.ipynb` (folded in from the original standalone script, since deleted).

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

### Issue #37 Reconciliation — Gaps A-G Progress (2026-06-24)

The informal 2b-2e staging above was superseded by reconciling 2a against Issue #37's 8 scope items, producing 7 concrete gaps (A-G — see the implementation-time plan, not duplicated here). Status:

| Gap | What | Code | Real-data applied |
|---|---|---|---|
| A | Shared-prior blending for the 2 validated blocks (creation, rebounding) only | ✅ done, tested | Not yet run on real `residual_df` |
| B | Observation-layer context adjustment (opponent `adj_d`/pace, competition tier, home/away) | ✅ done, tested | Not yet integrated into the pipeline |
| C | Real Stage 2A/2B possession-outcome + conditional rate model → `projected_rates`/`projected_box_score` | ❌ not started | — |
| D | Feed Phase 0's shrinkage prior into 2a's intra-season filter (`external_priors` on `player_projection_kalman.build_player_sequences`/`smooth_skill`), refit value heads | ✅ done, tested | ✅ real numbers in (see below) — Phase 2a beats Phase 0 on offense every fold |
| E | `player_archetypes` join for evaluation/explanation metadata + comparable-players helper — never a model feature, per Issue #37's explicit constraint | ✅ done, tested | Not yet run on real archetype data |
| F | Wire `skill_states`/`uncertainty`/`projected_rates` into actual `player_projections` writes (currently empty `{}` placeholders) | ❌ not started — Phase 2 has no write path to the DB at all yet | — |
| G | Point `player_projection_eval.py`'s rolling-origin CV at Gap D's output, add 2 new competition-tier transfer cohort slices, compare to Phase 0 | ✅ done, tested | ✅ real numbers in (see below) |

**Real performance findings while trying to get Gap D/G's real numbers (2026-06-24) — two distinct bottlenecks, found the hard way:**

1. **First attempt:** restarted the full notebook top-to-bottom with Gap D's prior-wiring change. Killed it after 3h44min (vs. the original 2a benchmark's ~2h15min) on the hypothesis that a per-row pandas column relookup inside the new `external_priors` lookup loop (`build_player_sequences` in `player_projection_kalman.py`) was the cause. Benchmarked the fix in isolation: **only ~5s of the 3h44min** — real bug (20x speedup on that one loop, kept the fix), but not remotely the explanation. Wrong diagnosis.
2. **Found the real bottleneck via a live `py-spy dump` of the actual stuck process** (not a guess) — the call stack showed execution in `fit_season_model`'s **Nelder-Mead search** (`player_projection_phase2.py`, the season-grain `rho`-fixed/drift-terms fit), not the intra-season Q-search that the first round of optimization had targeted. Nelder-Mead on 6 free dimensions needs far more function evaluations than the 1-D Brent search used for the intra-season Q fit, and each evaluation re-loops over every player's full multi-season sequence — a separate, bigger cost nobody had profiled before.
3. **Fix, same pattern as the (correct, but insufficient on its own) earlier optimization:** added population-subsampling to `fit_season_model`'s search (`max_sequences_for_search`, same `weight/(weight+k)`-style justification as `player_projection_kalman.fit_q_mle`'s subsampling — these are pooled population-level parameters, not per-player estimates, so a deterministic random subsample converges to essentially the same fit) and parallelized `fit_all_skills`'s 10-skill loop across a `ProcessPoolExecutor` (the skills are fully independent fits). Also flattened `build_season_skill_states`'s season x skill loop into one flat process pool (up to 70 independent tasks) instead of a nested seasons-loop-of-skills-loop, since nested process pools are fragile on their own. All changes tested against synthetic data first (11 new tests across `test_player_projection_kalman.py`/`test_player_projection_phase2.py`), full suite green (181 passing) before any real-data restart.
4. **Before committing to a third multi-hour real run, built a fast proxy:** added an optional `player_id_subset` parameter to `build_season_skill_states` (filters game logs + the Phase 0 prior lookup to a given player set before any fitting happens — not a change to the math, just a smaller population through the same unchanged pipeline) and ran the full `build_season_skill_states` → `build_season_covariates` → `fit_all_skills` → `compute_block_correlations` chain on a random 500-player sample across all 7 seasons. This is exactly what TODO item 4 below (written 2026-06-23, before any of this happened) had predicted should happen: *"validate on a smaller sample before committing to a multi-hour full run, rather than discovering problems after a 2+ hour fit."* The proxy ran in **157s total** (vs. the original ~2h15min for the intra-season fit alone) and its fitted params/block correlations matched the eventual full run closely (see below) — strong evidence the perf fixes don't distort the model, just speed it up. One real Windows-specific bug found while building the proxy: a standalone script using `ProcessPoolExecutor` needs an explicit `if __name__ == "__main__":` guard (Windows `spawn` re-imports the launching script in every worker, replaying all top-level code without it) — notebook/`ipykernel` execution doesn't need this (its own launcher is already guarded), but any future standalone script built on these functions (e.g. Gap F's eventual production script) will.
5. **A real, pre-existing bug surfaced on the next attempt** (unrelated to the performance work — just never executed against real data before): Gap G's notebook cell (`tune_hyperparameters` on `phase2_states`) crashed with `KeyError: 'games_played'`. `phase2_states` is Phase 2a's already-smoothed state frame (`skill_<x>` columns) and has no `games_played`/raw-rate columns at all — `shrink_skills()` can't run on it, but `tune_hyperparameters` called it unconditionally despite the cell's own comment already saying "Phase 2a states are already the skill — no shrink_skills call here." Fixed with a `skip_shrinkage: bool` parameter on `tune_hyperparameters`/`_fold_combined_val_rmse` (grid-searches only `alpha` when set, `k` is meaningless for an already-smoothed frame and is returned as `None`) — regression-tested in `test_player_projection_eval.py`.
6. **Real numbers, full population, all fixes applied (2026-06-24):** 33,540 season-states rows (~5,000 players × up to 7 seasons each), run completed clean, no errors, dramatically faster than either prior attempt (exact wall-clock not logged, but consistent with the 500-player proxy's 157s scaled up).

**Gap D/G real comparison — Phase 2a vs. Phase 0, rolling-origin held-out test, all 3 folds:**

| Fold (test season) | Off RMSE: 2a / P0 | Off R²: 2a / P0 | Off Spearman: 2a / P0 | Def RMSE: 2a / P0 | Def R²: 2a / P0 | Calibration: 2a / P0 |
|---|---|---|---|---|---|---|
| 1 (2024) | 1.483 / 1.560 | 0.531 / 0.481 | 0.760 / 0.717 | 1.473 / 1.458 | 0.090 / 0.107 | 0.904 / 0.907 |
| 2 (2025) | 1.547 / 1.642 | 0.506 / 0.445 | 0.720 / 0.674 | 1.546 / 1.544 | 0.101 / 0.108 | 0.899 / 0.891 |
| 3 (2026, headline) | 1.633 / 1.736 | 0.504 / 0.437 | 0.735 / 0.702 | 1.565 / 1.568 | 0.122 / 0.120 | 0.884 / 0.881 |

**Verdict (Issue #37's acceptance language: "beats Phase 0 on held-out validation or documents clearly where Phase 0 remains stronger"): Phase 2a beats Phase 0 on offense in every fold (~5-6% RMSE reduction, consistently higher R²/Spearman), and is essentially tied on defense** (both models cap out around R²~0.10-0.12 on defense — a known weak spot for both, not a 2a regression). Calibration ~88-90% both, slightly over-target (80%), consistent between models.

**Block correlations replicate closely across all three runs (original full-population run, 500-player proxy, this rerun)** — creation 0.20-0.35, rebounding 0.41-0.45, defensive playmaking ~-0.10, shooting touch weak/mixed every time. Stable, real signal, not sampling noise.

**New competition-tier transfer slices (Phase 0's evaluation structurally couldn't build these):**
- `low_major_to_high_major_transfers`: RMSE=1.578, R²=0.188, Spearman=0.666, n=156 — decent rank signal.
- `high_major_to_mid_major_transfers`: RMSE=1.389, R²=-0.082, Spearman=0.237, n=36 — weak, small sample, real limitation, reported as-is rather than hidden.

**`shooting_3p`'s rho-at-floor issue (TODO item 2 below) appears resolved**: this run's `rho=0.235` (not pinned at the 0.2 clip floor); `shooting_2p_finishing` (0.210) and `turnover_avoidance` (0.213) are now the ones sitting near the floor instead — consistent with the proxy run's pattern, suggesting this is genuine low-persistence signal for those specific skills rather than an artifact of any one run.

### Gap B/C real-data run (2026-06-24) — one real bug, one real bottleneck, one real (unresolved) finding

Wired Gap B (`use_context_adjustment=True`, Cell 14-1) and Gap C (Stage 2A/2B rate projections, new Cells 17-1/17-2) into the notebook together, per a deliberate decision to batch their first real-data validation into one run rather than two.

**Real bug #1 — `BrokenProcessPool` from a memory blowup, not a multiprocessing setup error.** First attempt crashed inside `build_season_skill_states`. Root cause: building all 70 `(season, skill)` tasks *eagerly* into a list before submitting any of them to the `ProcessPoolExecutor`, where each task carried a *full* `obs_df.copy()` (every skill's columns + raw game-log columns + the new context columns, ~67MB per copy with Gap B active) — ~4.7GB of redundant data resident in the main process before a single task was even submitted, on top of per-worker deserialized copies. Almost certainly an OS-level OOM kill, not a clean Python exception (matches the generic `BrokenProcessPool` message, which gives no further detail). Fixed by slimming each task to exactly the 3 columns `ppk.smooth_skill`/`build_player_sequences` actually reads (`player_id`, `y_<skill>`, `weight_<skill>`) instead of carrying the whole frame — verified via the 500-player proxy (too small to have hit the OOM threshold, which is why the proxy never caught this) and the full real run, both producing identical/near-identical fitted params before vs. after the slimming fix (confirms it only changed what's serialized, not the computation).

**Real bug #2 — Gap C's attempt-rate targets had an unusable `resid_std` (~227,000).** `build_attempt_rate_targets` divided by `total_minutes.clip(lower=1e-6)` — correct to avoid a literal zero-division, but a garbage-time player with a few seconds of minutes and even one attempt produces an astronomical per-40 rate (1 attempt / 1e-6 minutes × 40 ≈ 40 million), which dominates the Ridge fit's residual variance entirely. Fixed via `MIN_MINUTES_FOR_RATE_TARGET = 40.0` — drop low-minute rows outright (matching Phase 0's own `MIN_GAMES`-floor convention) instead of letting a near-zero denominator through. Regression-tested in `test_player_projection_phase2.py`. **Re-verified against real data (2026-06-24, same day):** `resid_std` dropped from ~227,000 to 1.95/2.22/1.49 (2PA/3PA/FT-trip) — sane scale; Stage 2A's training frame correctly shrank from 33,542 to 26,817 rows (low-minute players dropped as intended). Gap B's fold 3 numbers in this rerun matched the prior run to 6 decimal places (off_rmse=1.987044 both times) — confirms the fix only touched Gap C, nothing else moved.

**Real, currently unresolved finding — Gap B's context adjustment makes Phase 2a *worse*, not better, on real data:**

| | Off RMSE (fold 3) | Off R² | Def RMSE | Def R² |
|---|---|---|---|---|
| Phase 0 | 1.736 | 0.437 | 1.568 | 0.120 |
| Phase 2a (no context, prior run) | 1.633 | 0.504 | 1.565 | 0.122 |
| Phase 2a (Gap B context, this run) | **1.987** | **0.266** | **1.622** | **0.057** |

Context adjustment substantially hurts both offense and defense — worse than even Phase 0's baseline. Hypothesis, not yet confirmed: regressing out opponent-strength/pace/tier effects from the raw observations may be removing real, informative variance (a player's efficiency against good defenses may itself carry value-relevant signal), not just noise — i.e., the "context-neutral observation" assumption from the plan doc's §7 form may not hold for this label. **Flagged for follow-up, not resolved here** (explicit user decision, 2026-06-24: continue with other Phase 2 work for now rather than block on debugging this immediately). Concrete next steps when picked back up: (a) check whether `fit_context_adjustment`'s regression is itself well-specified (e.g., is it overfitting on a small per-skill sample, given it refits separately per skill per season); (b) compare context-adjusted vs. unadjusted *residual* correlations with the value-model targets directly, rather than only the end-to-end RMSE; (c) consider whether context-adjustment should apply only to a subset of skills (e.g., shooting efficiency, where opponent defense plausibly matters) rather than uniformly to all 10.

**Net effect on the recommended real-data configuration**: until Gap B's regression is understood, **`use_context_adjustment=False` (Gap D/G's already-recorded numbers above) remains the best real result**, not the context-adjusted run recorded in this section. Production-integration decisions (item 8/9) should reference the no-context numbers, not this run's.

### Gap F — Real Write to `player_projections` (2026-06-24)

Added `pp2.build_phase2_records()`/`pp2.MODEL_VERSION_PHASE2A` and a new notebook Cell 18-1, reusing `pp.upsert_neutral_projections` as-is (it's generic on `model_version`, no new SQL needed). Two real bugs found getting the write to succeed, both fixed and verified:
1. A `CardinalityViolation` (`ON CONFLICT DO UPDATE command cannot affect row a second time`) — `phase2_states` had a small number of duplicate `(player_id, season)` rows (33,540 season-states rows → 33,542 phase2_states rows) from a join fan-out in Cell 15-1, never fatal for fitting/eval but fatal for a uniqueness-constrained write. Fixed with `drop_duplicates(subset=["player_id","season"])`, the same defensive pattern Phase 0's own Cell 2 already uses after its HE left-join.
2. After that fix: **33,540 rows successfully upserted** under `model_version="player-projection-phase2a-v1"` (distinct from Phase 0's `"player-projection-shrinkage-v1"` — the partial unique index on `(player_id, season, model_version) WHERE school_id IS NULL` means these can never collide). The next rerun writes the defensive-sign-fixed generation as `player-projection-phase2a-v2` / `player-projection-shrinkage-v2`. Spot-checked real, non-empty `projected_rates`/`projected_box_score` — sane per-40 box-score lines (e.g. pts_per_40 ~20-30, ast ~2-6, reb ~3-7).

**Caveat carried over from Gap B**: this write used the still-active `use_context_adjustment=True` run (the regression). Re-write (same model_version, safe to rerun) once Gap B is resolved or once the no-context configuration is re-run for any other reason.

**Update, same day — Gap F's write redone with the recommended (no-context) configuration**, bundled with Part 1's `foul_discipline` rollout below (one real run covers both). 33,540 rows re-upserted under the same `model_version`, now sourced from the no-context states.

### Player Projection — `foul_discipline` Skill + Offense/Defense Feature-Set Split (2026-06-24, new user-initiated work)

Two follow-on ideas raised mid-session: (1) confirm all offensive/defensive metrics in `hoopr_player_game_logs` are used as features, (2) split the value-translation model so `off_adj_rapm` is predicted from offense-only skills and `def_adj_rapm` from defense-only skills (shared skills like position in both). Full design in the approved plan (`C:\Users\shant\.claude\plans\cosmic-waddling-deer.md` at plan time; now executing).

**Part 1 — `foul_discipline` (11th skill), done and validated against real data.** `hoopr_player_game_logs.fouls` existed at game grain and was never wired in (`points`/`rebounds`-total are redundant with already-used components, intentionally not added). Added to `player_projection_kalman.RATE_PER_40_SKILLS` (inverted, same convention as `turnover_avoidance`) and `player_projection.INVERTED_SKILLS`. Phase 0 stays at 10 skills (no season-grain fouls column in `player_season_stats`); Phase 1/2 move to 11 — an intentional, documented asymmetry.

Two real bugs surfaced getting this to run clean, both exactly the same root cause (code that hardcoded "the skill list" instead of taking one as a parameter, written before a Phase-0-vs-Phase-1/2 skill-count asymmetry existed):
1. Notebook Cell 12 (Phase 1-vs-Phase-0 calibration correlation) iterated `ppk.SKILLS` (11) against a `phase0_season_df` that only ever has 10 — `KeyError` on `skill_foul_discipline_phase0`. Fixed by restricting that one comparison-specific cell to skills present in both (`[s for s in ppk.SKILLS if s in pp.SKILLS]`) — `foul_discipline` structurally has no Phase 0 counterpart to compare against, this isn't fixable any other way.
2. `player_projection.skill_percentiles()` hardcoded the module-level `SKILLS` (10) internally regardless of what the input frame actually had — Phase 2a's `phase2_states` (11 skills) silently never got a `pctile_foul_discipline` column, and `build_phase2_records` (Gap F) then `KeyError`'d looking for it. Fixed by adding a `skills` parameter (default `SKILLS`, backward compatible), notebook's Gap F write cell now passes `skills=ppk.SKILLS` explicitly.

**Real-data result, fold 3 (no-context configuration, matching the established baseline exactly):** `foul_discipline` fits cleanly — `rho=0.389`, `Q=0.813`, none of beta_0-4 or Q at any bound, magnitude in line with the other count-rate skills. **Gap D/G's off/def RMSE/R² barely moved** (off_rmse 1.640 vs. the established 1.633; off_r2 0.500 vs. 0.504; def_rmse 1.568 vs. 1.565; def_r2 0.119 vs. 0.122) — expected and correct, not a red flag: `player_projection.build_design_matrix` still hardcodes Phase 0's 10-skill list as the value-model's feature set, so `foul_discipline` is now correctly fitted/smoothed/written to `skill_states` (verified via Gap F's rewrite, still 33,540 rows, same model_version) but doesn't yet *influence* `off_adj_rapm`/`def_adj_rapm` predictions. **That's exactly Part 2's job, next.**

**Part 2 — offense/defense feature-set split — done (2026-06-24), real tradeoff found and accepted.** Skill classification: Offense = `{shooting_3p, shooting_2p_finishing, free_throw_touch, shot_creation_usage, passing_creation, turnover_avoidance, offensive_rebounding}`; Defense = `{defensive_rebounding, steal_disruption, block_rim_protection, foul_discipline}`; Shared = position dummies only.

**Simplified from the approved plan during implementation** (lower-risk, same outcome): rather than changing `fit_value_model`'s return to a 3-tuple and adding `off_skills`/`def_skills` params to `project_value`, the target string (`"off_adj_rapm"`/`"def_adj_rapm"`) already disambiguates which feature set to use at every real call site — so `fit_value_model`/`project_value` keep their original signatures unchanged, and internally call the now-generalized `build_design_matrix(df, skills=...)` with the right list. Zero call sites needed signature changes; only `save_artifacts` (two feature-column lists instead of one), the MLflow pyfunc wrapper (`scripts/run_player_projection.py` + notebook Cell 7, both build `X_off`/`X_def` separately now), and notebook Cell 4's coefficient report (two separate tables, since off/def coefficient vectors are now different lengths) needed updating.

**Real bug found implementing this:** `DEFENSE_SKILLS` includes `foul_discipline`, but Phase 0 frames structurally never have a `skill_foul_discipline` column — `build_design_matrix` selected `df[skill_cols]` directly, so it `KeyError`'d on every Phase 0 `def_adj_rapm` fit. Fixed by zero-padding any requested skill column missing from the input frame (via the existing `reindex(..., fill_value=0.0)`, just no longer requiring the column to exist before that point) — confirmed on real data: Phase 0's def_model fits `foul_discipline`'s coefficient to exactly `0.000` (real but inert — no information, no effect on predictions), while Phase 2a's def_model gets the real column.

**Real-data result, fold 3 (no-context, with `foul_discipline`):**

| | Off RMSE / R² | Def RMSE / R² |
|---|---|---|
| Before split (shared 10-skill feature set) | 1.640 / 0.500 | 1.568 / 0.119 |
| After split (offense/defense-only features) | 1.641 / 0.499 | **1.600 / 0.083** |

Offense barely moved. **Defense measurably worse — R² dropped ~30% relative (0.119→0.083)**, and this held for *both* Phase 0 and Phase 2a (not a Phase-2a-specific artifact) — removing offensive skills from the defense model cost real predictive power, plausibly because skills like `shot_creation_usage`/`passing_creation` proxy for a "two-way IQ/effort" signal that also predicts `def_adj_rapm` despite not being literally defensive actions. **Decision (user, 2026-06-24): keep the split anyway** — the interpretability gain (each target regressed only on features that are actually offense or defense) is the explicit point of this change; the accuracy cost is real but accepted, not hidden. Gap F rewritten with this configuration (33,540 rows, same `model_version`).

### Script Consolidation + Production-Integration Decision (2026-06-25)

User asked two related questions: why does Phase 2 have no production script (only the notebook), and can item 8/9's production-integration decision finally be made rather than left open. Also asked to fold the two remaining never-applied-to-real-data gaps (A, E) into whatever this work produced.

**Why Phase 0 and Phase 2a need separate run functions, not one shared one:** they're structurally different models, not parameter variants. Phase 0 is season-aggregated input → one-step empirical-Bayes shrinkage → Ridge, seconds to run. Phase 2a is game-level input → two-layer Kalman (intra-season filter, then season-grain persistence/drift) → Gap C's rate decomposition → Ridge on the resulting states, ~10 minutes. They only converge at the very last step (fit value model → project → build records → upsert).

**`scripts/run_player_projection.py` now has `run_phase0()` / `run_phase2a()` + a `--phase {0,2a,both}` arg (default `both`)** — no new script, per the established "no script proliferation" convention. `run_phase2a()` is the first place Gap A and Gap E have ever run against real data:
- **Gap A (shared-prior blending), applied for real**: after `fit_all_skills`, the 5 skills in the 2 validated blocks (creation, rebounding) get their `_blended` estimate substituted in place of the raw one, *before* Gap C's attempt-rate fit or either value model sees them — not just an extra diagnostic column nobody reads, as it was before.
- **Gap E (archetype metadata), applied for real**: queries real `player_archetypes` (18,770 rows for 2020-2026), joins via `join_archetype_metadata`'s pattern, and adds `archetype_label`/`archetype_confidence` into `build_phase2_records`'s `explanation` JSON only — confirmed it never touches `skill_states` or either design matrix (regression-tested).
- **New: real MLflow tracking for Phase 2a** — registers under the same `"player-projection"` model name Phase 0 uses, going through the same `maybe_promote` 5% gate every other model in this codebase already uses. This didn't exist at all before this run — Gap F's original write (Cell 18-1) had zero MLflow tracking.

**Real bug found on the first run of the new MLflow tracking, fixed before trusting any result:** the script's first attempt logged Phase 2a's gate metric as `fold3_combined_rmse` (Gap G's real held-out metric) — but Phase 0's current Production run (`v1`) never logged a metric by that name (it predates Gap G's eval tooling). `maybe_promote` treats a missing comparison metric as `0.0`, and its `delta = inf` fallback for that case fired, producing a nonsensical **`Δ=+inf%` false auto-promotion** of Phase 2a to Production. Caught before reporting any result as real. Fixed by gating on `total_resid_std` — the same metric name Phase 0's runs actually log — while still logging the real held-out fold-3 metrics (`fold3_combined_rmse`/`off_rmse`/`def_rmse`) for visibility, just not as the automatic gate's comparison. Manually reverted the bad promotion (`v25` → Staging, `v1` restored to Production) before re-running.

**Real result, in-sample `total_resid_std` gate (the same metric/threshold every other model in this codebase is held to), after the fix:**
- Phase 0 rerun: `v27 → Staging (Δ=-0.7%)` — within noise of the existing Production baseline, as expected for a rerun on the same data/algorithm.
- Phase 2a (Gap A/E applied, real archetypes, fixed gate): `v26/v28 → Staging (Δ=+1.0%)` — real, small, **does not clear the 5% auto-promote threshold**.

**Decision: do not flip the API's default to Phase 2a.** `players.py`'s `GET /{id}/projection` filters on a hardcoded `model_version` constant — there's no query param and no connection to MLflow's registry stage, so this is the actual, separate lever (MLflow promotion governs/records; this constant is what users actually see). The ~1% in-sample improvement (and the ~2.9% held-out combined-RMSE improvement recorded alongside it, still real but also short of 5%) don't clear the same bar every other promotion in this codebase has had to clear. Both phases now write to `player_projections` under their own `model_version` whenever the script runs — Phase 2a's real numbers stay live and trackable in MLflow (`"player-projection"`, Staging) for whenever Gap B's resolution or any other change pushes the number further, or if the team wants to explicitly override the quantitative gate (a distinct, legitimate decision from "the gate says yes").

`--phase both` run end-to-end with no cross-talk: Phase 0 (27,047 rows) then Phase 2a (33,540 rows), each correctly compared against the same `v1` Production baseline independently.

### Follow-up verification pass + infra/process fixes (2026-06-25, same day)

A second verification pass (checking the actual code/git state, not memory) found two small real gaps and one known accepted divergence:
- `run_phase2a()` had no `off_adj_rapm_prod`/`adj_rapm_prod_margin` robustness check (Issue #37 item 4 asks for one; Phase 0's `run_phase0()` already had one).
- The script's Gap G metric only computed RMSE, not calibration (the notebook's Gap G section always had it).
- The notebook's Cells 14-18 didn't apply Gap A/E to real data — only the script did (already flagged as an optional follow-up in the approved plan).

All three fixed:
- **Robustness check + calibration logging added to `run_phase2a()`** — same pattern as Phase 0's, now logged as real MLflow metrics (`fold3_calibration_80pct_target`). Verified on a real run: `off_value_per_100` vs. `off_adj_rapm_prod` corr=0.667 (n=16,019, comparable to Phase 0's own ~0.64), calibration=0.875 (consistent with the notebook's own prior ~0.88).
- **Gap A/E folded into the notebook** (new Cell 14-4 substitutes the blended estimates; Cell 18-1 queries real archetypes) — notebook and script now match.

**Separately, also migrated `mlflow_helpers.maybe_promote` off MLflow's deprecated stages API** (real `FutureWarning`s seen on every run this session, not hypothetical — `get_latest_versions`/`transition_model_version_stage` are deprecated since MLflow 2.9, stages are being removed in a future major release) to the alias-based API (`get_model_version_by_alias`/`set_registered_model_alias`, alias `"champion"`). Backfilled the `champion` alias onto the current Production version for **every** registered model in this MLflow instance (`player-projection`, `gap-matching-scorer`, `player-clustering`, `scheme-fit-scorer`, `team-clustering`) — not just this one — since they all share `maybe_promote`, and without the backfill the *next* rerun of any of them would have seen "no champion" and auto-promoted regardless of merit (the same bug class just fixed for Phase 2a's gate, but latent for every other model too). Verified on a real run: identical `Δ=+1.0%` result as before the migration, now reported as `"stays below @champion"` instead of stage language — confirms the migration changed only the API surface, not the comparison logic.

**Two infra/process follow-ups explicitly deferred, not built** (no prerequisite infrastructure exists for either, confirmed by search — building either now would mean fabricating unused scaffolding):
- Wiring `--phase both` into a weekly Airflow cadence — no Airflow DAGs exist anywhere in this repo yet, for any model.
- Feature-drift/accuracy-decay monitoring — no Prometheus/Grafana exists yet. Noted as a TODO to build for **all models**, not just this one, once real monitoring infra exists (or as a lighter MLflow-history-only pilot, if picked up before then).

At this point in the implementation log, 195 tests were passing. The current
forecast-ready branch later passes 199 tests; see the status docs above.

### TODO before resuming Phase 2

1. ✅ **Done.** Moved `scripts/validate_phase2_season_model.py` into the notebook, matching Phase 1's precedent exactly (`validate_phase1_kalman.py` was folded in and deleted the same way). The script no longer exists standalone.
2. ✅ **Done, 2026-06-24.** `shooting_3p`'s rho is no longer at the clip floor (now 0.235, vs. the original run's 0.200-floor-pin) — see the Gap D/G real-results paragraph above.
3. Investigate the shooting-touch block's weak correlation result specifically before relying on cross-skill prior-blending for those 3 skills.
4. ✅ **Followed, 2026-06-24.** "Validate on a smaller sample before committing to a multi-hour full run" — see the proxy-run paragraph immediately above. 2b-2e's informal staging is superseded by Gaps A-G (table above); C and F remain unstarted, B is coded but not pipeline-integrated.
5. Phase 2a's code (`player_projection_phase2.py`, its tests, the validation script) is implemented and passing but **not yet committed** — exists only in the working tree as of this pause point. This is now also true of all Gap A/B/D/E/G code from 2026-06-24.

### Teammate review on Issue #37 (2026-06-25) — 3 real fixes, all verified against real data

A teammate's comment on the issue flagged 5 items. Verified each against the actual code/DB rather than taking the claims at face value:

1. **`scripts/seed_test_data.py`'s `player_projections` seed row going stale — confirmed real, fixed.** The seed row's `expires_at = now() + 30 days` is set once, at seed time, and the insert was `ON CONFLICT DO NOTHING` — on any dev DB older than 30 days, `/api/players/{id}/projection`'s `expires_at > now()` filter would silently start failing the projection tests, no code change required to trigger it. Confirmed directly: player_id 101 (the seed fixture) turns out to be a real ingested player (Noah Waterman) with real season stats — this session's own real pipeline runs kept re-upserting (and refreshing) that exact row via `DO UPDATE`, which is *why* the local suite stayed green throughout this whole session despite the underlying seed-script bug being real. Fixed: the `player_projections` insert now does `DO UPDATE SET expires_at = EXCLUDED.expires_at, computed_at = now()` on conflict, matching the real production upsert's own behavior. Verified by hand: re-running the seed script after this fix visibly bumps `expires_at` forward.
2. **Cache filenames missing the `seasons` list — confirmed real by reading the code, fixed.** `load_or_build_season_skill_states`'s cache filename only varied by prior/context suffix; `load_or_build_season_covariates`'s was a hardcoded constant (`"covariates.parquet"`) with *no* variation at all. Either would silently return a stale cache built for a different season range. Both now include a `seasons` (or covariates' own season range) component in the filename.
3. **`season_rank`-only join key — confirmed as a real architectural risk (not a confirmed-active bug), fixed properly.** `build_season_sequences` already had the real `season` value in scope per row but discarded it, and `smooth_season_skill` independently re-derived a synthetic positional `season_rank` from scratch. Two separately-computed positional indices being assumed to align by construction (across skills with potentially different per-season `dropna` behavior, and across the season-grain vs. covariates computations) was fragile. Fixed: `SeasonSequence` now carries the real `season` array through; `fit_all_skills` merges its per-skill frames on `(player_id, season)` instead of `season_rank`; Cell 15-1's season_rank↔`career_season_index` reconstruction is gone entirely (no longer needed — `residual_df` already has real `season`). **Real-data result: `phase2_states` is now exactly 33,540 rows, matching `season_states` exactly** — the 33,540→33,542 duplicate-row fan-out that needed the `drop_duplicates` band-aid (see the Gap F section above) is gone, confirming it was a real symptom of this exact issue. Gap D/G's fold-3 numbers came out essentially unchanged (off_rmse 1.641 vs. 1.641, def_rmse 1.600 vs. 1.600) — `season_rank` and `season` were correctly aligned for this specific dataset all along, so this is a pure robustness fix, not one that silently changes results.
4. **"Gap B is TBD, not rejected"** — fair wording correction, adopted. The no-context configuration remains the *reference* result for any current comparison, not a final verdict on context-adjustment — it stays a real, open, root-cause-pending question.
5. **Production integration stays an explicit decision after the Gap B question is settled** — already this doc's stance, no change needed.

At this point in the implementation log, 194 tests were passing (no new test
count change — fixes landed inside already-existing test coverage plus two
newly-caught unpacking-site fixes in `test_player_projection_phase2.py`'s own
synthetic-sequence helpers). The current forecast-ready branch later passes
199 tests; see the status docs above.
