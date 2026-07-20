# News Agent PR #50 — Review, Gap Analysis, and Path Forward

**PR:** https://github.com/shanthg01/MIDS210-Capstone/pull/50  
**Branch:** `news-agent`  
**Reviewed:** 2026-07-05

---

## What the PR Does

Adds a LangGraph ReAct agent prototype that monitors 247sports, ESPN, and On3 via Tavily for transfer portal entries and coaching changes, then writes directly to the DB. Ships two HTML presentation diagrams and doc updates across four files. Two notebooks exist: a v1 "educational" walkthrough (committed) and a v2 with live DB writes (referenced everywhere in docs/STATUS.md but **not committed**).

---

## Active Bugs — Fix Before Any Run

These corrupt data if the v2 notebook is executed as-is.

### Bug 1 — Wrong season in `transfers` INSERT (Critical)

`season = int(portal_entry_date[:4])` uses the calendar year of the portal-entry date. `transfers.season` is the year the player *plays* at the destination — entry year + 1 — exactly the same bug that destroyed destination-projection training (5 rows → 2,125 rows) in CLAUDE.md TODO #9 P1.

```python
# Wrong
season = int(portal_entry_date[:4])

# Fix
season = int(portal_entry_date[:4]) + 1
```

### Bug 2 — `is_portal_candidate` orphaned from source of truth (Critical)

Raw `UPDATE player_team_fit_scores SET is_portal_candidate = true WHERE player_id = :id` writes no `transfer_portal_events` row. `sync_portal_candidate_flags()` derives the flag solely from matched `transfer_portal_events` rows — it will never see this player. Next scheduled run of either `ingest_transfers_247sports.py` or `run_gap_matching.py` has no basis to keep the flag set.

**Fix:** Write a `transfer_portal_events` row and call `sync_portal_candidate_flags()`, OR add `WHERE season = :current_season` as a minimum guard (see architectural fix in Approach A below).

### Bug 3 — UPDATE missing `WHERE season = :season` (High)

The UPDATE sets `is_portal_candidate = true` across all historical seasons (2021–2025) for the player, not just the current portal season. Poisons historical fit-score rows used by destination-projection cohort validation and future M5 training data.

```sql
-- Wrong
UPDATE player_team_fit_scores SET is_portal_candidate = true WHERE player_id = :id

-- Fix
UPDATE player_team_fit_scores SET is_portal_candidate = true 
WHERE player_id = :id AND season = :current_season
```

### Bug 4 — Invalid Gemini model ID, silently returns zero events (High)

`model='gemini-3.1-flash-lite'` does not exist. With `USE_LLM_CLASSIFIER=True` (production default), every LLM classify call hits an API error. The except block swallows it with `confidence=0.0` — agent completes without error and produces zero classified events on every run.

```python
# Wrong
model = "gemini-3.1-flash-lite"

# Fix — verify current free-tier model ID in Gemini API docs
model = "gemini-1.5-flash-8b"
```

### Bug 5 — `portal_entry_date` overwritten unconditionally (High)

`DO UPDATE SET portal_entry_date = EXCLUDED.portal_entry_date` replaces accurate dates from `ingest_transfers_247sports.py` with the agent's `today()` fallback. One line below, `from_school_id` correctly uses `COALESCE` — apply same pattern.

```sql
-- Wrong
DO UPDATE SET portal_entry_date = EXCLUDED.portal_entry_date, ...

-- Fix
DO UPDATE SET portal_entry_date = COALESCE(transfers.portal_entry_date, EXCLUDED.portal_entry_date), ...
```

### Bug 6 — Presentation HTML falsely claims 8/10 models complete (Medium)

Both HTML files show "8/10 Models Complete" and a green Done dot on the Recommendation Engine node. Per CLAUDE.md: M7 Recommendation Engine is `❌ Not started`, M5 Transfer Success Predictor is `❌ Not started`. Fix stat card to "6/10" and mark Rec Engine / Team Rating as Planned.

### Bug 7 — `coach_departure` docstring param name mismatch (Medium)

Docstring says `player_name: Name of the head coach...` but actual parameter is `coach_name`. LangChain builds the tool's JSON schema from the docstring — mismatch can silently bind `None` to `coach_name` when the LLM calls the tool.

### Bug 8 — Regex classifier fixed confidence=0.85 nullifies confidence gate (Medium)

