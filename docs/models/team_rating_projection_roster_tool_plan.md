# Team Rating Projection Model Plan
## Roster-Based Counterfactual Team Projection System

**Planned notebook:** `notebooks/models/team_rating_projection_roster_tool.ipynb`  
**Model family:** Roster simulation + interpretable team efficiency translation  
**Primary output table:** `team_rating_projections`  
**Upstream dependency:** Player Projection system, Playing Time / Rotation model  
**Downstream consumers:** `/api/projections/team-rating`, Fit Score page, Compare page, Recommendation Engine  
**Related plan:** `docs/models/playing_time_rotation_model_plan.md`

---

## 1. Objective

PortalPoint's team projection should not be a one-row model that asks, "How many AdjEM points does this player add?" in isolation.

It should be a roster tool:

```text
Given a current roster and a candidate transfer,
simulate how the rotation, usage, style, offense, defense, and uncertainty change.
```

The core output remains API-compatible:

```text
current_adjEM
projected_adjEM
delta_adjEM
confidence_interval
national_percentile
conference_rank
expected_minutes_input
```

But the internal model should explain the delta as a counterfactual roster change, not a generic player bump.

The preferred team rating target should stay consistent with the player projection plan: if we can ingest enough historical Hoop Explorer data, train and validate against Hoop Explorer team adjusted efficiency fields first. BartTorvik adjusted efficiency should remain the fallback and compatibility target because it is already part of PortalPoint's existing data model and product language.

---

## 2. Core Insight

Adding a transfer is not additive in a simple way.

A new player affects:

- Which returning player loses minutes.
- Who handles the ball.
- How efficient teammates become with more or less usage.
- Team spacing, rim pressure, rebounding, turnover rate, and rim protection.
- Defensive lineup structure and position coverage.
- Scheme fit and pace.
- Uncertainty around players stepping into new roles.

Therefore, the model should compare two roster states:

```text
Baseline roster projection
vs.
Roster projection with candidate player added
```

The projected team impact is:

```text
delta_adjEM = projected_adjEM_with_candidate - baseline_projected_adjEM
```

---

## 3. Relationship to Player Projections

The team projection model consumes player-level posterior outputs:

```text
projected_offensive_impact_per_100
projected_defensive_impact_per_100
projected_total_impact_per_100
projected_usage
projected_box_score
skill_percentiles
uncertainty
```

These values come from the Player Projection system. For MVP, they should be calibrated to Hoop Explorer adjusted RAPM-style labels where available:

```text
off_adj_rapm
def_adj_rapm
adj_rapm_margin
```

The team projection also consumes opportunity outputs from the Playing Time / Rotation model:

```text
expected_minutes
usage_role
displaced_minutes
minutes_uncertainty
```

This is not the same thing as the user-facing Role Fit score. Role Fit should be treated as a product score derived from the Playing Time / Rotation model. The team projection needs the underlying minutes, slot, usage, and displacement estimates, not just the 0-100 score.

Starter probability and rotation slot may be derived for the UI if useful, but they are not required model inputs for MVP. Expected minutes plus usage role usually communicate the same basketball idea with less fragile labeling.

The team model should use posterior samples when available:

```text
for sample in player_projection_draws:
    build rotation
    translate roster to team offensive rating / defensive rating / net rating
    store projected team result
```

This produces a real confidence interval around team impact.

MVP fallback: consume only player projection means and approximate uncertainty from player confidence scores.

The same architecture should power interactive roster scenarios. A coach can keep a player's talent projection fixed, then adjust minutes, usage role, or displaced minutes to see the domino effect on team ratings:

```text
player talent projection
    + scenario minutes / usage role / displacement
    -> updated roster state
    -> updated offense, defense, and net rating
```

---

## 4. Roster State Representation

Represent each school-season as a projected rotation, not just aggregate roster talent.

### Rotation slots

Start with 10 standard slots:

