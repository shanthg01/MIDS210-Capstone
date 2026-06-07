// ── Auth ─────────────────────────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string;
  expires_in: number;
  user_id: number;
}

export interface SignupRequest {
  email: string;
  password: string;
  full_name: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

// ── Fit scores ────────────────────────────────────────────────────────────────

export interface FitWeights {
  gap: number;
  scheme: number;
  role_fit: number;
  program_fit: number;
}

export interface ImportanceWeights {
  scheme_fit: number;
  role_fit: number;
  gap_match: number;
  program_fit: number;
}

export interface UserFilters {
  recruiting_regions: string[];
  conferences: string[];
  positions: string[];
  target_archetypes: string[];
  nil_budget_min: number | null;
  nil_budget_max: number | null;
  min_stats: Record<string, number> | null;
}

export interface UserPreferences {
  importance_weights: ImportanceWeights;
  filters: UserFilters;
  fit_weights: FitWeights;
}

// ── Players ───────────────────────────────────────────────────────────────────

export interface PlayerStats {
  season: string;
  games_played: number;
  minutes_per_game: number;
  points_per_game: number;
  rebounds_per_game: number;
  assists_per_game: number;
  steals_per_game: number;
  blocks_per_game: number;
  turnovers_per_game: number;
  per: number;
  true_shooting_pct: number;
  usage_rate: number;
  assist_rate: number;
  bpm: number | null;
  win_shares: number | null;
  three_point_rate: number;
  rim_rate: number;
  mid_range_rate: number;
  assisted_fg_pct: number;
}

export interface PlayerArchetype {
  archetype_id: number;
  label: string;
  confidence: number;
}

export interface PlayerProfile {
  player_id: number;
  full_name: string;
  position: string;
  height_inches: number | null;
  class_year: string;
  hometown: string | null;
  current_school: string;
  current_school_id: number;
  archetype: PlayerArchetype | null;
  current_season_stats: PlayerStats | null;
  is_in_portal: boolean;
  portal_entry_date: string | null;
  twitter_handle: string | null;
  social_followers: number | null;
}

export interface PlayerBase {
  player_id: number;
  full_name: string;
  position: string;
  class_year: string;
  hometown: string | null;
  current_school: string;
  current_school_id: number;
}

export interface PlayerSearchResponse {
  results: PlayerBase[];
  total: number;
  query: string;
}

// ── Recommendations ───────────────────────────────────────────────────────────

export interface FitComponents {
  gap_match: number;
  scheme_fit: number;
  role_fit: number;
  program_fit: number;
}

export interface RecommendationItem {
  rank: number;
  player_id: number;
  player_name: string;
  position: string;
  overall_fit: number;
  components: FitComponents;
  reasoning: string;
}

export interface RecommendationsResponse {
  program_id: number;
  recommendations: RecommendationItem[];
  total: number;
  generated_at: string;
  model_version: string;
}

// ── Shortlist ─────────────────────────────────────────────────────────────────

export interface ShortlistItem {
  player_id: number;
  player_name: string;
  position: string;
  overall_fit: number | null;
  added_at: string;
}

export interface ShortlistResponse {
  user_id: number;
  players: ShortlistItem[];
  total: number;
}
