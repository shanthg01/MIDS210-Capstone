"""
scripts/seed_test_data.py

Idempotent seed for the rows the pytest suite assumes already exist:
school_id in (9900301, 9900302), player_id in (1-8, 42, 101) — see tests/conftest.py
and tests/test_players.py / tests/test_users.py. The suite is integration-style
(hits the real DB, not fixtures) — locally these rows come from running the
ingest pipeline (ingest_barttorvik.py etc.) and the modeling scripts
(run_player_projection.py etc.); CI has neither, so this fills the gap
deterministically and fast. Safe to run against an already-populated dev DB
too — every insert is ON CONFLICT DO NOTHING (or DO NOTHING on the relevant
partial unique index for player_projections), so a real row from an actual
model run always wins over the seed placeholder.

school_id=9900301 (not a real ingested id like 1/"Houston") is deliberately what
conftest.py's test user signs up with — a real schools.id in CI's fresh,
unseeded DB has to come from here, not from barttorvik ingestion that never
runs there (real regression, 2026-06-26: signup hardcoded to school_id=1,
which only exists locally after a real ingest; CI's empty schools table made
every login-dependent test fail with "Invalid email or password" since the
seed signup itself 404'd). school_id=9900302 exists solely so
test_update_school_succeeds_and_restores has a second real school to swap to
without depending on locally-ingested data either.

Players 4-8 exist solely so GET /api/recommendations (recommendations.py,
which now pulls its 10 stub-score "recommendations" from real Player rows
ORDER BY id LIMIT 10, not fabricated ids — see PR comment history) has 10
real players to draw from in CI; locally this was always masked by ~13k
real ingested players, so the test only failed in CI (real regression,
2026-06-26, found right after the school_id one above).

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

from portalpoint.core.security import hash_password
from portalpoint.modeling.io import get_sync_engine
from portalpoint.modeling.player_projection import MODEL_VERSION_CROSS_SEASON_FORECAST as PLAYER_PROJECTION_MODEL_VERSION

TEST_EMAIL = "player@example.com"
TEST_PASS = "testpass123"
TEST_NAME = "Test Player"

SEED_SQL = """
DELETE FROM player_season_stats
WHERE player_id IN (1, 2, 101)
  AND school_id IN (301, 302);

DELETE FROM team_system_profiles
WHERE school_id IN (301, 302)
  AND model_version = 'seed-test-v1';

INSERT INTO schools (id, name, conference, city, state, region)
VALUES
    (9900301, 'Test University', 'Test Conference', 'Testville', 'TS', 'Test Region'),
    (9900302, 'Test University Two', 'Test Conference', 'Testville', 'TS', 'Test Region')
ON CONFLICT (id) DO NOTHING;

INSERT INTO team_system_profiles
    (school_id, season, cluster_id, system_label, offense_cluster_id, defense_cluster_id, model_version)
VALUES (9900301, 2026, 0, 'Test System Label', 0, 0, 'seed-test-v1')
ON CONFLICT (school_id, season) DO NOTHING;

INSERT INTO players (id, full_name, position, class_year, barttorvik_id)
VALUES
    (1,   'Test Player One',      'PG', 'so', 'seed-test-player-1'),
    (2,   'Test Player Two',      'SG', 'jr', 'seed-test-player-2'),
    (3,   'Test Player Three',    'SF', 'sr', 'seed-test-player-3'),
    (4,   'Test Player Four',     'PF', 'fr', 'seed-test-player-4'),
    (5,   'Test Player Five',     'C',  'so', 'seed-test-player-5'),
    (6,   'Test Player Six',      'PG', 'jr', 'seed-test-player-6'),
    (7,   'Test Player Seven',    'SG', 'sr', 'seed-test-player-7'),
    (8,   'Test Player Eight',    'SF', 'fr', 'seed-test-player-8'),
    (42,  'Test Player FortyTwo', 'PF', 'fr', 'seed-test-player-42'),
    (101, 'Marcus Test Player',   'C',  'sr', 'seed-test-player-101')
ON CONFLICT (id) DO UPDATE SET barttorvik_id = COALESCE(players.barttorvik_id, EXCLUDED.barttorvik_id);

INSERT INTO player_season_stats
    (player_id, school_id, season, games_played, minutes_per_game, min_pct,
     points_per_game, rebounds_per_game, assists_per_game, steals_per_game,
     blocks_per_game, turnovers_per_game, true_shooting_pct, usage_rate,
     three_point_rate, rim_rate, mid_range_rate, data_complete, minutes_threshold_met)
