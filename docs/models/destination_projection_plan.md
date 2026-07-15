# Destination-Adjusted Player Projection Plan

**Status:** Complete baseline — re-run 2026-07-01 with P0-P3 fixes applied; 454,790 rows written, 2,420 training rows, CV total_resid_std=2.892 (improved from 2.967), MLflow v3 Staging (Δ=+2.5% vs @champion v2 — below 5% auto-promote threshold). Per-game box-score translation fixed (P0: minutes/40 not possessions/100). Value deltas and box-score stats now use consistent rate basis. Cohort validation (P3) logged to MLflow. Known blocker before coach-facing box-score display: Jalik Dunkley sanity check surfaced usage-compression issues; value deltas are suitable for model review, but projected PPG/RPG/APG should be validated against a named-player checklist before coach-facing exposure.
**Target module:** `src/portalpoint/modeling/destination_projection.py`  
**Target script:** `scripts/run_destination_projection.py`  
**Target notebook:** `notebooks/models/destination_projection.ipynb`  
**Primary output table:** `player_projections` (destination rows, `school_id` populated)  
**Upstream dependencies:** Neutral Player Projection, Playing Time / Rotation  
**Downstream consumers:** Recommendation Engine, Team Rating Projection, Fit Score API, player profile UI

---

## 1. Objective

Neutral projection answers: *how good is this player, independent of context?*

Destination projection answers: *what does this player project to produce at this specific school, if they transfer there?*

This is a hypothetical transfer evaluation tool. The target user is a basketball program staff member who inputs their school and retrieves projections for portal candidates relative to *their program* — not predictions about where a player will actually go. The output is a ranked, explainable view of how each available player would perform in the user's specific system, roster, and competition context.

The inference population is:

```text
portal candidates (is_portal_candidate = true)
  × all D1 destination schools with usable roster context
```

This mirrors the all-pairs scope of scheme_fit and gap_match. A program queries the slice where `school_id = their school` to retrieve destination projections for all portal candidates evaluated against that specific program.

---

## 2. Position in the Stack

```text
Neutral Player Projection  (player-proj-phase2a-fcast-v1, real, ~30K rows)
    +
Playing Time / Rotation    (playing-time-rotation-v2, real — full 2027 all-pairs write required)
    +
Team / Roster Context      (team_season_stats, team_system_profiles, roster_state_features)
    +
Pairwise Fit Context       (player_team_fit_scores.scheme_fit / gap_match)
    |
    v
Destination Projection     (player-destination-proj-v1, writes player_projections destination rows)
    |
    v
Team Rating Projection, Recommendation Engine, Fit Score API
```

### Hard dependencies

| Dependency | Status | Notes |
|---|---|---|
| Neutral projections for target season | Real — `player-proj-phase2a-fcast-v1`, 30,304 rows | Must exist for portal candidates; `is_portal_candidate` flag gates inference scope |
| Playing time projections — all-pairs coverage | Implemented; **full 2027 all-pairs write pending** | Destination script inner-joins on this table; pairs missing PT rows are skipped. Coverage gap = destination projection coverage gap. `run_playing_time.py` full all-pairs run must complete before `run_destination_projection.py` is useful at scale |

The playing time dependency is a coverage gate, not a schema/code gate. The table exists and has rows. The issue is whether it covers all `portal_candidate × school` pairs for the target season. Any pairs missing from `playing_time_projections` are silently dropped from destination projection output — this is intentional (no PT row = no opportunity estimate = no meaningful destination stat translation), but coverage should be logged explicitly on each run.

---

## 3. Inference Population

```text
portal candidates:
    player_id WHERE is_portal_candidate = true for target season
    OR explicitly included via --player-ids override

destination schools:
    all D1 schools with a usable roster snapshot / roster_state_features for the target season

inference grid:
    portal_candidates × destination_schools
    inner-joined to playing_time_projections (hard gate)
    inner-joined to neutral projections (hard gate)
    left-joined to team context / pairwise fit context (soft — flag missing but don't drop)
```

The program-facing query pattern:

```sql
SELECT * FROM player_projections
WHERE school_id = :program_school_id
  AND projection_mode = 'destination'
  AND model_version = 'player-destination-proj-v1'
  AND season = :target_season
ORDER BY value_per_100 DESC;
```

---

## 4. Architecture — The Destination Value Formula

```text
destination_adjusted_value_per_100
  = neutral_value_per_100
  + role_usage_delta
  + style_skill_fit_delta
  + roster_context_delta
  + competition_level_delta
```

The neutral projection is the anchor. Deltas are conservative modifiers derived from historical transfer outcomes. They can move the projection up or down but cannot dominate it.

### MVP Caps (hard-enforced in code)

| Delta | Cap |
|---|---|
| `role_usage_delta` | ±0.75 |
| `style_skill_fit_delta` | ±0.50 |
| `roster_context_delta` | ±0.50 |
| `competition_level_delta` | ±0.75 |
| `total_context_delta` (sum) | ±1.50 |

Caps are enforced via `np.clip` after each delta is computed. The total cap is applied after summing all four. Widen only after held-out validation proves calibration supports it.

---

## 5. The Four Delta Components

### 5.1 Role/Usage Delta (`role_usage_delta`)

**Question:** How does projected usage at the destination differ from source usage, and how does that change value?

**Inputs:**
- `playing_time_projections.expected_usage` — destination usage
- `player_season_stats.usage_rate` (source season) — prior usage baseline
- `playing_time_projections.usage_role` — role label
- `player_projections.projected_rates` (neutral) — skill texture (creation, shooting, turnover profile)

