# Architecture & Patterns — Multi-Agent Pipeline (LangGraph + Claude Agent SDK)

Reference doc. Describe stack + reusable patterns from `multi_agent/`. Apply to other multi-agent projects.

## Stack

- **Python** asyncio, no web framework.
- **LangGraph** (`StateGraph`) — orchestration/control flow.
- **Claude Agent SDK** (`claude_agent_sdk.query`) — actual LLM calls per agent.
- **Pydantic** — result schema validation (`AgentResult`).
- No infra files (no Dockerfile, no requirements.txt, no CI). Pure script, run via `python main.py`. Deps implicit: `langgraph`, `claude_agent_sdk`, `pydantic`.

## Core pattern: layered agent pipeline

Agents grouped into sequential **layers**, each layer fans out to multiple agents in parallel:

```
init -> research (5 agents, parallel) -> validate_research -> analysis (4 agents, mixed) -> synthesis (2 agents, sequential-dependent) -> output
```

Generalizes to any domain: gather (parallel, cheap) -> assess (parallel/dependent, smarter) -> synthesize (sequential, smartest) -> finalize.

### Why layers instead of one big graph of independent agents
Later layers consume earlier layers' outputs as context (e.g. financial_analyst reads company_profiler + market_researcher output; risk_assessor reads ALL analysis output; decision_agent reads the synthesized report). Layering keeps dependency direction one-way and makes fan-out/fan-in explicit instead of buried in conditional edges per agent.

## Key reusable pieces

### 1. Central typed state (single source of truth)
`state/schema.py` — one `TypedDict` flows through every node. Two field categories:
- plain overwrite fields (`current_stage`, `full_report`)
- **accumulator fields** using `Annotated[List[X], operator.add]` — required when parallel branches each return partial state and LangGraph needs to merge list outputs instead of clobbering.

```python
research_outputs: Annotated[List[dict], add]
```

Apply this anywhere multiple parallel nodes write to the same list-shaped state key.

### 2. Enums over raw string keys
`state/enums.py` — `StateField`, `Stage`, `AgentName` as `str, Enum`. Prevents typo'd dict keys (`state["statup_name"]` fails silently) and dead code dictionaries (`Stage.RESEARCH_COMPLETE`). Worth doing in any LangGraph project once state dict exceeds ~5 keys.

### 3. Agent config as data, not code
`config/agent_configs.py` — every agent declared as a flat `AgentConfig(name, model, tools, timeout_seconds, system_prompt)` dataclass, grouped into layer lists (`RESEARCH_AGENTS`, `ANALYSIS_AGENTS`, ...). Decouples "which model / which tools / what timeout" from agent logic. To swap an agent's model for a cheaper one, or add a tool, touch one line in config — zero logic changes.

**Model tiering by role**: cheap/fast model (haiku) for I/O-bound parallel research agents (web search/fetch), stronger model (sonnet) for judgment/synthesis agents (financial analysis, decisions, report writing). Deliberate cost/latency lever — copy this tiering decision into other multi-agent designs.

### 4. One thin wrapper around the SDK call
`agents/base.py::run_agent()` — every agent, regardless of layer, calls this single function. It:
- builds `ClaudeAgentOptions` from config (model, tools, permission_mode, cwd)
- streams `query()`, collects `TextBlock`s
- wraps everything in `asyncio.wait_for` for a per-agent timeout
- **never raises** — catches `TimeoutError` and generic `Exception`, always returns a uniform `AgentResult(success, output, raw_output, error, agent_name, execution_time_ms)`

This is the single most reusable piece: standardize the agent-invocation boundary once, and every node/agent above it can treat agent calls as "always returns a result object, never throws." Removes per-agent try/except duplication.

### 5. Per-agent function = prompt + parse, nothing else
Each file in `agents/research/`, `agents/analysis/`, `agents/synthesis/` is a thin function: build a prompt (often asking for JSON output), call `run_agent(...)` with that agent's `AgentConfig`, then `parse_json_from_output(result.raw_output)` and attach to `result.output`. No control flow, no error handling — that's all pushed down into `run_agent` and up into the graph nodes.

### 6. Robust output parsing for LLM text
`parse_json_from_output()` — LLMs don't reliably return pure JSON. Tries, in order: direct `json.loads`, regex-extract from ` ```json ` fences, fallback to first-`{`/last-`}` slice. Cheap and reusable for any agent expecting structured output from free text.

### 7. Fan-out/fan-in via `asyncio.gather(..., return_exceptions=True)`
Every layer node (`research_node`, first half of `analysis_node`) launches all agents in that layer concurrently and gathers with `return_exceptions=True`, then loops over results classifying each as exception / reported-failure / success. **One agent failing never crashes the layer or the workflow** — it's recorded into `errors` and `research_outputs`/`analysis_outputs` with `success: False`, and the layer continues with partial data.

### 8. Quality-gated retry via conditional edges
`workflow/routing.py` + `validate_research_node`:
- `score_research_quality()` — cheap heuristic (success rate * 50 + completeness * 50) instead of a full LLM judge call.
- Conditional edge loops the **same layer** ("research") back on itself up to `retry_count < 2` if quality < threshold, otherwise proceeds with what it has.
- Degrades gracefully: never hard-fails because of one slow/bad agent; caps retries so it can't loop forever.

This retry-with-cap pattern generalizes to any layer where partial failure is recoverable by re-running just that layer.

### 9. Terminal status from accumulated state, not a flag
`output_node` derives final status (`complete` / `partial` / `failed`) by inspecting whether `full_report` and `investment_decision` exist — not by trusting an explicit "success" flag threaded through every node. Reduces state surface area; status is a pure function of what actually got produced.

## Sequence per layer (template to copy)

```python
async def layer_node(state):
    tasks = [run_agent_a(...), run_agent_b(...), ...]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    outputs, errors = [], []
    for name, result in zip(agent_names, results):
        if isinstance(result, Exception) or not result.success:
            errors.append(f"{name}: ...")
            outputs.append({"agent": name, "success": False, "error": ...})
        else:
            outputs.append({"agent": name, "output": result.output, "success": True, ...})
    return {"layer_outputs": outputs, "errors": errors, "current_stage": "layer_complete"}
```

## Checklist for applying this to a new project

1. Define the state `TypedDict`; mark any field multiple parallel nodes write to as `Annotated[list, operator.add]`.
2. Enumerate agents as config data (model/tools/timeout/system_prompt), grouped by layer.
3. Build one `run_agent()`-style wrapper that never throws and returns a uniform result type.
4. Each agent = prompt builder + call wrapper + parse output. No try/except inside.
5. Group agents into layers; within a layer, `asyncio.gather(..., return_exceptions=True)`.
6. Add a cheap quality/completeness score per layer instead of trusting raw success counts; gate retries on it with a hard cap.
7. Tier models by task: cheap model for parallel/IO agents, stronger model for synthesis/decision agents.
8. Derive final status from produced artifacts, not a threaded boolean.
