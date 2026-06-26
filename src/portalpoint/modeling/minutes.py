"""Shared playing-time conversions.

The BartTorvik ingest currently stores a legacy `minutes_per_game` field that
does not represent true MPG in local data. Use `min_pct` (0-100 share of team
minutes) as the source of truth, then derive coach-facing MPG from a 40-minute
team game.
"""
from __future__ import annotations

STANDARD_GAME_MINUTES = 40.0


def minutes_share_from_min_pct(min_pct: float | None) -> float | None:
    """Convert BartTorvik min_pct (0-100) to a 0-1 minutes share."""
    if min_pct is None:
        return None
    return max(0.0, float(min_pct)) / 100.0


def minutes_per_game_from_min_pct(min_pct: float | None) -> float | None:
    """Convert BartTorvik min_pct to estimated minutes per game."""
    share = minutes_share_from_min_pct(min_pct)
    if share is None:
        return None
    return share * STANDARD_GAME_MINUTES


def resolved_minutes_per_game(
    min_pct: float | None,
    stored_minutes_per_game: float | None = None,
) -> float | None:
    """Return derived MPG from min_pct, falling back only when min_pct is absent."""
    derived = minutes_per_game_from_min_pct(min_pct)
    if derived is not None:
        return derived
    if stored_minutes_per_game is None:
        return None
    return float(stored_minutes_per_game)
