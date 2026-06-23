# Branch TODO — `feature/player-projection-v1`

Temporary working doc. Delete once items are resolved or migrated into permanent docs/issues.

---

## Section 1 — Issues Preceding the Notebook (data/ingestion)

These are upstream of `player_projection_state_space.ipynb` — fix here first, then
re-run Phase 0 (and re-check Phase 1's calibration cells) since both consume this data.

### 1.1 `players.position` is hardcoded `'G'` for all 13,303 players (Q1)

Root cause confirmed: pre-2026-06-21 code literally hardcoded
`"position": "G",  # barttorvik doesn't give position directly; update from cbbpy later`.
A real fix (`_infer_position()` — maps barttorvik role + height to PG/SG/SF/PF/C) landed in
commit `5be701e` (2026-06-21), but `ingest_barttorvik.py` was never rerun since. Code is
fixed; data is stale.

- [x] Rerun `ingest_barttorvik.py --seasons 2021 2022 2023 2024 2025 2026` to repopulate
      `players.position` via `_infer_position()` (upsert's `ON CONFLICT DO UPDATE` covers
      `position` — no migration needed, just a rerun). **Done 2026-06-23.**
- [x] Verify the rerun actually produces a real PG/SG/SF/PF/C distribution. **Confirmed:**
      `SG=5172, C=3947, PG=1874, SF=1354, PF=956` — real distribution, no more uniform `'G'`.
- [ ] Fix downstream dependents that assumed/depended on `players.position`:
  - [x] `gap_matching.py` `assign_soft_positions()` — code reviewed, **no bug**: the
        `players.position` fallback layer does an exact `df["position"] == pos_name` check
        against PG/SG/SF/PF/C, which `'G'` could never match — that's why it was silently
        dead before. Now live with real data. **Not yet re-run** — `run_gap_matching.py`/
        `run_scheme_fit.py` full all-pairs rerun (~1h51min per ARCHITECTURE_STATUS) needed to
        get this into `player_team_fit_scores`; deferred, not blocking.
  - [x] `api/routers/players.py` `_safe_position()` / `_POSITION_MAP` — confirmed fine, no
        change needed (already handles PG/SG/SF/PF/C natively).
  - [x] `tests/test_players.py`, `tests/test_gap_matching.py` — full suite re-run, **139
        passed, no regressions.**
  - [x] M1 player clustering (`modeling/player_clustering.py`) — confirmed **not a
        dependent**, grepped for `position` usage, zero matches.
  - [ ] `player_projection.py` (Phase 0) already uses HE `pos_class` instead of
        `players.position` — no change needed there, but revisit later whether
        `players.position` (once real) should become the primary source instead. Still open,
        low priority.

- [ ] **Long-term, separate PR — remove `cbbpy` from the data model entirely.** It's
      referenced but never implemented (`cbbpy_id` always `None`); the "update position from
      cbbpy later" plan never happened and nothing currently writes to it.
  - [ ] Drop `players.cbbpy_id` column (new alembic migration — don't rewrite the original
        `064d7a23e792_initial_schema.py` migration that added it).
  - [ ] Remove `cbbpy_id=None` field from `ingest_barttorvik.py`'s player upsert dict.
  - [ ] Remove/update `cbbpy_id` references in `notebooks/features/feature_eng_m1_m2_m3.ipynb`,
        `notebooks/features/barttorvik_feature_eng.ipynb`, `notebooks/eda/eda_barttorvik.ipynb`.
  - [ ] Update mention in `notebooks/eda/justin/Hoop Explorer.md` (CBBpy comparison section —
        keep the comparative discussion, just drop the "we'll use this for position" framing
        since that plan is being abandoned).
  - [ ] Flagged long-term/non-blocking — does not block the position fix above, which doesn't
        depend on cbbpy at all.

### 1.2 Hoop Explorer ingestion drops real columns (Q3)

Root cause confirmed: `off_adj_rapm_prod`, `def_adj_prod_rapm`, `adj_rapm_prod_margin`,
`off_adj_rapm_pred`, `def_adj_rapm_pred`, and the full `rank_*`/`pctile_*` set all exist in
the raw `data/hoop_explorer/all_player_stats_*.csv` files. `ingest_hoop_explorer.py`'s
player-row mapping (~line 486) only selects 5 fields
(`off_adj_rapm`, `def_adj_rapm`, `adj_rtg_margin`, `adj_rapm_margin`, `adj_rapm_margin_pred`).
Real ingestion gap, not a documentation error (plan doc corrected 2026-06-23).

- [x] Extend `ingest_hoop_explorer.py`'s player mapping to add: `off_adj_rapm_prod`,
      `def_adj_prod_rapm`, `adj_rapm_prod_margin`, `off_adj_rapm_pred`, `def_adj_rapm_pred`.
      **Done 2026-06-23.** (Decided: did not add the full `rank_*`/`pctile_*` set — ~80
      columns — no concrete use case yet; revisit if one emerges.)
- [x] Add alembic migration for the new `hoop_explorer_player_stats` columns.
      **`f1c4a8d3e570`, applied.**
- [x] Rerun `ingest_hoop_explorer.py --all-seasons` to backfill. **Done — but found a real
      limit, not a PortalPoint bug:** only `off_adj_rapm_prod` and `adj_rapm_prod_margin` are
      actually populated (16,568/16,568, 100%). `def_adj_prod_rapm`, `off_adj_rapm_pred`,
      `def_adj_rapm_pred` are **100% empty in the raw source CSV itself**, confirmed across
      all 6 seasons + the sample file — not an ingest mapping issue. HE's own docs note the
      `_pred` fields are tied to a "for transfers" leaderboard view we don't currently export
      with; getting these 3 populated needs a different HE export configuration (manual,
      external, out of scope for this branch). Columns kept in schema (harmless NULL) for
      if/when a future export populates them.
- [x] Update `player_projection.py` (Phase 0) to actually use the now-real secondary
      value labels. **Done** — added a robustness-check log (not a retrain) in
      `scripts/run_player_projection.py` and the notebook (Cell 5): correlates
      `off_value_per_100` vs `off_adj_rapm_prod` (0.643) and `value_per_100` vs
      `adj_rapm_prod_margin` (0.433) — sane, moderate positive, as expected for a
      playing-time-weighted secondary label per the plan doc's own framing.
- [x] Mark `docs/models/player_projection_state_space_plan.md` §19's HE-ingestion-gap row as
      done.

---

## Section 2 — Notebook Known Issues / Follow-Ups

From `player_projection_state_space.ipynb` §13, plus the Q4 finding from review. **Ordered by
priority — work top to bottom.**

1. **[P1] Fix `R_t` scaling for count-rate skills (Phase 1).** `usage`/`assist`/`turnover`/
   rebound/`steal`/`block` skills use `R_t = 1/minutes` — wrong shape for count data (true
   variance scales with the rate itself, Poisson-style, not flat inverse-minutes). This is
   *why* Q saturates at the upper bound for exactly these skills (Cell 9/10) and why Phase 0
   agreement is weak for them (Cell 12, corr 0.15-0.39). Fix: derive `R_t` from an empirical
   or Poisson-approximate per-skill noise scale, not a flat `1/weight`.
   **[x] Done 2026-06-23.** Added `_r_numerator()` in `player_projection_kalman.py` —
   Bernoulli `p(1-p)` for shooting skills, Poisson-derived `mean_rate * 40` for count-rate
   skills (full derivation in the function docstring). Widened `Q_BOUNDS` from `(1e-6, 2.0)`
   to `(1e-6, 100.0)` to match the corrected scale. **Result: zero skills at-bound**, and
   Phase 0/Phase 1 correlation jumped for every count-rate skill: usage 0.39→0.77, passing
   0.37→0.81, turnover 0.15→0.50, off/def rebounding 0.29/0.27→0.78/0.77, steal 0.17→0.72,
   block 0.39→0.80. Shooting skills dipped slightly (0.61→0.60, 0.68→0.59) — expected,
   harmless (numerator changed from flat 1.0 to `p(1-p)`, not a regression). Added 5 unit
   tests (`tests/test_player_projection_kalman.py`); notebook re-executed end-to-end with
   the fix, zero errors; full suite 144 passed.

2. **[P2] Investigate the `def_adj_rapm` vs. box-score-defense sign anomaly (new — from Q4
   review).** `block_rim_protection`/`steal_disruption` have *negative* coefficients against
   `def_adj_rapm` in the Phase 0 value model — backwards from HE's documented convention
   (higher `def_adj_rapm` = better defense). Confirmed this is not a Ridge/multicollinearity
   artifact — the raw bivariate correlation is already negative (block_pct vs def_adj_rapm =
   -0.21, steal_pct vs def_adj_rapm = -0.18). Real, surprising, needs investigation before
   trusting `value_per_100` for defense-heavy players: check by competition tier first
   (cheapest test — does this hold within high-major only, or is it a low-major/high-major
   mixing artifact), then check sample-size weighting.

3. **[P3] Re-run notebook Cells 9-12 after the P1 fix.** Success criteria: count-rate skills'
   fitted Q lands in the interior of `(1e-6, 2.0)`, not pinned; Phase 0/Phase 1 correlation
   improves materially from the current 0.15-0.39 range for those skills.
   **[x] Done 2026-06-23** — folded into the P1 re-run above; same numbers.

4. **[P4] Do not start Phase 2 (block covariance, cross-season `rho`/dev-curve) until P1-P3
   are resolved.** The 2020-2026 game-log backfill already removed the data blocker — adding
   cross-season complexity on top of a miscalibrated single-season model would bury the P1/P2
   bugs deeper, not fix them.
   **P1/P3 now resolved — Phase 2 is unblocked on both axes (data + calibration). P2 (the
   def_adj_rapm sign anomaly) is still open** and arguably should gate Phase 2 too, since it
   questions Phase 0's value-model validity for defense, which Phase 2 would build on.

5. **[P5] Re-run Phase 0 (Cells 2-7) once Section 1's upstream fixes land** — real
   `players.position` and the expanded HE RAPM label set both feed Phase 0's inputs/training
   labels. Re-check the value-model coefficients (Cell 4) and top-10 spot-check (Cell 5) for
   material changes, not just a mechanical rerun.
   **[x] Done 2026-06-23** — done as part of closing out Section 1 (notebook re-executed
   twice: once after the position/HE fixes, once after the P1 R_t fix). Top-10 list shifted
   only slightly (minor name-match drift from the barttorvik rerun) — no material change.

**Remaining open: P2 only.** Recommend tackling the `def_adj_rapm` sign anomaly next — it's
the one item left that questions whether Phase 0's current production output (`value_per_100`
for defense-heavy players) is trustworthy as-is.

---

**Next:** start on Section 1.
