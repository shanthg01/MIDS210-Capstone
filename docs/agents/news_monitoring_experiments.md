# News Monitoring — Experiment Log

Manual audit trail for prompt/config changes and eval performance.
Automated metrics are logged to MLflow experiment **`news-monitoring-eval`**.

## How to run evals

```bash
# Regex only (fast, CI-safe layer)
uv run python scripts/run_news_monitoring_eval.py

# Full suite (live API calls; reads GOOGLE_API_KEY / TAVILY_API_KEY from `.env`)
uv run python scripts/run_news_monitoring_eval.py --llm --tavily

# Tavily config A/B (baseline 10/3 vs expanded 20/5)
uv run python scripts/compare_tavily_recall_configs.py
```

View MLflow runs: `mlflow ui` (tracking URI from `.env` / `mlruns.db` at repo root).

## Versioned artifacts

| Artifact | Location |
|----------|----------|
| Tavily + classifier config | `src/portalpoint/agents/news_monitoring/config.py` |
| Agent system prompt | `src/portalpoint/agents/news_monitoring/prompts.py` |
| Golden eval set | `tests/fixtures/news_classification/golden_eval_set.json` |
| Eval runner + MLflow | `src/portalpoint/agents/news_monitoring/eval.py` |
| Pytest regression gates | `tests/test_news_classification_eval.py` |

## Experiment history

| Date | Git commit | Change | Golden | Regex acc | LLM acc | Tavily recall | Notes |
|------|------------|--------|--------|-----------|---------|---------------|-------|
| 2026-07-12 | — | Baseline: max_results=10, chunks=3, golden v1 | v1 (20) | 100% | 100% | 15% master / 40% entity-augmented | Initial golden set from Gemini research agent |
| 2026-07-12 | — | Expanded test: max_results=20, chunks=5 | v1 (20) | — | — | 20% master (+5%) | +1 coach case (`cincinnati_miller_fired_2026_03`); portal unchanged |
| 2026-07-12 | `69572b8`* | **247sports-only domains** — `TAVILY_INCLUDE_DOMAINS=["247sports.com"]` (removed on3.com, espn.com) | v1 (20) | 100% | 100% | **0% master** / **10% entity** / **5% raw** | Master −15pp (0/20); portal 0% (−10pp); coach 0% (−20pp); entity −30pp. **Lost all 3 baseline master hits** (`unc_trimble_portal_2026_04`, `kansas_state_tang_fired_2026_02`, `georgia_tech_stoudamire_fired_2026_03` — all on3/espn). Entity hits retained: `unc_trimble_portal_2026_04`, `cincinnati_miller_fired_2026_03` (247s). No newly recovered cases. MLflow: `cb74d423b67d4817a135ca2604b3d382`. **Reverted** — production config restored to 3 domains after poor recall. |

\* Eval run at commit `69572b866a90ab1231029015a501cdeeb2da8168` with a temporary uncommitted config change; domains since restored.

**When adding a row:** run `scripts/run_news_monitoring_eval.py --llm --tavily`, copy metrics from MLflow or script output, and note the git commit hash.

## Regression baselines (pytest)

| Metric | Floor | Test file |
|--------|-------|-----------|
| Regex classifier accuracy | 100% on golden v1 | `test_news_classification_eval.py` |
| Tavily master-query recall | 10% | `_MASTER_QUERY_RECALL_BASELINE` in same file |

Bump baselines only when you intentionally improve search and re-run on the same golden version.