| Slot | Position band | Typical minutes | Notes |
|---|---|---:|---|
| PG1 | Guard | 28-34 | Primary initiator |
| G2 | Guard | 24-32 | Secondary handler/shooter |
| W1 | Wing | 24-32 | Main two-way wing |
| F1 | Forward | 22-30 | Frontcourt scorer/defender |
| C1 | Big | 20-28 | Primary rim/rebound role |
| G3 | Guard | 14-24 | Bench handler/shooter |
| W2 | Wing | 14-24 | Bench wing |
| F2 | Forward | 10-20 | Bench forward |
| C2 | Big | 8-18 | Backup big |
| FLEX | Any | 0-16 | Extra rotation minutes |

Each slot stores:

```text
player_id
position
minutes
usage
projected_offensive_impact_per_100
projected_defensive_impact_per_100
projected_total_impact_per_100
skill_vector
uncertainty
```

---

## 5. Baseline Roster Concept

For incomplete rosters, we need replacement assumptions. The baseline should answer:

```text
If this school does not add this candidate,
what kind of player probably fills the available slot?
```

### Average slot baseline

Build average player projections by:

- Roster slot
- Position band
- Conference tier
- Team quality tier
- Team system/style cluster
- Returning minutes tier

Example:

```text
baseline_slot_value["Mid-major", "Top-100", "G3"]
= average projected impact, usage, shooting, defense, uncertainty
```

This prevents every transfer from being compared against a zero-minute vacuum. The right counterfactual is usually "candidate vs likely replacement slot", not "candidate vs nobody".

---

## 6. Candidate Add / Swap Logic

For a candidate `p` and school `s`:

### Step 1 - Build baseline roster

```text
returning players
- known departures
+ baseline replacement slots for open minutes
```

### Step 2 - Insert candidate

Estimate candidate slot and minutes using:

- Position fit
- Gap match
- Projected offensive/defensive impact
- Returning roster depth
- School quality tier
- Candidate prior minutes
- Scheme fit

### Step 3 - Redistribute minutes

Constraints:

```text
sum(minutes) = 200 per game
0 <= player_minutes <= realistic cap
position coverage remains plausible
usage shares sum to team offensive possessions
```

The candidate should displace the weakest/most redundant minutes in his position band first, then pull FLEX minutes if needed.

### Step 4 - Reallocate usage

Usage is not fixed. Adding a high-usage guard reduces usage for teammates. Adding a low-usage rim protector may barely affect offensive usage but can affect defensive projection and rebounding.

---

## 7. Team Translation Model

Convert roster state into team ratings:

```text
Roster State -> projected offensive rating, defensive rating, tempo, net rating
```

Preferred target names depend on the rating source:

| Rating source | Offense | Defense | Net | Tempo |
|---|---|---|---|---|
| Hoop Explorer | `off_adj_ppp` | `def_adj_ppp` | `adj_net` | `tempo` |
| BartTorvik fallback | `AdjO` / `adj_o` | `AdjD` / `adj_d` | `AdjEM` / `adj_em` | `AdjTempo` / `adj_tempo` |

Use Hoop Explorer as the primary target when enough multi-season coverage is ingested. Use BartTorvik as the fallback target and as a compatibility bridge for the existing API, database columns, and coach-facing adjusted efficiency language.

### Team features

| Feature group | Examples |
|---|---|
| Weighted player value | minute-weighted offensive, defensive, and total impact per 100 |
| Slot strength | PG1 offensive creation, C1 defensive impact, bench impact, weakest starter impact |
| Skill coverage | 3PT shooting, playmaking, rim pressure, rebounding, rim protection |
| Balance | spacing without turnovers, defense without fouling, usage concentration |
| Continuity | returning minutes, returning impact, same-coach/system if available |
| Style | projected tempo, 3PT rate, rim rate, assist rate |
| Uncertainty | weighted player uncertainty, unknown slot share |

### Model recommendation

Use an interpretable two-stage model:

```text
Stage A: Roster strength model
minute-weighted player values + slot baselines
    -> baseline team offense and defense

Stage B: Interaction adjustment
spacing + usage concentration + rim protection + rebounding + continuity
    -> offensive/defensive adjustment
```

Recommended MVP estimator:

