# Destination Projection — Historical Backtest & Error Diagnostics Plan

Status: planning, not started. Companion to `docs/models/destination_projection_plan.md` (the
model itself) — this doc scopes a separate, standalone validation project: compare the
Destination-Adjusted Projection model's **actual production output** against **actual realized
stats** for real historical transfers, then mine the residuals for systematic bias patterns.

## 1. Motivation

`destination_projection.py` has one validation path today: `compute_cohort_validation()` /
`run_rolling_origin_cv()`. Both are useful but narrow in three specific ways this plan exists to
cover:

1. **Target scope.** They validate only the `role_usage_delta` submodel's regression target
   (`value_delta`, a RAPM-value-per-100 quantity) — not the final per-game box score
   (`translate_rates_to_destination_stats()`'s output, `player_projections.projected_box_score`)
   that users and the frontend actually see.
2. **Pipeline scope.** They re-fit and evaluate `fit_role_usage_model` in isolation. They do not
   run the full 4-delta pipeline (role/usage + style/skill + roster context + competition tier)
   end to end the way `run_destination_projection()` does in production.
3. **Diagnostic depth.** Cohort slicing exists (tier direction, position group, usage context)
   but stops at reporting Spearman/RMSE per slice. There's no residual-pattern mining — no
   attempt to discover *which kinds of players* (beyond the pre-chosen slices) the model is
   systematically wrong about.

This plan adds a second, complementary validation layer on top — it does not replace or re-derive
`compute_cohort_validation`.

## 2. What this does NOT duplicate

- Does not re-fit `fit_role_usage_model` in a new CV loop — reuses `run_destination_projection()`
  unchanged, called with historical `(target_season, source_season, train_seasons)` triples.
- Does not re-derive `translate_rates_to_destination_stats()` — reads its stored output
  (`player_projections.projected_box_score`) directly.
- Does not re-implement the tier/position/usage cohort logic — reuses those same slice
  definitions where useful, but on box-score residuals instead of `value_delta`, and adds
  clustering as a second, independent diagnostic on top.
- Does not touch `player_archetypes` (M1) — reuses it as a ready-made categorical grouping
  variable before reaching for new unsupervised clustering.

## 3. Real constraint found while scoping this (read before building)

Checked whether the full 4-delta pipeline can actually be re-run point-in-time for a past season,
by reading the actual scoring code (`compute_roster_context_delta`, `compute_style_skill_fit_delta`,
`_add_style_interaction_features` in `destination_projection.py`), not just the plan doc's prose.
The real dependency is narrower than the plan doc's §5.3 description implies:

- **`role_usage_delta` and `competition_level_delta`** — fully historical. `team_season_stats`,
  `hoop_explorer_team_stats`, `player_season_stats` all have real per-season rows back to 2021,
  and `load_historical_transfer_outcomes(engine, train_seasons)` already takes a season list.
- **`roster_context_delta` (`compute_roster_context_delta`, line 912)** — reads **only
  `df["gap_match"]`**, nothing else. Fully historical *if* `gap_match` itself is backfilled for
  the historical season — i.e. Scheme Fit + Gap Matching re-run for that season. That's real
  prerequisite work (separate from this plan), but it is **not blocked** by anything — those two
  models consume barttorvik/HE/hoopR features and `transfer_portal_events`, all season-scoped and
  already ingested 2021-2026. No dependency on `roster_snapshots` at all.
- **`style_skill_fit_delta` (`compute_style_skill_fit_delta`, line 822)** — 5 of its 6 interaction
  terms are also fully historical (team style features from `hoop_explorer_team_stats`/
  `team_season_stats`, or derived from `expected_usage`, which `playing_time_projections` already
  supplies per historical season per §5). **Exactly one term is snapshot-locked:**
  `team_frontcourt_need` (`_add_style_interaction_features`, line 1458-1464), derived from
  `roster_state_features.open_usage_by_position`. `roster_snapshots`
  (`scripts/ingest_roster_snapshots.py`) scrapes barttorvik's *live* rostercast page — one
  snapshot of the *current* roster, no historical archive — so `roster_state_features` (1:1
  derived, `UniqueConstraint("snapshot_id")`) can't be reconstructed as of a past season.

**Net scope of the gap: one interaction term (`team_frontcourt_need`) inside
`style_skill_fit_delta`, not the whole `roster_context_delta` component.** For a historical
backtest row, that one term reflects *today's* frontcourt depth at the destination school, not
the depth that actually existed when the transfer happened — everything else in the pipeline is
cleanly point-in-time re-scoreable (`gap_match` backfill permitting).

Handling, in order of preference:
- **(chosen)** Compute `style_skill_fit_delta`'s residual with and without the
  `team_frontcourt_need` term isolated — report its contribution separately (it's one of 6 summed
  terms, so this is a clean subtraction) rather than let one wrong-season input quietly bias the
  whole component's backtest read.
- (rejected) Zero out `team_frontcourt_need` entirely for historical rows — simpler, but throws
  away a real signal for backtest rows that happen to land in a recent season where today's
  roster is still a decent proxy; isolating and reporting is more informative for the same
  effort.
- (rejected) Back-fill historical roster snapshots from old page caches — no such archive exists;
  would need scraping infra this project doesn't have.

See §3b for a related, separate finding this investigation surfaced (why the other 5
`roster_state_features` columns are unused at all).

## 3b. Related finding: `roster_state_features` is mostly unwired, not by roster-snapshot necessity

While tracing the `team_frontcourt_need` dependency, checked what happens to the other 5 columns
`build_destination_inference_frame` joins in from `roster_state_features`
(`returning_minutes_by_position`, `departing_minutes_by_position`,
`incoming_transfer_minutes_by_position`, `open_minutes_by_position` for non-frontcourt positions,
`returning_production`, `returning_player_impact`) — they're merged into the inference frame
(line 1371) but **never read by any active formula**. Only `open_usage_by_position` (via
`team_frontcourt_need`) is consumed.

This is not a deliberate "only bigs need a roster-based signal" design — the plan doc's own §5.2
table specifies two more roster-based interactions that were never wired that way:
`passing_creation` was meant to pair with "open ball-handler minutes (few returning primary
creators)" and `shot_creation_usage` with "crowded creation (strong returning guards)" — both
phrased as roster-composition signals, i.e. a backcourt analog of `team_frontcourt_need` sourced
from `returning_minutes_by_position`/`departing_minutes_by_position`. The actual implementation
substitutes `expected_usage`-derived proxies instead (`open_usage_signal`, `team_usage_crowding`
— both computed from `playing_time_projections`, not `roster_state_features`). That substitution
may well be a reasonable simplification (per-player expected usage is arguably a richer signal
than a coarse team-level backcourt aggregate), but it was never reconciled against the original
spec, and it leaves `team_frontcourt_need` as an asymmetric one-off rather than a completed
"need signal per position group" pattern.

