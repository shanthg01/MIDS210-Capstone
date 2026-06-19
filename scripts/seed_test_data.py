"""
scripts/seed_test_data.py

Idempotent seed for the rows the pytest suite assumes already exist:
school_id=301, player_id in (1, 2, 3, 42, 101) — see tests/conftest.py and
tests/test_players.py / tests/test_users.py. The suite is integration-style
(hits the real DB, not fixtures) — locally these rows come from running the
ingest pipeline (ingest_barttorvik.py etc.); CI has no ingest step, so this
fills the gap deterministically and fast. Safe to run against an
already-populated dev DB too — every insert is ON CONFLICT DO NOTHING.

Usage:
  uv run python scripts/seed_test_data.py
"""
from __future__ import annotations

from sqlalchemy import text

from portalpoint.modeling.io import get_sync_engine

SEED_SQL = """
INSERT INTO schools (id, name, conference, city, state, region)
VALUES (301, 'Test University', 'Test Conference', 'Testville', 'TS', 'Test Region')
ON CONFLICT (id) DO NOTHING;

INSERT INTO players (id, full_name, position, class_year)
VALUES
    (1,   'Test Player One',      'PG', 'so'),
    (2,   'Test Player Two',      'SG', 'jr'),
    (3,   'Test Player Three',    'SF', 'sr'),
    (42,  'Test Player FortyTwo', 'PF', 'fr'),
    (101, 'Marcus Test Player',   'C',  'sr')
ON CONFLICT (id) DO NOTHING;

INSERT INTO player_season_stats
    (player_id, school_id, season, games_played, minutes_per_game,
     points_per_game, rebounds_per_game, assists_per_game, steals_per_game,
     blocks_per_game, turnovers_per_game, true_shooting_pct, usage_rate,
     three_point_rate, rim_rate, mid_range_rate, data_complete, minutes_threshold_met)
VALUES
    (1,   301, 2026, 20, 25.0, 12.0, 4.0, 3.0, 1.0, 0.5, 2.0, 55.0, 20.0, 0.30, 0.40, 0.20, true, true),
    (2,   301, 2026, 22, 28.0, 18.0, 5.0, 2.0, 0.8, 0.3, 1.5, 58.0, 24.0, 0.45, 0.30, 0.20, true, true),
    (101, 301, 2026, 25, 30.0, 15.0, 6.0, 4.0, 1.2, 0.9, 2.5, 56.0, 22.0, 0.35, 0.35, 0.25, true, true)
ON CONFLICT (player_id, school_id, season) DO NOTHING;

INSERT INTO player_archetypes (player_id, season, archetype_id, archetype_label, confidence, model_version)
VALUES (101, 2026, 0, 'Test Archetype', 0.85, 'seed-test-v1')
ON CONFLICT (player_id, season) DO NOTHING;
"""


def main() -> None:
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(text(SEED_SQL))
    print("Seed data ready: school(301), players(1,2,3,42,101), player_season_stats, player_archetypes")


if __name__ == "__main__":
    main()
