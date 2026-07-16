# Program Fit Model Plan
## Manual-Input + Proxy-Based Recruiting Alignment Calculator

**Status (2026-07-15):** Still descoped (product decision, 2026-07-11) — nothing in this plan has been
started. Confirmed via direct DB query that `player_team_fit_scores.program_fit` is a hardcoded `50.0`
on every real row (8.2M + 3.9M rows, zero variation) — not a placeholder with partial signal, a pure
constant, baked in at write time by `gap_matching.py`'s `overall_fit` formula. A UI-only honesty pass
landed this date: the frontend previously rendered 5 fabricated sub-metric bars (NIL/Geographic/
Academic/Cultural/NIL Budget — `stub_program_fit_breakdown()`'s per-request random numbers) under a
correctly-labeled "Placeholder" chip, which read as real data with an asterisk rather than "we don't
have this." Removed those bars; every surface where Program Fit appears (`FitScorePage`, `ComparePage`
tooltip, `SettingsPage` weight-slider caption) now shows one shared "not live yet, here's what it's
meant to represent" string instead. No backend/scoring change — `gap_matching.py`/`playing_time.py`'s
`overall_fit` formula still bakes in `W_PROG*50.0`, explicitly deferred pending a separate decision on
whether to reweight to 3 real components (see `docs/status/MODEL_STATUS.md`'s Program Fit row).

**Planned notebook:** `notebooks/models/program_fit_model.ipynb`  
**Planned script:** `scripts/run_program_fit.py`  
**Primary module:** `src/portalpoint/modeling/program_fit.py`  
**Primary output:** `player_team_fit_scores.program_fit`  
**Model family:** multi-criteria utility calculator + user-configurable weights + missing-data confidence  
**Hard dependencies:** user/program preferences, school metadata, agreed manual/proxy input contract  
**Soft dependencies:** NIL estimates, geography, academics/major fit, competition tier, player preferences, availability status  
**Downstream consumers:** Fit Score, Recommendation Engine, Dashboard, Compare page

---

## 1. Product Objective

Program Fit answers a blended recruiting question:

```text
Does this player align with this program, and is this program a plausible fit for the player?
```

This is intentionally a blend of two perspectives:

```text
program-side alignment
    -> does the player match the program's stated priorities?

player-side feasibility
    -> would the program plausibly match the player's preferences and market?
```

For MVP, Program Fit should not be a learned black-box model. The relevant data is incomplete, noisy, and often private. Manual inputs and proxies are acceptable first-class data as long as the score exposes which inputs are real, proxy, manual, missing, or defaulted.

---

## 2. Design Principle

Do not fake precision.

Program Fit should be deterministic, configurable, and explainable. A coach should be able to see that a score came from:

- program-entered priorities,
- manual NIL tier estimates,
- region/geography proxies,
- school metadata,
- player hometown or origin,
- conference/tier context,
- missing-data penalties.

The score should carry a confidence value separate from the 0-100 fit value.

```text
program_fit = estimated alignment
program_fit_confidence = how much real/manual/proxy evidence supports the estimate
```

---

## 3. Research Basis And Feasibility

Program Fit is a multi-criteria decision problem, not a high-signal supervised learning problem.

Public-methods check:

- Multi-criteria decision-making commonly uses weighted scalarization to convert multiple objectives into a single score by assigning weights to each objective. This maps directly to user-configurable Program Fit priorities: [Williams and Cai 2024](https://arxiv.org/abs/2410.03931).
- Multi-criteria recommender systems improve recommendations by modeling multiple criteria instead of one global rating. This supports keeping Program Fit components separate and letting Recommendation Engine combine or subsort by them: [Zheng and Wang 2023](https://arxiv.org/abs/2306.11233).
- Weighted-sum utility models are simple, auditable, and robust for MVP when the key challenge is preference elicitation and missing data rather than model capacity.

Implementation implication:

```text
MVP
    weighted multi-attribute utility calculator
    + manual/proxy input ledger
    + confidence and missing-data flags

Future
    learn component weights from feedback
    + improve player-preference signals
    + add availability/commitment likelihood
```

---

## 4. Score Definition

Program Fit should produce:

```text
program_fit: 0-100
program_fit_confidence: 0-1
program_fit_breakdown: JSON
```

The MVP score:

```text
program_fit =
    w_nil * nil_alignment
  + w_geo * geography_alignment
  + w_academic * academic_alignment
  + w_competition * competition_level_alignment
  + w_preferences * user_preference_alignment
  + w_relationship * relationship_or_pipeline_alignment
  - missing_data_penalty
  - hard_constraint_penalty
```

Weights should be user/program-configurable. Defaults should sum to 1.0 after excluding unavailable factors:

| Component | Default weight | Notes |
|---|---:|---|
| NIL alignment | 0.25 | Manual/proxy-heavy |
| Geography alignment | 0.20 | School region vs player origin/preference |
| Academic alignment | 0.15 | Manual/program metadata; optional for MVP |
| Competition level alignment | 0.15 | Conference/team tier fit |
| User preference alignment | 0.15 | Coach/program priorities and filters |
| Relationship/pipeline alignment | 0.10 | Optional manual field; default neutral if absent |

The stored breakdown should include both raw component scores and effective weights after missing-data handling.

---

## 5. Component Contracts

### NIL Alignment

Goal:

```text
Is the program's NIL budget/market tier plausible for the player's estimated market?
```

Inputs:

```text
school.nil_tier
school.nil_estimated_budget_usd nullable
player_nil_tier nullable/manual
player_estimated_market_value_usd nullable/manual/proxy
user_override_nil_priority
```

MVP scoring:

| Situation | Score behavior |
|---|---|
| Program tier meets/exceeds player tier | High |
| Program tier one level below player tier | Medium |
| Program tier far below player tier | Low |
| Player NIL estimate missing | Neutral score, lower confidence |
| Program NIL estimate missing | Neutral score, lower confidence |

Output fields:

```json
{
  "score": 72.0,
  "confidence": 0.55,
  "program_nil_tier": "medium",
  "player_nil_tier": "high",
  "input_sources": ["school_proxy", "manual_player_estimate"],
  "flags": ["player_nil_manual"]
}
```

### Geography Alignment

Goal:

```text
Does the player's origin or stated preference align with the school location?
```

Inputs:

```text
school.region
school.state
school.latitude nullable
school.longitude nullable
player.hometown nullable
player_home_region nullable/derived
player_preferred_regions nullable/manual
distance_miles nullable
```

MVP scoring:

- Same preferred region: high.
- Neighboring/nearby region: medium.
- Far region without preference: neutral-to-low.
- Missing hometown/preference: neutral score, lower confidence.

### Academic Alignment

Goal:

```text
Can the program plausibly match the player's academic or major preferences?
```

Inputs:

```text
school.majors_offered nullable
school.graduation_rate nullable
school_academic_tier nullable/manual
player_desired_major nullable/manual
player_academic_priority nullable/manual
```

MVP scoring:

- Desired major offered: high.
- Related major family offered: medium.
- Unknown major preference: neutral, lower confidence.
- Desired major not offered: low if academic priority is high.

Academic data is expected to be incomplete. Do not block Program Fit on this component.

### Competition Level Alignment

Goal:

```text
Is the school a plausible competitive step for the player's talent, ambition, and current level?
```

Inputs:

```text
school.conference
team_season_stats.adj_em
team_quality_tier
player_current_conference
player_projection_value nullable
player_prior_minutes
transfer_direction
```

MVP scoring examples:

- High-impact player moving to strong high-major with plausible minutes: high.
- Low-minute player moving to elite roster with heavy crowding: low-to-medium.
- Productive low-major moving to mid/high-major with role opportunity: medium-to-high.

This component should not duplicate Role Fit. It should represent competitive level plausibility, while Role Fit represents roster opportunity.

### User Preference Alignment

Goal:

```text
Does the player match program-entered recruiting preferences and hard filters?
```

Inputs:

```text
user_preferences.filters
user_preferences.importance_* fields
program_fit_weights
preferred_positions
preferred_archetypes
preferred_class_years
minimum_height_by_position nullable
desired_regions nullable
excluded_statuses
```

MVP behavior:

- Soft preferences adjust scores.
- Hard constraints can apply large penalties or exclude candidates from recommendations.
- The raw fit-score row should still be computable unless the player is unavailable or the user explicitly requests exclusion.

### Relationship / Pipeline Alignment

Goal:

```text
Does the program have an existing recruiting relationship or contextual advantage?
```

Inputs are manual/proxy:

```text
prior_recruitment_level nullable/manual
staff_relationship nullable/manual
former_teammate_at_school nullable/manual
regional_pipeline nullable/manual
coach_connection nullable/manual
```

This component should default to neutral with low confidence until the product supports manual entry.

---

## 6. Manual Input Contract

Manual inputs should be stored in a structured way rather than buried in free text.

Potential future table:

```text
program_fit_inputs
```

Suggested fields:

```text
id
user_id
school_id
player_id nullable
season
input_scope              # school, player, player_school_pair
nil_tier nullable
player_market_tier nullable
academic_tier nullable
desired_major nullable
preferred_regions jsonb nullable
relationship_score nullable
relationship_notes nullable
hard_constraints jsonb nullable
input_source             # user, admin, proxy, import
created_by_user_id nullable
created_at
updated_at
expires_at nullable
```

MVP can avoid this table by storing program preferences in `user_preferences.filters`, but the model plan should assume a dedicated table will be useful once coaches start entering player-specific notes.

---

## 7. Missing Data Rules

Every component should return:

```text
score
confidence
input_status
flags
```

Input statuses:

| Status | Meaning |
|---|---|
| `real` | Directly observed from trusted source |
| `manual` | User/admin-entered |
| `proxy` | Derived from related fields |
| `defaulted` | Default neutral fallback |
| `missing` | No usable input |

Component confidence:

```text
real/manual high-quality input -> 0.80-1.00
reasonable proxy -> 0.45-0.75
defaulted neutral -> 0.20-0.45
missing critical input -> 0.00-0.25
```

Final confidence:

```text
program_fit_confidence =
    weighted average component confidence
    - stale_manual_input_penalty
    - conflicting_input_penalty
```

Do not let missing data automatically imply bad fit. Missing data should usually produce a neutral component score and lower confidence.

---

## 8. Weighting And User Preferences

Weights should support two layers:

### Program Defaults

Each user/program can set global Program Fit weights:

```json
{
  "nil": 0.25,
  "geography": 0.20,
  "academic": 0.15,
  "competition_level": 0.15,
  "user_preferences": 0.15,
  "relationship": 0.10
}
```

### Candidate-Specific Overrides

For a specific evaluation, a coach may care more about one factor:

```json
{
  "player_id": 123,
  "weight_overrides": {
    "nil": 0.40,
    "geography": 0.10
  }
}
```

MVP can skip candidate-specific overrides and use only global preferences, but the formula should be designed to accept overrides later.

Effective-weight rule:

```text
If a component is unavailable:
    keep its score neutral
    keep a small effective weight
    lower confidence

If a component is explicitly disabled by user:
    set effective weight to 0
    renormalize remaining enabled weights
```

---

## 9. Output Contract

### `player_team_fit_scores`

Program Fit writes:

```text
program_fit
overall_fit
breakdown.program_fit
model_version
computed_at
expires_at
```

Example `breakdown.program_fit`:

```json
{
  "program_fit": 73.4,
  "confidence": 0.58,
  "components": {
    "nil_alignment": {
      "score": 65.0,
      "weight": 0.25,
      "confidence": 0.50,
      "input_status": "manual",
      "flags": ["player_market_tier_manual"]
    },
    "geography_alignment": {
      "score": 82.0,
      "weight": 0.20,
      "confidence": 0.70,
      "input_status": "proxy",
      "flags": ["hometown_region_derived"]
    },
    "academic_alignment": {
      "score": 50.0,
      "weight": 0.15,
      "confidence": 0.25,
      "input_status": "defaulted",
      "flags": ["player_major_missing"]
    },
    "competition_level_alignment": {
      "score": 76.0,
      "weight": 0.15,
      "confidence": 0.75,
      "input_status": "proxy",
      "flags": []
    },
    "user_preference_alignment": {
      "score": 80.0,
      "weight": 0.15,
      "confidence": 0.80,
      "input_status": "manual",
      "flags": []
    },
    "relationship_alignment": {
      "score": 50.0,
      "weight": 0.10,
      "confidence": 0.20,
      "input_status": "defaulted",
      "flags": ["relationship_data_missing"]
    }
  },
  "hard_constraints": {
    "excluded": false,
    "penalties": []
  },
  "data_quality_flags": [
    "academic_defaulted",
    "relationship_defaulted"
  ]
}
```

---

## 10. Module Contract

Create:

```text
src/portalpoint/modeling/program_fit.py
scripts/run_program_fit.py
notebooks/models/program_fit_model.ipynb
```

Public functions should be pure and testable:

```python
score_nil_alignment(...)
score_geography_alignment(...)
score_academic_alignment(...)
score_competition_level_alignment(...)
score_user_preference_alignment(...)
score_relationship_alignment(...)
normalize_program_fit_weights(...)
combine_program_fit_components(...)
compute_program_fit(...)
score_program_fit_pairs(...)
upsert_program_fit_scores(...)
```

Each component scorer returns:

```python
{
    "score": float,
    "confidence": float,
    "input_status": str,
    "flags": list[str],
    "details": dict,
}
```

---

## 11. Script Contract

`scripts/run_program_fit.py` should:

1. Load scored `(player_id, school_id, season)` pairs from `player_team_fit_scores`.
2. Load players, schools, latest team stats, and user/program preferences.
3. Load optional manual/proxy Program Fit inputs.
4. Compute component scores.
5. Combine component scores using program-specific weights.
6. Write `program_fit` and `breakdown.program_fit`.
7. Recompute `overall_fit`.
8. Log component distributions, confidence distribution, and missing-data flags.

Important: because Program Fit may vary by user/program preferences, decide whether MVP writes one canonical program-level row per `(player_id, school_id, season)` or computes user-specific Program Fit on demand. Since `player_team_fit_scores` has no `user_id`, the first implementation should write the school-default Program Fit and let user-specific overrides happen in the Recommendation layer or API response.

---

## 12. Notebook Structure

### Cell 0 - Setup

```python
MODEL_VERSION = "program-fit-v1"
SCORING_SEASON = 2026
DEFAULT_COMPONENT_SCORE = 50.0
```

### Cell 1 - Data Coverage Audit

Audit:

- school NIL fields,
- school region/location fields,
- player hometown coverage,
- majors/academic fields,
- user preference coverage,
- manual/proxy input availability.

### Cell 2 - Component Scorers

Implement each scorer independently and inspect score distributions.

### Cell 3 - Weighting Rules

Test default weights, user weights, disabled components, and missing-data handling.

### Cell 4 - Pair Scoring

Score player-school-season pairs.

### Cell 5 - Explanation Payloads

Build `breakdown.program_fit` JSON and review examples.

### Cell 6 - Validation And Sensitivity

Run sensitivity checks over weights and missing-data assumptions.

### Cell 7 - DB Write Dry Run

Preview upsert rows.

### Cell 8 - MLflow / Artifact Logging

Log version, default weights, score distributions, and coverage metrics.

---

## 13. Validation Strategy

Program Fit does not have an obvious supervised truth label for MVP. Validate it as a decision-support calculator:

| Validation | Goal |
|---|---|
| Distribution check | Scores should have useful spread without extremes dominating |
| Sensitivity analysis | Reasonable weight changes should produce understandable ranking changes |
| Missing-data audit | Missing inputs should lower confidence, not create fake certainty |
| Manual case review | Coaches/team members should agree examples are directionally sane |
| Hard-constraint tests | Excluded candidates should not surface in recommendations unless requested |
| Stability check | Re-running with same inputs should produce identical scores |

Future supervised validation can use:

- players who committed to a school,
- players shortlisted/contacted by users,
- positive/negative user feedback,
- historical transfer destination choices.

Do not train on these signals until there is enough clean data; use them first for monitoring and calibration.

---

## 14. Interaction With Recommendation Engine

Recommendation Engine should use Program Fit as one signal, not the whole ranking.

Recommended MVP ranking inputs:

```text
overall_fit
scheme_fit
gap_match
role_fit
program_fit
program_fit_confidence
player_projection
availability_status
user hard filters
data_quality_flags
```

If Program Fit confidence is low, Recommendation Engine should either:

- downweight Program Fit,
- show a "needs manual review" flag,
- ask the user for missing manual inputs.

---

## 15. Implementation Order

1. Define Program Fit component schema.
2. Add default weights to preferences or config.
3. Implement pure component scorers.
4. Build notebook coverage audit.
5. Score current fit-score pairs with default/manual inputs.
6. Write `program_fit` into `player_team_fit_scores`.
7. Update Fit Score API to expose real vs defaulted Program Fit status.
8. Add UI copy for manual/proxy/missing Program Fit components.
9. Later, add `program_fit_inputs` table if manual data collection becomes central.

---

## 16. MVP vs Full Version

### MVP

- Deterministic weighted calculator.
- Program-level default weights.
- Manual/proxy inputs allowed.
- Component-level confidence.
- Missing-data flags.
- Write `program_fit` and breakdown JSON.
- Recompute `overall_fit`.

### Full Version

- Dedicated manual input table.
- Candidate-specific weight overrides.
- Learned preference calibration from user feedback.
- Player-side preference estimates.
- Availability/commitment feasibility model.
- Recommendation-level multi-criteria ranking and subsorting.

---

## 17. Open Questions

1. Should Program Fit be computed globally per school or user-specific on demand?
2. Where should manual player-specific fields live for MVP: `user_preferences.filters`, a new table, or JSON in shortlist notes?
3. Should missing NIL/player-market data default to neutral `50`, or should it use team/player tier proxies?
4. Should hard constraints remove players from recommendations or only apply large penalties?
5. What Program Fit components should be visible to coaches in the first UI version?
6. Should Program Fit include availability/commitment status, or should that remain a Recommendation Engine filter?