Not fixing this here — flagging it as a real, separate finding worth a decision (either delete
the 5 dead columns' join if the usage-based proxies are being kept intentionally, or add the
missing backcourt-need interaction terms to match the original §5.2 spec). Out of scope for this
backtest plan; noting it in `docs/models/destination_projection_plan.md` as its own dated finding
is the right home for it, not a fix bundled into this backtest work.

## 4. Backtest population

Source: `transfers` table, matched rows only (`player_id` and `season` both set), joined to:
- `player_season_stats` (or `hoop_explorer_player_stats` for RAPM-adjacent fields) at
  `(player_id, dest_school_id, season = transfers.season + 1)` — matching the same
  `dest_season = t.season + 1` convention already fixed in the training SQL
  (`destination_projection_plan.md` §20, P1).
- Filter to seasons where the **destination season has already completed** and its actual stats
  are ingested: `dest_season <= 2026`. `2027` is the live/current inference target — has no
  actual outcome yet, excluded from backtest by construction.
- Apply the same `MIN_GAMES`-style floor already used elsewhere (Phase 0's `MIN_GAMES`,
  Destination Projection's own row-count conventions) — drop players with too few games played
  in the destination season for their per-game stats to be a meaningful comparison target
  (injury, late-season addition, DNP-heavy bench role). Reuse whatever floor constant Phase 0
  already defines rather than inventing a new one.

Expected size: `transfers` currently yields ~5,324 matched rows total (2022-2026, per the
247Sports matching overhaul in CLAUDE.md TODO #9) before any games-played floor — real number to
confirm once the query is written, not assumed here.

## 5. Point-in-time re-scoring methodology

For each historical `dest_season` in `{2022, ..., 2026}`:

1. `target_season = dest_season`, `source_season = dest_season - 1`,
   `train_seasons = [s for s in available_seasons if s < dest_season]` — strictly prior seasons
   only, same temporal-holdout discipline `run_rolling_origin_cv` already uses. This is the one
   piece of new "training" happening here, and it's just a parameter choice into existing code,
   not new modeling logic.
2. Requires `playing_time_projections` to exist for that historical `target_season` — check
   first; if missing, backfill via the **existing, unmodified** `scripts/run_playing_time.py
   --target-season {dest_season} --source-season {dest_season-1}` (this is a real prerequisite,
   not optional — `run_destination_projection` hard-gates on it).
3. Call `run_destination_projection(engine, target_season=dest_season, source_season=dest_season-1,
   train_seasons=train_seasons, player_id_subset=<backtest population for this season>,
   dry_run=False)` — writes real destination rows to `player_projections` under model_version
   `player-destination-proj-v1`, keyed by `(player_id, school_id, season)`. Existing partial
   unique index already supports multi-season coexistence (checked: keyed on
   `player_id, school_id, season, model_version`, not "latest run wins") — historical backfill
   rows and the current 2027 production rows do not collide.
4. Per §3, treat `roster_context_delta` in these historical rows as using today's live
   `roster_state_features` (not point-in-time) — record this in the output so downstream
   diagnostics can filter it in or out.

## 6. Comparison / residual computation

New pure module: `src/portalpoint/modeling/destination_backtest.py`. Functions:

- `load_backtest_population(engine, min_dest_season=2022, max_dest_season=2026) -> DataFrame` —
  the query in §4.
- `load_actual_outcomes(engine, population_df) -> DataFrame` — pulls real per-game stats
  (points/rebounds/assists/3PA/3P%/usage/etc.) from `player_season_stats` +
  `hoop_explorer_player_stats` for each `(player_id, dest_school_id, dest_season)`.
- `load_projected_outcomes(engine, population_df) -> DataFrame` — reads
  `player_projections.projected_box_score` (+ `explanation`, for delta attribution) for the same
  keys, `projection_mode='destination'`.
- `compute_residuals(actual_df, projected_df) -> DataFrame` — one row per player-transfer, one
  column per stat: `residual = actual - projected`, plus signed % error. No re-fitting; pure
  arithmetic over already-computed values.
- `summarize_residuals(residual_df, group_by=None) -> dict` — mean/median/RMSE/MAE per stat,
  optionally grouped (position, tier direction, usage delta bucket, archetype) — reuses the
  cohort *definitions* from `compute_cohort_validation` where they overlap (tier direction,
  position group) but operates on box-score residuals across every stat, not just `value_delta`.

## 7. Diagnostics beyond per-cohort summary stats

Two complementary passes, per the user's "clustering or something else" framing — do both, they
answer different questions:

**(a) Grouped by known categories (extends existing cohort logic, doesn't replace it).**
Slice `compute_residuals` output by dimensions already computed elsewhere in the codebase —
`player_archetypes.archetype_label` (M1, already real for every player-season), position,
tier-direction, usage-delta bucket, class year, minutes-delta bucket. Cheap, interpretable,
answers "is the model worse for post players than guards" directly. Do this first — it's nearly
free given existing tables, and probably resolves most of what's actionable.

**(b) Unsupervised clustering on residual vectors (new, exploratory).**
Build a per-player residual vector across all projected stats (normalized/scaled), run k-means
(or hierarchical, given likely small-to-moderate n) to find clusters of players who share a
*residual pattern* — e.g. "usage overprojected + 3P% underprojected" as one discovered cluster —
that may not align with any pre-defined cohort in (a). Purpose is to surface a bias mode nobody
thought to slice by ahead of time. Treat this as exploratory/hypothesis-generating, not a metric
to optimize — cluster count and interpretation need human review each run, same posture M1/M2
clustering notebooks already take (interactive, not a non-interactive `run_*.py` script).
Reuse `sklearn.cluster.KMeans` directly; no new shared library code needed unless a second use
case shows up later.

Report both together: does the archetype-based cohort split in (a) already explain most of the
variance the residual-clustering in (b) finds, or does (b) surface something (a) misses? That
comparison is itself a useful output, not just "run both and list results."

## 8. Deliverables

- `src/portalpoint/modeling/destination_backtest.py` — pure functions per §6, unit-testable
  without a DB connection (same pattern as `test_destination_projection.py`).
- `scripts/run_destination_backtest.py` — thin CLI: backfill check → historical re-score (§5,
  skips seasons already backfilled) → residual computation → cohort + cluster summary → writes a
  metrics dict, logs to MLflow as a **non-promoting** run (this is a diagnostic report, not a
  model candidate — do not wire it into `maybe_promote`).
- `notebooks/models/destination_backtest.ipynb` — interactive cohort/cluster exploration,
  cluster-count tuning, visual residual review, matching the interactive-vs-script split every
  other model in this repo already follows.
- Short results section appended to `docs/models/destination_projection_plan.md` once real
  numbers exist (§21+, following that doc's own convention of dated, numbered findings).

## 9. Non-goals

- Not retraining or replacing any existing model. Output is diagnostic, not a new
  `model_version`.
- Not fixing the roster-context-delta historical gap (§3) — flagged and reported around, not
  solved, in this pass.
- Not building general feature-drift/accuracy-decay monitoring (CLAUDE.md TODO #8) — that's
  ongoing production monitoring across all models; this is a one-time (or periodically re-run)
  historical study of one model.
- Not automatically feeding findings back into `TODO #10`'s roadmap items — this plan produces
  evidence; deciding which of (a)-(h) to act on based on it is a separate follow-up decision.

## 10. Decisions (resolved 2026-07-14)

1. **Population = all historical matched transfers** (§4), no additional sampling — every
   matched `transfers` row with `dest_season <= 2026` and enough games played to pass the
   games-played floor. No per-season pooling unless a real season turns out too thin after the
   floor is applied (check, don't pre-decide).
2. **Stats = `player_projection.py`'s existing skill taxonomy**
   (`RATE_PER_40_SKILLS`/`OFFENSE_SKILLS`/`DEFENSE_SKILLS`), not a separately-invented box-score
   stat list — keeps this backtest's vocabulary identical to Phase 0/2a's, so residual findings
   here map directly onto that model family's own skill names instead of requiring a translation
   layer.
3. **`playing_time_projections` backfill is scoped to observed transfers only, not all-pairs.**
   `run_playing_time.py` already supports this with zero code changes:
   `--include-school-ids` (destination schools actually appearing in that season's backtest
   population) and `--include-player-ids` (players actually transferring that season) — both
   flags exist today (`scripts/run_playing_time.py` args, wired into `fit_score_school_ids` /
   `apply_player_filter`). Per historical `target_season`, call:
   ```
   uv run python scripts/run_playing_time.py \
     --target-season {dest_season} --source-season {dest_season - 1} \
     --include-school-ids <union of that season's destination school_ids> \
     --include-player-ids <union of that season's transferred player_ids>
   ```
   This replaces the full candidates×365-schools cross join with candidates×(a few dozen
   schools), cutting backfill compute by roughly the same ratio. Known minor inefficiency, not
   worth fixing: `build_inference_pairs` still cross-joins all listed players against all listed
   schools in the batch before `apply_player_filter` trims non-real pairs — so if 5 seasons'
   worth of distinct schools/players get batched together carelessly, some wasted rows get
   computed and discarded. Keep each `run_playing_time.py` call scoped to a single season's own
   population to avoid unnecessarily inflating that cross join.
   No collision risk with production `playing_time_projections`/`role_fit` rows — these calls
   target historical seasons (2022-2026), production inference targets 2027.

## 11. Remaining open question — resolved 2026-07-14

Real population size (§4's query, run read-only against the live DB): **3,603 matched historical
transfers** (2022-2026), **3,301** after the games-played floor (`MIN_GAMES=5`, reused from Phase
0, not reinvented). Per-season: 2022=435, 2023=565, 2024=710, 2025=932, 2026=961 — no season is
thin enough to need pooling; smallest (2022, right after the 247Sports matching overhaul) still
clears `min_group_n=10` comfortably for every cohort split tried. No pooling decision needed.

**Superseded by §13 — 2022 dropped from scope entirely** (not a pooling question, a hard
infeasibility): see §13's real backfill finding.

## 13. Real backfill finding (2026-07-14): 2022 cannot be point-in-time re-scored at all

First real `run_playing_time.py` invocation (`--target-season 2022 --source-season 2021`, scoped
to 2022's 214 schools/435 players) failed: `RuntimeError: Need at least two train seasons, got
[2021]`. Root cause: barttorvik data (this project's earliest-ingested source) starts at season
2021 — there is no season before 2021 to train the Playing Time model's minutes/usage GBT on, so
scoring target_season=2022 (source_season=2021) can never have the ≥2 historical seasons that
model hard-requires. This is not a bug — it's the same structural limit
`destination_projection.run_rolling_origin_cv` already encodes (it never evaluates the first
available season either, `seasons[1:]`, precisely because there's nothing prior to train on for
it).

**Decision: drop 2022 from the backtest population — `min_dest_season` moves from 2022 to 2023.**
`--target-season 2023 --source-season 2022` trains on `[2021, 2022]` (2 seasons), clears the
check. Real population impact: 3,603 → 3,168 matched transfers (loses 2022's 435 rows, ~12%),
3,301 → ~2,900 after the games-played floor (exact number pending the 2023-2026 backfill
completing) — still no thin seasons per §11's per-season breakdown (565/710/932/961). No further
scope reduction expected; 2023-2026 all have 2+ real prior seasons of barttorvik data to train on.

## 12. Built (2026-07-14) — module + script + notebook skeleton, no historical backfill run yet

Per the "build skeleton first, run the real backfill later" sequencing decision:

- **`src/portalpoint/modeling/destination_backtest.py`** — `load_backtest_population`,
  `load_actual_outcomes`, `load_projected_outcomes`, `compute_residuals`, `summarize_residuals`,
  `enrich_with_cohorts` (archetype at source season + tier direction, reusing
  `destination_projection.assign_competition_tiers` — not a forked tier definition).
  `BACKTEST_STATS` compares the 6 fields actually present in `projected_box_score`
  (pts/reb/ast/stl/blk/tov per game) against their `player_season_stats` counterparts — this is a
  narrower, more direct correction to §10's decision #2 ("use `player_projection.py`'s skill
  taxonomy"): the 11-skill taxonomy (`shooting_3p`, `passing_creation`, ...) is the Kalman layer's
  internal skill-percentile vocabulary, not stored per-game production: there's no real "actual"
  to compare a skill percentile against without a translation step. The box-score fields are
  already the translated, comparable-to-real-stats output — the right vocabulary was the one
  already being written to the DB, not a separately-invented one.
- **`scripts/run_destination_backtest.py`** — checks per-season readiness
  (`player_projections` destination rows present for that `dest_season`/`model_version`), reports
  missing seasons with the exact manual commands to fill them, or (`--backfill`) invokes
  `run_playing_time.py` + `run_destination_projection.py` itself, scoped per §10's
  `--include-school-ids`/`--include-player-ids`. Logs a **non-promoting** MLflow run (no
  `maybe_promote` call) — flags in its own docstring and a runtime warning that
  `run_destination_projection.py`'s non-dry-run path calls `maybe_promote` on its own, using a
  historical/population-restricted `total_resid_std` that isn't necessarily representative —
  review any resulting promotion, don't trust it blindly.
- **`notebooks/models/destination_backtest.ipynb`** — interactive: load/residual cells (§1),
  §7a cohort splits (position/archetype/tier direction, §2), §7b k-means residual-vector
  clustering with a K-sweep inertia check (§3), and a cluster-vs-cohort cross-tab (§4) — answers
  the plan's own closing question (does §7a already explain what §7b finds).
- **20 new unit tests** (12 `test_destination_backtest.py` covering `compute_residuals`/
  `summarize_residuals`; matches `test_destination_projection.py`'s own convention of only
  unit-testing pure transform functions, not the DB-touching `load_*`/`enrich_with_cohorts`
  functions). 107 tests green across the touched files.
- **Read-only smoke test against the real DB** (no writes): `load_backtest_population` +
  `load_actual_outcomes` confirmed working end-to-end with the real row counts in §11.
  `load_projected_outcomes` correctly returns 0 rows — no historical `player_projections`
  destination-mode backfill has been run yet, exactly as expected before §5's backfill step
  happens.

**Not done yet:** the actual historical backfill (§5) — `run_playing_time.py` +
`run_destination_projection.py` for each of the 5 historical seasons, scoped to that season's
population. Real compute, explicitly held for a separate go-ahead per the plan's original §10.3
caution. Once backfilled, rerun `scripts/run_destination_backtest.py` (or the notebook) to get
real residual/cohort/cluster results.
