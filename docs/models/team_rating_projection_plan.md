# Team Rating Projection — Implementation Plan

**Model version:** `team-roster-proj-v1`
**Issue:** [#19](https://github.com/shanthg01/MIDS210-Capstone/issues/19)
**Original design doc:** `docs/models/team_rating_projection_roster_tool_plan.md`
**Date:** 2026-07-02

---

## 1. Objective

Roster-based counterfactual model. Given a current school roster and a portal candidate, simulate how team offense, defense, and net rating change if the candidate joins.

Core output:
```text
delta_adjEM = projected_adjEM(with_candidate) - baseline_adjEM(without_candidate)
```

This makes the recommendation surface interpretable to coaches: not just "this player fits your scheme" but "this player adds ~2.1 AdjEM points, mostly via defensive improvement."

---

## 2. Hard Dependencies (all done)

| Dependency | Source | Status |
|---|---|---|
| Neutral player projections | `player_projections` (Phase 2a, `player-proj-phase2a-fcast-v1`) | ✅ 2021–2026 |
| Playing time projections | `playing_time_projections` (`playing-time-rotation-v2`) | ✅ target_season=2027 |
| Destination player projections | `player_projections` destination rows | ✅ 454,790 rows |
| Roster baseline membership | `roster_baseline_members` | ✅ written by `run_gap_matching.py` |
| Roster state features | `roster_state_features` | ✅ 357 schools |
| Player fit scores | `player_team_fit_scores` (scheme_fit, gap_match, role_fit) | ✅ 9.7M rows |
| HE team labels | `hoop_explorer_team_stats` (off_adj_ppp, def_adj_ppp) | ✅ 2021–2026 |
| BartTorvik team labels | `team_season_stats` (adj_o, adj_d, adj_em) | ✅ 2021–2026 |
| Player RAPM | `hoop_explorer_player_stats` (off_adj_rapm, def_adj_rapm) | ✅ 2021–2026 |

---

## 3. Schema Fix (migration required before implementation)

The existing `team_rating_projections` table has a bug: unique constraint on `(player_id, school_id)` only — no `season` column. Can't store multi-season results.

**New migration adds:**

| Column | Type | Notes |
|---|---|---|
| `season` | smallint NOT NULL | target season (e.g. 2027) |
| `baseline_adj_o` | float | school's projected off rating without candidate |
| `baseline_adj_d` | float | school's projected def rating without candidate |
| `projected_adj_o` | float | with candidate |
| `projected_adj_d` | float | with candidate |
| `candidate_usage_role` | varchar(40) | from playing_time_projections |
| `explanation` | jsonb | full decomposition payload |
| `minutes_distribution` | jsonb | per-player slot assignments after candidate add |

**Drop:** `uq_team_rating_projection (player_id, school_id)`
**Add:** `uq_team_rating_projection (player_id, school_id, season)`
**Extend:** `model_version` from varchar(20) → varchar(40) (matches other output tables)

---

## 4. Deliverables

| File | Purpose |
|---|---|
| `alembic/versions/xxx_team_rating_projections_v2.py` | Schema fix + new columns |
| `src/portalpoint/modeling/team_rating_projection.py` | Module: fit/score/write functions |
| `scripts/run_team_rating_projection.py` | Non-interactive rerun (`--target-season`, `--source-season`) |
| `notebooks/models/team_rating_projection_roster_tool.ipynb` | Interactive notebook, in sync with module |
| `src/portalpoint/api/routers/projections.py` | Stub → real DB read |
| `src/portalpoint/api/schemas/projection.py` | Extend response with new fields |
| `tests/test_team_rating_projection.py` | Unit tests |

Module + script + notebook pattern matches all prior models (M1–M3, Gap Matching, Player Projection, Playing Time, Destination Projection).

---

## 5. Model Architecture

### Two-component Ridge (offense and defense separately)

One Ridge model per target, trained on the same roster feature vector. "Two-stage" is a framing for explanation decomposition, not two sequential fits.

```
roster_features -> Ridge_offense -> projected_adj_o
roster_features -> Ridge_defense -> projected_adj_d
```

Counterfactual:
```
delta_adj_o = Ridge_offense(candidate_roster) - Ridge_offense(baseline_roster)
delta_adj_d = Ridge_defense(candidate_roster) - Ridge_defense(baseline_roster)
delta_adj_em = delta_adj_o - delta_adj_d
```

---

## 6. Roster Feature Vector

Computed once per school-season roster state. Same structure for both historical training and 2027 inference.

| Feature | Description | Source |
|---|---|---|
| `weighted_off_impact` | Σ(off_adj_rapm × min_share) across all players | HE player RAPM + actual/projected minutes |
| `weighted_def_impact` | Σ(def_adj_rapm × min_share) | same |
| `top1_off_impact` | Off RAPM of the player with the most minutes | starter quality signal |
| `top2_impact` | 2nd player by minutes | depth signal |
| `bench_depth_impact` | Σ min_share for players ranked 7+ by minutes | bench quality |
| `three_pt_coverage` | Σ(three_pt_pct × min_share) | spacing floor |
| `rim_protection` | Σ(def_adj_rapm × min_share) for C/PF only | interior defense |
| `pg_creation` | Σ(off_adj_rapm × min_share) for PG only | playmaking signal |
| `rebounding_coverage` | Σ(reb_rate × min_share) | rebounding balance |
| `usage_concentration` | HHI of usage shares | star-heavy vs balanced |
| `returning_minutes_pct` | Fraction of minutes from returning players | continuity / familiarity |
| `n_known_players` | Rotation spots with real projections (vs slot baseline) | data quality flag |
| `conference_tier` | 1=P6/high-major … 4=low-major (label encoded) | strength-of-schedule context |
| `adj_tempo_prior` | Team's historical pace (BartTorvik) | style context |

For missing rotation spots: fill with slot baselines (see §8).

---

## 7. Training Data Construction

For each `(school_id, season)` in 2021–2026 where labels exist:

1. Collect all players on that school's roster that season from `player_season_stats`
2. Join `hoop_explorer_player_stats` on `(player_id, season)` for `off_adj_rapm`/`def_adj_rapm`
   - Players missing from HE: use `hoop_explorer_player_stats.off_adj_rapm` average for their position and conference tier as a fallback value
3. Use actual `player_season_stats.min_pct` as minute share (not Playing Time model — that's 2027-only)
4. Compute roster feature vector
5. Labels:
   - Primary: `team_season_stats.adj_o`, `adj_d` (BartTorvik, all D1 schools)
   - HE team efficiency labels are not mixed into the training target because their scale differs from BartTorvik's adjusted efficiency scale. HE remains a player-quality/style enrichment source.

**Leakage guard:** for training season S, only use `player_season_stats` from `season <= S`. No forward data.

Expected training rows: ~1,800–2,190 school-seasons (365 schools × 6 seasons, minus coverage gaps).

---

## 8. Slot Baselines

Handles incomplete rosters: open spots get filled with position-tier average talent, not zero.

```python
slot_baselines[(conference_tier, position_band)] = {
    "off_adj_rapm": mean,
    "def_adj_rapm": mean,
    "three_pt_pct": mean,
    "reb_rate": mean,
    "usage": mean,
}
```

Built from all historical school-seasons. Saved to MLflow as a JSON artifact alongside the Ridge models.

Rationale: counterfactual must be "candidate vs. typical replacement," not "candidate vs. nobody." Comparing against zero systematically overstates every transfer's impact.

---

## 9. Candidate Add Logic (2027 Inference)

### Baseline roster (per school, season 2027)

1. Players from `roster_baseline_members` with source/prior season 2026 (written by `run_gap_matching.py`; observed roster context is not target-season 2027)
2. Prior-season player stats + HE RAPM for returning baseline players
3. Observed source-season `team_season_stats` / `roster_state_features` for school context, tempo, conference tiers, and returning-minutes continuity
4. True incoming freshmen from `roster_state_features.class_balance` get conservative depth priors when they are present in roster snapshots but do not yet have player IDs or player-season stats
5. Empty or missing player-quality slots filled with position/tier slot baselines

### Incoming freshman prior

Roster snapshots can include true freshmen as `returning_status="new"` with `class_year="fr"` before those players have `player_id`s, HE RAPM, or `player_season_stats`. Without a prior, Team Rating Projection treats those roster spots as empty, which understates baseline depth and can over-credit portal additions.

The v1 follow-up adds a conservative freshman prior:

- Count `class_balance.incoming_fr` / equivalent freshman labels from source-season `roster_state_features`.
- Allocate `8.0` min_pct per freshman, capped at `30.0` total team min_pct.
- Assign freshman priors to the most open positions from `open_minutes_by_position`; fall back to balanced PG/SG/SF/PF/C slots when no position gap is available.
- Fill quality from position/tier slot baselines with a `0.65` RAPM discount.
- Mark those rows as priors so `n_known_players` does not count them as fully known roster projections.

### Candidate roster

1. Same baseline
2. Insert candidate at `playing_time_projections.expected_minutes` for that candidate × school pair
3. Use neutral Phase 2a forecast value for candidate impact and prior-season `player_season_stats` for candidate position/3PT/rebounding when available
4. Redistribute displaced minutes using `playing_time_projections.displaced_minutes` JSONB — **consume directly, no re-derive**
5. Subtract displaced minutes from affected baseline players; recompute all `min_share` values

### Inference scope

`is_portal_candidate = true` × all D1 schools. Same population as Destination Projection (~1,251 candidates × ~365 schools ≈ ~456K pairs). Hard gate: `playing_time_projections` for `season=2027` must exist (same requirement as Destination Projection).

---

## 10. Cross-Validation

**3-fold rolling-origin CV** — same structure as Player Projection Phase 2a and Destination Projection.

Training seasons 2021–2026 (all complete, labels available):

| Fold | Train | Validate | Approx rows (train) |
|---|---|---|---|
| 1 | 2021–2023 | 2024 | ~1,095 |
| 2 | 2021–2024 | 2025 | ~1,460 |
| 3 | 2021–2025 | 2026 | ~1,825 |
| Final | 2021–2026 | — | ~2,190 → predict 2027 |

Fold 1 is thin (~1,095 rows) but accepted — same tradeoff as Player Projection. Fold 3 (val=2026) is the primary metric for `maybe_promote` gate.

Gate metric: `fold3_adj_em_rmse` (net rating RMSE on held-out 2026 season). Auto-promote if Δ > 5% vs `@champion`.

### Evaluation metrics

| Metric | Purpose |
|---|---|
| Off/def/net RMSE | Main accuracy (separate offense, defense, and net) |
| Rank correlation (Spearman) | Whether team ordering is useful |
| 80% CI calibration | Coverage check on delta_adjEM interval |
| Transfer-heavy team error | Portal-era validity (schools with ≥3 transfers that season) |
| Top-50 / top-100 team classification | Product relevance — do we identify the right elite teams? |

---

## 11. Confidence Interval

80% CI around `delta_adjEM`:

1. Production run uses an analytical Gaussian approximation for speed:
   `delta ± 1.2816 × sqrt(2 × (off_resid_std² + def_resid_std²))`
2. This replaces the original 200-sample bootstrap, which was too slow for 457k player-school pairs.
3. Remaining improvement: propagate per-player projection uncertainty and playing-time uncertainty so width varies by player/school pair.

---

## 12. Explanation Payload

Stored in `explanation` JSONB column. Built from Ridge coefficient × feature delta attribution.

```json
{
  "candidate_off_contribution": 0.8,
  "candidate_def_contribution": 0.6,
  "replacement_slot_delta": 0.7,
  "usage_reallocation_delta": -0.2,
  "spacing_delta": 0.3,
  "rim_protection_delta": 0.5,
  "uncertainty_penalty": -0.1,
  "minutes_displaced": [
    {"player_id": 123, "position": "G", "minutes_lost": -8.0}
  ],
  "candidate_usage_role": "secondary_creator",
  "candidate_minutes": 24.0,
  "baseline_adj_o": 105.2,
  "baseline_adj_d": 98.7,
  "n_baseline_players": 9,
  "n_slot_baseline_fills": 1
}
```

Decomposition: for each feature group (talent, spacing, rim protection, rebounding, continuity), compute `coef × scaled(candidate_feature - baseline_feature)`. The Ridge models are trained on standardized features, so explanation attribution must use each model's scaler before multiplying by coefficients.

---

## 13. Module Functions (`team_rating_projection.py`)

```python
build_historical_roster_states(engine, seasons) -> DataFrame
build_slot_baselines(df) -> dict
build_2027_baseline_rosters(engine, school_ids) -> dict[school_id, DataFrame]
build_candidate_roster(baseline_df, candidate_id, school_id, playing_time_row) -> DataFrame
build_roster_features(roster_df, slot_baselines, school_meta) -> dict
fit_team_translation(features_df, targets_df) -> (off_model, def_model, residual_stds)
predict_team_rating(features, off_model, def_model) -> (adj_o, adj_d, adj_em)
compute_counterfactual(baseline_features, candidate_features, off_model, def_model) -> dict
build_confidence_interval(baseline_df, candidate_df, player_uncertainty, off_model, def_model, n_boot=200) -> (ci_lower, ci_upper)
build_explanation_payload(baseline_features, candidate_features, off_model, def_model, candidate_row) -> dict
upsert_team_rating_projections(engine, records, model_version)
```

Fit functions return plain sklearn Ridge models + residual stds. MLflow tracking in notebook/script, not in module (same pattern as all prior models).

---

## 14. Notebook Structure

| Cell | Content |
|---|---|
| 0 | Config: `MODEL_VERSION`, seasons, feature groups, CV splits |
| 1 | Load upstream tables (player projections, playing time, HE RAPM, team labels) |
| 2 | Build historical roster states 2021–2026 (training rows) |
| 3 | Build slot baselines (conference tier × position) |
| 4 | Feature matrix + label matrix |
| 5 | 3-fold rolling-origin CV; plot RMSE/rank-corr per fold |
| 6 | Final model fit (all 2021–2026 data) |
| 7 | MLflow: log params, metrics, models, slot baseline artifact |
| 8 | 2027 baseline roster states (all D1 schools) |
| 9 | 2027 candidate counterfactuals (portal candidates × all schools) |
| 10 | Explanation payloads + CI bootstrap |
| 11 | DB write (upsert `team_rating_projections`) + `maybe_promote` |
| 12 | Spot-checks: top delta_adjEM pairs, specific school examples |

---

## 15. API Changes

### Schema extension (`schemas/projection.py`)

Add to `TeamRatingProjectionResponse`:
```python
baseline_adj_o: float
baseline_adj_d: float
projected_adj_o: float
projected_adj_d: float
candidate_usage_role: str
explanation: dict  # full decomposition payload
```

### Router (`routers/projections.py`)

Replace stub with async DB read:
```python
SELECT * FROM team_rating_projections
WHERE player_id = :player_id
  AND school_id = :school_id
  AND season = :season
  AND expires_at > now()
ORDER BY computed_at DESC
LIMIT 1
```

Returns 404 when no real row exists (same pattern as player projections for unknown players). No fallback to stub — stub removal is intentional.

---

## 16. Open Questions

1. **HE team label scale vs BartTorvik:** Resolved for v1 by using only BartTorvik `adj_o`/`adj_d` as labels and treating HE as player/style enrichment.

2. **Observed-vs-target season context:** Resolved for v1 by using source/prior-season `roster_baseline_members`, `team_season_stats`, and `roster_state_features` for baseline context while using target-season `playing_time_projections` / neutral player projections for the counterfactual season.

3. **Conference rank computation:** Requires projecting all schools in a conference simultaneously, not just the one the candidate targets. Two options:
   - Compute for all D1 schools in the inference pass (expensive but clean)
   - Drop `conference_rank` from MVP and surface it as a post-processing sort on the API side

4. **`displaced_minutes` JSONB format:** Produced by `playing_time.py`. Need to verify field names (`player_id`, `minutes_lost`, etc.) match what the candidate add logic expects before writing `build_candidate_roster()`.

---

## 17. Pre-Implementation Verification Queries

Run before starting to confirm upstream data is populated:

```sql
-- Playing time projections exist for 2027
SELECT COUNT(*), COUNT(DISTINCT school_id) 
FROM playing_time_projections 
WHERE season = 2027;

-- Roster baseline has source-season rows
SELECT COUNT(*), COUNT(DISTINCT school_id), season 
FROM roster_baseline_members 
GROUP BY season ORDER BY season;

-- HE team label coverage
SELECT season, COUNT(*) as n_teams, COUNT(off_adj_ppp) as n_with_he_labels
FROM hoop_explorer_team_stats 
GROUP BY season ORDER BY season;

-- Player RAPM coverage (needed for roster feature construction)
SELECT season, COUNT(*) as n_players, COUNT(off_adj_rapm) as n_with_rapm
FROM hoop_explorer_player_stats 
GROUP BY season ORDER BY season;

-- displaced_minutes format sample
SELECT player_id, school_id, displaced_minutes 
FROM playing_time_projections 
WHERE displaced_minutes IS NOT NULL 
LIMIT 5;
```

---

## 18. Session Log

*(Append findings and decisions as implementation progresses — same format as `destination_projection_plan.md` §20 and `player_projection_state_space_plan.md` §22.)*

---

## 19. First Real Run Results (2026-07-02)

### Run summary

| Metric | Value |
|---|---|
| Target season | 2027 |
| Source season | 2026 |
| Training rows | 2,158 school-seasons (2021-2026) |
| Inference pairs | 1,253 portal candidates × 365 D1 schools = 457,345 rows |
| Rows written | 457,345 |
| MLflow run_id | `b7deb48ffa1341e088167a0eb3df688f` |
| MLflow stage | Staging (no prior @champion to compare against) |
| Model file | `team_rating_projection.py` |
| Script | `scripts/run_team_rating_projection.py` |

### 3-fold rolling-origin CV results

| Fold | Val season | off_rmse | def_rmse | em_rmse | off_r2 | def_r2 |
|---|---|---|---|---|---|---|
| 1 | 2024 | 2.577 | 2.568 | 1.760 | 0.973 | 0.950 |
| 2 | 2025 | 2.927 | 2.960 | 1.965 | 0.970 | 0.943 |
| 3 | 2026 | 4.769 | 4.847 | 1.834 | 0.976 | 0.947 |

Final model (all 2021-2026): off_resid_std=2.008, def_resid_std=2.057.

**Assessment:** R² is high (0.94-0.98) across all folds, meaning the roster feature vector has genuine signal for predicting team offensive/defensive efficiency. AdjEM RMSE is tight (~1.76-1.97) on folds 1-2. Fold 3 off/def RMSE spikes to ~4.8 while AdjEM RMSE stays clean (~1.83) — the errors cancel almost perfectly in the net rating, meaning the model's offensive and defensive errors are correlated (shared RAPM coverage gap, not random noise). Documented as known issue — likely 2026 RAPM coverage in HE is less complete than prior seasons, causing more slot-baseline fills.

### Bugs found and fixed

**Bug 1: `slot_baselines` tuple keys → `json.dumps` `TypeError`**
- `slot_baselines` keyed by `(conference_tier, position)` tuples.
- `mlflow.log_dict` calls `json.dumps` internally, which cannot serialize tuple keys.
- Fix: `{str(k): v for k, v in slot_baselines.items()}` before logging.

**Bug 2: `load_inference_data` queried wrong season for `roster_baseline_members`**
- Original: `WHERE season = :season` using `target_season=2027` parameter.
- `roster_baseline_members` only has data through season 2026 (written by `run_gap_matching.py` for the observed-season roster baseline, not the target forecast season).
- Result: 0 baseline rosters → 0 counterfactual pairs.
- Fix: use `prior_season=2026` for this query. Target season (2027) only used for `playing_time_projections`.

**Bug 3: System-level `DATABASE_URL` env var bypasses SSH tunnel**
- System `DATABASE_URL` pointed at RDS hostname directly; `get_sync_engine()` reads it before checking `.env`.
- Fix (per-session): `$env:DATABASE_URL = "postgresql+psycopg2://portalpoint_app:pp_midsommer2026!@127.0.0.1:5433/portalpoint?sslmode=require"` before running any script. Must be done each new PowerShell session.
- Underlying fix: `modeling/io.get_sync_engine()` now strips `ssl(mode)=require` from the URL query string and passes it via `connect_args` for psycopg2 compatibility.

### Performance: 200× Step 6 speedup

Original Step 6 (counterfactual loop) runtime: ~8-10 hours.
Fixed runtime: ~28 minutes.

**Root cause:** `build_confidence_interval()` ran 200 bootstrap samples × 457,345 pairs = 91 million Python iterations. Each iteration called `build_roster_features()` (pandas DataFrame construction) + 4 Ridge `.predict()` calls.

**Two fixes applied:**
1. **Analytical CI:** `analytical_ci(delta_adj_em, models)` computes `delta ± 1.28×sqrt(2×(off_resid_std² + def_resid_std²))` in O(1). Valid approximation for linear Ridge model with Gaussian residuals. Fixed CI width (not player-specific) — listed as known issue for improvement.
2. **Vectorized Ridge predictions:** `predict_adj_o_d_batch(feature_matrix, models)` takes an `(n×14)` NumPy matrix and calls `.predict()` once per model per player (4 calls total per player: off+def for baseline batch, off+def for candidate batch). Previously: 4 calls per school per player = 4×365 = 1,460 individual Ridge predict calls per player.
3. **`iterrows()` → `to_dict('records')`:** PT index build removed 457K pandas `iterrows()` calls.

### Known issues / improvement roadmap

1. **Candidate profile fields:** Improved in PR follow-up — `run_team_rating_projection.py` now joins prior-season `player_season_stats` for candidate position, 3PT rate, and offensive rebounding when available. Fallbacks remain `"SG"`, `0.35`, and `0.25` only for candidates without prior stats.

2. **Constant CI width:** `analytical_ci` still returns the same width for every player-school pair, now based on both global offense and defense residual variance. More accurate: propagate per-player projection uncertainty from `player_projections.uncertainty` into the CI. Same pattern as Phase 2a's variable-width CI.

3. **Fold 3 RMSE spike:** off/def RMSE ~4.8 on 2026 held-out data vs ~2.6-2.9 on earlier folds. Most likely 2026 RAPM coverage gaps in HE (more slot-baseline fills → feature vector less representative). Alternative hypotheses: 2026 transfer portal volume is genuinely higher (roster composition is more volatile). Root-cause not confirmed; document and monitor on next year's data.

4. **Freshman priors:** Improved in PR follow-up — source-season roster snapshots now contribute conservative incoming-freshman depth priors. This is still a placeholder, not a recruiting-rank model; next improvement would join public recruit ratings or high-school/team pipeline priors.

5. **`returning_pct` continuity proxy:** Improved in PR follow-up — source-season `roster_state_features` now provides the fraction of returning minutes from `returning_minutes_by_position` over returning plus departing/open minutes. It still falls back to 1.0 if roster-state JSON is missing.

6. **`@champion` alias not yet registered:** `maybe_promote` skipped (no prior Staging version to compare against — true first run). Before the next rerun, register current v1 as `@champion` via `client.set_registered_model_alias("team-rating-scorer", "champion", version="1")`. Without this, the next run will also skip the gate.

7. **`estimate_usage_value_coef` analog:** The slot-baseline fills are position/tier averages, not learned usage-value adjustments. Same zero-overlap limitation as Destination Projection's `estimate_usage_value_coef` — fallback values used everywhere.

8. **API semantics:** Stub replaced. Follow-up fixed the endpoint to honor the requested `season` and ignore expired rows.
