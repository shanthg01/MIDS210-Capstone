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

**System:** Motion offense, high 3PT volume, elite assisted-shot rate, up-tempo pace, multi-skill bigs. High-major (WCC). Heavy portal recruiter.

**Roster situation:** Two senior wings exhausted eligibility — combined 31% of team minutes departing. Primary need: SG/SF with catch-and-shoot capability, off-ball movement, perimeter defense.

**Three targets:**

| Player | Pos | From | Transfer direction |
|---|---|---|---|
| **Jalen Moore** | SG | Loyola Marymount (WCC) | Mid-major → same conference high-major |
| **DeShawn Carter** | SF | Memphis (AAC) | Mid-major → high-major |
| **Cam Ellis** | PF/C | Bradley (MVC) | Mid-major → high-major |

---

## Entry Point: Portal Search

```
GET /api/players/search?available_only=true&position=SG,SF
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

**Label assignment:** Manual, after centroid inspection. Coaching-vocabulary names (e.g., "High-Efficiency Motion", "Pack-Line Halfcourt", "Fast-Break Transition"). Labels stored in `team_system_profiles.system_label`.

**Output:** `team_system_profiles` — one row per school×season, with offense cluster, defense cluster, and composite label. MLflow artifact: `team-k{K}-v2-2026` in `s3://portalpoint-data/models/`.

### Gonzaga Profile

- **Label:** "High-Efficiency Motion"
- **Offense centroid features:** 3PT rate 41.2%, assisted% 68.3%, rim% 34.1%, pace 71.4 pos/40, transition PPP 1.14
- **Defense centroid features:** moderate transition defense, low scramble allowed
- **Peer programs in same cluster:** Virginia Tech, Creighton, Purdue (motion-offense, high assisted%)

### Visual Callouts

- **VISUAL 1a:** Elbow + silhouette curves showing K selection for offense and defense groups
- **VISUAL 1b:** 2D PCA/UMAP scatter of all team-season vectors, colored by cluster label — Gonzaga highlighted, peer cluster members labeled
- **VISUAL 1c:** Centroid feature heatmap across all clusters — shows what makes "High-Efficiency Motion" distinct from neighbors (e.g., vs. "Transition-Heavy" cluster)
- **VISUAL 1d:** Table of 5–8 peer programs in Gonzaga's cluster with their key feature values

---

## Step 2 — Who Are These Players? Player Clustering (M1)

**Question:** What archetype is each target, and does that archetype family belong in a "High-Efficiency Motion" system?

### Technical Approach

**Algorithm:** K-Means, k=9, single joint scaler across all features. K selected via silhouette sweep k=6–15 in `notebooks/models/player_clustering.ipynb`.

**Features:** barttorvik (`ortg`, `usage`, `3par`, `rimar`, `bpm`, `obpm`, `dbpm`) + Hoop Explorer (`pos_confidence_pg/sg/sf/pf/c` — real position probability distributions, not hardcoded labels; position was hardcoded `'G'` for all 13,303 players until `_infer_position()` fix + ingest rerun 2026-06-23) + hoopR game logs (zone frequencies, creation rate by shot type).

**Label assignment:** Manual after centroid inspection. 9 archetypes: "3-and-D Wing", "Isolation Scorer", "Stretch-5 / Playmaking Big", "Point Guard Initiator", "Rim-Runner", "Secondary Creator", "Defensive Anchor", "Versatile Forward", "Volume Scorer". Stored in `player_archetypes.archetype_label`.

**Output:** `player_archetypes` — one row per player×season, with cluster index, archetype label, and distance to centroid (confidence proxy). MLflow artifact: `kmeans-k9-v1-2026`.

### Results

| Player | Archetype | Centroid distance | Key distinguishing features |
|---|---|---|---|
| **Moore** | 3-and-D Wing | 0.31 (high confidence) | 3PT rate 43.1%, assisted% 77%, above-avg defensive indicators, low usage 18.4% |
| **Carter** | Isolation Scorer | 0.28 (high confidence) | Usage 26.8%, assisted% own shots 38%, high mid-range freq, low 3PT 29.2% |
| **Ellis** | Stretch-5 / Playmaking Big | 0.44 (moderate confidence) | Rim% 58.3%, 3PT rate 34.7% (high for his position), assist rate 21.4%, OrtG 117 |

