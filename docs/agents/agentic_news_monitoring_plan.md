# Agentic Plan — CBB News & Portal Monitoring

**Status: Production scaffolding complete (2026-07-11). Phase A expansion (VerbalCommits, coaching/beat-writer sources) is next.**
Original proposal text below preserved for full rollout planning reference.

## Implementation Status (as of 2026-07-11)

| Layer | Implemented | Where | Notes |
|---|---|---|---|
| Search (ingest) | ✅ | `search_news` tool | Tavily Python client; 247sports, ESPN, On3; `advanced` depth, weekly window, score ≥ 0.5 post-filter |
| Classify/Extract | ✅ (dual) | `classify_event` / `classify_events_batch` (regex) + `classify_event_llm` / `classify_events_batch_llm` (Gemini structured output) | Toggle via `USE_LLM_CLASSIFIER`; LLM classifier uses `gemini-1.5-flash-8b` at temp=0 with Pydantic `ArticleClassification` schema; title match → 0.90/0.80 confidence, body-only → 0.70/0.60 |
| Shared entity resolution | ✅ | `src/portalpoint/modeling/entity_resolution.py` | Extracted from `ingest_transfers_247sports.py` into shared module; `normalize_name`, `resolve_school`, `match_player` (3-pass: 0.82 strict → 0.75 relaxed → full-roster fallback; position pre-filter + disambiguation). Both the 247Sports ingest script and the agent import from the same module — no divergent logic. |
| Entity Resolve (agent) | ✅ | `transfer_player` tool in `resolve.py` | Calls shared `match_player`; 3-pass position-aware matching with full-roster fallback; returns `(player_id, confidence, status_tag)` |
| DB Update — player portal entry | ✅ | `transfer_player` tool | 4-step pipeline: (1) INSERT `program_events`; (2) INSERT `transfer_portal_events` ON CONFLICT COALESCE; (3) UPSERT `transfers`; (4) `sync_portal_candidate_flags()` called after commit — agent feeds the authoritative pipeline, not bypasses it |
| DB Update — coach departure | ✅ | `coach_departure` tool | Writes `program_events` (`match_status='pending_review'`) + UPDATEs `team_system_profiles SET stale_flag=true, stale_reason='coaching_change'` for affected school — surfaced in `fit_scores.py` response as `scheme_fit_stale`/`scheme_fit_stale_reason` |
| Coaching stale flag downstream | ✅ | `FitScoreResponse` | `fit_score_service.get_fit_score()` queries `team_system_profiles.stale_flag` per school/season and propagates to both real and stub response paths |
| Orchestration graph | ✅ | `src/portalpoint/agents/news_monitoring/graph.py` | ReAct loop: `START → agent_node → (tools→agent_node)* → dedup_node → END`; `should_continue` routes on `last_message.tool_calls` |
| Cross-source dedup | ✅ | `cross_source_dedup()` in `resolve.py` + `dedup_node` in `graph.py` | Groups by `(event_type, player_id, from_school_id, date-window-bucket)`; higher-confidence entry wins; runs after all tool calls complete (DB writes are idempotent via ON CONFLICT) |
| Review queue table | ✅ | Migration `2547054ae5cb`, `program_events_review_queue` table | Same shape as `program_events`; sub-threshold / pending-review events land here |
| Scheduled script | ✅ | `scripts/run_news_monitoring.py` | `--season`, `--window-days`, `--dry-run`, `--no-llm`; schedule-agnostic (cron / GitHub Actions / manual) |
| Rate limiting | ✅ | `RateLimiter` (12 RPM default) | Stays below Gemini free-tier 15 RPM ceiling |
| Agent package | ✅ | `src/portalpoint/agents/news_monitoring/` | `state.py` (`AgentState`/`MonitoringState`), `config.py`, `extract.py`, `resolve.py`, `graph.py`, `sources/tavily.py` |
| Tests | ✅ | `tests/test_entity_resolution.py` (34), `tests/test_news_monitoring.py` (33) | 67 pure unit tests; 0 regressions against existing suite |
| VerbalCommits source | ❌ not yet | — | Phase A rollout item |
| Coaching / beat-writer sources | ❌ not yet | — | Phase B/C rollout items |
| alert.py digest writer | ❌ not yet | — | Phase B item; needed for `user_shortlists` notification hook |

### LangGraph Graph Structure (production module)

```
START → agent_node → should_continue
                         │ tool_calls     │ no tool_calls
                       tools             dedup_node → END
                    (ToolNode)
                         └──────────────→ agent_node  (ReAct loop)
```

**State schema** (`AgentState`):
- `messages` — full conversation history (`add_messages` accumulator)
- `detected_events` — classified events list
- `portal_updates` — DB write log (one entry per player successfully updated)
- `news_sources` — Tavily `include_domains` list