**Training signal:** `transfers.pre_usage_rate`, `post_usage_rate`, `per_change` — real pre/post role-change observations for every confirmed transfer. Regress `post_transfer_value_delta` on `(source_skill_states, usage_delta, position, tier)`. Ridge with strong regularization (~5-10 features). This is the only delta with a learnable model; the others use calibrated rules or lookup tables.

**Key intuitions encoded:**
- High-usage creator scaling down into secondary role at stronger program: lower turnover burden, better shot selection → efficiency gain, fewer raw possessions → position-dependent net effect
- Low-usage specialist forced into primary usage: projects with wide CI and typically declines in per-100 efficiency
- Guards with strong passing/creation who scale down maintain more per-100 value than shot-volume-dependent scorers

### 5.2 Style/Skill Fit Delta (`style_skill_fit_delta`)

**Question:** Does the destination team's system amplify or suppress this player's specific skills?

**Inputs:**
- `team_style_vectors.parquet` / `team_system_profiles` — team shot and style profile
- `hoop_explorer_team_stats` — `off_style_*_pct`, `def_style_*_pct` where available
- `player_projections.skill_states` (neutral) — per-skill percentiles and states
- `player_team_fit_scores.scheme_fit` — compatibility context only (never added directly to value)

**Computed interactions:**

| Player skill (high percentile) | Team feature | Adjustment direction |
|---|---|---|
| `shooting_3p` | High team 3PA rate / spacing need | + small |
| `passing_creation` | Open ball-handler minutes (few returning primary creators) | + |
| `shot_creation_usage` | Crowded creation (strong returning guards) | − and wider CI |
| `block_rim_protection` | High `def_style_rim_pct` defensive need | + |
| `offensive_rebounding` | High `off_style_rim_pct` / frontcourt need | + |
| `defensive_rebounding` | Frontcourt depth shortage | + |

**Constraint:** `scheme_fit` is compatibility, not value. Style delta only produces value through role/usage mechanics. A high scheme_fit player still needs open minutes and appropriate role to realize value. Never treat `scheme_fit` as an additive value component directly.

**Implementation:** Rule-based scoring of explicit interactions, each scaled by a coefficient fit on historical transfer residuals. No tree model — n is too small to prevent overfitting on this delta specifically.

### 5.3 Roster Context Delta (`roster_context_delta`)

**Question:** Given this roster's specific situation, does this player's arrival land in favorable or unfavorable conditions?

**Inputs:**
- `roster_state_features` — returning/departing/incoming minutes and usage by position, class balance, archetype counts
- `playing_time_projections.displaced_minutes` — the opportunity source
- `player_team_fit_scores.gap_match` — pairwise need score (context only)

**Logic:** A player filling a genuine roster gap (open usage, departing peer at the same role, position-band shortfall) faces favorable conditions. A player entering a crowded situation faces suppressed expected production regardless of raw talent. `gap_match` already quantifies this pairwise need. Translate to a production delta via a 3-piece monotonic mapping from gap_match decile to historical production delta for transfers in similar roster situations. Simple, calibrated, principled.

### 5.4 Competition Level Delta (`competition_level_delta`)

**Question:** How does the competition tier change affect expected per-100 value?

