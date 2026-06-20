# Agentic Plan — Scouting / Recommendation Explainer

Status: proposal. Applies layered multi-agent pattern from `AGENT_ARCHITECTURE.md` (LangGraph + Claude Agent SDK) to PortalPoint's Recommendation Engine (Model 7, not started) and to explaining the 4-component Fit Score.

## Why this, why now

CLAUDE.md already commits to "Explainability over black-box — multi-component scoring so users understand *why* a school ranks highly." Today the 4 fit components (`gap_match`, `scheme_fit`, `role_fit`, `program_fit`) are numbers in `player_team_fit_scores`. Nothing turns them into the sentence a coach actually wants: *"Why is this kid a top-5 fit for us?"* Model 7 (Recommendation Engine) is unbuilt and needs to fuse SVD + content + fit scores anyway — this pipeline is the natural place to add the narrative layer on top of that fusion, not a separate bolt-on.

**Scope boundary:** this pipeline does not compute fit scores. `gap_match`/`scheme_fit` (real) and `role_fit`/`program_fit` (stubbed until Models 4/5/6 land) stay exactly where they are — `src/portalpoint/modeling/`. The agent pipeline *consumes* those numbers and produces ranking + narrative. Keeps the deterministic ML math out of LLM-land entirely (no hallucinated numbers — every figure quoted in a report must trace to a DB row).

## Pipeline shape

```
init -> gather (4 agents, parallel)  -> assess (4 agents, parallel/dependent)
     -> synthesize (2 agents, sequential-dependent) -> output
```

| Layer | Agents | Model tier | Parallel? |
|---|---|---|---|
| gather | player_profile, team_context, fit_components, comparable_transfers | haiku (fetch+format, light judgment) | yes, full fan-out |
| assess | strength_risk, role_projection, program_fit_interpreter, comparable_outcomes | sonnet | yes, all read only from `gather` outputs |
| synthesize | recommendation_writer, groundedness_reviewer | sonnet/opus | sequential — reviewer reads writer's draft |
| output | — | — | derives status from produced artifacts |

### Layer 1 — gather (parallel, cheap)