VALUES
    (1,   9900301, 2026, 20, 25.0, 62.5, 12.0, 4.0, 3.0, 1.0, 0.5, 2.0, 55.0, 20.0, 0.30, 0.40, 0.20, true, true),
    (2,   9900301, 2026, 22, 28.0, 70.0, 18.0, 5.0, 2.0, 0.8, 0.3, 1.5, 58.0, 24.0, 0.45, 0.30, 0.20, true, true),
    (101, 9900301, 2026, 25, 30.0, 75.0, 15.0, 6.0, 4.0, 1.2, 0.9, 2.5, 56.0, 22.0, 0.35, 0.35, 0.25, true, true)
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

INSERT INTO team_rating_projections
    (player_id, school_id, season,
     current_adj_em, projected_adj_em, delta_adj_em,
     baseline_adj_o, baseline_adj_d, projected_adj_o, projected_adj_d,
     ci_lower, ci_upper,
     national_percentile, conference_rank,
     expected_minutes_input, candidate_usage_role,
     explanation, minutes_distribution,
     model_version, expires_at)
VALUES
    (101, 9900301, 2027, 5.0, 7.2, 2.2,
     105.0, 100.0, 107.0, 99.8,
     1.1, 3.3,
     60, 3,
     22.0, 'secondary_creator',
     '{"candidate_off_contribution": 0.8, "candidate_def_contribution": 0.6}'::jsonb,
     '{"replacement_slot": 5.0, "same_position_depth": 11.0, "flexible_bench": 6.0}'::jsonb,
     'team-roster-proj-v1', now() + interval '7 days'),
    (101, 9900302, 2027, 3.0, 4.5, 1.5,
     103.0, 100.0, 104.0, 99.5,
     0.5, 2.5,
     45, 5,
     18.0, 'rotation',
     '{"candidate_off_contribution": 0.5, "candidate_def_contribution": 0.3}'::jsonb,
     '{}'::jsonb,
     'team-roster-proj-v1', now() + interval '7 days'),
    (101, 9900301, 2026, 4.0, 4.4, 0.4,
     104.0, 100.0, 104.4, 100.0,
     -0.6, 1.4,
     52, 4,
     12.0, 'depth',
     '{"candidate_off_contribution": 0.2, "candidate_def_contribution": 0.1}'::jsonb,
     '{}'::jsonb,
     'team-roster-proj-v1', now() + interval '7 days'),
    (42, 9900301, 2027, 2.0, 2.3, 0.3,
     101.0, 99.0, 101.2, 98.9,
     -0.5, 1.1,
     40, 6,
     8.0, 'depth',
     '{}'::jsonb,
     '{}'::jsonb,
     'team-roster-proj-v1', now() - interval '1 day')
ON CONFLICT ON CONSTRAINT uq_team_rating_projection DO UPDATE SET
    current_adj_em = EXCLUDED.current_adj_em,
    projected_adj_em = EXCLUDED.projected_adj_em,
    delta_adj_em = EXCLUDED.delta_adj_em,
    baseline_adj_o = EXCLUDED.baseline_adj_o,
    baseline_adj_d = EXCLUDED.baseline_adj_d,
    projected_adj_o = EXCLUDED.projected_adj_o,
    projected_adj_d = EXCLUDED.projected_adj_d,
    ci_lower = EXCLUDED.ci_lower,
    ci_upper = EXCLUDED.ci_upper,
    national_percentile = EXCLUDED.national_percentile,
    conference_rank = EXCLUDED.conference_rank,
    expected_minutes_input = EXCLUDED.expected_minutes_input,
    candidate_usage_role = EXCLUDED.candidate_usage_role,
    explanation = EXCLUDED.explanation,
    minutes_distribution = EXCLUDED.minutes_distribution,
    expires_at = EXCLUDED.expires_at,
    computed_at = now();

INSERT INTO users (email, hashed_password, full_name, school_id, is_active, is_verified)
VALUES (:test_email, :test_password_hash, :test_name, 9900301, true, false)
ON CONFLICT (email) DO UPDATE SET
    hashed_password = EXCLUDED.hashed_password,
    full_name = EXCLUDED.full_name,
    school_id = EXCLUDED.school_id,
    updated_at = now();

INSERT INTO user_preferences
    (user_id, importance_scheme_fit, importance_role_fit, importance_gap_match, importance_program_fit,
     weight_gap, weight_scheme, weight_role, weight_program, filters)
SELECT id, 7, 5, 5, 5, 0.20, 0.30, 0.25, 0.25, '{}'::jsonb
FROM users
WHERE email = :test_email
ON CONFLICT (user_id) DO NOTHING;
"""


def seed_test_data() -> None:
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(
            text(SEED_SQL),
            {
                "player_projection_model_version": PLAYER_PROJECTION_MODEL_VERSION,
                "test_email": TEST_EMAIL,
                "test_password_hash": hash_password(TEST_PASS),
                "test_name": TEST_NAME,
            },
        )


def main() -> None:
    seed_test_data()
    print("Seed data ready: schools(9900301,9900302), players(1-8,42,101), player_season_stats, player_archetypes, player_projections, team_system_profiles")


if __name__ == "__main__":
    main()