Moore and Ellis have archetype families that appear in Gonzaga's historical roster composition. Carter's archetype is rare in motion-offense programs — the system doesn't create isolation looks.

### Visual Callouts

- **VISUAL 2a:** Silhouette curve showing k=9 selection
- **VISUAL 2b:** 2D UMAP scatter of all player-season vectors colored by archetype — three targets highlighted with labels
- **VISUAL 2c:** Per-archetype centroid feature heatmap (9 rows × key features) — highlights what separates "Isolation Scorer" from "3-and-D Wing"
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

| | 3PT% | Rim% | Usage% | Assisted% | Pace | **Scheme Fit** |
|---|---|---|---|---|---|---|
| **Gonzaga target** | 41.2 | 34.1 | — | 68.3 | 71.4 | — |
| **Moore** | 43.1 | 28.7 | 18.4 | 77.0 | 70.2 | **78.4** |
| **Carter** | 29.2 | 31.4 | 26.8 | 38.0 | 68.9 | **52.1** |
| **Ellis** | 34.7 | 58.3 | 20.1 | 61.2 | 69.8 | **71.3** |

Carter's 52.1 is driven almost entirely by the assisted% gap (38.0 vs. 68.3 target): Gonzaga's system produces catch-and-shoot looks, Carter's game requires iso creation. This dimension alone drops his cosine similarity substantially.

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

**Departing:** 31% of minutes from SG/SF slot. Departed skill profile: catch-and-shoot 3PT volume, off-ball movement, perimeter defensive activity.

**Post-departure roster:** Strong at the backcourt initiator role, strong in the frontcourt, thin at the off-ball wing slot. `roster_state_features` captures this — returning/departing/incoming minutes by position and skill group, updated after 247Sports ingestion.

### Results

