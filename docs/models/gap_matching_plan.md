Gap Matching Notebook Plan
File: notebooks/models/gap_matching.ipynb

Pattern: Mirrors scheme_fit_scorer.ipynb — same imports, config, DB connect, cosine_similarity, execute_values upsert.

Feature Space
8 dims from player_season_stats (all in DB, none in parquet):

Dim	Column
PPG	points_per_game
RPG	rebounds_per_game
APG	assists_per_game
SPG	steals_per_game
BPG	blocks_per_game
TS%	true_shooting_pct
USG%	usage_rate
3PT Rate	three_point_rate
Requires games_played >= 5 and all 8 features non-null.

Notebook Structure (11 cells)
Cell 0 — Setup

Same imports as scheme_fit_scorer + StandardScaler from sklearn. Config block:


SEASON = 2025
GAP_FEATURES = ['points_per_game', 'rebounds_per_game', 'assists_per_game',
                 'steals_per_game', 'blocks_per_game', 'true_shooting_pct',
                 'usage_rate', 'three_point_rate']
POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C']
MODEL_VERSION = 'gap-cos-v1'
Cell 1 — Position Coverage Audit

Query player_season_stats JOIN players for 2025. Count position null rate. Decision:


PER_POSITION_MODE = (null_rate < 0.30)  # >70% populated → per-position
Print mode chosen. Rest of notebook branches on this flag.

Cell 2 — Load Departures

**Done (Issue #26, 2026-06-21) — in both `scripts/run_gap_matching.py` and
`notebooks/models/gap_matching.ipynb` (its actual Cell 1b — numbered differently than
this plan doc's Cell 2 since it landed between the existing Cell 1/Cell 2).** This exact
query is `gap_matching.filter_departed()`'s `departed_pairs` load, `gap-cos-v2`. Both
re-executed end to end and verified to produce identical numbers (1,280,700 rows, same
per-season mean/std, same MLflow Δ).

**Update (2026-06-22) — `gap-cos-v3`, all-pairs.** Gap Matching no longer scores only
the pairs Scheme Fit pre-seeded (the "8 dims from player_season_stats" feature space
above is also stale — see `gap_matching.GAP_FEATURES`, a 14-dim rate/style vector now).
Scores every eligible player×school×season pair, matching Scheme Fit's own all-pairs
rewrite the same day (`scheme-cos-v3`). 9,731,957 rows. `filter_departed()`'s departure
query/scope is unchanged by this — still current-season-only, still `transfers`-sourced.
See CLAUDE.md Process Improvement TODO #5 and `docs/status/MODEL_STATUS.md` for full
detail, including two bugs found and fixed at this scale (role_fit/program_fit
placeholder mismatch; the "preserve existing Scheme Fit context" step not scaling past
~1.3M rows).

**Update (2026-06-22 branch) — `gap-cos-v4`, shared roster baseline.** The
current script no longer uses the `filter_departed()` query below as the
complete roster-baseline definition. It now calls
`portalpoint.modeling.roster_baseline`: historical seasons infer target roster
membership from `player_season_stats(S+1)`, while the latest season uses latest
`roster_snapshots` with expected-departure fallback for schools without usable
snapshots. Candidate availability still comes from `portalpoint.modeling.availability`.

SELECT player_id, from_school_id
FROM transfers
WHERE from_school_id IS NOT NULL
  AND portal_entry_date IS NOT NULL
  AND season = 2026   -- "transferred INTO" 2026 = left before 2025-26
Build departed = set(zip(df.player_id, df.from_school_id)).

Cell 3 — Load Full Roster + Stats

Two queries merged:

player_school_seasons — who is on each school's 2025 roster
player_season_stats JOIN players — their stat vectors + position
Build roster_df: (player_id, school_id, position, ppg, rpg, …). Exclude departed players via anti-join on departed set.

Cell 4 — League Benchmarks

If PER_POSITION_MODE:


benchmark = roster_df.groupby('position')[GAP_FEATURES].mean()
# benchmark.loc['PG'] = mean stats for all PGs still on any roster
Else: benchmark = roster_df[GAP_FEATURES].mean() (single vector).

Cell 5 — Gap Vectors Per School

For each school × position:


remaining = roster_df[(roster_df.school_id == sid) & (roster_df.position == pos)]
if len(remaining) == 0:
    gap_vec = benchmark.loc[pos].values   # entire position missing
else:
    roster_mean = remaining[GAP_FEATURES].mean().values
    gap_vec = np.maximum(0, benchmark.loc[pos].values - roster_mean)
Store as gap_vecs: dict[school_id, dict[position, np.ndarray]].

Also compute depth_count[school_id][position] = len(remaining) for breakdown.

Cell 6 — Player Stat Vectors

Load portal players (players in transfers for 2026 season):


SELECT pss.player_id, p.position, pss.points_per_game, ...
FROM player_season_stats pss
JOIN players p ON p.id = pss.player_id
JOIN transfers t ON t.player_id = pss.player_id AND t.season = 2026
WHERE pss.season = 2025 AND all features non-null
StandardScaler fit on full player population (same scale as benchmarks).

Scale both player vectors and gap vectors before similarity.

Cell 7 — Batch Cosine Similarity

For each player-school pair (load existing player_team_fit_scores rows to know which pairs to score):


for player in portal_players:
    pos = player.position
    p_vec = scaled_player_vectors[player.player_id]
    for school_id in all_schools:
        g_vec = gap_vecs[school_id].get(pos, benchmark.loc[pos].values)
        sim = cosine_similarity([p_vec], [g_vec])[0][0]
        gap_match = float(np.clip(sim * 100, 0, 100))
Vectorize by grouping players by position → single cosine_similarity() call per position per school.

Cell 8 — Breakdown Fields

Four sub-scores per player-school pair:

Field	Computation
position_depth_score	max(0, 100 - depth_count[s][pos] * 20) (0 players = 100, 5+ = 0)
archetype_needed	roster_gap_analysis.archetype_needed == player.archetype_label (boolean)
uniqueness_bonus	Player's archetype cluster size in portal ÷ total portal → inverted percentile
redundancy_penalty	Mean cosine similarity of player vec vs. remaining roster players at same position × 100
Cell 9 — Validation

Distribution histogram of gap_match scores (expect wider spread than scheme_fit, more differentiation)
Spot-check: a team that lost 3 starters should show high gap scores for players at those positions
Position-level analysis: average gap_match by player position × school tier (high/mid/low AdjEM)
Cell 10 — DB Write

Load existing scheme_fit scores from DB. Merge gap_match. Recalculate overall_fit:


overall = W_SCHEME * scheme_fit + W_GAP * gap_match + W_OPP * 50.0 + W_PERS * 50.0
Upsert with merged breakdown JSONB:


DO UPDATE SET
    gap_match   = EXCLUDED.gap_match,
    overall_fit = EXCLUDED.overall_fit,
    breakdown   = player_team_fit_scores.breakdown || EXCLUDED.breakdown::jsonb,
    model_version = EXCLUDED.model_version,
    computed_at = EXCLUDED.computed_at
Key Design Decision
Position coverage check in Cell 1 determines the entire branching logic. If data is thin on positions (<70% populated), the per-position gap vectors collapse to a single school-wide aggregate — less precise but still valid.

Build the notebook now? Only question before I start: do you have 2026 transfer data populated in the DB (players who entered the portal for the 2025-26 season), or should the departure filter use whatever's most recent in the transfers table?
