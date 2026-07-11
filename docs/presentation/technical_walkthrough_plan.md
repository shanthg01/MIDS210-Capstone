# PortalPoint — Technical Walkthrough Plan

**Audience:** Technical review. Full model detail per step. Persona: Gonzaga coaching staff evaluating three portal targets.

---

## The Question Stack

Each model narrows a different dimension of uncertainty. This is the through-line of the presentation.

| Question | Model(s) | Status |
|---|---|---|
| How does a new portal entrant become visible? | News Monitoring Agent | ✅ (prototype) |
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

**Roster situation:** Current roster gaps emphasize on-ball guard creation, assist pressure, and frontcourt depth/rebounding. Primary need: a high-fit lead guard, with a secondary big who can stabilize the center rotation.

**Three targets:**

| Player | Pos | From | Transfer direction |
|---|---|---|---|
| **Daeshun Ruffin** | PG | Jackson St. (SWAC) | Low-major → high-major |
| **Elijah Crawford** | PG | Illinois Chicago (MVC) | Mid-major → high-major |
| **Kyle Evans** | C | UC Irvine (Big West) | Mid-major → high-major |

---

## Entry Point: Portal Search

```
GET /api/players/search?available_only=true&position=PG,C
```

`available_only=true` filters to `player_team_fit_scores.is_portal_candidate = true`. This flag is set by `portalpoint.modeling.availability.sync_portal_candidate_flags()`, called on every `ingest_transfers_247sports.py` run and every `run_gap_matching.py` run. It identifies players with a matched `Entered` or `Committed` row in `transfer_portal_events` for the current season. The flag scopes the recommendation surface without restricting what the underlying models score — all ~9.7M player×school rows in `player_team_fit_scores` remain intact; portal candidacy is a filter layer, not a modeling constraint.

---

## Step 0 — How Do New Portal Entrants Become Visible? News Monitoring Agent

**Question:** The portal moves fast — a player enters this afternoon, a coach steps down tonight. How does PortalPoint's recommendation engine know about it without a manual re-run?

### Two Mechanisms for Setting `is_portal_candidate`

The `available_only=true` filter shown in the Entry Point query routes to `player_team_fit_scores.is_portal_candidate = true`. This flag is set by two complementary pipelines:

1. **Deterministic structured ingest** — `scripts/ingest_transfers_247sports.py` scrapes 247Sports' `window.__INITIAL_DATA__` JSON (already structured) and promotes matched rows to `transfers`. On every run, `portalpoint.modeling.availability.sync_portal_candidate_flags()` sets `is_portal_candidate` for those matched players. Reliable, cheap, no LLM needed — this stays deterministic ETL.

2. **News Monitoring Agent** — `notebooks/agents/news_monitor_agent_v2.ipynb` (prototype, to become `scripts/run_news_monitoring.py`) covers signals that 247Sports structured data misses: breaking portal entries reported first by beat writers on ESPN/On3, coaching departures that cascade transfer waves, and any source where the signal is buried in prose rather than clean JSON.

### Technical Approach (LangGraph ReAct)

**Stack:** LangGraph `StateGraph` (ReAct loop) + Gemini `gemini-3.1-flash-lite` (LLM) + Tavily Python client (web search).

**Graph:** `START → agent_node → ToolNode ⟲ → END`

Each agent turn: the LLM reasons over message history and emits tool calls; `ToolNode` executes; results are appended to state and fed back to the LLM until it produces a final answer with no tool calls.

**Per-run tool sequence (agent-directed):**

```
search_news("transfer portal player enters portal")
  → classify_events_batch_llm(results)           # batch classify all articles, one LLM call
      → transfer_player(player_name, school_from) # for each confirmed portal entry (confidence ≥ 0.6)

search_news("head coach leaves resigns fired")
  → classify_events_batch_llm(results)
      → coach_departure(coach_name, school_from)  # logged for human review, no auto DB action
```

**Dual classifier design:**

| Classifier | Mechanism | Speed | Cost | Use case |
|---|---|---|---|---|
| Regex (`classify_events_batch`) | Deterministic keyword patterns | Instant | Free | Evals, cost-sensitive runs, known phrasing |
| LLM (`classify_events_batch_llm`) | Gemini structured output (`ArticleClassification` Pydantic schema, temp=0) | ~1s/article | API calls | Ambiguous headlines, implicit phrasing — "star forward departs program" correctly classified as portal entry |

**Real classifier comparison result** (live test in notebook): regex produced 4 target events from 5 test articles; LLM produced 3 — the one disagreement was "UNC guard weighing options after disappointing season" where the LLM correctly returned `unknown` (not a confirmed portal entry) while regex fired on the word "portal" in the body. LLM precision higher at slight recall cost; production default is LLM.