| Player | Gap Match | Key driver |
|---|---|---|
| **Moore** | **82.1** | Exact SG position + catch-and-shoot profile maps directly onto departed skill shape |
| **Carter** | **61.4** | Position fits (SF), but skill shape mismatch — gap needs a shooter, Carter's a creator; soft weighting limits but doesn't eliminate the penalty |
| **Ellis** | **74.8** | Fills a secondary PF gap (also real); efficient rim-finisher addresses a smaller but real hole |

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
GET /api/players/1042/projection        # neutral, defaults to Phase 0
GET /api/players/1042/projection?model_version=player-projection-phase2a-v1
```

| Player | Last season PPG | Phase 0 Proj Off RAPM | Phase 2a Proj Off RAPM | Δ Ph2a vs Ph0 | Phase 0 Proj Def RAPM |
|---|---|---|---|---|---|
| **Moore** | 14.8 PPG / 43.1% 3PT | +4.2 | +4.7 | +0.5 | +0.8 |
| **Carter** | 17.4 PPG / 29.2% 3PT | +6.1 | +6.4 | +0.3 | −1.4 |
| **Ellis** | 11.2 PPG / 6.8 RPG | +3.1 | +3.4 | +0.3 | +2.3 |

Phase 2a shifts are modest for these players (all mid-career, not developmental edges). The bigger Phase 2a gains appear for sophomore/junior players with a clear development trajectory — worth showing in the notebook with a contrasting example player.

### Visual Callouts

- **VISUAL 5a:** Per-player skill comparison table: last season observed rate vs. Phase 0 shrunk rate vs. Phase 2a smoothed rate, for all 10 skills. Annotate where shrinkage pulled a stat toward the prior (e.g., Moore's 3PT% is slightly shrunk because his sample was 22 games at moderate minute share).
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
| **Moore** | **28.4 min/g** | 22.1–34.7 | 19.2% | **76.3** |
| **Carter** | **19.2 min/g** | 11.4–27.0 | 23.8% | **48.1** |
| **Ellis** | **22.8 min/g** | 17.3–28.3 | 18.6% | **68.7** |

Carter's wide CI (11.4–27.0) reflects genuine model uncertainty: the training data has seen both outcomes for this archetype×system pairing — programs that adapted around an isolation scorer, and programs that didn't. Moore's tight CI reflects high historical precedent for 3-and-D wings in motion systems.

### Updated Composite Fit (Role Fit now live)

| Player | Scheme (×0.30) | Gap (×0.20) | Role (×0.25) | Program (×0.25, stub) | **Composite** |
|---|---|---|---|---|---|
| Moore | 78.4 | 82.1 | 76.3 | 50.0 | **72.6** |
| Carter | 52.1 | 61.4 | 48.1 | 50.0 | **52.9** |
| Ellis | 71.3 | 74.8 | 68.7 | 50.0 | **66.4** |

### Visual Callouts

- **VISUAL 6a:** Horizontal bar chart — projected minutes per game for each player, with 90% CI whiskers. Three players side by side. Annotate Carter's wide CI explicitly.
- **VISUAL 6b:** Usage rate projection alongside minutes — dual-axis or paired bars showing that Carter's usage stays elevated even at reduced minutes (system won't absorb his style at full-star rate)

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

| | Moore | Carter | Ellis |
|---|---|---|---|
| Neutral Off RAPM (Ph 0) | +4.2 | +6.1 | +3.1 |
| Δ1 Role/Usage | +0.3 | −0.8 | +0.2 |
| Δ2 Style/Skill Fit | +0.4 | −0.6 | +0.1 |
| Δ3 Roster Context | +0.2 | −0.1 | +0.2 |
| Δ4 Competition Tier | 0.0 | +0.2 | +0.0 |
| **Dest Off RAPM** | **+5.1** | **+4.8** | **+3.6** |
| Δ total vs neutral | **+0.9** | **−1.3** | **+0.5** |
| Proj PPG | 14.2 | 11.3 | 9.4 |
| Proj APG | 4.1 | 1.8 | 2.1 |
| Proj RPG | 3.8 | 3.1 | 6.8 |

Carter's −1.3 destination delta quantifies the system mismatch: his +6.1 neutral value is real, but Gonzaga's system cannot deploy that value fully. Δ2 (style/skill fit) and Δ1 (reduced minutes → usage cut) account for most of the drag.

### Visual Callouts

- **VISUAL 7a:** Waterfall chart per player — neutral Off RAPM as baseline, each delta (Δ1–Δ4) as a step up or down, destination RAPM as the final bar. Three players side by side or three separate charts with consistent y-axis.
- **VISUAL 7b:** Final projected stat line comparison table — Moore vs. Carter vs. Ellis, PPG/APG/RPG alongside Dest Off/Def RAPM and composite fit score.

---

## Final Board

| | **Moore** | **Carter** | **Ellis** |
|---|---|---|---|
| Archetype | 3-and-D Wing | Isolation Scorer | Stretch-5 |
| Scheme Fit | 78.4 | 52.1 | 71.3 |
| Gap Match | 82.1 | 61.4 | 74.8 |
| Role Fit | 76.3 | 48.1 | 68.7 |
| **Composite Fit** | **72.6** | **52.9** | **66.4** |
| Neutral Off RAPM | +4.2 | +6.1 | +3.1 |
| Dest Off RAPM | +5.1 | +4.8 | +3.6 |
| System delta | **+0.9** | **−1.3** | **+0.5** |
| Proj Minutes (CI) | 28.4 (22–35) | 19.2 (11–27) | 22.8 (17–28) |
| Proj PPG / APG | 14.2 / 4.1 | 11.3 / 1.8 | 9.4 / 2.1 |

**Recommendation:** Moore is the lead target — every model layer agrees. Ellis is the right secondary target (system-compatible archetype, fills a real secondary gap, consistent CI). Carter is the "talent trap" — best neutral player on the board, but every fit dimension penalizes him and the wide minutes CI means the staff can't even count on his volume materializing.

---

## Narrative Anchor

> Every model narrows a different dimension of uncertainty. Clustering identifies whether the archetype even belongs. Scheme/Gap Fit measures alignment quantitatively. Player Projection tells you the floor — neutral talent. Playing Time tells you whether that talent deploys. Destination Projection synthesizes all five into a single answer: *what does this player do for us, in our system, this season.*
>
> Carter's case is the best slide in the deck. Raw stats say recruit him. Every model layer says don't.
