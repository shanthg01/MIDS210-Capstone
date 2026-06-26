"""
scripts/seed_test_data.py

Idempotent seed for the rows the pytest suite assumes already exist:
school_id=301, player_id in (1, 2, 3, 42, 101) — see tests/conftest.py and
tests/test_players.py / tests/test_users.py. The suite is integration-style
(hits the real DB, not fixtures) — locally these rows come from running the
ingest pipeline (ingest_barttorvik.py etc.) and the modeling scripts
(run_player_projection.py etc.); CI has neither, so this fills the gap
deterministically and fast. Safe to run against an already-populated dev DB
too — every insert is ON CONFLICT DO NOTHING (or DO NOTHING on the relevant
partial unique index for player_projections), so a real row from an actual
model run always wins over the seed placeholder.

Usage:
  uv run python scripts/seed_test_data.py

`player_projections`' seed row is the one exception to "every insert is
ON CONFLICT DO NOTHING" below — it has a time-dependent `expires_at`
(`now() + 30 days` at seed time), and `/api/players/{id}/projection`
filters on `expires_at > now()`. A plain DO NOTHING would let an old seed
row go stale on any dev DB more than 30 days old, silently breaking the
projection tests with no code change (real bug, found 2026-06-25 via a
teammate's CI-adjacent run — confirmed by checking player_id=101, which
turns out to be a real ingested player, not just a seed fixture: a real
model run upserting that same row coincidentally kept the test passing
locally, masking the underlying staleness bug). Refreshes
`expires_at`/`computed_at` on conflict instead.
"""
from __future__ import annotations

from sqlalchemy import text

from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.player_projection import MODEL_VERSION_CROSS_SEASON_FORECAST as PLAYER_PROJECTION_MODEL_VERSION

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
    (player_id, school_id, season, games_played, minutes_per_game, min_pct,
     points_per_game, rebounds_per_game, assists_per_game, steals_per_game,
     blocks_per_game, turnovers_per_game, true_shooting_pct, usage_rate,
     three_point_rate, rim_rate, mid_range_rate, data_complete, minutes_threshold_met)
VALUES
    (1,   301, 2026, 20, 25.0, 62.5, 12.0, 4.0, 3.0, 1.0, 0.5, 2.0, 55.0, 20.0, 0.30, 0.40, 0.20, true, true),
    (2,   301, 2026, 22, 28.0, 70.0, 18.0, 5.0, 2.0, 0.8, 0.3, 1.5, 58.0, 24.0, 0.45, 0.30, 0.20, true, true),
    (101, 301, 2026, 25, 30.0, 75.0, 15.0, 6.0, 4.0, 1.2, 0.9, 2.5, 56.0, 22.0, 0.35, 0.35, 0.25, true, true)
ON CONFLICT (player_id, school_id, season) DO NOTHING;

INSERT INTO player_archetypes (player_id, season, archetype_id, archetype_label, confidence, model_version)
VALUES (101, 2026, 0, 'Test Archetype', 0.85, 'seed-test-v1')
ON CONFLICT (player_id, season) DO NOTHING;

INSERT INTO player_projections
    (player_id, school_id, season, projection_mode, value_per_100, value_ci_lower, value_ci_upper,
     projected_box_score, projected_rates, skill_states, skill_percentiles, model_version, expires_at)
VALUES (
    101, NULL, 2026, 'neutral', 2.5, 0.5, 4.5,
    '{"pts_per_40": 15.0, "reb_per_40": 6.0, "ast_per_40": 4.0, "stl_per_40": 1.2,
      "blk_per_40": 0.9, "tov_per_40": 2.5}'::jsonb,
    '{"rate_assist": 4.0, "rate_stl": 1.2, "rate_blk": 0.9, "rate_tov": 2.5}'::jsonb,
    '{"shooting_3p": 0.35, "shooting_2p_finishing": 0.55, "free_throw_touch": 0.75,
      "shot_creation_usage": 22.0, "passing_creation": 15.0, "turnover_avoidance": -12.0,
      "offensive_rebounding": 5.0, "defensive_rebounding": 10.0,
      "steal_disruption": 1.2, "block_rim_protection": 0.9, "foul_discipline": -2.5}'::jsonb,
    '{"shooting_3p": 60.0, "shooting_2p_finishing": 65.0, "free_throw_touch": 70.0,
      "shot_creation_usage": 55.0, "passing_creation": 80.0, "turnover_avoidance": 50.0,
      "offensive_rebounding": 40.0, "defensive_rebounding": 60.0,
      "steal_disruption": 45.0, "block_rim_protection": 35.0, "foul_discipline": 55.0}'::jsonb,
    :player_projection_model_version, now() + interval '30 days'
)
ON CONFLICT (player_id, season, model_version) WHERE school_id IS NULL
DO UPDATE SET expires_at = EXCLUDED.expires_at, computed_at = now();
"""


def seed_test_data() -> None:
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(text(SEED_SQL), {"player_projection_model_version": PLAYER_PROJECTION_MODEL_VERSION})


def main() -> None:
    seed_test_data()
    print("Seed data ready: school(301), players(1,2,3,42,101), player_season_stats, player_archetypes, player_projections")


if __name__ == "__main__":
    main()