Each agent is a thin DB-fetch-and-format function (per pattern item #5 — "prompt + parse, nothing else"; here several gather agents don't even need an LLM call, they're `AgentResult`-shaped wrappers around a query so they compose uniformly with the LLM-backed ones).

- `player_profile_agent` — `player_season_stats`, `player_archetypes` (archetype label), recent `transfers` row if any, season trend.
- `team_context_agent` — `team_system_profiles` (system label), `roster_depth_charts`, `roster_gap_analysis` for the target school/season.
- `fit_components_agent` — current row(s) from `player_team_fit_scores` (gap_match, scheme_fit, role_fit, program_fit, weighted overall) — straight passthrough, zero interpretation. This is the ground truth every later claim must cite.
- `comparable_transfers_agent` — players with the same `player_archetypes` cluster who transferred into teams with a similar `team_system_profiles` label in prior seasons; pulls outcome if Model 5 (Transfer Success) exists, else just lists the comparables with pre/post minutes deltas from `transfers.pre_minutes_per_game`.

### Layer 2 — assess (parallel, dependent on Layer 1 only)

LLM agents, sonnet tier — judgment calls, but each is scoped to interpreting one component so no agent has to reason about the whole picture yet.

- `strength_risk_analyst` — reads `player_profile` + `team_context` + `fit_components`; lists 2-4 concrete strengths/risks tied to specific numbers (e.g. "scheme_fit 91 driven by 3PT rate matching team's spacing system").
- `role_projection_analyst` — turns `role_fit`'s Bayesian posterior (once Model 4 exists; until then, flags as unavailable rather than inventing a number) into a plain-language minutes projection with the credible interval explicitly stated, not just a point estimate — matches CLAUDE.md's "surface confidence intervals in UI."
- `program_fit_interpreter` — unpacks the multi-attribute utility breakdown (NIL/geography/academics weights) into which attributes drove the score, respecting the program's custom weights from `PUT /api/programs/{id}/preferences`.
- `comparable_outcomes_analyst` — summarizes what happened to similar past transfers (pattern, not prediction) — explicit "historically, players like this..." framing so it isn't mistaken for the Model 5 prediction itself.

### Layer 3 — synthesize (sequential-dependent, smartest tier)

- `recommendation_writer` — reads ALL Layer 2 output, drafts the final structured report: ranked position among other candidates for that school + narrative scouting paragraph + the one-line "why" used in list views.
- `groundedness_reviewer` — quality gate. Extracts every numeric/factual claim from the draft and checks it against Layer 1's raw `fit_components`/`player_profile` data (cheap, deterministic — not another freeform LLM judgment call). If a claim doesn't trace to source data, send back to `recommendation_writer` with the flagged claims; cap at 2 retries (pattern item #8), then ship with an `"unverified_claims"` flag rather than blocking forever.

### Output

Terminal status (`complete` / `partial` / `failed`) derived from whether `narrative_report` and `ranked_position` exist (pattern item #9) — not a threaded success flag. `partial` covers the common case: role_fit/program_fit still stubbed, so reports run today with 2/4 components real and should say so explicitly rather than pretending full confidence.

## State schema (`state/schema.py` equivalent)

```python
class ScoutingState(TypedDict):
    player_id: int
    school_id: int
    season: int
    current_stage: str
    gather_outputs: Annotated[list[dict], operator.add]
    assess_outputs: Annotated[list[dict], operator.add]
    narrative_report: str | None
    ranked_position: int | None
    unverified_claims: list[str]
    errors: Annotated[list[str], operator.add]
    retry_count: int
```

## Where this lives in the repo

Mirrors the existing `modeling/` convention — pure logic separated from notebook/script/API callers:

```
src/portalpoint/agents/
  state.py                 # ScoutingState TypedDict
  config.py                 # AgentConfig per agent, grouped GATHER_AGENTS / ASSESS_AGENTS / SYNTH_AGENTS
  base.py                   # run_agent() wrapper — never raises, uniform AgentResult
  gather/                   # player_profile.py, team_context.py, fit_components.py, comparable_transfers.py
  assess/                   # strength_risk.py, role_projection.py, program_fit.py, comparable_outcomes.py
  synthesize/               # recommendation_writer.py, groundedness_reviewer.py
  graph.py                  # LangGraph StateGraph wiring layers + conditional retry edge
scripts/
  run_scouting_report.py    # CLI: single player x school, for testing/backfill
```

## Integration points

- **`recommendations.py` router** — real implementation calls the graph per `(school_id, season)`, returns top-N with narrative. Replaces the stub.
- **`comparison.py` router** — can reuse `gather` + `strength_risk_analyst` per player, run side-by-side instead of building a separate pipeline.
- **Caching** — per CLAUDE.md's precompute strategy: nightly batch for top-50 portal players per program (Sunday batch, alongside `weekly_model_training_dag`), Redis 30-min cache for on-demand edge cases (a coach pulling up a player not in the precomputed top-50).
- **Depends on [agentic_news_monitoring_plan.md](agentic_news_monitoring_plan.md)** — `comparable_transfers_agent` and a future `program_context_agent` get materially better once coaching-staff/news events are in DB (e.g. a coaching change should visibly move `program_fit_interpreter`'s output for that school).

## Rollout phases

1. **Phase A (now-buildable):** gather + `strength_risk_analyst` + `fit_components` passthrough only, using real `gap_match`/`scheme_fit`. Ship as the "why" tooltip on existing fit score UI — no recommendation ranking yet, since that needs Model 7's SVD/content fusion.
2. **Phase B:** add `role_projection_analyst`/`program_fit_interpreter` once Models 4/6/program-fit weighting are real; until then they explicitly emit "model not yet available" rather than fabricating numbers.
3. **Phase C:** full `recommendation_writer` + `groundedness_reviewer` + ranking, wired into `recommendations.py`, once Model 7 fusion logic exists upstream.

## Open questions

- Model tiering cost: haiku gather agents are mostly DB fetches — confirm whether they need an LLM call at all, or can be plain async functions returning `AgentResult` (likely the latter; keep the uniform wrapper for composability, skip the SDK call).
- Where does `groundedness_reviewer`'s claim-extraction logic live — regex/NER, or a cheap LLM call? Start with regex over known fields (player names, percentages, the 4 component names) since the universe of valid claims is small and structured.
- NIL data gap (`nil_valuations` empty, CLAUDE.md open question #6) — `program_fit_interpreter` must degrade gracefully (omit NIL commentary) rather than guess.
