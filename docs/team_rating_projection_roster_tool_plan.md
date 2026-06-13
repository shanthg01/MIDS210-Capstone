# Team Rating Projection Model Plan
## Roster-Based Counterfactual Team Projection System

**Planned notebook:** `notebooks/models/team_rating_projection_roster_tool.ipynb`  
**Model family:** Roster simulation + interpretable team efficiency translation  
**Primary output table:** `team_rating_projections`  
**Upstream dependency:** Player Projection system, Role Fit/minutes model  
**Downstream consumers:** `/api/projections/team-rating`, Fit Score page, Compare page, Recommendation Engine

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
projected_obpv
projected_dbpv
projected_ppv
projected_minutes
projected_usage
projected_box_score
skill_percentiles
uncertainty
```

The team model should use posterior samples when available:

```text
for sample in player_projection_draws:
    build rotation
    translate roster to team AdjO/AdjD/AdjEM
    store projected team result
```

This produces a real confidence interval around team impact.

MVP fallback: consume only player projection means and approximate uncertainty from player confidence scores.

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
projected_obpv
projected_dbpv
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
= average projected PPV, usage, shooting, defense, uncertainty
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
- Projected PPV
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
Roster State -> projected AdjO, AdjD, AdjTempo, AdjEM
```

### Team features

| Feature group | Examples |
|---|---|
| Weighted player value | minute-weighted OBPV, DBPV, PPV |
| Slot strength | PG1 PPV, C1 DBPV, bench PPV, weakest starter PPV |
| Skill coverage | 3PT shooting, playmaking, rim pressure, rebounding, rim protection |
| Balance | spacing without turnovers, defense without fouling, usage concentration |
| Continuity | returning minutes, returning PPV, same-coach/system if available |
| Style | projected tempo, 3PT rate, rim rate, assist rate |
| Uncertainty | weighted player uncertainty, unknown slot share |

### Model recommendation

Use an interpretable two-stage model:

```text
Stage A: Roster strength model
minute-weighted player values + slot baselines
    -> baseline team AdjO, AdjD

Stage B: Interaction adjustment
spacing + usage concentration + rim protection + rebounding + continuity
    -> offensive/defensive adjustment
```

Recommended MVP estimator:

- Ridge/elastic-net regression or Bayesian additive regression for `AdjO` and `AdjD`.
- Separate offense and defense models.
- Monotonic constraints where possible: better weighted PPV should not lower projected AdjEM unless explained by role/fit interactions.
- Gradient boosting only as a challenger model, not the default, unless the interpretability gap is solved.

---

## 8. Counterfactual Decomposition

Every `delta_adjEM` should decompose into coach-readable pieces:

```text
delta_adjEM
= direct_player_value_delta
  + replacement_slot_delta
  + usage_reallocation_delta
  + spacing_style_delta
  + defense_rebounding_delta
  + continuity_uncertainty_penalty
```

### Example explanation payload

```json
{
  "direct_player_value_delta": 1.4,
  "replacement_slot_delta": 0.7,
  "usage_reallocation_delta": -0.2,
  "spacing_style_delta": 0.3,
  "defense_rebounding_delta": 0.5,
  "uncertainty_penalty": -0.1,
  "minutes_displaced": [
    {"slot": "G3", "minutes": -10.0},
    {"slot": "FLEX", "minutes": -6.0}
  ],
  "candidate_role": "G2",
  "candidate_minutes": 24.0
}
```

This is more useful than saying "XGBoost says +2.6".

---

## 9. Training Labels

Primary labels:

```text
team_season_stats.adj_o
team_season_stats.adj_d
team_season_stats.adj_em
team_season_stats.adj_tempo
```

Training rows:

```text
school_id, season
projected preseason roster state built only from data available before season
actual end-of-season adjusted team ratings
```

Important: avoid leakage. For each historical season, build the roster projection using only prior seasons, known transfers, and preseason roster assumptions.

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

MVP API can start with Add mode only.

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

Load projected PPV, OBPV, DBPV, box stats, minutes, usage, and uncertainty.

### Cell 2 - Build Historical Roster States

For each school-season, reconstruct projected roster using only pre-season-available data.

### Cell 3 - Build Slot Baselines

Compute average slot projections by tier/style/position. Save to artifact for use when rosters are incomplete.

### Cell 4 - Rotation Builder

Assign players to slots and minutes. Validate that team minutes sum to 200 and position constraints are plausible.

### Cell 5 - Train Team Translation Models

Train offense and defense models separately:

```text
roster_features -> adj_o
roster_features -> adj_d
adj_em = adj_o - adj_d
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

### Cell 8 - Uncertainty Simulation

Use posterior samples from player projections where available. Otherwise bootstrap player projection intervals and team model residuals.

### Cell 9 - Explanation Payloads

Build decomposition and coach-facing context.

### Cell 10 - DB Write

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
candidate_role
explanation jsonb
minutes_distribution jsonb
roster_state_hash
```

---

## 13. Evaluation

### Team projection metrics

| Metric | Use |
|---|---|
| AdjEM RMSE | Main accuracy metric |
| AdjO/AdjD RMSE | Diagnose offense vs defense |
| Rank correlation | Whether ordering teams is useful |
| Calibration | Whether 80% intervals contain outcomes |
| Top-50/top-100 classification | Product relevance for coaches |
| Transfer-heavy team error | Validate portal-era roster churn |

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
| `candidate_role` | Projected slot and minutes |
| `displaced_minutes` | Which slots lose minutes |
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
- Expected minutes from deterministic role fit heuristic.
- Roster features from existing player season stats and player projections.
- Interpretable ridge/elastic-net model for AdjO and AdjD.
- One Add scenario per player-school pair.
- Upsert to existing `team_rating_projections` table.

### Full version

- Uses posterior samples from player state-space model.
- Supports Add, Swap, Remove, Build roster, and Compare candidates modes.
- Optimizes rotation and usage jointly.
- Models lineup/interaction effects with stronger play-by-play data.
- Stores explanation JSON and roster state hashes.
- Powers a UI roster builder, not just a single projection card.

---

## 16. Open Questions

1. How detailed should the rotation slot taxonomy be for MVP: 8 slots, 10 slots, or position-only?
2. Should baseline replacements be conference-tier averages, team-quality averages, or team-system-specific averages?
3. Should candidate minutes be chosen by a rule-based optimizer first, or should Role Fit produce the minutes distribution?
4. How should we handle teams with massive roster churn where returning-player data is sparse?
5. Should the first team target be `AdjEM` only, or should we model `AdjO` and `AdjD` separately from day one?
6. Do we want the team projection UI to expose a "typical replacement" player concept explicitly?

---

## 17. Research Context

- EvanMiya team glossary defines team ratings around offensive/defensive efficiency, opponent adjustment, pace adjustment, and roster strength. It also describes preseason roster grade as a minutes-weighted mean of projected player BPR.  
  https://evanmiya.com/
- EvanMiya BPR methodology motivates using player value as points per 100 possessions above average, with offensive and defensive components that can aggregate into lineup/team expectations.  
  https://blog.evanmiya.com/p/bayesian-performance-rating
- EvanMiya Player Skill Projections motivate using projected player skills and roster strengths/weaknesses for transfer portal search and roster evaluation.  
  https://blog.evanmiya.com/p/new-tool-player-skill-projections
- BartTorvik provides the adjusted efficiency ecosystem already used by PortalPoint ingest: team adjusted efficiency margin, offensive/defensive ratings, tempo, four factors, and player box-score inputs.  
  https://barttorvik.com/