Every regex match returns exactly 0.85 regardless of pattern specificity, always clearing the 0.7 production threshold. A tangential article matching "portal" in the body gets the same score as a direct player-entry headline, triggering real DB writes for non-events.

### Misc — v2 notebook not committed

The PR description, all docs, and STATUS.md reference `news_monitor_agent_v2.ipynb` as the functional artifact. Only v1 (with no-op `transfer_player`/`coach_departure` stubs) is in the diff.

---

## Approach Comparison

### Approach A — Patch the Current Implementation

Keep the single ReAct loop, fix the active bugs, add minimal guards. Ship a working prototype quickly.

**What changes:**
- Fix Bugs 1–8 above
- Commit v2 notebook
- Add `WHERE season = :current_season` to the UPDATE
- Call `sync_portal_candidate_flags()` after `UPDATE` instead of relying on raw SQL alone
- Fix Gemini model ID
- Fix `portal_entry_date` upsert to use `COALESCE`
- Fix `coach_departure` docstring
- Fix presentation HTML model count

**What stays:**
- Single ReAct loop (agent_node ↔ ToolNode)
- Tavily as the sole search mechanism (replaces per-source fetchers)
- Gemini for all classification
- `AgentState` with `messages`, `detected_events`, `portal_updates`, `news_sources`
- Writes to existing `transfers` + `player_team_fit_scores` tables
- No `program_events` table
- No cross-source dedup
- No review queue table
- No `portalpoint.modeling.entity_resolution` shared module

**Pros:**
- Shippable in hours — most code already written
- Simpler mental model — one agent, one loop, easy to explain in a presentation
- Tavily abstracts multi-source search behind one API call, reducing infrastructure
- LangGraph MemorySaver checkpointing is already wired for resume/debug
- Dual classifier (regex for evals, LLM for ambiguous headlines) is genuinely good design
- Notebook format works for a prototype demonstration

**Cons:**
- Permanently competing with `sync_portal_candidate_flags()` — even with the season guard, raw SQL writes bypass the authoritative availability pipeline
- No `program_events` table means coaching changes, injuries, and recruiting decommits have nowhere to land — they're either logged and lost or force future schema changes
- No review queue means low-confidence events are silently dropped; no human-review path exists
- No cross-source dedup — same player reported by ESPN + On3 writes duplicate rows and triggers duplicate `sync_portal_candidate_flags()` calls
- Entity resolution logic duplicated (not shared with `ingest_transfers_247sports.py`); diverges over time
- State schema has no run-window tracking — impossible to answer "why did this run find 0 events" without re-running
- Tavily search covers all domains with one query; per-source fetchers can apply source-specific scraping logic and rate limits independently
- Harder to extend to Phases B/C (coaching news, beat writers, recruiting) — all currently unsupported event types have no home

---

### Approach B — Refactor to Match Original Plan

Rebuild to the 4-layer pipeline architecture specified in `agentic_news_monitoring_plan.md`.

**What changes:**
- Fix all active bugs (same as Approach A)
- Create `src/portalpoint/agents/news_monitoring/` Python module structure
- Create `src/portalpoint/modeling/entity_resolution.py` (extract `_normalize_name`, `_resolve_school`, `_match_player`, `SCHOOL_ALIASES` from `ingest_transfers_247sports.py`)
- Add alembic migration for `program_events` + `program_events_review_queue` tables
- Rewrite `transfer_player` / `coach_departure` tools to write `program_events` rows, then call `sync_portal_candidate_flags()` (not raw UPDATE)
- Replace `AgentState` with `MonitoringState` (`run_window_start/end`, `raw_items`, `extracted_items`, `resolved_events`, `review_queue`, `errors`)
- Add cross-source dedup node keyed on `(event_type, resolved_entity_id, school_id, date ± 2 days)`
- Add `program_events_review_queue` path for events below confidence threshold
- Add `coaching_change` downstream effect: flag `team_system_profiles` as stale for affected school
- Wire `scripts/run_news_monitoring.py` CLI entrypoint for Airflow scheduling
- Notebook becomes a walkthrough that imports from the module (matching M1–M4 pattern)
- Expand event taxonomy to include `coaching_hire`, `coaching_fire`, `injury`, `suspension`, `nil_deal`, `recruiting_decommit`, `recruiting_commitment` (even if only `transfer_entry` and `coaching_fire` are active in Phase A)

