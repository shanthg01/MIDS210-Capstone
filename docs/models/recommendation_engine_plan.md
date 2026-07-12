# Recommendation Engine (M7) — Team Rating Projection Macro-Signal Plan

**Model version (current):** `rec-v1.1` → proposed `rec-v1.2` on landing this work
**Depends on:** Team Rating Projection (`team-roster-proj-v1`, PR #49 merged to `main` 2026-07-11)
**Issue:** [#22](https://github.com/shanthg01/MIDS210-Capstone/issues/22)
**Date:** 2026-07-11
**Status:** ✅ Implemented and run for real against the live DB. `rec-v1.2`, `tests/test_recommendation_engine.py` extended to 35 tests (pure-unit, no DB), 236 pure-unit tests green repo-wide. Real run: school_id=301 (Lehigh), season=2027, 8 active users, 80 rows written to `recommendations`. MLflow `recommendation-engine` v1 registered, `@champion` alias set (first production run, no prior baseline to gate against).

**One more real bug found and fixed during the live run (not in the original plan):** `maybe_promote()` was called with `artifact_path=""` and no model had ever been logged to the run — `register_model` failed with `Unable to find a logged_model with artifact_path None`. This script had apparently never completed a real (non-dry-run) run before; the failure mode was always latent, just never exercised. Fixed the same way `destination_projection.py` already had to (`DestProjectionPyfunc` precedent) — added a trivial `RecEnginePyfunc` marker model (the engine is a weighted-sum formula, not a serializable sklearn model) and log it via `mlflow.pyfunc.log_model(artifact_path="rec_engine_model", ...)` before calling `maybe_promote(..., "rec_engine_model", ...)`.

**Season-semantics correction found during the live run (see §2):** the original draft assumed `team_rating_projections.season = player_team_fit_scores.season + 1`. Verified against the live DB and that was wrong — `sync_role_fit_scores()` had already upserted real `role_fit` directly into the same `season=2027` rows Scheme Fit/Gap Matching cover there, so `player_team_fit_scores`'s current season is 2027, matching `team_rating_projections` directly. Confirmed empirically before shipping: `trp.season = ptf.season` → 449,315 joined rows; `trp.season = ptf.season + 1` → 0.

---

## 1. Objective

Recommendation Engine v1 ranks portal candidates for a program using three real
per-player-per-school fit components: `scheme_fit`, `gap_match`, `role_fit`
(`DEFAULT_FIT_WEIGHTS` in `src/portalpoint/modeling/recommendations.py`).

Team Rating Projection (M6) answers a different, complementary question:
*"if this player joins, how much does the **team** actually improve"*
(`team_rating_projections.delta_adj_em`). This is a macro/roster-context signal
that individual fit scores don't capture — a player can be a great scheme/gap/role
fit and still add little marginal value if the team is already strong at his
position, or be a modest fit on paper but unlock real AdjEM gains by filling a
real hole. Both M6's hard dependencies (shared roster baseline, player
projections, role/minutes) and M7's fit stack are now real, so this is the next
concrete step for the Recommendation Engine, per the existing scaffolding
already left in the code (`modeling/recommendations.py`'s commented-out
`team_rating_delta` column and `run_recommendations.py`'s commented-out
`LEFT JOIN team_rating_projections`).

Program Fit (Issue #20) is **descoped** and is not part of this plan — M7's
weight formula stays a 3-then-4-component split (scheme/gap/role, +team-rating),
not a wait for a 5th.

---

## 2. Current State (what's real today)

| Piece | State |
|---|---|
| `modeling/recommendations.py` | `calculate_overall_fit()`, `generate_top_50_candidates()`, `refine_to_top_10()` — real, weighted-sum over whatever `0-100` columns are present, weights auto-normalized to the columns actually available (`_normalize_available_weights`). `team_rating_delta` is referenced only in comments. |
| `scripts/run_recommendations.py` | `CANDIDATE_SQL` does Stage 1 ranking **in SQL** today (`ORDER BY rec_stage1_fit DESC LIMIT 50`), duplicating `DEFAULT_FIT_WEIGHTS` as raw SQL float constants (`_STAGE1_SCHEME_WEIGHT` etc. — a maintenance risk: SQL and Python weights can silently drift). A commented `LEFT JOIN team_rating_projections trp ON trp.player_id = ptf.player_id AND trp.school_id = ptf.school_id` already exists but is missing a season condition (see §4.1 — a real bug, not just a stub). |
| `team_rating_projections` | Real, 457,345 rows, **`season` is the target/forecast season (2027)**, not the observed season. Unique on `(player_id, school_id, season)`. Has `delta_adj_em`, `ci_lower`, `ci_upper`, `expires_at`, `model_version`. |
| `player_team_fit_scores` | `season` is the **observed/current** season (`MAX(season)`, currently 2026 — see `fit_score_service.get_current_season()`). |
| `user_preferences` | Has `weight_gap`/`weight_scheme`/`weight_role`/`weight_program` columns. No `weight_team_rating` column yet. |

**Season semantics — the load-bearing detail for this whole plan, corrected
against the live DB (2026-07-11):** The original draft of this plan assumed
`team_rating_projections.season` sits one year ahead of
`player_team_fit_scores.season` (by analogy with Destination Projection's
`dest_season = t.season + 1`). **That assumption was wrong for the current
live data.** Verified directly: `MAX(player_team_fit_scores.season) = 2027`,
not 2026 — `sync_role_fit_scores()` upserted real `role_fit` directly into the
same season=2027 rows Scheme Fit/Gap Matching already cover there (it did not
create a separate "observed" season below the target season), so
`player_team_fit_scores.season=2027` already carries real `scheme_fit` +
`gap_match` + `role_fit` together. That's the same season
`team_rating_projections` uses. The correct join is
**`trp.season = ptf.season`** — confirmed empirically:
`trp.season = ptf.season` (both 2027) returns 449,315 joined rows;
`trp.season = ptf.season + 1` returns 0. Destination Projection's `+1`
convention is real but applies to a different pair of tables
(`transfers.season` → `player_season_stats` at the destination school) — don't
assume it generalizes without checking the actual data, as this plan
originally did.

---

## 3. Design Decision — Normalize `delta_adj_em` Into a 0-100 Component, Don't Add It Raw

The commented-out formula in `generate_top_50_candidates()` is:

```python
pool["adjusted_projection"] = pool["player_projection"] * pool["data_confidence"]
pool["stage1_rank_score"] = pool["adjusted_projection"] + pool["team_rating_delta"] + (pool["overall_fit"] / 100)
```

This adds raw `delta_adj_em` (real range roughly -5 to +5 AdjEM points, per M6's
CV residuals) directly to `overall_fit / 100` (range 0-1). A +3 AdjEM delta would
swamp the entire fit-score term. **Don't wire it up this way.**

**Decision: convert `delta_adj_em` into a normalized `team_impact_fit` column on
the same 0-100 scale as `scheme_fit`/`gap_match`/`role_fit`, and add it to
`DEFAULT_FIT_WEIGHTS` like any other component.** This reuses
`calculate_overall_fit()`'s existing weighted-sum + auto-normalization machinery
unchanged — no new formula, no unit mismatch, and it's consistent with how
`role_fit`/`program_fit` already default to a neutral `50.0` when absent.

```python
# fixed calibration, not per-pool min-max — keeps 50.0 meaning "no team
# impact" consistently across runs/schools, matching the 50.0 stub convention
# already used elsewhere (role_fit/program_fit placeholders)
DELTA_ADJ_EM_CLIP = 5.0  # AdjEM points; ~2.5x M6's fold em_rmse of ~1.8-2.0

def team_impact_fit(delta_adj_em: pd.Series) -> pd.Series:
    clipped = delta_adj_em.clip(-DELTA_ADJ_EM_CLIP, DELTA_ADJ_EM_CLIP)
    return ((clipped + DELTA_ADJ_EM_CLIP) / (2 * DELTA_ADJ_EM_CLIP)) * 100
```

`0` delta → `50.0` (neutral). `+5` or worse → `100`/`0`. Rows with no matching
`team_rating_projections` row (LEFT JOIN miss) get `team_impact_fit = 50.0`
(neutral, same convention as the `program_fit` placeholder) via `fillna(50.0)`
**before** calling `calculate_overall_fit()` — a per-row `NaN` would otherwise
poison the weighted sum for that row, not just leave the column "absent"
(`_normalize_available_weights` only handles whole-column absence, not
per-row nulls).

**New default weights** (re-proportioned from the current 0.30/0.35/0.35):

```python
DEFAULT_FIT_WEIGHTS = {
    "scheme_fit":       0.25,
    "gap_match":        0.30,
    "role_fit":         0.25,
    "team_impact_fit":  0.20,
}
```

These are a starting point, not a tuned result — gate the change through the
existing `recommendation-engine` MLflow run the same way every other model
change here has been gated (§6).

---

## 4. Implementation Steps

### 4.1 Fix + wire the SQL join in `run_recommendations.py`

Replace the commented placeholder:

```sql
-- future — uncomment when Model 6 ready:
-- LEFT JOIN team_rating_projections trp
--     ON trp.player_id = ptf.player_id AND trp.school_id = ptf.school_id
```

with a season-correct, freshness-correct join:

```sql
LEFT JOIN team_rating_projections trp
    ON trp.player_id  = ptf.player_id
   AND trp.school_id  = ptf.school_id
   AND trp.season     = ptf.season
   AND trp.expires_at > now()
```

and select `trp.delta_adj_em`. This is the one real bug fix in this plan, not
just a stub activation — the placeholder as originally commented would have
silently multiplied rows once a second season's worth of
`team_rating_projections` rows exists (no season filter = one-to-many). (Note:
an earlier draft of this plan had this as `ptf.season + 1`, by incorrect
analogy with Destination Projection — verified wrong against live data, see §2.)

### 4.2 Move Stage 1 ranking from SQL to Python

`CANDIDATE_SQL`'s `_STAGE1_SCHEME_WEIGHT`/`_STAGE1_GAP_WEIGHT`/`_STAGE1_ROLE_WEIGHT`
constants duplicate `DEFAULT_FIT_WEIGHTS` in raw SQL and have no way to express
the clip/normalize step `team_impact_fit` needs. Per the docstring's own stated
intent ("Stage 1 as a Python step is not needed today ... will be reintroduced
when predictions + team_rating_projections tables are ready"), that trigger has
now happened:

1. Drop `ORDER BY rec_stage1_fit DESC LIMIT 50` and the `rec_stage1_fit`
   computed column from `CANDIDATE_SQL` — just select the raw component columns
   plus `trp.delta_adj_em`.
2. In `main()`, call the already-written (currently unused/commented)
   `generate_top_50_candidates()` from `modeling/recommendations.py`, after
   computing `team_impact_fit` and `fillna(50.0)`.
3. Delete the now-redundant `_STAGE1_*_WEIGHT` SQL constants.

### 4.3 Freshness check

Mirror `check_role_fit_freshness()` / `ROLE_FIT_FRESHNESS_SQL`:

```python
TEAM_RATING_FRESHNESS_SQL = """
SELECT EXISTS(
    SELECT 1 FROM team_rating_projections
    WHERE season = :season AND expires_at > now() LIMIT 1
) AS has_team_rating_data
"""
```

(Checked against `:season` directly, not a `+1` target season — see §2.)

Warn (don't hard-gate — matches the existing role_fit precedent, not
`destination_projection.py`'s hard gate) if empty: `team_impact_fit` will be
uniformly `50.0` and the signal is inert for that run, which is a valid
degraded state, not an error.

### 4.4 `modeling/recommendations.py` changes

- Add `team_impact_fit()` helper (§3).
- Update `DEFAULT_FIT_WEIGHTS` (§3).
- Update docstrings' "Current fit columns" / "Future columns" lists — move
  `team_rating_delta`/`team_impact_fit` from "future" to "current."
- `refine_to_top_10()` needs no logic change — it already re-normalizes
  whatever weighted columns are passed in `user_preferences`; just include
  `team_impact_fit_weight` in the default dict `run_recommendations.py` builds.

### 4.5 Bump `MODEL_VERSION`

`rec-v1.1` → `rec-v1.2` in `run_recommendations.py` — the ranking formula is
materially changing (new component, reweighted existing ones), matching this
repo's convention of version-bumping on formula changes (`scheme-cos-v3`,
`gap-cos-v4`, etc.).

---

## 5. Explicitly Out of Scope for This Change

- **Program Fit.** Descoped (2026-07-11). Not added to `DEFAULT_FIT_WEIGHTS`.
- **User-adjustable `weight_team_rating`.** MVP wires `team_impact_fit` into the
  shared Stage 1 pool and Stage 2 default weights as a fixed macro signal, not
  a per-user preference. Adding a `user_preferences.weight_team_rating` column
  (migration + `USERS_SQL` change + `PUT /api/programs/{id}/preferences`
  schema update) is a clean v2 follow-up once the fixed-weight version has been
  validated, not bundled here.
- **CI-aware confidence discount.** `team_rating_projections.ci_lower/ci_upper`
  could feed the existing `_RISK_CONFIG` confidence-penalty mechanism (already
  built for a future `data_confidence` column) — worth doing, but a separate
  follow-up, not required to get a real signal wired in.
- **`/api/recommendations` router wiring.** The router is still a hardcoded
  stub list (`_STUB_SCORES` in `src/portalpoint/api/routers/recommendations.py`)
  regardless of this change — reading real `recommendations` rows is a
  separate, already-tracked gap (see `docs/status/MODEL_STATUS.md` M7 row).

---

## 6. Rollout

1. ✅ Implemented §4.1-4.5 (2026-07-11).
2. ✅ Ran `scripts/run_recommendations.py --school_id 301 --season 2027 --dry-run`
   (Lehigh) — `team_impact_fit` came back sane (52-53 range, near-neutral,
   plausible), Top-10 was PG-heavy but internally consistent with the pool's
   real gap/scheme/role scores.
3. ✅ Full run: school_id=301, season=2027, 8 active users, 80 rows written to
   `recommendations` under `rec-v1.2`. MLflow `recommendation-engine` v1
   registered, `@champion` set via `maybe_promote(..., metric_name="mean_overall_fit", higher_is_better=True)`
   (first production run, no prior baseline to gate against).
4. ✅ `docs/status/MODEL_STATUS.md`, `CLAUDE.md`, `docs/status/STATUS.md`, and
   `docs/models/model_dependency_graph.md` all updated (2026-07-11).

Only school_id=301 has been run so far — this is a real production write for
one school, not yet a full-population backfill across all schools/seasons.
Extending to the rest of the D1 population (or wiring it into a scheduled
job) is a natural next step, not yet done.

---

## 7. Test Coverage — Done

Extended the existing `tests/test_recommendation_engine.py` (pure-unit, no
DB — distinct from `tests/test_recommendations.py`, which is API router
tests requiring a DB fixture). 35 tests total in the file after this change
(fixed 2 stale assertions from the old 3-component formula, added the rest),
all green alongside 236 pure-unit tests repo-wide:

- `team_impact_fit()`: `0.0` delta → `50.0`; `±DELTA_ADJ_EM_CLIP` → `100.0`/`0.0`;
  values beyond the clip saturate; monotonic in delta; `NaN` input propagates
  (caller's job to `fillna` first — documented on the function itself).
- `calculate_overall_fit()` with the new 4-column `DEFAULT_FIT_WEIGHTS` sums
  to `[0, 100]`; requires the `team_impact_fit` column when using
  `DEFAULT_FIT_WEIGHTS` (Stage 1 does **not** degrade gracefully — that's
  `refine_to_top_10()`'s job via `_normalize_available_weights()`, a real
  distinction found while writing these tests, not assumed going in).
- `refine_to_top_10()` accepts `team_impact_fit_weight` and produces the
  expected weighted result; ignores it gracefully (re-normalizes over the
  remaining 3 columns) when the column is absent entirely.
- Fixed 2 pre-existing stale assertions in the file that hardcoded the old
  3-component `DEFAULT_FIT_WEIGHTS` values/ranking.
