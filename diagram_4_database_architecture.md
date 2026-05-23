# DIAGRAM 4: Database Architecture & Schema
## Complete Data Model with Tables, Relationships, and Indexes

[Content too long - see previous attempt for full schema details]

## Schema Summary

The database is organized into 5 logical layers:

### 1. Core Data Layer (Raw Entities)
- schools, coaches, players, player_school_seasons, games

### 2. Analytics Layer (Computed Features)  
- player_season_stats, team_season_stats, player_archetypes, team_system_profiles, coaching_tendencies, roster_depth_charts, roster_gap_analysis

### 3. Transfer Data Layer
- transfers, nil_valuations

### 4. ML Layer (Model Outputs)
- player_team_fit_scores, predictions, recommendations, team_rating_projections

### 5. User Layer
- users, user_preferences, user_feedback, user_shortlists, audit_log

**Total:** ~25 main tables, partitioned by season where appropriate, with comprehensive indexing for fast queries.