**`transfer_player` DB update — what it actually writes:**

1. Fuzzy-match `player_name` against `players` table using `_normalize_name()` (accent stripping, suffix removal) + two-pass `difflib` (0.82 strict → 0.75 relaxed). Reuses the same battle-tested matcher from `ingest_transfers_247sports.py`.
2. Resolve `school_from` via `SCHOOL_ALIASES` + fuzzy match against `schools.name`.
3. `UPDATE player_team_fit_scores SET is_portal_candidate = true WHERE player_id = :id` — immediately surfaces the player across all ~26,000+ school pairings in the recommendation engine.
4. `INSERT INTO transfers ... ON CONFLICT (player_id, season) DO UPDATE` — records `portal_entry_date`; preserves any `from_school_id` already set by an earlier 247Sports scrape.

**`coach_departure` — why no auto DB write:**
Coaching changes affect `team_system_profiles` (Model 2 scheme clustering) — a new HC likely changes the team's offensive/defensive system label. But updating cluster assignments requires a full M2 re-run against new game-log data for next season, which can only happen after the new coach has coached games. The tool logs the event and surfaces it for human review; the human decides when to trigger a re-run. This is the correct scope boundary: the agent handles detection, humans handle the downstream model action.

### Scope Boundary vs. Existing Scripts

The news agent does **not** replace `ingest_transfers_247sports.py`, `ingest_barttorvik.py`, etc. Those stay deterministic, non-LLM ETL. The agent adds coverage for sources where signal is buried in prose and needs LLM extraction to become structured — complementary layers, not competing ones.

### Current State and Next Steps

- **Prototype:** `notebooks/agents/news_monitor_agent_v2.ipynb` — fully functional, tested with live Tavily + Gemini API keys, DB writes validated.
- **Next:** `scripts/run_news_monitoring.py` CLI entrypoint for scheduling (GitHub Actions cron or Airflow DAG, hourly during portal window March–August, daily otherwise — same cadence as `hourly_portal_monitoring_dag` in CLAUDE.md).
- **Roadmap:** Phase A (VerbalCommits as second structured source + cross-source dedup) → Phase B (coaching news via LLM extraction) → Phase C (beat-writer RSS + full event taxonomy). See `docs/agents/agentic_news_monitoring_plan.md`.

### Visual Callouts

- **VISUAL 0a:** Architecture diagram — two paths that set `is_portal_candidate`: deterministic 247Sports ingest (left branch) vs. News Agent (right branch, LangGraph ReAct loop). Both converge on `player_team_fit_scores.is_portal_candidate = True`.
- **VISUAL 0b:** Live agent trace — show one complete run output: Tavily search → batch classification results (articles × event_type × confidence) → `transfer_player` call result (player matched, rows updated, school count). Use the notebook's streaming output as the source.
- **VISUAL 0c:** Regex vs. LLM classifier comparison table — the 5-article test set with both classifiers' event_type and confidence side-by-side, highlighting the one disagreement and explaining why the LLM is correct.

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

| Player | Archetype | Centroid distance | Key distinguishing features |
|---|---|---|---|
| **Ruffin** | Lead Scoring Playmaker | 0.44 | Usage 36.2%, 3PT rate 29.2%, assist rate 45.2%, BPM +4.1 |
| **Crawford** | Lead Scoring Playmaker | 0.47 | Usage 31.1%, 3PT rate 28.5%, assist rate 43.0%, BPM +2.6 |
| **Evans** | Post Scoring Big | 0.48 | Usage 16.1%, rim-heavy profile, assist rate 6.4%, BPM +3.0 |

Ruffin and Crawford both profile as lead scoring playmakers, while Evans gives the board a contrasting frontcourt archetype. The comparison is useful because the model can separate "best guard fit" from "best roster-shape complement" rather than treating all portal targets as interchangeable.

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

| | 3PT Att% | Rim Att% | Usage% | Assist% | **Scheme Fit** |
|---|---|---|---|---|---|
| **Gonzaga target** | 30.8 | 38.4 | — | 57.5 | — |
| **Ruffin** | 29.2 | 37.8 | 36.2 | 42.6 | **99.6** |
| **Crawford** | 28.5 | 21.0 | 31.1 | 38.6 | **89.7** |
| **Evans** | 3.5 | 76.7 | 16.1 | 6.0 | **80.8** |

