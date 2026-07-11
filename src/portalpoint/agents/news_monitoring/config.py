"""Constants and configuration for the news-monitoring agent."""
from __future__ import annotations

# Gemini model used for LLM classification.  Free-tier limits: 15 RPM, 1500 RPD.
GEMINI_MODEL: str = "gemini-3.1-flash-lite"

# Default calls-per-minute ceiling — stays comfortably below the 15 RPM free tier.
GEMINI_CALLS_PER_MINUTE: int = 12

# Tavily domains searched on each run.
TAVILY_INCLUDE_DOMAINS: list[str] = ["247sports.com", "on3.com", "espn.com"]
TAVILY_SEARCH_DEPTH: str = "advanced"
TAVILY_MIN_SCORE: float = 0.5
TAVILY_WINDOW_DAYS: int = 7

# Minimum classifier confidence to trigger a DB write.
CONFIDENCE_THRESHOLD: float = 0.6

# Event types that route to downstream DB-write tools.
TARGET_EVENT_TYPES: set[str] = {"player_enters_portal", "coach_leaves"}

# Regex patterns per event type (deterministic fast-path classifier).
EVENT_PATTERNS: dict[str, list[str]] = {
    "player_enters_portal": [
        r"enter(?:s|ed|ing)?\s+(?:the\s+)?(?:ncaa\s+)?transfer\s+portal",
        r"entered\s+(?:the\s+)?(?:ncaa\s+)?portal",
        r"declared\s+for\s+(?:the\s+)?transfer\s+portal",
        r"in\s+(?:the\s+)?(?:ncaa\s+)?transfer\s+portal",
        r"hit(?:s|ting)?\s+(?:the\s+)?portal",
    ],
    "coach_leaves": [
        r"coach.*(?:leav|left|depart|resign|step.*down)",
        r"(?:leav|left|depart|resign|step.*down).*coach",
        r"coach.*(?:fired|dismissed|let\s+go)",
        r"parting\s+ways\s+with.*coach",
    ],
}

# Cross-source dedup window in days (events within this window on same entity
# are collapsed — higher-confidence or earlier source wins).
DEDUP_WINDOW_DAYS: int = 2