**Inputs:**
- `team_season_stats.adj_em` (source school, source season) → tier of origin
- `team_season_stats.adj_em` (destination school, target season) → tier of destination
- Competition tier: 4 buckets from adj_em percentile per season (same derivation as Phase 2a's `compute_level_tier`)

**Training signal:** Phase 2a's transfer cohort slices:
- Low-major → high-major: mean value drops (n=156, Spearman 0.67 — reasonable signal)
- High-major → mid-major: weak, small sample (n=36) — wide CI, conservative delta
- Same-tier: delta ≈ 0

**Implementation:** 4×4 tier transition lookup matrix. Populate cells with historical mean value delta from `transfers` + `hoop_explorer_player_stats` join. Regularize empty cells toward 0. Add per-transition-type residual variance for CI widening.

---

## 6. Training Set Construction

**Row grain:** `(player_id, school_id, season)` — player at actual destination school in actual completed season.

**Label:** Hoop Explorer `off_adj_rapm` / `def_adj_rapm` at the destination school in the target season.

**Leakage constraints (critical):**

| Feature type | Allowed source | Not allowed |
|---|---|---|
| Player talent | Neutral projection for season `n` (Phase 2a forecast, observed `n-1` → projected `n`) | Realized target-season RAPM |
| Playing time features | Out-of-fold PT model predictions | Actual realized minutes from target season |
| Destination team context | `team_system_profiles`, `team_season_stats` for season `n` or most-recent prior | Realized season `n` team outcomes |
| Roster features | Pre-season snapshot for school `s`, season `n` | Target-season actual roster outcomes |

Do not create negative training examples. A player not attending a school is an unobserved counterfactual — not an observed zero-production outcome. Unchosen schools are not in the training set.

**Expected training volume:** 2021-2025 transfer seasons where destination HE RAPM labels exist. Target ~1,000-2,500 clean rows (historical transfer rate × HE coverage × neutral projection coverage). Run a coverage query first — if < 500 clean rows, reduce delta model complexity further (fewer free parameters, more rule-based fallbacks).

**Temporal split:**

```text
train:      2021-2024 destination seasons
validation: 2025
test:       2026 (actuals not fully settled during portal window — defer until HE 2026 labels available)
```

---

## 7. Model Shape

Given small training n (~1-2K rows), models must be simple and regularized:

| Delta | Model shape | Justification |
|---|---|---|
| Role/usage | Ridge regression | ~5-10 features, overdetermined on this n — ridge controls variance |
| Style/skill fit | Rule-based + calibrated coefficients | Avoid overfitting; interactions are domain-specified, not learned from data |
| Roster context | Monotonic mapping from gap_match decile | gap_match is already calibrated; simple translation avoids double-fitting |
| Competition level | Tier-transition lookup + ridge bias | Sparse n per cell; regularize toward 0 |

No tree ensembles for delta models until training set is meaningfully larger. Gradient boosting on 500 rows with ~20 features overfit badly.

---

## 8. Uncertainty Propagation

Destination CI starts from neutral CI and widens for each additional uncertainty source:

```python
dest_variance = (
    neutral_variance               # from player_projections.value_ci_*
  + playing_time_variance          # from playing_time_projections.minutes_ci_*
  + role_usage_residual_variance   # from delta 1 regression residual std
  + style_skill_residual_variance  # from delta 2 fit residual
  + competition_transition_variance  # larger for multi-tier jumps
  + roster_staleness_penalty       # snapshot freshness flag
)
```

Scale-up triggers:
- `data_quality_flags` present on the PT row
- Roster snapshot age > 30 days
- Tier jump ≥ 2 levels
- Source-season sample < 15 games
- Wide neutral CI

Calibrate to 80% nominal coverage using the same conformal scaling pattern Phase 0 uses — fit the scale factor on the held-out 2025 season before applying to 2027 inference.

---

## 9. Rate and Box Score Translation

After computing `destination_adjusted_value_per_100`, translate to per-game stats:

```python
destination_pace = team_season_stats.adj_tempo  # for destination school, target season
expected_minutes = playing_time_projections.expected_minutes
possessions_per_game = destination_pace * (expected_minutes / 40)

projected_per_game_box = {
    "points": neutral_projected_rates["pts_per_100"] * possessions_per_game / 100,
    "rebounds": neutral_projected_rates["reb_per_100"] * possessions_per_game / 100,
    "assists": neutral_projected_rates["ast_per_100"] * possessions_per_game / 100,
    ...
}
```

The per-100 value is role-adjusted (via delta stack). The per-game box is pace and minutes adjusted — both must be presented for a coach-facing surface.

---

## 10. Module Design

**File:** `src/portalpoint/modeling/destination_projection.py`

All functions pure and testable. No globals other than constants.

```python
MODEL_VERSION = "player-destination-proj-v1"
NEUTRAL_MODEL_VERSION = "player-proj-phase2a-fcast-v1"
PLAYING_TIME_MODEL_VERSION = "playing-time-rotation-v2"

DELTA_CAPS = {
    "role_usage_delta": 0.75,
    "style_skill_fit_delta": 0.50,
    "roster_context_delta": 0.50,
    "competition_level_delta": 0.75,
    "total_context_delta": 1.50,
}

# Data loading
def load_neutral_projections(engine, season) -> pd.DataFrame: ...
def load_playing_time_projections(engine, season) -> pd.DataFrame: ...
def load_destination_team_context(engine, season) -> pd.DataFrame: ...
def load_pairwise_fit_context(engine, season) -> pd.DataFrame: ...
def load_historical_transfer_outcomes(engine, train_seasons) -> pd.DataFrame: ...

# Feature construction
def build_destination_training_examples(...) -> pd.DataFrame: ...
def build_destination_inference_frame(...) -> pd.DataFrame: ...

# Delta components (each independently testable)
def fit_role_usage_model(training_df) -> RoleUsageModel: ...
def compute_role_usage_delta(df, model) -> pd.Series: ...
def compute_style_skill_fit_delta(df) -> pd.Series: ...
def compute_roster_context_delta(df) -> pd.Series: ...
def build_competition_tier_matrix(training_df) -> pd.DataFrame: ...
def compute_competition_level_delta(df, tier_matrix) -> pd.Series: ...

# Guardrails
def apply_delta_caps(df) -> pd.DataFrame: ...  # per-delta + total cap

# Value + uncertainty
def translate_neutral_to_destination_value(df) -> pd.DataFrame: ...
def propagate_destination_uncertainty(df, calibration_scale) -> pd.DataFrame: ...
def calibrate_uncertainty(pred_df, actual_df) -> float: ...  # conformal scale

# Rate/box translation
def translate_rates_to_destination_stats(df, team_pace_df) -> pd.DataFrame: ...

# Explanation
def build_explanation_payload(df) -> pd.Series: ...  # JSON per row

# Output
def build_destination_projection_records(df) -> list[dict]: ...
def upsert_destination_projections(engine, records) -> int: ...  # returns rows written

# MLflow
def log_destination_projection_run(metrics, params, model_artifacts): ...
```

---

## 11. Script Design

**File:** `scripts/run_destination_projection.py`

Follows the same pattern as `run_player_projection.py` and `run_playing_time.py`.

```text
Steps:
1.  Load neutral portal-candidate projections for target season
    (filter: is_portal_candidate = true OR --player-ids override)
2.  Load playing_time_projections for target season — log coverage, pairs present vs. expected
3.  Inner join neutral × PT — rows missing PT are skipped and logged by reason
4.  Left-join destination team context (team_season_stats, team_system_profiles, roster_state_features)
5.  Left-join pairwise fit context (player_team_fit_scores.scheme_fit, gap_match, breakdown)
6.  Load historical transfer outcomes for training seasons — run coverage query, log n
7.  Fit role/usage delta model on training examples
8.  Build tier transition matrix from historical transfer outcomes
9.  Apply all four deltas to inference frame
10. Apply per-delta and total caps
11. Propagate uncertainty (neutral CI + PT CI + delta residual variances + staleness flags)
12. Calibrate CI scale factor on held-out validation season
13. Translate per-100 value + rates to per-game stats (destination pace + expected minutes)
14. Build explanation JSON (all delta components + source model versions)
15. Upsert to player_projections (destination rows, school_id populated)
16. Log: coverage counts, skipped-row reasons, delta distributions, CI widths, MLflow metrics
```

CLI flags:

```bash
uv run python scripts/run_destination_projection.py
    --target-season 2027          # default
    --source-season 2026          # neutral projection observed season
    --portal-only                 # restrict to is_portal_candidate=true (default true)
    --player-ids 123 456          # one-off override — forces inclusion regardless of portal flag
    --school-ids 789              # restrict destination schools (e.g. dry-run for one program)
    --dry-run                     # compute but do not write to DB
    --no-mlflow                   # skip MLflow tracking (local dev)
```

---

## 12. Database Write

Destination rows go into `player_projections` with:
- `school_id` populated (not null)
- `projection_mode = "destination"`
- `model_version = "player-destination-proj-v1"`
- `projected_minutes` — from `playing_time_projections.expected_minutes`
- `projected_usage` — from `playing_time_projections.expected_usage`

No migration required. The partial unique index on `(player_id, school_id, season, model_version) WHERE school_id IS NOT NULL` already handles reruns.

**Explanation JSON schema:**

```json
{
  "neutral_value_per_100": float,
  "role_usage_delta": float,
  "style_skill_fit_delta": float,
  "roster_context_delta": float,
  "competition_level_delta": float,
  "total_context_delta": float,
  "destination_adjusted_value_per_100": float,
  "uncertainty_adjustment": float,
  "minutes_source_model_version": "playing-time-rotation-v2",
  "neutral_projection_model_version": "player-proj-phase2a-fcast-v1",
  "source_season": int,
  "target_season": int,
  "team_style_features_used": [str],
  "data_quality_flags": [str]
}
```

The sum `neutral + role + style + roster + competition` must equal `destination_adjusted_value_per_100` — verified in tests.

---

## 13. API Integration

The existing `GET /api/players/{id}/projection` serves neutral rows only. Two extensions needed:

**Option A — Query param on existing endpoint:**
```
GET /api/players/{id}/projection?school_id=456
```
If `school_id` provided: look up destination row for that player×school pair. Return combined response with both neutral and destination values for comparison.

**Option B — Fit scores endpoint enrichment:**
```
GET /api/fit-scores?player_id=X&school_id=Y
```
Add `destination_value_per_100`, `projected_per_game_box`, and delta breakdown to the fit score response once destination rows exist.

The program-facing query is always anchored to their school: they see all portal candidates evaluated against *their* program's system, roster, and context.

---

## 14. Validation Strategy

### Primary metrics (temporal holdout, 2025 test season)

| Metric | Baseline | Target |
|---|---|---|
| Destination value RMSE vs. HE RAPM | Phase 0 neutral RMSE on same transfer cohort (~1.60-1.71 offense) | < neutral RMSE (must improve) |
| Delta lift over neutral | 0.0 | > 0.0 |
| 80% CI coverage | 80% nominal | 75-85% |
| Rank correlation (destination value vs. actual) | — | > 0.60 |
| Delta magnitude distribution | — | Mean total delta near 0, no systematic bias |

### Required validation cohorts

| Cohort | Reason |
|---|---|
| Up-transfers (low→high major) | Largest risk of over-optimism; competition delta most active |
| Down-transfers (high→mid/low major) | Small n, widen CIs, report separately |
| Same-tier transfers | Largest slice, cleanest signal for role/style deltas |
| High-usage → restricted role | Validates role/usage delta specifically |
| Guards vs. bigs | Position-specific delta behavior |
| Crowded vs. open roster situations | Validates roster context delta |

### Sanity checks (run on every output)

- `abs(total_context_delta) <= 1.50` on every row — hard cap test
- Zeroing all deltas recovers neutral `value_per_100` — decomposition coherence
- Explanation JSON sums correctly: `neutral + role + style + roster + competition = destination`
- No destination rows with `school_id IS NULL`
- Coverage log present — rows written vs. pairs attempted

---

## 15. Key Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Small training set for delta models | Ridge with strong regularization; rule-based fallbacks for style/roster deltas; per-tier lookup for competition delta. Run coverage query first — if n < 500, reduce free parameters further |
| Playing time coverage gap | Log skipped pairs by reason; destination projection coverage exactly equals PT coverage — run `run_playing_time.py` full all-pairs write first |
| Leakage in training (actual minutes used as feature) | Use out-of-fold PT predictions in training set construction, not realized minutes |
| Gap B regression analogy (context adjustment hurts Phase 2a) | Destination adapter applies context at translation time, outside the Kalman filter — different mechanism. Still requires explicit validation that each delta individually improves held-out accuracy before being enabled |
| Roster snapshot staleness | `data_quality_flags` propagated into explanation; CI widened proportional to staleness |
| Scheme_fit → value conflation | Hard constraint: `scheme_fit` is context input only — never added to value directly. Tested in unit tests |
| Program sees projections for non-portal players | `is_portal_candidate` flag gates inference by default; `--player-ids` override for hypothetical scenario analysis |

---

## 16. Files to Create

| File | Purpose |
|---|---|
| `src/portalpoint/modeling/destination_projection.py` | Pure modeling functions |
| `scripts/run_destination_projection.py` | Production script |
| `notebooks/models/destination_projection.ipynb` | Training data audit, delta model fitting, validation plots |
| `tests/test_destination_projection.py` | Unit tests — each delta, caps, uncertainty propagation, JSON coherence |

No new migrations required. `player_projections` destination partial index already exists (`uq_player_projections_destination`).

---

## 17. Build Order

1. Coverage query — determine actual training n before committing to model complexity
2. `destination_projection.py` — pure functions, constants, tested in isolation
3. `tests/test_destination_projection.py` — synthetic data, verify each delta + cap + uncertainty math
4. Notebook — training data audit, delta model fitting, validation plots, spot-checks
5. `run_destination_projection.py` — production script
6. API extension — `?school_id=` param on existing endpoint

**Pre-condition:** `run_playing_time.py` full all-pairs 2027 write must complete before step 6 is useful at scale.

---

## 18. Open Questions Before Building

1. **Training n coverage:** How many completed player-school-seasons exist with both a Phase 2a neutral projection and a Hoop Explorer destination RAPM label? This gates the delta model complexity decision. Run the coverage query before finalizing model shapes.

2. **Inference scope:** Is the inference grid all portal candidates × all D1 schools, or scoped further by some minimum `scheme_fit` / `gap_match` threshold? All-pairs is cleanest for the recommendation use case (the program should see all candidates, even poor fits, so they can see *why* a player is a poor fit). Scoping risks hiding relevant information from the program.

3. **Gap B analogy:** Gap B (opponent context adjustment inside the Kalman filter) hurt Phase 2a. The destination adapter applies context at translation time, not inside the state model — different mechanism. But the underlying question (does adjusting for opponent strength help or hurt value prediction?) is still open and should be tested explicitly when the competition delta is validated.

4. **Role/usage delta direction for non-transfers:** Portal candidates who have not yet committed have no known destination usage. The PT model projects usage for each hypothetical destination. Ensure the training set for the role/usage delta uses the same out-of-fold PT usage predictions — not `transfers.post_usage_rate`, which is realized (leaked) usage.

---

## 19. First Real Run — Results and Findings (2026-07-01)

**Run complete.** 454,790 destination rows written for target_season=2027 under `model_version=player-destination-proj-v1`. MLflow `destination-projection` v2 promoted to @champion (Δ=+99.7% vs v1 which had 5 training rows due to the season offset bug — see below).

### Real data counts

| Metric | Value |
|---|---|
| Portal candidates (2026) | 1,253 |
| Playing time pairs | 457,345 |
| Inference pairs (after PT/neutral join) | 454,790 |
| Training rows (role/usage Ridge) | 2,125 |
| CV total_resid_std (mean fold RMSE) | 2.967 |
| CV fold 1 (eval=2024) RMSE / R² | 2.917 / 0.071 |
| CV fold 2 (eval=2025) RMSE / R² | 2.858 / 0.025 |
| CV fold 3 (eval=2026) RMSE / R² | 3.124 / 0.068 |

R² is low but expected — high inherent noise in transfer outcomes, rule-based style/roster deltas not yet fitted empirically.

### Delta summary (2027 inference means)

| Delta | Mean |
|---|---|
| role_usage_delta | +0.192 |
| style_skill_fit_delta | -0.025 |
| roster_context_delta | -0.326 |
| competition_level_delta | +0.186 |
| total_context_delta | +0.027 |
| destination_value_mean | +0.527 |

### Bugs found and fixed during first real run

1. **Season offset in `_TRANSFER_TRAINING_SQL` (critical, 5 vs 2,125 training rows):** `247sports transfers.season` = portal entry year Y, but barttorvik records destination stats at season Y+1. The SQL used `dest_season = t.season` (portal year) and `source_season = t.season - 1`; changed to `dest_season = t.season + 1` and `source_season = t.season`. Also removed a redundant `WHERE season = ANY(:train_seasons)` filter from the `dest_labels` CTE that was filtering by portal year (wrong). Result: 5 rows → 2,125 rows.

2. **247Sports player matching rate ~0%:** `_match_player()` was doing direct string equality on un-normalized names. Added `_normalize_name()` (NFD accent stripping, suffix removal, lowercase), position pre-filter (PG/SG/SF/PF/C exact match), two-pass strict (0.82) / relaxed (0.75) `difflib.SequenceMatcher` thresholds, and multi-season fallback (tries season-1 roster for unmatched). Match rate: 87-91% across all seasons. 21 tests in `tests/test_ingest_transfers_247sports.py` cover normalization and matching logic.

3. **`estimate_usage_value_coef` always returns fallback 1.5:** "only 0 overlap rows" between `neutral_df` (Phase 2a forecast players) and `source_stats_df` (barttorvik 2026 source stats). Root cause not yet diagnosed — likely player-ID scope or `min_pct` filter mismatch between the two frames. OLS coefficient that would replace the hardcoded 1.5 is never computed. See improvement roadmap item (b).

4. **`build_roster_state_features.py` first run had `incoming_minutes = 0`:** ran before the 247Sports matching fix populated real `player_id` on `transfers`. Re-ran post-fix — 357 rows with real `incoming_minutes`. Destination projection re-run consumed these real values.

5. **Per-game box-score output is currently not reliable for scorer sanity checks:** Jalik Dunkley is the concrete smoke test. His 2026 Nicholls source row is real and correctly loaded (`29` games, `min_pct=65.5` ≈ 26.2 MPG, `usage_rate=21.9`, `points_per_game=12.43`). The neutral Phase 2a payload projects him at `pts_per_40=13.91`, `reb_per_40=7.85`, `ast_per_40=1.99`, with `value_per_100=1.953`. But the destination rows display only `5.26` PPG at Georgia State, `4.34` at Texas A&M, and `4.34` at Alabama. Root causes to fix before UI exposure:
   - `projected_box_score` fields in the neutral payload are named and populated as `*_per_40`, but `translate_rates_to_destination_stats()` scales them by projected possessions divided by 100. That mixes a per-40-minute stat basis with a per-100-possession denominator and suppresses scorer box lines.
   - Playing Time's constrained `expected_usage` may be too aggressive for one-player counterfactual destination projections. Dunkley's `opportunity_drivers.expected_usage_raw` is about `21`, but constrained destination `expected_usage` is `12.5` at Georgia State and about `10.8` at Texas A&M/Alabama. That may be appropriate for total roster accounting, but the destination adapter should either use the raw and constrained usage separately or apply a less global compression when evaluating one hypothetical transfer at a time.
   - The neutral model itself reasonably regresses his source scoring from about 19 points/40 to 13.91 points/40, so not every drop is a bug. The suspicious part is the additional destination translation from a double-digit scorer into a 4-5 PPG player despite 21-28 projected minutes.

**Current trust boundary after the Dunkley check:** destination-adjusted `value_per_100`, delta direction, and explanatory context are suitable for model review. The per-game `projected_box_score` rate basis is now fixed; coach-facing exposure should still wait until usage-compression behavior is reviewed and the output passes a small named-player scorer/rebounder/playmaker checklist.

## 20. P0-P3 Bug Fixes + Re-Run (2026-07-01)

Teammate PR review surfaced four issues (P0-P3); implemented and re-run same session.

### P0 — Per-game box-score basis fix (critical)

`translate_rates_to_destination_stats()` was scaling `projected_box_score` fields (`*_per_40` basis) by `possessions / 100.0` — mixing a per-40-minute numerator with a per-100-possession denominator, suppressing stats ~45-50%. Dunkley 12.4 PPG source → 5.26 PPG shown.

Fix: `per_40_to_per_game = minutes / 40.0` (correct identity for per-40 rates). Tests updated in `TestRateTranslation` — `test_per_game_uses_minutes_not_possessions` now expects `9.0` (18.0 × 20/40 × 1.0); `test_pace_affects_possessions_not_per_game_stats` inverted since pace only affects possessions/total_value, not per-game stats when minutes are fixed.

### P1 — `--train-seasons` SQL filter semantic mismatch

`run_destination_projection.py` documents `--train-seasons` as destination seasons (year player appeared at destination). But `_TRANSFER_TRAINING_SQL` filtered `WHERE t.season = ANY(:train_seasons)` where `t.season` is the **portal entry year** (source year). This excluded the 2022 destination cohort entirely and included 2027 (which has no HE RAPM labels).

Fix: `WHERE t.season + 1 = ANY(:train_seasons)` — training rows now filtered by destination season consistently with the argument semantics and the CV fold logic.

### P1b — Destination query ordering

`GET /api/players/{id}/projection?school_id=X` sorted only by `computed_at DESC` — a row from a stale season could appear over a newer target-season row if the stale row was recomputed more recently.

Fix: `ORDER BY season DESC, computed_at DESC` — target season is always the primary sort key.

### P2 — Uncertainty components verification

Teammate had already assembled `uncertainty_components` dict in `propagate_destination_uncertainty` (lines 1129-1138 at time of review). No code change needed — verified present.

### P3 — Cohort validation slices

`compute_cohort_validation()` added to `destination_projection.py`. Loops same rolling-origin fold structure as `run_rolling_origin_cv`, collects held-out (actual, predicted) pairs, reports Spearman correlation and RMSE per:
- Tier direction: up / same / down
- Position group: guard (PG/SG) / wing (SF) / big (PF/C)
- Usage context: high-usage scaling down / usage increase

Wired into `run_destination_projection` pipeline; all cohort metrics merged into returned dict and logged to MLflow. 11 tests in `TestCohortValidation`.

### Re-run results (2026-07-01)

| Metric | First run (v2) | P0-P3 re-run (v3) |
|---|---|---|
| n_train | 2,125 | 2,420 |
| total_resid_std | 2.967 | 2.892 |
| MLflow | @champion | Staging (Δ=+2.5%) |
| n_records_written | 454,790 | 454,790 |

CV folds (v3):

| Fold | eval_season | RMSE | R² | n_eval |
|---|---|---|---|---|
| 1 | 2022 | 2.701 | 0.061 | 383 |
| 2 | 2023 | 2.905 | 0.078 | 455 |
| 3 | 2024 | 2.843 | 0.035 | 632 |
| 4 | 2025 | 3.120 | 0.071 | 655 |

Delta means (2027 inference, v3):

| Delta | Mean |
|---|---|
| role_usage_delta | +0.248 |
| style_skill_fit_delta | +0.003 |
| roster_context_delta | -0.329 |
| competition_level_delta | -0.008 |
| total_context_delta | -0.086 |
| destination_value_mean | +1.041 |

Cohort validation results (real data, 8 cohorts):

| Cohort | n | Spearman | RMSE |
|---|---|---|---|
| tier_up | 716 | 0.276 | 2.834 |
| tier_same | 787 | 0.153 | 3.053 |
| tier_down | 622 | 0.402 | 2.846 |
| guard | 1,238 | 0.304 | 2.974 |
| wing | 261 | 0.374 | 2.740 |
| big | 626 | 0.361 | 2.885 |
| high_usage_scaling_down | 204 | 0.197 | 2.690 |
| usage_increase | 342 | 0.336 | 2.945 |

Notable: tier_down (moving to easier competition) shows strongest signal (0.402); tier_same weakest (0.153 — hard to distinguish within-tier outcomes). Wings/bigs slightly stronger than guards. High-usage players scaling down (0.197) consistent with the unresolved Gap B regression finding — this cohort may benefit most from context-feature improvements.

---

### Improvement roadmap

In priority order (see CLAUDE.md Process Improvement TODO #10 for full detail):

(a) Completed P0: per-game box-score translation now uses `projected_box_score` per-40 fields with `expected_minutes / 40`; remaining follow-up is only to add coverage if a future `projected_rates` per-100 translation path is introduced.
(b) Inspect Playing Time usage-budget compression for destination one-player counterfactuals — keep raw vs. constrained usage in the adapter, and avoid treating every portal candidate as if they all join the roster simultaneously.
(c) Add named-player projection sanity checks — start with Dunkley-like double-digit low/mid-major scorers, high-rebound bigs, and high-assist guards; compare source per-40, neutral per-40, destination minutes, usage, and displayed per-game box output.
(d) ~~Fit style/skill delta empirically~~ — **attempted 2026-07-14, gated off real data**: infrastructure built (`fit_style_skill_weights`), but the real fit showed no genuine signal (R²=-0.0228, one coefficient sign-flipped) — see the 2026-07-14 finding below. Not closed; revisit only if the underlying feature set changes (e.g. the roster_state_features player_id fix, or resolving the upstream double-counting with the Playing Time model noted in the same finding) — refitting on the same features would just rediscover the same result.
(e) Fix `estimate_usage_value_coef` zero-overlap bug — OLS coefficient always falls back to 1.5.
(f) Position-specific competition tier effects — extend 4×4 matrix to 4×4×5 (tier×tier×position).
(g) Re-run roster context with real incoming/outgoing minutes (done for this run).
(h) Add eligibility year + portal timing features (`portal_entry_date`, eligibility year).
(i) Impute missing `pre_usage_rate` from barttorvik (~8% of matched transfers excluded).
(j) Position-specific Ridge model — one per position or position dummies.
(k) Serial transfer handling — multi-transfer players currently counted once per transfer event.
(l) Use barttorvik stats as secondary RAPM label — expands training from ~14K HE rows to ~70K.

### Finding (2026-07-14): `roster_state_features` is mostly unwired in `style_skill_fit_delta`, not by necessity

Surfaced while scoping `docs/models/destination_projection_backtest_plan.md` (checking whether
the 4-delta pipeline can be re-scored point-in-time for historical seasons). `roster_state_features`
is joined into `build_destination_inference_frame` (destination_projection.py:1371, 6 columns:
`returning_minutes_by_position`, `departing_minutes_by_position`,
`incoming_transfer_minutes_by_position`, `open_minutes_by_position`, `returning_production`,
`returning_player_impact`, `open_usage_by_position`), but only one of those seven columns —
`open_usage_by_position` — is ever read downstream, feeding a single interaction term
(`team_frontcourt_need`, `_add_style_interaction_features` line 1458-1464) inside
`compute_style_skill_fit_delta`'s 6 rule-based interactions. The other 6 are dead weight in the
frame.

This isn't the deliberate "only bigs get a roster-based need signal" design it might look like —
this doc's own §5.2 table specifies two more roster-based interactions that were never wired that
way: `passing_creation` was meant to pair with "open ball-handler minutes (few returning primary
creators)" and `shot_creation_usage` with "crowded creation (strong returning guards)" — both
worded as roster-composition signals, i.e. a backcourt-need analog of `team_frontcourt_need`
sourced from `returning_minutes_by_position`/`departing_minutes_by_position`. The actual code
substitutes `expected_usage`-derived proxies instead (`open_usage_signal`, `team_usage_crowding`,
both sourced from `playing_time_projections`, not `roster_state_features`) — plausibly a
reasonable simplification (per-player expected usage may be a richer signal than a coarse
team-level backcourt aggregate), but never reconciled against the original spec, leaving
`team_frontcourt_need` as an asymmetric one-off instead of a completed pattern.

Also relevant to roadmap item (d): if `passing_creation`/`shot_creation_usage`'s interaction terms
get empirically fit rather than hardcoded, decide then whether to keep the usage-based proxies or
switch to the originally-specified roster-composition signals — don't carry the asymmetry forward
unexamined. Not fixed here — this is a finding, not a change; either delete the 6 dead columns'
join or complete the backcourt-need interaction terms is a decision for whoever picks up (d).

### Finding (2026-07-14): the `roster_state_features` bug above was a real player_id corruption, and the empirical fit for (d) was attempted, found no signal, and is now gated off

Two follow-ups to the finding above, same day.

**1. `roster_state_features` wasn't just under-wired — the player_id join feeding it was silently corrupted.** Root cause: `scripts/build_roster_state_features.py`'s `SNAPSHOT_PLAYERS_SQL` result went through `pd.DataFrame(cur.fetchall(), columns=cols)`; any snapshot with ≥1 unmatched "new" player (a true freshman, `player_id IS NULL` — true for 346/357 snapshots, i.e. almost every school) forces pandas to upcast the whole `player_id` column to `float64` (numpy has no native nullable-int type). `player_id` is a 63-bit `BigInteger` (`hash(barttorvik_id)`); `float64`'s 52-bit mantissa silently loses precision the instant this happens, so the later `pss.player_id = ANY(%s)` lookup against `player_season_stats` matched nothing for 346/357 schools. Confirmed: `returning_minutes_by_position` was 0 for every position at 346 schools (not because nobody returned — because the lookup silently failed), which also explained why `open_minutes_by_position` ≈ `departing_minutes_by_position` — both degenerate to ≈`prior_total` when `returning`/`current_matched_ids` are corrupted to non-matching floats, so *every* prior-roster player reads as departed. Concrete production impact: `team_frontcourt_need` for a sample school (id=1) was **79.3** (as if 0% of bigs returned) vs. a real value of **46.22** once fixed. Fixed via `roster_state_features.safe_bigint_series()` (nullable-`Int64` construction from raw cursor rows, before pandas can upcast) + rerun (`scripts/build_roster_state_features.py`) — all 357 rows corrected in production. Second, independent, smaller bug found in the same investigation: `player_season_stats.per` (meant to feed `returning_player_impact`) is **100% NULL across all 6 seasons** — `ingest_barttorvik.py` hardcodes it to `None`, no source field maps to it. Switched `IMPACT_COL` to `bpm` (real, populated). Regression tests: `tests/test_roster_state_features.py` (3 tests, including a test that proves the naive pandas upcast really does corrupt these values, so the fix can't silently regress into a no-op).

