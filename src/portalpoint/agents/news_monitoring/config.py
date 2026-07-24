"""Constants and configuration for the news-monitoring agent."""
from __future__ import annotations

# Gemini model used for LLM classification.  Free-tier limits: 15 RPM, 1500 RPD.
GEMINI_MODEL: str = "gemini-3.1-flash-lite"

# Default calls-per-minute ceiling — stays comfortably below the 15 RPM free tier.
GEMINI_CALLS_PER_MINUTE: int = 12

# Tavily domains searched on each run.
TAVILY_INCLUDE_DOMAINS: list[str] = ["247sports.com", "on3.com", "espn.com"]
TAVILY_SEARCH_DEPTH: str = "advanced"
TAVILY_MIN_SCORE: float = 0.3
TAVILY_WINDOW_DAYS: int = 1
TAVILY_MAX_RESULTS: int = 10
TAVILY_CHUNKS_PER_SOURCE: int = 3  # Tavily default for advanced depth

# Expanded search params for recall experiments (Tavily API ceilings).
TAVILY_MAX_RESULTS_EXPANDED: int = 20
TAVILY_CHUNKS_PER_SOURCE_EXPANDED: int = 5

# Agent system-prompt search queries (graph.py SYSTEM_PROMPT step 1).
AGENT_SEARCH_QUERIES: dict[str, str] = {
    "player_enters_portal": "college basketball transfer portal player enters portal",
    "coach_leaves": "college basketball head coach leaves resigns fired departs",
}

# Minimum classifier confidence to trigger a DB write.
CONFIDENCE_THRESHOLD: float = 0.6

# Event types that route to downstream DB-write tools.
TARGET_EVENT_TYPES: set[str] = {"player_enters_portal", "coach_leaves"}

# Regex patterns per event type (deterministic fast-path classifier).
# Patterns validated against golden eval set in tests/fixtures/news_classification/
EVENT_PATTERNS: dict[str, list[str]] = {
    "player_enters_portal": [
        r"enter(?:s|ed|ing)?\s+(?:the\s+)?(?:ncaa\s+)?transfer\s+portal",
        r"entered\s+(?:the\s+)?(?:ncaa\s+)?portal",
        r"declared\s+for\s+(?:the\s+)?transfer\s+portal",
        r"in\s+(?:the\s+)?(?:ncaa\s+)?transfer\s+portal",
        r"hit(?:s|ting)?\s+(?:the\s+)?portal",
        r"entered\s+(?:his|her)\s+name\s+into\s+(?:the\s+)?(?:ncaa\s+)?transfer\s+portal",
    ],
    "coach_leaves": [
        # Patterns with "coach" in context
        r"coach.*(?:leav|left|depart|resign|step.*down)",
        r"(?:leav|left|depart|resign|step.*down).*coach",
        r"coach.*(?:fired|dismissed|let\s+go)",
        r"parting\s+ways\s+with.*coach",
        # Standalone firing/dismissal patterns (common in headlines)
        r"(?:fires|fired|firing)\s+\w+",
        r"\w+\s+(?:fires|fired)\s+",
        r"(?:dismiss(?:es|ed)|out\s+as)\s+.*(?:coach|basketball)",
        # Retirement and voluntary departure
        r"(?:announces?\s+)?retire(?:s|d|ment)",
        r"step(?:s|ped|ping)?\s+(?:down|away)",
        # Contract non-renewal / mutual separation
        r"part(?:ing|ed)?\s+ways",
        r"(?:will|would)\s+not\s+return",
        r"(?:end(?:s|ed|ing)?|conclud(?:es|ed))\s+(?:his|her|their)?\s*tenure",
        r"coaching\s+(?:change|tenure\s+ends)",
    ],
}

# Cross-source dedup window in days (events within this window on same entity
# are collapsed — higher-confidence or earlier source wins).
DEDUP_WINDOW_DAYS: int = 14