- Ridge/elastic-net regression or Bayesian additive regression for offense and defense separately.
- Separate offense and defense models.
- Monotonic constraints where possible: better weighted impact should not lower projected net rating unless explained by role/fit interactions.
- Gradient boosting only as a challenger model, not the default, unless the interpretability gap is solved.

---

## 8. Counterfactual Decomposition

Every `delta_adjEM` should decompose into coach-readable pieces:

```text
delta_adjEM
= candidate_offensive_impact_delta
  + candidate_defensive_impact_delta
  + replacement_slot_delta
  + usage_reallocation_delta
  + spacing_style_delta
  + rim_protection_rebounding_delta
  + continuity_uncertainty_penalty
```

### Example explanation payload

```json
{
  "candidate_offensive_impact_delta": 0.8,
  "candidate_defensive_impact_delta": 0.6,
  "replacement_slot_delta": 0.7,
  "usage_reallocation_delta": -0.2,
  "spacing_style_delta": 0.3,
  "rim_protection_rebounding_delta": 0.5,
  "uncertainty_penalty": -0.1,
  "minutes_displaced": [
    {"slot": "G3", "minutes": -10.0},
    {"slot": "FLEX", "minutes": -6.0}
  ],
  "candidate_usage_role": "secondary_creator",
  "candidate_minutes": 24.0
}
```

This is more useful than saying "XGBoost says +2.6".

---

## 9. Training Labels

Preferred primary labels after historical Hoop Explorer team ingest:

```text
hoop_explorer.off_adj_ppp
hoop_explorer.def_adj_ppp
hoop_explorer.adj_net
hoop_explorer.tempo
```

Fallback / compatibility labels:

```text
team_season_stats.adj_o
team_season_stats.adj_d
team_season_stats.adj_em
team_season_stats.adj_tempo
```

Hoop Explorer should be preferred because the player projection value layer is also calibrated to Hoop Explorer RAPM-style player impact. That creates a cleaner bridge:

```text
player adjusted RAPM-style impact
    -> minute-weighted roster impact
    -> Hoop Explorer adjusted team offense/defense/net
```

BartTorvik remains valuable because it provides a broad adjusted efficiency ecosystem, existing PortalPoint schema compatibility, and a public comparison point coaches already recognize.

Training rows:

```text
school_id, season
projected preseason roster state built only from data available before season
actual end-of-season adjusted team ratings
```

Important: avoid leakage. For each historical season, build the roster projection using only prior seasons, known transfers, and preseason roster assumptions.

Historical transfer events should use BartTorvik historical transfer portal pages as the primary source where available. For training and validation, also infer transfer events from player-team-season histories where stable IDs are available:

```text
player primary team in season Y
!= player primary team in season Y+1
```

**Superseded (2026-06-21):** BartTorvik's transfer JSON would have been the cleanest source (exact `barttorvik_id` join, no fuzzy matching) but its `robots.txt` disallows `/*.json` and `/playerstat.php` — see `playing_time_rotation_model_plan.md` §4/§7 for the full finding. **247Sports is the actual current/historical transfer-event source** (`scripts/ingest_transfers_247sports.py` → `transfers`/`transfer_portal_events`, season 2026 done, 2020-2026 backfill documented in `ARCHITECTURE_STATUS.md` but not yet run). Use inferred player-team histories as a backfill and validation layer. Where Hoop Explorer `transfer_src` / `transfer_dest` exists, use it as an enrichment and cross-check.

---

## 10. Scenario Modes

The model should support multiple use cases:

| Mode | Question | Output |
|---|---|---|
| Add | What if we add this player to our open roster? | Delta vs baseline replacement |
| Swap | What if this player replaces a specific player/slot? | Delta vs named displacement |
| Remove | What if this current player leaves? | Roster loss estimate |
| Build roster | How strong is this complete custom roster? | Projected AdjEM/rank |
| Compare candidates | Which player improves us most? | Delta table with explanation |
| Role tuning | What if this player is a 1st, 2nd, 3rd option, connector, or specialist? | Team rating under each usage-role assumption |
| Slider scenario | What if a coach manually changes minutes, usage, or displaced players? | Live dashboard counterfactual |

