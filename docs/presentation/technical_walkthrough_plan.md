# PortalPoint — Technical Walkthrough Plan

**Audience:** Technical review. Full model detail per step. Persona: Gonzaga coaching staff evaluating three portal targets.

---

## The Question Stack

Each model narrows a different dimension of uncertainty. This is the through-line of the presentation.

| Question | Model(s) | Status |
|---|---|---|
| Who are we as a program? | Team Clustering (M2) | ✅ |
| Who is this player, type-wise? | Player Clustering (M1) | ✅ |
| Does this player fit our system? | Scheme Fit (M3) | ✅ |
| Does this player fill our roster holes? | Gap Matching | ✅ |
| How good is this player, absent context? | Player Projection (Ph 0 → Ph 2a) | ✅ |
| How many minutes will they actually get here? | Playing Time (M4) | ✅ |
| What will they contribute *at our program*? | Destination Projection | ✅ |

---

## Persona: Gonzaga Bulldogs

**System:** Rim-pressure offense, strong assisted-shot rate, up-tempo pace, and a jump-shot funnel defensive profile. High-major (WCC). Heavy portal recruiter.

**Roster situation:** Significant minutes departing across all five positions (SG: 143 min/g, SF: 111, PG: 101, PF: 76, C: 69). Primary needs: backcourt depth and a rim-running center.

**Three targets (real portal candidates, complete data across all model sections):**

| Player | Pos | From | Conference | Transfer direction | player_id |
|---|---|---|---|---|---|
| **Daeshun Ruffin** | PG | Jackson St. | SWAC | Low-major → high-major (+3 tiers) | 7578028029286400392 |
| **Elijah Crawford** | PG | Illinois Chicago | MVC | Mid-major → high-major (+1 tier) | 6910442837336165955 |
| **Kyle Evans** | C | UC Irvine | Big West | Mid-major → high-major (+1 tier) | 9023425631028193516 |

**2026 season stats:**

| Player | GP | PPG | Usage% | AST% | TS% | 3P Rate | Rim% | BPM | OrtG |
|---|---|---|---|---|---|---|---|---|---|
| **Ruffin** | 28 | 23.3 | 36.2 | 45.2 | 55.2 | 29.2% | 37.8% | +4.06 | 108 |
| **Crawford** | 23 | 14.1 | 31.1 | 43.0 | 53.0 | 28.5% | 22.1% | +2.61 | 107 |
| **Evans** | 33 | 12.1 | 16.1 | 6.4 | 64.8 | 3.5% | 76.3% | +3.01 | 129 |

---

## Entry Point: Portal Search

```
GET /api/players/search?available_only=true&position=PG,C
```

`available_only=true` filters to `player_team_fit_scores.is_portal_candidate = true`. This flag is set by `portalpoint.modeling.availability.sync_portal_candidate_flags()`, called on every `ingest_transfers_247sports.py` run and every `run_gap_matching.py` run. It identifies players with a matched `Entered` or `Committed` row in `transfer_portal_events` for the current season. The flag scopes the recommendation surface without restricting what the underlying models score — all ~9.7M player×school rows in `player_team_fit_scores` remain intact; portal candidacy is a filter layer, not a modeling constraint.

---

## Step 1 — Who Are We? Team Clustering (M2)

**Question:** What system profile does Gonzaga run, and which stylistic features define membership in that cluster?

### Technical Approach