**2. Roadmap item (d) (fit style/skill delta empirically) was attempted on the now-correct data, and rejected by a real held-out check — this is a genuine, useful negative result, not a failure to execute.** Built `fit_style_skill_weights()`: extends `_TRANSFER_TRAINING_SQL` with real historical team-style (`hoop_explorer_team_stats`) and roster-state (`roster_state_features`) joins at the destination school/season, fits a no-intercept Ridge on the residual of `value_delta` net of `competition_level_delta` and the already-fit `role_usage_delta` prediction (isolating the marginal variance actually attributable to style/skill interactions), for 5 of the 6 interaction terms. `def_reb_x_frontcourt_need` is deliberately excluded from the fit — checked real coverage first: `roster_state_features`-derived training features have real values in only 636/2425 rows, **100% concentrated in a single season** (2026) — a fit here would just be memorizing one season's noise, not a generalizable coefficient (the same live-only-snapshot constraint independently found while scoping `destination_projection_backtest_plan.md` §3, now confirmed to block this too).

**Real result on the other 5 terms: no real signal.** Full historical panel (n=2,425): in-sample R²=-0.0228 (residual_std 2.9195→2.9125, a 0.24% reduction — negative R² means the fit is worse than predicting zero). Held-out (train seasons <2026, eval on 2026, n=655): RMSE with no style adjustment 3.1792 → 3.1692 with fitted weights (0.31% better) vs. 3.1786 with the hardcoded weights (0.02% better) — technically better, but nowhere near this repo's own 5% auto-promote bar used everywhere else (Phase 2a, this same model's own Δ=+2.5%-stays-in-Staging call). Worse: `shot_creation_x_usage_crowding` fitted to **+0.7595** vs. the hardcoded **-0.10** — a sign flip that contradicts the stated basketball logic ("crowded = suppressed creation value"), consistent with overfitting on collinear features (`usage_crowding` and `open_usage_signal` are both derived from the same `dest_usage_rate` that also drives `role_usage_delta`'s own primary feature).

**Decision: don't ship the fitted weights.** Added `MIN_STYLE_SKILL_IMPROVEMENT` (2%) as a real gate inside `fit_style_skill_weights` itself — same discipline as `mlflow_helpers.maybe_promote`, applied to a delta component instead of a full model. Confirmed on real data: the gate correctly rejects this fit (`improvement_frac=0.0024`) and `run_destination_projection` keeps the hardcoded `STYLE_SKILL_INTERACTION_WEIGHTS` in production automatically — no manual intervention needed, and no future rerun can silently ship an unvalidated fit either. `compute_style_skill_fit_delta` now takes an optional `weights` param (defaults to the hardcoded dict) so the gate's fallback and the hardcoded default are the same code path. 6 new tests in `tests/test_destination_projection.py` (92 total, all green), including a regression test built directly from this real finding (rejects a pure-noise fit rather than shipping it).

**Net conclusion on (d):** the hardcoded weights aren't provably wrong — there currently isn't a real, robust empirical alternative to replace them with, given the available features. The infrastructure to re-attempt this (real training joins, residual-netting, the gate) is now in place; revisiting only makes sense if the underlying feature set changes materially (e.g. a `roster_state_features` historical archive existing, or resolving the double-counting against the Playing Time model's own use of the same roster features — see the "already fitted upstream" concern raised the same day and left as a separate open question, not resolved by this fit).

**3. Re-validated via existing CV/cohort infra, and ran the `team_frontcourt_need` ablation the "already fitted upstream" concern called for — real evidence found, question not fully closed.** `run_rolling_origin_cv`/`compute_cohort_validation` re-run against the (unchanged, gate-rejected) production pipeline match the pre-session P0-P3 baseline almost exactly (fold RMSEs 2.72/2.90/2.84/3.12 vs. the documented 2.70/2.91/2.84/3.12) — confirms this session's SQL/fit-infrastructure changes are provably inert on real production output, as expected since the gate keeps the hardcoded weights.

Ablation (restricted to the 636 rows with real `roster_state_features` coverage — the single season, 2026, this term can ever be tested on; 70/30 held-out split): a 5-term model (the same trainable set, excluding `team_frontcourt_need`) got RMSE=3.1904; adding `team_frontcourt_need` as a 6th term dropped it to RMSE=3.1568 (**1.05% better**), with a fitted coefficient of **+0.59** — same sign as the hardcoded +0.07, larger magnitude. Since this test uses real historical `dest_usage_rate` (not the Playing Time model's predicted `expected_usage`), it shows `team_frontcourt_need` carries real marginal signal beyond the 5 usage/style terms *in training* — evidence against the redundancy worry, not for it.

This doesn't fully close the original concern, which was specifically about *inference time*: whether the Playing Time GBT model's `expected_usage` output (fit using `open_usage_by_position` among its own features) already encodes this signal by the time `style_skill_fit_delta` reuses it raw. Testing that directly would need a real historical `expected_usage` prediction to compare against (i.e. a `playing_time_projections` backfill for a past season) — real added cost, not done here. **Decision: keep `team_frontcourt_need` as-is** — the available evidence supports keeping it, not removing it, even though the sample (n=636, one season, no cross-season validation possible) is thinner than every other term in this delta.