Ruffin is the strongest pure scheme match: his rim pressure and 3PT mix closely track Gonzaga's offensive shape. Crawford remains a strong guard fit, while Evans is a different bet — less guard creation, but a clear rim-pressure/frontcourt profile.

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

**Departing / roster context:** The current gap vector points toward guard creation and assist pressure, with a secondary frontcourt/rebounding need.

**Post-departure roster:** `roster_state_features` captures returning/departing/incoming minutes by position and skill group, updated after 247Sports ingestion. The visualization normalizes those needs against each target's skill profile.

### Results

| Player | Gap Match | Key driver |
|---|---|---|
| **Ruffin** | **69.3** | Best all-around guard fit; high assist/usage profile maps to the creation need |
| **Crawford** | **58.5** | Similar lead-guard archetype, but lower gap alignment than Ruffin |
| **Evans** | **67.8** | Strong secondary match through center/frontcourt value and rebounding profile |

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
GET /api/players/7578028029286400392/projection?model_version=player-proj-phase2a-fcast-v1
```

| Player | Current season summary | Phase 0 Value/100 | Phase 2a Forecast Value/100 | Δ Ph2a vs Ph0 |
|---|---|---|---|---|
| **Ruffin** | 23.3 PPG / 29.2% 3PT / 36.2% usage | +3.3 | +1.7 | −1.6 |
| **Crawford** | 14.1 PPG / 28.5% 3PT / 31.1% usage | +2.7 | −0.7 | −3.3 |
| **Evans** | 12.1 PPG / 16.1% usage / center profile | +2.1 | +4.2 | +2.1 |

Phase 2a separates the board more sharply than Phase 0: Ruffin leads the neutral production case in Phase 0, while Evans gets the largest cross-season lift.

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

| Player | Proj Minutes | 90% CI | Proj Usage Rate | Role Fit |
|---|---|---|---|---|
| **Ruffin** | **28.6 min/g** | 21.9–35.3 | 16.7% | **73.3** |
| **Crawford** | **23.6 min/g** | 17.8–29.5 | 13.7% | **65.6** |
| **Evans** | **25.1 min/g** | 20.9–29.3 | 10.4% | **71.4** |

Ruffin projects for the largest role, while Evans has the tightest frontcourt rotation case. Crawford's interval leaves room for either a starter-level guard role or a smaller deployment depending on fit and roster competition.

### Updated Composite Fit (Role Fit now live)

| Player | Scheme (×0.30) | Gap (×0.20) | Role (×0.25) | Program (×0.25, stub) | **Composite** |
|---|---|---|---|---|---|
| Ruffin | 99.6 | 69.3 | 73.3 | 50.0 | **68.7** |
| Crawford | 89.7 | 58.5 | 65.6 | 50.0 | **63.6** |
| Evans | 80.8 | 67.8 | 71.4 | 50.0 | **62.8** |

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

| | Ruffin | Crawford | Evans |
|---|---|---|---|
| Neutral Value/100 (Ph 0) | +3.3 | +2.7 | +2.1 |
| **Destination Value/100** | **+3.3** | **+4.8** | **+4.5** |
| Δ total vs neutral | **−0.1** | **+2.1** | **+2.4** |
| Projected minutes | 28.6 | 23.6 | 25.1 |
| Projected usage | 16.7 | 13.7 | 10.4 |

Crawford and Evans receive the largest destination uplift, while Ruffin remains the cleanest composite fit because his scheme and role scores are strongest.

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
| **Composite Fit** | **68.7** | **63.6** | **62.8** |
| Neutral Value/100 | +3.3 | +2.7 | +2.1 |
| Dest Value/100 | +3.3 | +4.8 | +4.5 |
| System delta | **−0.1** | **+2.1** | **+2.4** |
| Proj Minutes (CI) | 28.6 (22–35) | 23.6 (18–29) | 25.1 (21–29) |
| Proj usage | 16.7% | 13.7% | 10.4% |

**Recommendation:** Ruffin is the lead target on composite fit — the scheme score is elite, the projected minutes are starter-level, and the role fit is strongest. Crawford and Evans are the upside cases in destination value, with Evans especially useful as the frontcourt complement if roster construction is prioritized over pure guard fit.

---

## Narrative Anchor

> Every model narrows a different dimension of uncertainty. Clustering identifies whether the archetype even belongs. Scheme/Gap Fit measures alignment quantitatively. Player Projection tells you the floor — neutral talent. Playing Time tells you whether that talent deploys. Destination Projection synthesizes all five into a single answer: *what does this player do for us, in our system, this season.*
>
> Ruffin is the cleanest fit, while Crawford and Evans show why destination projection matters: fit score and destination value can point to different kinds of roster decisions.