MVP API can start with Add mode only.

The interactive dashboard should distinguish the model recommendation from coach-adjusted assumptions:

```text
base projection
vs.
scenario projection
```

This is useful for cases like a high-usage mid-major guard transferring up. The model can show whether the player is more valuable as a scaled-down secondary creator than as a high-usage primary option, and how that choice affects teammates' usage and the overall team rating.

---

## 11. Notebook Structure

### Cell 0 - Setup

```python
MODEL_VERSION = "team-roster-proj-v1"
TRAIN_SEASONS = range(2020, 2026)
PROJECTION_SEASON = 2026
ROTATION_SLOTS = [...]
```

### Cell 1 - Load Player Projections

Load projected offensive impact per 100, defensive impact per 100, total impact per 100, box rates, usage, and uncertainty.

Load Playing Time / Rotation outputs:

```text
expected_minutes
usage_role
displaced_minutes
minutes_uncertainty
```

### Cell 2 - Build Historical Roster States

For each school-season, reconstruct projected roster using only pre-season-available data.

### Cell 3 - Build Slot Baselines

Compute average slot projections by tier/style/position. Save to artifact for use when rosters are incomplete.

### Cell 4 - Rotation Builder

Assign players to slots and minutes. Validate that team minutes sum to 200 and position constraints are plausible.

### Cell 5 - Train Team Translation Models

Train offense and defense models separately:

```text
roster_features -> off_adj_ppp or adj_o
roster_features -> def_adj_ppp or adj_d
net = offense - defense
```

### Cell 6 - Validate

Temporal validation:

```text
train through season N
predict season N+1
evaluate AdjEM RMSE, rank correlation, and calibration
```

### Cell 7 - Candidate Counterfactuals

For each player-school pair:

```text
baseline roster
candidate roster
delta = candidate - baseline
```

### Cell 8 - Interactive Scenario Adapter

Apply optional dashboard overrides:

```text
minutes_override
usage_role_override
usage_rate_override
displaced_player_or_group_override
```

Recompute roster features and team ratings for each scenario.

### Cell 9 - Uncertainty Simulation

Use posterior samples from player projections where available. Otherwise bootstrap player projection intervals and team model residuals.

### Cell 10 - Explanation Payloads

Build decomposition and coach-facing context.

### Cell 11 - DB Write

Upsert to `team_rating_projections`.

---

## 12. DB Write Contract

Current table:

```text
team_rating_projections
```

Upsert fields:

| Column | Source |
|---|---|
| `player_id` | Candidate |
| `school_id` | Destination |
| `current_adj_em` | Baseline roster projection |
| `projected_adj_em` | Candidate roster projection |
| `delta_adj_em` | Candidate minus baseline |
| `ci_lower`, `ci_upper` | 80% interval around delta |
| `national_percentile` | Percentile from simulated national distribution |
| `conference_rank` | Rank among projected conference teams |
| `expected_minutes_input` | Candidate minutes from rotation builder |
| `model_version` | `team-roster-proj-v1` |

Future migration should add:

```text
baseline_adj_o
baseline_adj_d
projected_adj_o
projected_adj_d
candidate_usage_role
scenario_overrides jsonb
explanation jsonb
minutes_distribution jsonb
roster_state_hash
```

---

## 13. Evaluation

### Team projection metrics

| Metric | Use |
|---|---|
| Net rating RMSE | Main accuracy metric |
| Offense/defense RMSE | Diagnose source of error |
| Rank correlation | Whether ordering teams is useful |
| Calibration | Whether 80% intervals contain outcomes |
| Top-50/top-100 classification | Product relevance for coaches |
| Transfer-heavy team error | Validate portal-era roster churn |

Evaluate against the primary target source and the compatibility source when both are available:

```text
Hoop Explorer adj_net / off_adj_ppp / def_adj_ppp
BartTorvik AdjEM / AdjO / AdjD
```

### Counterfactual validation

Historical transfer test:

```text
For each completed transfer:
    reconstruct destination roster without player
    add actual transfer
    predict delta
    compare to actual team rating change after controlling for other roster changes
```

