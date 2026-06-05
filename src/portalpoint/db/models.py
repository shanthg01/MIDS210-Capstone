# Step 6: SQLAlchemy ORM models matching the 5-layer schema from diagram_4_database_architecture.md
#
# Layer 1 — Core: schools, coaches, players, player_school_seasons, games
# Layer 2 — Analytics: player_season_stats, team_season_stats, player_archetypes,
#            team_system_profiles, coaching_tendencies, roster_depth_charts, roster_gap_analysis
# Layer 3 — Transfer: transfers, nil_valuations
# Layer 4 — ML Outputs: player_team_fit_scores, predictions, recommendations, team_rating_projections
# Layer 5 — User: users, user_preferences, user_feedback, user_shortlists, audit_log
#
# Notes:
#   - Partition player_season_stats and team_season_stats by season
#   - Use pg_vector column type for cosine similarity searches (player/team style vectors)
#   - Trigram GIN index on players.full_name for fuzzy search