**Algorithm:** K-Means with two independent scalers — one for offense feature group, one for defense feature group. Motivation: prevents scale leakage across dimensions (a team's 110 offensive rating variance should not dominate its defensive cluster assignment). Standard scaler applied within each group before concatenation.

**Features:**
- Offense: 3PT attempt rate, rim attempt rate, assisted shot %, transition PPP, pace (possessions/40), mid-range rate
- Defense: opp 3PT rate allowed, transition defense PPP, scramble defense PPP, opp assisted %

**Sources:** `hoop_explorer_team_stats` (transition/scramble/assisted %), `player_season_stats` (pace, 3PT/rim rates), `team_season_stats` (efficiency). Joined on `school_id × season`.

**K selection:** Silhouette score sweep across k=4–16 + elbow inspection of inertia. Final K chosen per-group (offense K and defense K may differ). Run interactively in `notebooks/models/team_clustering.ipynb`; fixed params used by `scripts/run_team_clustering.py`.

**Label assignment:** Manual, after centroid inspection. Coaching-vocabulary names (e.g., "Rim Pressure Offense", "Pack-Line Halfcourt", "Fast-Break Transition"). Labels stored in `team_system_profiles.system_label`.

**Output:** `team_system_profiles` — one row per school×season, with offense cluster, defense cluster, and composite label. MLflow artifact: `team-k{K}-v2-2026` in `s3://portalpoint-data/models/`.

### Gonzaga Profile

- **Label:** "Rim Pressure Offense / Jump-Shot Funnel Defense"
- **Gonzaga offensive feature values:** 3PT rate 30.8%, assisted% 57.5%, rim rate 38.4%, pace 69.5 pos/40, transition frequency 23.8%
- **Defense centroid features:** funnels opponents into jump-shot volume while protecting the rim and transition floor balance
- **Peer programs in same cluster:** visible in the team cluster scatter and centroid heatmap generated from `team_system_profiles`

### Visual Callouts

- **VISUAL 1a:** Elbow + silhouette curves showing K selection for offense and defense groups
- **VISUAL 1b:** 2D PCA/UMAP scatter of all team-season vectors, colored by cluster label — Gonzaga highlighted, peer cluster members labeled
- **VISUAL 1c:** Centroid feature heatmap across all clusters — shows what makes "Rim Pressure Offense / Jump-Shot Funnel Defense" distinct from neighbors
- **VISUAL 1d:** Table of 5–8 peer programs in Gonzaga's cluster with their key feature values

---

## Step 2 — Who Are These Players? Player Clustering (M1)

**Question:** What archetype is each target, and does that archetype family belong in Gonzaga's rim-pressure system?

### Technical Approach

**Algorithm:** K-Means, k=9, single joint scaler across all features. K selected via silhouette sweep k=6–15 in `notebooks/models/player_clustering.ipynb`.

**Features:** barttorvik (`ortg`, `usage`, `3par`, `rimar`, `bpm`, `obpm`, `dbpm`) + Hoop Explorer (`pos_confidence_pg/sg/sf/pf/c` — real position probability distributions, not hardcoded labels; position was hardcoded `'G'` for all 13,303 players until `_infer_position()` fix + ingest rerun 2026-06-23) + hoopR game logs (zone frequencies, creation rate by shot type).

**Label assignment:** Manual after centroid inspection. 9 archetypes: "3-and-D Wing", "Isolation Scorer", "Stretch-5 / Playmaking Big", "Point Guard Initiator", "Rim-Runner", "Secondary Creator", "Defensive Anchor", "Versatile Forward", "Volume Scorer". Stored in `player_archetypes.archetype_label`.

**Output:** `player_archetypes` — one row per player×season, with cluster index, archetype label, and distance to centroid (confidence proxy). MLflow artifact: `kmeans-k9-v1-2026`.

### Results

| Player | Archetype | Key distinguishing features |
|---|---|---|
| **Ruffin** | Lead Scoring Playmaker | Usage 36.2%, AST% 45.2%, high creation volume, low 3PT rate (29.2%), mixed rim/3PT shot diet |
| **Crawford** | Lead Scoring Playmaker | Usage 31.1%, AST% 43.0%, similar creation profile to Ruffin but lower volume; BYU bench → UIC starter arc |
| **Evans** | Post Scoring Big | Rim% 76.3%, BLK% 11.8%, DReb% 21.8%, OrtG 129 — elite rim-running efficiency, near-zero 3PT |

Ruffin and Crawford share the same archetype (Lead Scoring Playmaker), which creates a narrative tension: the cluster is the same, but the tier gap and usage context are very different. Evans's archetype (Post Scoring Big) is orthogonal to both and maps directly onto Gonzaga's center vacancy.

### Visual Callouts

- **VISUAL 2a:** Silhouette curve showing k=9 selection
- **VISUAL 2b:** 2D UMAP scatter of all player-season vectors colored by archetype — three targets highlighted with labels
- **VISUAL 2c:** Per-archetype centroid feature heatmap (9 rows × key features) — highlights what separates lead scoring playmakers from post scoring bigs
- **VISUAL 2d:** Table of 5 peer players in each target's cluster (name, school, season) — grounds archetypes in recognizable players

---

## Step 3 — Does Each Player Fit Our System? Scheme Fit (M3)

**Question:** How similar is each player's on-court style to Gonzaga's system demands, quantitatively?

### Technical Approach

**Algorithm:** Cosine similarity between a 5-dimensional player style vector and a 5-dimensional school style vector. Both normalized to unit length before similarity computation; cosine distance in this space measures directional alignment (shape of play, not volume).

**5 dimensions:** `[3PT%, rim%, usage%, assisted%, pace_contribution]`

**Scale:** `scheme-cos-v3`. All-pairs — every eligible player×school×season (~9.7M rows). Replaced top-50-per-player approach so clustering and projection models use the full population, not a pre-filtered slice. Full delete+rebuild per season (not incremental) to avoid stale rows from roster changes.

**Optimization:** `scheme_breakdown()` — per-pair per-element breakdown — was the dominant compute cost once top-k was removed. Vectorized via numpy broadcasting, same pattern Gap Matching already used. Reduces per-season runtime significantly.

**Output:** `player_team_fit_scores.scheme_fit` (0–100 scale). School style vector sourced from `team_system_profiles`; player style vector from `player_season_stats` + `hoop_explorer_player_stats`.

### Results

| | 3PT Rate | Rim Rate | Usage% | AST% | **Scheme Fit** |
|---|---|---|---|---|---|
| **Gonzaga system** | 30.8% | 35.9% | — | — | — |
| **Ruffin** | 29.2% | 37.8% | 36.2 | 45.2 | **99.6** |
| **Crawford** | 28.5% | 22.1% | 31.1 | 43.0 | **89.7** |
| **Evans** | 3.5% | 76.3% | 16.1 | 6.4 | **80.8** |

Ruffin's 99.6 is the highest scheme fit in the portal candidate pool for Gonzaga — his 3PT/rim balance and pace contribution align almost perfectly with the team style vector. Crawford scores 89.7, penalized by lower rim rate. Evans scores 80.8 — the cosine penalty comes from his near-zero 3PT and assist dimensions; the rim and usage dimensions align but the other three pull him down. All three are above the median portal candidate score, making Gonzaga's system a strong stylistic match for each.

### Visual Callouts

- **VISUAL 3a:** Side-by-side 5-column table (as above) — Gonzaga target row vs. each player; delta column highlighting mismatches in red/green
- **VISUAL 3b:** Radar/spider chart — Gonzaga style vector vs. each player's vector (3 overlapping players + Gonzaga on one chart, or one chart per player)

---

## Step 4 — Does Each Player Fill Our Roster Holes? Gap Matching

**Question:** Beyond stylistic fit, does each player's skill profile map onto the *specific gaps* left by Gonzaga's departing players?

### Technical Approach

**Algorithm:** Soft-weighted cosine similarity between a player's skill vector and a school's *roster gap vector* — the delta between the school's historical target skill mix and its current post-departure composition.

**Departure awareness:** `filter_departed()` removes outgoing players before computing roster state. The gap vector is computed against who is *actually* still on the roster, not the previous year's full squad. This is what makes the gap meaningful — a school that just lost a 3PT specialist has a gap vector pointing toward 3PT creation; a school that lost a rim protector points toward shot-blocking/rim defense.

**Soft weighting:** Positional need weighted higher but not exclusive. A player at an adjacent position who covers the departed player's skill profile can still score well — the model scores *skill gap fill*, not just roster slot fill.

**Model version:** `gap-cos-v3`. All-pairs (same universe as Scheme Fit). Requires `run_scheme_fit.py` to have run first — Gap Matching layers `gap_match` onto existing `player_team_fit_scores` rows.

**Scheme context preservation:** `load_existing_scheme_context(engine, season, school_ids)` — a per-chunk indexed query that reads back real `scheme_fit` values before writing gap scores. Previously a full-table preload into one Python dict (~64 min at 9.7M rows). Replaced with `ix_fit_scores_school_season_candidate` index lookup per school chunk — same correctness, fraction of the cost.

**Output:** `player_team_fit_scores.gap_match` (0–100). Also syncs `is_portal_candidate` flag per season.

### Gonzaga Roster State

**Departing minutes by position (2026 snapshot):** SG 143 min/g, SF 111, PG 101, PF 76, C 69 — every position has a real gap. Note: `roster_state_features.returning_minutes_by_position` is all-zero for this snapshot (2026-06-21 data quality issue — barttorvik rostercast had no committed incoming players recorded yet). The departing minutes are real and drive the gap vector accurately; the gap chart is correctly all-red (net minutes open at every position).

**Post-departure roster:** Thin across the board, with SG and PG as the largest raw-minute gaps. The gap vector rewards players whose skill profile maps onto the departed production shape, not just the position.

### Results

| Player | Gap Match | Key driver |
|---|---|---|
| **Ruffin** | **69.3** | PG position maps onto the 101-min PG gap; high usage/creation profile fills a real initiator void; soft-weighted penalty for his non-3PT shot diet (Gonzaga's departed PG production skewed toward assisted looks) |
| **Crawford** | **58.5** | Same position as Ruffin (competing PG), lower gap score because Crawford's lower-volume profile is a weaker match to the high-minute PG gap shape |
| **Evans** | **67.8** | C position maps cleanly onto the 69-min C gap; elite rim rate (76.3%) and block% (11.8%) align directly with the departed center's skill fingerprint; highest gap_match among all C portal candidates at Gonzaga |

### Visual Callouts

- **VISUAL 4a:** Stacked bar chart — Gonzaga's historical target minute distribution by skill group (e.g., "off-ball shooting", "perimeter creation", "rim finishing", "frontcourt") across last 3 seasons, vs. current post-departure composition
- **VISUAL 4b:** Gap vector visualization — the delta between target mix and current mix shown as a bar chart (positive = need, negative = surplus)
- **VISUAL 4c:** Player skill profile vs. gap vector — one chart per player showing their skill vector alongside the gap shape, and the resulting gap_match score

---

## Step 5 — How Good Is Each Player? Player Projection (Ph 0 → Ph 2a)

**Question:** If this player played for an average D1 team at average competition, what would their skill output and value look like next season? And how does cross-season development trajectory change that estimate?

### Why Show All Three Phases

Phase 0 is in production. Phase 2a is the technically complex, novel contribution — cross-season state-space modeling that adds a development curve and persistence layer on top of per-season Kalman estimates. Showing all three phases demonstrates the full model lineage and justifies why Phase 2a beats Phase 0 on offense.

---

### Phase 0 — Empirical-Bayes Shrinkage + Ridge Value Model

**Stage A: Shrinkage.** Each player's observed per-40 rates for 10 skills are shrunk toward a `pos_class × season` prior. Shrinkage weight = `games_played × min_pct` — players with more games and higher minute share shrink less (more signal). Handles sparse data: an 8-game player gets pulled strongly toward the positional prior; a 30-game starter barely moves.

**10 skills:** shooting touch (eFG%), 3PT creation rate, rim creation rate, mid-range creation rate, usage rate, assist rate, turnover rate, total rebound rate, steal rate, block rate.

**Stage B: Ridge value translation.** Shrunk rates feed separate Ridge regressions trained against Hoop Explorer `off_adj_rapm` (offense features) and `def_adj_rapm` (defense features) as labels. Feature sets split into `OFFENSE_SKILLS` and `DEFENSE_SKILLS` — the split was a real tradeoff: defense R² dropped ~30% relative (0.119→0.083) when offensive skills were removed, plausibly because shared "two-way IQ" features (assist rate, turnover rate) carry information about defensive positioning. User decision: keep the split for interpretability.

**Notable coefficient finding:** Block/steal have *negative* Ridge coefficients against `def_adj_rapm` — consistent with analytics literature (gambling for blocks trades off positioning). Left as-is, not a pipeline artifact.

**Output:** 27,047 player-seasons (2021–2026) in `player_projections`, `model_version='player-projection-shrinkage-v1'`.

---

### Phase 1 — Per-Season Scalar Kalman Filter/Smoother

**Algorithm:** A scalar Kalman filter+smoother applied independently to each skill, using game-log observations within a single season.

**Observation model:** `y_t = x_t + ε_t` where `x_t` is the latent true skill level and `ε_t ~ N(0, R_t)`. Key fix: `R_t` (observation noise) must scale differently for Bernoulli rates (shooting) vs. Poisson count-rates (usage, assists, rebounds, etc.). `_r_numerator()` implements the correct scaling — `R_t = p(1-p)/minutes` for Bernoulli skills vs. `R_t = rate/minutes` for Poisson skills. Before this fix, count-rate skills had `R_t` pinned at the wrong scale, driving process variance `Q` to its upper search bound (1e-6 to 2.0, later widened to 100.0 after fix). Phase 0/Phase 1 correlation jumped from 0.15–0.39 to 0.50–0.81 for all affected skills after the fix.

**Output:** Smoothed skill estimates + uncertainty (posterior variance) per player×skill×game. Validation only — does not write to `player_projections`.

---

### Phase 2a — Cross-Season State-Space Model

**What it adds:** A season-grain Kalman layer on top of Phase 1's per-season output. Fits cross-season persistence (`ρ`) and drift terms (development curve, transfer adjustment, competition-level change) across the 2020–2026 backfill.

**Key design decisions and findings:**

- **`ρ` is not jointly identifiable with drift terms** on this data. Confirmed three ways: naive joint MLE, pooling from the long-career subset, and a Gaussian prior penalty all failed to converge. Fix: estimate `ρ` via simple lag-1 autocorrelation (stable, interpretable) and hold it fixed during drift fitting. This is the most technically important finding from Phase 2a.

- **Drift terms fitted via Nelder-Mead** (6 free dimensions per skill). Real bottleneck was `fit_season_model` — not the intra-season Q search everyone had assumed. Fixed via: (1) population subsampling for the search (`max_sequences_for_search`), same justification as Phase 1's `fit_q_mle`; (2) parallelizing `fit_all_skills` (10 skills) and `build_season_skill_states` (season×skill tasks) across `ProcessPoolExecutor`.

- **OOM fix:** Eager construction of 70 `(season, skill)` tasks each holding a full `obs_df.copy()` (~4.7GB resident) caused an OS-level OOM kill before any pool submission. Fix: each task receives only the 3 columns it needs.

- **Gap D/G real results (2026-06-24):** Phase 2a beats Phase 0 on offense in all 3 rolling-origin folds (~5–6% RMSE reduction each fold). Fold 3/2026: off_rmse 1.633 vs 1.736, off_r2 0.504 vs 0.437. Defense is a tie (both ~R² 0.10–0.12). Satisfies Issue #37 acceptance language directly.

- **Gap B (context adjustment) unresolved regression:** Phase 2a with context adjustment is *worse* on real data (fold 3 off_rmse 1.987 vs. 1.633 without it). Root cause not yet identified. Reference results use no-context config.

**11th skill — foul_discipline:** Added as a Phase 1/2 skill (not Phase 0 — no season-grain fouls column in barttorvik). Fits cleanly (ρ=0.39, Q=0.81, no bounds hit). Phase 0 stays at 10 skills; the asymmetry is intentional and documented.

**Output:** `player_projections`, `model_version='player-projection-phase2a-v1'`. Production status: Staging (Δ=+1.0% vs Phase 0 champion — real improvement, doesn't clear the 5% auto-promote threshold). API default remains Phase 0.

---

### Projection Results

```
GET /api/players/7578028029286400392/projection        # neutral, defaults to Phase 0
GET /api/players/7578028029286400392/projection?model_version=player-projection-phase2a-v1
```

| Player | 2026 PPG / TS% | Ph0 value/100 | 90% CI | Ph2a value/100 | Δ Ph2a vs Ph0 |
|---|---|---|---|---|---|
| **Ruffin** | 23.3 / 55.2% | **+3.33** | [+0.49, +6.16] | +2.02 | −1.31 |
| **Crawford** | 14.1 / 53.0% | **+2.69** | [−0.15, +5.52] | +2.00 | −0.69 |
| **Evans** | 12.1 / 64.8% | **+2.12** | [−0.71, +4.96] | +1.76 | −0.36 |

Phase 2a is *more conservative* than Phase 0 for all three. This is the expected behavior of the cross-season Kalman: it regresses toward the multi-year prior and applies a persistence discount for players who haven't yet shown multi-season consistency at this level. Ruffin's large Ph2a pullback (−1.31) reflects his limited career history in the DB (2022, 2025, 2026 — gap year). Crawford's 2025 season was 7.5% minutes at BYU (near-zero signal), so the smoother discounts it and the 2026 breakout is treated with partial skepticism. Evans has the flattest Ph0→Ph2a delta (−0.36), consistent with his steady multi-season progression at UC Irvine. In all three cases the Ph0 CI is wide (±2.8), indicating high per-season uncertainty — this is expected for mid-majors where fewer cross-referencing data points exist in the HE RAPM training set.

### Visual Callouts

- **VISUAL 5a:** Per-player skill comparison table: last season observed rate vs. Phase 0 shrunk rate vs. Phase 2a smoothed rate, for all 10 skills.
- **VISUAL 5b:** Phase 0 shrinkage weight chart — bar showing `games_played × min_pct` for each player alongside how much their estimates moved from raw to shrunk (small movement = high confidence; large movement = sparse signal).
- **VISUAL 5c:** Phase 2a cross-season trajectory — for each player, a line chart showing their skill estimate (± uncertainty band) across seasons 2021–2026 with the Phase 2a Kalman smoothed estimate overlaid. Demonstrates where the persistence/drift model adds vs. subtracts from the per-season estimate.
- **VISUAL 5d:** Phase 0 vs. Phase 2a scatter (all players in DB) — show that Ph2a is uniformly better on offense, neutral on defense. Highlight the three targets.

---

## Step 6 — How Many Minutes Will They Get? Playing Time (M4)

**Question:** Given this specific player's profile and Gonzaga's specific roster context, what is the realistic minutes projection and confidence interval?

### Technical Approach

**Algorithm:** `HistGradientBoosting` (gradient boosted trees with histogram binning — handles mixed data types, robust to missing values, faster than standard GBT at this scale). Two outputs: `minutes_per_game` and `usage_rate`. Quantile regression head for CI (targeting 90% coverage).

**Features from `INFERENCE_SQL`** — a 12-table CTE:
1. `player_team_fit_scores` — scheme_fit, gap_match, is_portal_candidate (9.7M row base)
2. `player_projections` — neutral skill projections, projected off/def RAPM
3. `player_season_stats` — historical minutes, usage, ortg
4. `hoopr_player_game_logs` — game-level consistency metrics (minutes variance, usage variance)
5. `roster_state_features` — returning/departing/incoming minutes by position and skill group
6. `hoop_explorer_player_stats` — pos_confidence distributions
7. `team_season_stats` — target school's pace, system features (for usage-context fit)
8. `transfers` — transfer history (repeated portal entrants have different projections)

**Efficiency finding:** Original `--school-chunk-size 25` re-evaluated 6 player-side CTEs 15× (once per 25-school chunk out of 365 total) — none of those CTEs depend on `school_ids`. 15× redundant CTE work. Fix: default changed to `--school-chunk-size 365` (all schools in one query). Dropped runtime from ~2.75h to ~25 min.

**CV results:** 3-fold rolling-origin (temporal leakage prevention). minutes_rmse=5.58 min/game, interval_coverage=0.87 (targeting 0.90).

**Role Fit:** Playing time output also back-calculates a `role_fit` score (how well the projected minutes fit this player's archetype's typical role) and syncs it into `player_team_fit_scores.role_fit` — this closes the loop on the composite fit score's role component.

**Output:** `playing_time_projections`. Served by `GET /api/players/{id}/playing-time?school_id=X`.

### Results

| Player | Proj Minutes | 90% CI | Proj Usage% | Role | Role Fit |
|---|---|---|---|---|---|
| **Ruffin** | **28.6 min/g** | 21.9–35.3 | 16.7% | defensive_specialist | **73.3** |
| **Crawford** | **23.6 min/g** | 17.8–29.5 | 13.7% | defensive_specialist | **65.6** |
| **Evans** | **25.1 min/g** | 20.9–29.3 | 10.4% | spacing_specialist | **71.4** |

All three project into real rotation minutes (23–29 min/g). Evans has the tightest CI (±4.2) — consistent role archetype, clear positional need, minimal competition at C. Ruffin's CI is the widest (±6.7) reflecting the higher uncertainty of a SWAC star jumping three competition tiers. Usage projections reflect a substantial reduction for both PGs: Ruffin drops from 36.2% source usage to 16.7% projected at Gonzaga (he becomes a complementary player, not a usage anchor), Crawford from 31.1% to 13.7%.

Note: `player_team_fit_scores.role_fit` is still 50.0 for these players in the fit scores table (the playing-time run writes to `playing_time_projections.role_fit` but the back-sync to `player_team_fit_scores` requires a follow-up `sync_role_fit_scores()` call). The overall_fit scores (68.7/63.6/62.8) reflect this stub. With real role_fit values wired in, the composite updates:

### Updated Composite Fit (with real Role Fit from playing time model)

| Player | Scheme (×0.30) | Gap (×0.20) | Role (×0.25) | Program (×0.25, stub) | **Composite** |
|---|---|---|---|---|---|
| Ruffin | 99.6 | 69.3 | 73.3 | 50.0 | **74.6** |
| Crawford | 89.7 | 58.5 | 65.6 | 50.0 | **67.5** |
| Evans | 80.8 | 67.8 | 71.4 | 50.0 | **68.2** |

Evans moves above Crawford in composite when real role_fit is applied — his gap_match (67.8) and role_fit (71.4) together outweigh Crawford's scheme advantage.

### Visual Callouts

- **VISUAL 6a:** Horizontal bar chart — projected minutes per game for each player, with 90% CI whiskers.
- **VISUAL 6b:** Usage rate projection alongside minutes — paired bars showing how role expectations differ across the two guards and the center.

---

## Step 7 — What Will They Actually Contribute Here? Destination Projection

**Question:** Given neutral talent, expected minutes, stylistic fit, roster context, and competition-tier jump — what are the real per-game stats we should expect from each player *at Gonzaga*?

### Technical Approach

Four sequential delta adjustments layered on the neutral Phase 0 projection. Each delta is additive on the RAPM scale; the sum is the destination-adjusted value estimate.

**Δ1 — Role/Usage Adjustment (Ridge, fitted)**
Ridge regression trained on historical transfer outcomes. Training set: 2,420 rows from matched `transfer_portal_events` × `player_season_stats` pairs.

*Key bug found and fixed:* `_TRANSFER_TRAINING_SQL` used `dest_season = t.season` (247sports portal entry year). Barttorvik records the player at the destination in `t.season + 1` (they play there the following year). This dropped 3,837/3,843 training rows, leaving 5. Fix: `dest_season = t.season + 1`.

*Player matching overhaul:* `_match_player()` gained name normalization (NFD accent stripping, suffix removal for Jr./Sr./II/III), position pre-filtering, two-pass threshold (strict 0.82 → relaxed 0.75), position disambiguation on ties, multi-season fallback. Match rate jumped from ~0% to 87–91% across all seasons. 5,324 matched transfers total.

Δ1 adjusts value based on whether expected minutes at Gonzaga are higher/lower than source minutes — players taking a significant usage cut see a negative adjustment even if their rate stats hold.

**Δ2 — Style/Skill Fit (rule-based, 6 interactions)**
Six hardcoded interaction terms encoding basketball domain knowledge: 3PT-rate alignment (player vs. school target), pace delta, usage-context fit, assist-rate fit, transition frequency fit, scramble frequency fit. Each interaction produces a directional RAPM delta. Example: isolation scorer in a high-assist system → negative usage-context fit term.

*Known gap:* With 2,420 labeled rows, this could be a small Ridge/GBT fit instead of hardcoded rules — highest potential R² improvement on the roadmap.

**Δ3 — Roster Context (piecewise-linear interpolation)**
Maps `gap_match` score (from Step 4) to an opportunity delta via piecewise-linear interpolation — players filling larger gaps get a bonus (clearer role, better deployment). Uses `roster_state_features` incoming/outgoing minutes to avoid rewarding gaps already being filled by committed transfers.

**Δ4 — Competition Tier (4×4 matrix)**
Transfer-outcome matrix: source tier × destination tier (4 tiers each). Real signal: low-major→high-major Spearman 0.67 (n=156), high-major→mid-major weak/small-sample (n=36). Position-agnostic (a known limitation — a C and a PG face different opportunity landscapes in the same tier jump).

**Box-score translation:** Per-40 rates converted to per-game via `projected_minutes / 40.0`. *Critical bug found and fixed:* original used `possessions / 100` instead — suppressed displayed stats ~45–50% (Dunkley example: 12.4 PPG source → 5.26 PPG shown). Fix: `per_game = per_40_rate × (minutes / 40.0)`.

**CV results:** n_train=2,420, total_resid_std=2.892, fold RMSE: 2.917/2.858/3.124, R²: 0.071/0.025/0.068. R² is low — high inherent noise in transfer outcomes, and Δ2 is still rule-based. Model is calibrated and directionally correct; tier and role signals are real.

**Cohort validation (added 2026-07-01):** `compute_cohort_validation()` runs the same rolling-origin CV folds and reports Spearman/RMSE per tier direction, position group, and usage context. Logged to MLflow. Real results: tier_down=0.402 Spearman (strongest), tier_same=0.153 (weakest), tier_up=0.276; guard=0.304, wing=0.374, big=0.361.

**Output:** Destination rows in `player_projections`, `model_version='player-destination-proj-v1'`. Served by `GET /api/players/{id}/projection?school_id=X`.

### Results

The destination model uses `player-proj-phase2a-fcast-v1` (the Phase 2a forward forecast) as its neutral baseline, not Phase 0. Forecast neutral values are higher than Ph0 for all three (Ruffin 4.52, Crawford 5.08, Evans 5.21), reflecting the Kalman smoother's forward extrapolation. All deltas are computed relative to this forecast baseline.

| | Ruffin | Crawford | Evans |
|---|---|---|---|
| Neutral (Ph2a fcast) | +4.52 | +5.08 | +5.21 |
| Δ1 Role/Usage | −0.75 | −0.17 | −0.74 |
| Δ2 Style/Skill Fit | −0.06 | +0.04 | +0.07 |
| Δ3 Roster Context | +0.15 | +0.07 | +0.14 |
| Δ4 Competition Tier | −0.60 | −0.20 | −0.20 |
| **Total context Δ** | **−1.25** | **−0.26** | **−0.73** |
| **Dest value/100** | **+3.26** | **+4.81** | **+4.48** |
| Source tier → dest tier | SWAC(4)→WCC(1) | MVC(2)→WCC(1) | Big West(2)→WCC(1) |
| Proj minutes | 28.6 | 23.6 | 25.1 |

**Destination rank reverses the fit score rank.** Crawford (4.81) > Evans (4.48) > Ruffin (3.26) on destination value, despite Ruffin having the highest composite fit (74.6). Ruffin's SWAC→WCC jump (+3 tiers) drives a −0.60 tier penalty — the largest of any player in this set. Combined with a −0.75 role/usage delta (usage drops from 36.2% to 16.7%), the total context adjustment is −1.25, the steepest. Crawford's single-tier MVC→WCC jump produces a much smaller −0.20 tier penalty, so his underlying quality survives the translation. Evans takes the same tier penalty as Crawford (−0.20) but incurs a larger role/usage delta (−0.74, usage drops from 16.1% to 10.4% as he shifts from featured big to role center), landing him in between.

### Visual Callouts

- **VISUAL 7a:** Waterfall chart per player — neutral value/100 as baseline, each delta (Δ1–Δ4) as a step up or down, destination value/100 as the final bar. Three players side by side or three separate charts with consistent y-axis.
- **VISUAL 7b:** Final comparison table — Ruffin vs. Crawford vs. Evans, destination value/100, projected minutes, role fit, and composite fit score.

---

## Final Board

| | **Ruffin** | **Crawford** | **Evans** |
|---|---|---|---|
| Archetype | Lead Scoring Playmaker | Lead Scoring Playmaker | Post Scoring Big |
| Scheme Fit | 99.6 | 89.7 | 80.8 |
| Gap Match | 69.3 | 58.5 | 67.8 |
| Role Fit | 73.3 | 65.6 | 71.4 |
| **Composite Fit** | **74.6** | **67.5** | **68.2** |
| Ph0 Neutral value/100 | +3.33 | +2.69 | +2.12 |
| Ph2a Neutral value/100 | +2.02 | +2.00 | +1.76 |
| Total context Δ | **−1.25** | **−0.26** | **−0.73** |
| **Dest value/100** | **+3.26** | **+4.81** | **+4.48** |
| Proj Minutes (CI) | 28.6 (21.9–35.3) | 23.6 (17.8–29.5) | 25.1 (20.9–29.3) |
| Source → Dest tier | SWAC(4)→WCC(1) | MVC(2)→WCC(1) | Big West(2)→WCC(1) |

**Recommendation:** Crawford is the lead target on value delivered at Gonzaga (dest 4.81) — his MVC→WCC tier jump is manageable and his game survives the translation. Evans is the correct secondary target (only C in the pool, clear gap fill, tightest CI, spacing_specialist role). Ruffin is the scheme-fit trap: 99.6 scheme fit and the highest composite score, but the SWAC→WCC tier jump and a usage cut from 36.2% to 16.7% erode the raw advantage. His destination value (3.26) is the lowest of the three. The models surface a counter-intuitive result that pure scouting would miss.

---

## Narrative Anchor

> Every model narrows a different dimension of uncertainty. Clustering identifies whether the archetype even belongs. Scheme/Gap Fit measures alignment quantitatively. Player Projection tells you the floor — neutral talent. Playing Time tells you whether that talent deploys. Destination Projection synthesizes all five into a single answer: *what does this player do for us, in our system, this season.*
>
> Ruffin's case is the best slide in the deck. Scheme fit 99.6 — the closest match in the portal. Highest composite score. Raw recruiting instinct says he's the obvious answer. But a three-tier competition jump from the SWAC and a usage cut from 36% to 17% leave him projected at just 3.26 value/100 at Gonzaga — the lowest of the three targets. Crawford, with a lower fit score and a more modest profile, delivers 4.81 because the models know that one tier of competition adjustment is survivable, three is not. That gap only exists on paper if you have the model.