This is noisy, so evaluate in groups:

- High-minute transfers
- Low-major to high-major transfers
- Bigs vs guards
- High-usage creators
- Defensive specialists

---

## 14. Interpretability Contract

The API should eventually expose:

| Field | Meaning |
|---|---|
| `baseline_roster_rating` | Team projection before the candidate |
| `candidate_roster_rating` | Team projection after the candidate |
| `candidate_usage_role` | Projected or coach-adjusted usage role |
| `displaced_minutes` | Which slots lose minutes |
| `scenario_overrides` | Manual changes to minutes, usage, or displacement assumptions |
| `offensive_delta` | AdjO change |
| `defensive_delta` | AdjD change |
| `style_delta` | Pace/shot profile change |
| `main_positive_drivers` | Top contributors to improvement |
| `main_risks` | Uncertainty, redundancy, weak fit, role squeeze |

Coaches should be able to tell whether a player helps because he is simply better than the replacement slot, because he unlocks spacing, because he fixes a defensive weakness, or because the current roster has a specific hole.

---

## 15. MVP vs Full Version

### MVP

- Baseline roster from returning players plus average slot replacements.
- Expected minutes from deterministic Playing Time / Rotation heuristic.
- Roster features from existing player season stats and player projections.
- Interpretable ridge/elastic-net model for offense and defense.
- Hoop Explorer adjusted team ratings as preferred labels when historical coverage is available; BartTorvik adjusted ratings as fallback.
- One Add scenario per player-school pair.
- Simple role-tuning scenario for minutes and usage role overrides.
- Upsert to existing `team_rating_projections` table.

### Full version

- Uses posterior samples from player state-space model.
- Supports Add, Swap, Remove, Build roster, and Compare candidates modes.
- Optimizes rotation and usage jointly.
- Models lineup/interaction effects with stronger play-by-play data.
- Stores explanation JSON and roster state hashes.
- Powers a UI roster builder, not just a single projection card.
- Supports live sliders for usage role, minutes, and displaced-player assumptions.

---

## 16. Open Questions

1. How detailed should the internal roster representation be for MVP: position bands only, coarse role groups, or named rotation slots?
2. Should baseline replacements be conference-tier averages, team-quality averages, or team-system-specific averages?
3. Should candidate minutes be chosen by a rule-based optimizer first, or should the Playing Time / Rotation model produce the full minutes distribution?
4. How should we handle teams with massive roster churn where returning-player data is sparse?
5. After broader ingest, is Hoop Explorer historical coverage strong enough to be the only primary team target, or should BartTorvik remain a co-primary target?
6. Do we want the team projection UI to expose a "typical replacement" player concept explicitly?
7. How much interaction modeling should MVP include beyond minute-weighted impact and simple spacing/defense/rebounding adjustments?
8. Which dashboard controls should be available first: minutes, usage role, usage rate, displaced player/group, or replacement baseline?

---

## 17. Research Context

- EvanMiya team glossary defines team ratings around offensive/defensive efficiency, opponent adjustment, pace adjustment, and roster strength. It also describes preseason roster grade as a minutes-weighted mean of projected player BPR.  
  https://evanmiya.com/
- EvanMiya BPR methodology motivates using player value as points per 100 possessions above average, with offensive and defensive components that can aggregate into lineup/team expectations.  
  https://blog.evanmiya.com/p/bayesian-performance-rating
- EvanMiya Player Skill Projections motivate using projected player skills and roster strengths/weaknesses for transfer portal search and roster evaluation.  
  https://blog.evanmiya.com/p/new-tool-player-skill-projections
- Hoop Explorer team exports provide adjusted team offense, defense, net rating, tempo, style, and four-factor fields. These should be the preferred team projection labels if broader historical coverage can be ingested.  
  `data/hoop_explorer/all_team_explorer_stats_power_6.csv`
- BartTorvik provides the adjusted efficiency ecosystem already used by PortalPoint ingest: team adjusted efficiency margin, offensive/defensive ratings, tempo, four factors, and player box-score inputs.  
  https://barttorvik.com/