**What stays:**
- LangGraph as orchestration framework
- Tavily for search (acceptable abstraction for Phase A)
- Dual classifier design
- Gemini for LLM classification (or switch to Haiku per original plan — either works)
- Rate limiting

**Pros:**
- Fully complementary to existing pipeline — `sync_portal_candidate_flags()` remains the single source of truth; agent feeds it, not bypasses it
- `program_events` table gives coaching changes, injuries, and future event types a proper home without touching `transfers` or `player_team_fit_scores` directly
- `program_events_review_queue` gives humans a path to review and promote low-confidence events — mirrors the `transfer_portal_events.match_status='ambiguous'` pattern already established
- Cross-source dedup prevents duplicate rows and duplicate flag-syncs
- `portalpoint.modeling.entity_resolution` shared module eliminates logic divergence — same matcher for 247Sports ETL and this agent
- `MonitoringState` with `run_window_start/end` + per-layer accumulators enables real observability and debugging
- State counts (`events_written`, `flagged_for_review`, `errors`) are the correct status output — not a boolean
- Extensible to Phases B/C without schema changes (new event types just need new `event_type` enum values)
- `user_shortlists` notification hook is a one-liner once `program_events` exists
- Coaching change stale flag surfaces useful signal in the recommendation UI
- Matches every other model's pattern (module + script + notebook, not notebook-only)

**Cons:**
- Significantly more work — estimate 3–5x the effort of Approach A
- `program_events` migration adds schema complexity before any of it is battle-tested
- 4-layer architecture is harder to explain in a presentation than "one agent loop"
- Per the original plan, `beat_writer_source` needs a Twitter/X API decision that isn't resolved yet
- Promoting a notebook prototype to a production module mid-semester is risky if scope shifts again

---

## Recommendation

**Hybrid: fix Approach A's bugs immediately, build toward Approach B's architecture incrementally.**

The single-ReAct-loop design is acceptable for a prototype. What is not acceptable are the data-correctness bugs and the permanent bypass of `sync_portal_candidate_flags()`. The recommended path:

### Immediate (unblock PR merge)
1. Fix Bugs 1–8 — all are 1–5 line changes
2. Commit v2 notebook
3. Add `WHERE season = :current_season` to the UPDATE
4. After the UPDATE, explicitly call `sync_portal_candidate_flags()` so the flag's source of truth is always respected

This makes the current prototype safe to run and correct.

### Next iteration (before scheduling in Airflow)
5. Create `portalpoint.modeling.entity_resolution` — extract shared fuzzy-match logic; both `ingest_transfers_247sports.py` and the agent import from it. Zero behavior change, eliminates future divergence.
6. Add `program_events` + `program_events_review_queue` migration. Rewrite `transfer_player` to write there first, then call `sync_portal_candidate_flags()`. Rewrite `coach_departure` to write there (instead of just logging). This is the critical step — it converts the agent from competing to complementary.
7. Replace `AgentState` with `MonitoringState` — add `run_window_start/end` and per-layer accumulators. Required for any production observability.

### Before Phase B (coaching news / free-text sources)
8. Add cross-source dedup node — required before adding more sources or you get duplicate rows
9. Wire `scripts/run_news_monitoring.py` — same `run_*.py` pattern as every other model
10. Move notebook code into `src/portalpoint/agents/news_monitoring/` module; notebook becomes the walkthrough that imports from it

The key judgment: steps 1–4 are mandatory before merging. Steps 5–7 should be done before this runs on a schedule. Steps 8–10 are prerequisites for Phase B expansion, not for Phase A correctness.

The Tavily-based single-source-search approach is an acceptable simplification of the original per-source-fetcher design for Phase A — Tavily abstracts the source routing well enough. The original plan's per-source fetchers add value when source-specific scraping logic is needed (rate limits per domain, session cookies like barttorvik's JS challenge, different content structures) — that matters more in Phase B/C than Phase A.

The original plan's `coaches` table extension question is still open — before `coach_departure` can write a properly FK'd `program_events` row with `coach_id`, confirm whether `coaches` in `src/portalpoint/db/models.py` already tracks HC/assistant identity with enough granularity, or needs `hire_date`/`departure_date`/`status` columns.