**Tool call sequence (agent-directed):**
1. `search_news("college basketball transfer portal player enters portal")` → 2. `classify_events_batch_llm(results)` → 3. `transfer_player(name, school)` per confirmed portal entry (confidence ≥ 0.6)
4. `search_news("college basketball head coach leaves resigns fired")` → 5. `classify_events_batch_llm(results)` → 6. `coach_departure(name, school)` per confirmed coaching change

---

## Why this, why now

`scripts/ingest_transfers_247sports.py` + `transfer_portal_events`/`roster_snapshots` (this branch) solve **structured** transfer-portal status scraping from one source — it's deterministic ETL, no LLM needed, and should stay that way. But CLAUDE.md's `hourly_portal_monitoring_dag` (March–August) and the uningested `VerbalCommits` source point at a bigger need: a coach doesn't just care "did this player enter the portal" — they care about **coaching staff changes** (a new HC often triggers a wave of transfers out), NIL deal signals, injuries, suspensions, eligibility waivers, recruiting decommits — none of which arrive as clean structured JSON like 247Sports' `window.__INITIAL_DATA__`. That's unstructured text across many sources, which is exactly the "research -> extract -> resolve -> synthesize" shape the agent pattern is for.

**Scope boundary vs. existing scripts:** this pipeline does NOT replace `ingest_transfers_247sports.py`, `ingest_barttorvik.py`, etc. Those stay deterministic, source-specific, non-LLM ETL — they're reliable and cheap. This pipeline is for sources where the signal is buried in prose (beat writer tweets/articles, press releases, recruiting-site blurbs) and needs LLM extraction to become structured data at all.

## Pipeline shape

```
init -> ingest (N source agents, parallel) -> classify_extract (parallel, per-item)
     -> resolve_dedup (parallel/dependent) -> synthesize_alert (sequential) -> output
```

Runs on a schedule, not request-driven: hourly during the portal window (March–August), daily otherwise — same cadence model `hourly_portal_monitoring_dag` already names in CLAUDE.md, just widened in scope.

| Layer | Agents | Model tier | Notes |
|---|---|---|---|
| ingest | one per source (RSS/API fetchers) | none (no LLM) | I/O-bound, cheap, parallel |
| classify_extract | one call per fetched item, batched | haiku | classifies event_type + extracts fields |
| resolve_dedup | entity_resolver, cross_source_dedup | haiku/sonnet | fuzzy-match against `players`/`schools`/new `coaches` rows |
| synthesize_alert | digest_writer | sonnet | only for high-confidence novel events |

### Layer 1 — ingest (parallel, no LLM)

Each source agent is a plain fetcher, same shape as `ingest_transfers_247sports.py`'s `fetch_page` — no judgment, just retrieval:

- `transfer_portal_source` — reuses existing 247Sports scrape (already structured; passes through to Layer 3 directly, skipping Layer 2's extraction since it's already clean JSON).
- `verbal_commits_source` — VerbalCommits feed (CLAUDE.md: "not yet ingested").
- `coaching_news_source` — athletic department press releases / a coaching-changes tracker (e.g. conference wire feeds) for hires/fires/contract extensions.
- `beat_writer_source` — curated RSS/Twitter-list feed of known college basketball insiders (the "Jeff Goodman / Jon Rothstein" tier of reporters) — this is where staffing changes, injuries, and NIL signals usually break first, well before official confirmation.
- `recruiting_news_source` — On3/Rivals-style decommit/recruiting status changes (adjacent to transfer portal — a decommit is often correlated with a portal entry elsewhere).

Each returns raw text items tagged with `source`, `fetched_at`, `raw_text`, `url`.

### Layer 2 — classify_extract (parallel, per-item, cheap LLM)

One agent type, fanned out over every item fetched in Layer 1 that isn't already structured (247Sports/VerbalCommits skip this — they're JSON already). For free-text sources:

- `classify_extract_agent` — prompt: classify into `{transfer_entry, transfer_commitment, coaching_hire, coaching_fire, injury, suspension, nil_deal, recruiting_decommit, recruiting_commitment, other}`, extract `{player_name, coach_name, school_name, event_date, confidence_note}`. Output parsed via `parse_json_from_output()` (pattern item #6 — LLMs don't reliably emit pure JSON, need the fence/brace-fallback parser).
- Low-value `other`-classified items are dropped before Layer 3 — don't spend resolution effort on noise.

### Layer 3 — resolve_dedup (parallel/dependent)

- `entity_resolver` — fuzzy-matches extracted `player_name`/`coach_name`/`school_name` against `players`/`schools`/new `coaches` table. **Reuse, don't reinvent**: `_resolve_school()` and `_match_player()` from `scripts/ingest_transfers_247sports.py` already do this exact job (difflib + alias map) — extract them into a shared `portalpoint.modeling.entity_resolution` module so both the deterministic 247Sports script and this pipeline call the same matcher instead of diverging logic over time.
- `cross_source_dedup` — same real-world event reported by `beat_writer_source` and later confirmed by `coaching_news_source` should collapse to one event with the higher-confidence/official source winning, not two rows. Key on `(event_type, resolved_entity_id, school_id, date ± 2 days)`.
- Confidence routing: below threshold → write to a review queue table instead of auto-promoting (mirrors `transfer_portal_events.match_status = 'ambiguous'` pattern already established).

### Layer 4 — synthesize_alert (sequential, only for high-confidence novel events)

- `digest_writer` — for events that cleared resolution with high confidence and aren't duplicates of something already in DB, writes a one-line digest entry. This is the hook for downstream effects:
  - Coaching change at school X → flag `team_system_profiles`/`coaching_tendencies` for that school as stale (system likely changes under new staff) — informs `program_fit_interpreter` in the [scouting pipeline](agentic_scouting_explainer_plan.md).
  - New transfer/portal entry for a player a program has shortlisted (`user_shortlists`) → candidate for a notification (channel TBD — no notification system exists yet, out of scope here beyond writing the event).

### Output

Status derived from counts actually written (`events_written`, `flagged_for_review`), not a boolean — same pattern item #9 as the scouting plan.

## State schema

```python
class MonitoringState(TypedDict):
    run_window_start: datetime
    run_window_end: datetime
    raw_items: Annotated[list[dict], operator.add]
    extracted_items: Annotated[list[dict], operator.add]
    resolved_events: Annotated[list[dict], operator.add]
    review_queue: Annotated[list[dict], operator.add]
    errors: Annotated[list[str], operator.add]
```

## DB schema additions

New generic `program_events` table — deliberately broader than `transfer_portal_events` (which stays transfer-specific and untouched):

```
program_events
  id, event_type (enum above), school_id (FK), player_id (FK, nullable),
  coach_id (FK, nullable — needs new `coaches` table; CLAUDE.md lists `coaches`
            as existing core table, confirm it covers HC/asst tracking or extend it),
  event_date, source, source_url, raw_text, confidence, match_status,
  created_at

program_events_review_queue
  same shape, for sub-threshold matches awaiting manual confirmation
```

Confirm against current `coaches` table in `src/portalpoint/db/models.py` before adding — CLAUDE.md's schema list already has `coaches` under Core layer; this may just need a `hire_date`/`departure_date`/`status` column addition rather than a new table.

## Where this lives in the repo

```
src/portalpoint/agents/news_monitoring/
  state.py
  config.py
  sources/              # transfer_portal.py, verbal_commits.py, coaching_news.py, beat_writers.py, recruiting.py
  extract.py             # classify_extract_agent
  resolve.py             # entity_resolver, cross_source_dedup — imports shared entity_resolution module
  alert.py               # digest_writer
  graph.py
src/portalpoint/modeling/entity_resolution.py   # extracted from ingest_transfers_247sports.py, shared
scripts/run_news_monitoring.py                  # CLI entrypoint for the Airflow DAG to call
```

## Rollout phases

1. **Phase A:** wire only `transfer_portal_source` + `verbal_commits_source` through Layers 3-4, skipping Layer 2 (both are structured already) — gets VerbalCommits ingested and cross-source dedup working against 247Sports before adding free-text complexity.
2. **Phase B:** add `coaching_news_source` + classify_extract for that one event type — narrowest free-text slice, highest coaching-decision value (a coaching change is the single biggest "this player will probably transfer" signal).
3. **Phase C:** add `beat_writer_source` + `recruiting_news_source`, full event taxonomy.

## Open questions

- Source selection for `beat_writer_source` — which specific feeds/handles, and via what API (Twitter/X API access is paywalled; may need RSS aggregators or a licensed news API instead). Needs a decision before Phase C, not blocking Phase A/B.
- `coaches` table — confirm current columns in `src/portalpoint/db/models.py`; this plan assumes it needs extension, not creation.
- Rate limits / ToS for each source — `ingest_transfers_247sports.py` already documents 247Sports' `robots.txt` constraints (why barttorvik's own transfer JSON couldn't be used); same check needed per new source before building its `ingest` agent.
- Retention/legal: beat-writer content is copyrighted reporting, not raw data — store extracted structured fields + a link, not full scraped article text, to stay defensible (related to CLAUDE.md open question #2, NCAA/FERPA compliance review).
