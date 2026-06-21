# DIAGRAM 4: Database Architecture & Schema
## Complete Data Model with Tables, Relationships, and Indexes

[Content too long - see previous attempt for full schema details]

## Schema Summary

The database is organized into 5 logical layers:

### 1. Core Data Layer (Raw Entities)
- schools, coaches, players, player_school_seasons, games

### 2. Analytics Layer (Computed Features)
- player_season_stats, team_season_stats, player_archetypes, team_system_profiles, coaching_tendencies, roster_depth_charts, roster_gap_analysis

### 2b. Ingest / Enrichment Layer (External Source Outputs)
- hoop_explorer_team_stats (356 HE-covered teams; 17 play-type frequency + spatial cols)
- hoop_explorer_player_stats (player-level HE data; linked via he_player_code + nullable player_id)
- hoopr_team_season_stats (365 D1 teams; 11 PBP features: pbp_possession_sec, pbp_rim_pct, pbp_three_pct, pbp_mid_pct, pbp_zone1–5_pct, pbp_turnover_rate, pbp_transition_rate; UniqueConstraint on school_id+season)
- hoopr_player_season_stats (player-level PBP features; player_id nullable until espn_id/fuzzy match resolves)
- hoopr_games, hoopr_team_game_logs, hoopr_player_game_logs (game-level grain, added 2026-06-21 — Issue #17 items 1-2; same sportsdataverse-data host as hoopr_team/player_season_stats, different release tag)
- roster_snapshots, roster_snapshot_players (barttorvik rostercast.php scrape; point-in-time roster composition per school per scrape date; `returning_status` computed by diffing against player_season_stats, not given by the source; added 2026-06-21 — Issue #17 item 4)

### 3. Transfer Data Layer
- transfers (`(player_id, season)` unique — supports upsert), transfer_portal_events (raw 247Sports scrape staging, nullable player_id; added 2026-06-21 — Issue #17 item 3), nil_valuations

### 4. ML Layer (Model Outputs)
- player_team_fit_scores, predictions, recommendations (program_id, player_id, fit_score, rank), team_rating_projections

### 5. User Layer
- users (program/staff accounts: program_id, school_id, role), user_preferences (position needs, target archetypes, system style, NIL budget, geographic focus), user_feedback, user_shortlists (recruiting pipeline: program's portal player targets with status), audit_log

**Total:** ~35 main tables, partitioned by season where appropriate, with comprehensive indexing for fast queries.
