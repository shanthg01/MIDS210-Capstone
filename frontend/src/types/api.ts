// ── Auth ─────────────────────────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string;
  expires_in: number;
  user_id: number;
  school_id: number | null;
}

export interface SignupRequest {
  email: string;
  password: string;
  full_name: string;
  school_id: number;
}

// ── Schools ───────────────────────────────────────────────────────────────────

export interface SchoolListItem {
  school_id: number;
  name: string;
  conference: string;
}

export interface SchoolListResponse {
  schools: SchoolListItem[];
}

export interface UpdateSchoolResponse {
  school_id: number;
}

export interface LoginRequest {
  email: string;
  password: string;
}

// ── Preferences ───────────────────────────────────────────────────────────────

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

// Mirrors schemas/user.py StatKey — player_season_stats columns eligible for a hard min-value filter.
export type StatKey =
  | 'usage_rate'
  | 'fg3_pct'
  | 'ft_pct'
  | 'rim_pct'
  | 'assist_rate'
  | 'tov_pct'
  | 'off_reb_pct'
  | 'def_reb_pct'
  | 'steal_pct'
  | 'block_pct'
  | 'min_pct';

export interface StatThreshold {
  stat: StatKey;
  min_value: number;
}

export interface UserFilters {
  recruiting_regions: string[];
  conferences: string[];
  positions: string[];
  target_archetypes: string[];
  nil_budget_min: number | null;
  nil_budget_max: number | null;
  min_stats: StatThreshold[] | null;
}

export interface UserPreferences {
  importance_weights: ImportanceWeights;
  filters: UserFilters;
  fit_weights: FitWeights;
}

export interface UserPreferencesUpdate {
  importance_weights?: ImportanceWeights;
  filters?: UserFilters;
  fit_weights?: FitWeights;
}

export interface PreferenceProfile {
  id: number;
  name: string;
  created_at: string;
  fit_weights: FitWeights;
  importance_weights: ImportanceWeights;
  filters: UserFilters;
}

export interface PreferenceProfileCreate {
  name: string;
  fit_weights: FitWeights;
  importance_weights: ImportanceWeights;
  filters: UserFilters;
}

export interface PreferenceProfileListResponse {
  profiles: PreferenceProfile[];
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
  player_id: string; // string, not number — backend serializes as string to avoid JS double precision loss (63-bit ids)
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
  player_id: string; // string, not number — see PlayerProfile.player_id comment
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

// ── Player Projection ─────────────────────────────────────────────────────────

export interface PlayerProjectionResponse {
  player_id: string; // string, not number — see PlayerProfile.player_id comment
  season: number;
  projection_mode: string;
  value_per_100: number;
  value_ci_lower: number | null;
  value_ci_upper: number | null;
  projected_box_score: Record<string, number> | null;
  projected_rates: Record<string, number> | null;
  skill_states: Record<string, number> | null;
  skill_percentiles: Record<string, number> | null;
  uncertainty: Record<string, unknown> | null;
  explanation: Record<string, unknown> | null;
  model_version: string;
  computed_at: string;
}

// ── Schools ───────────────────────────────────────────────────────────────────

export interface RosterGapResponse {
  school_id: number;
  season: number;
  open_minutes_by_position: Record<string, number>;
  open_usage_by_position: Record<string, number> | null;
  suggested_position: string | null;
  suggested_open_minutes: number | null;
}

// ── Fit Scores ────────────────────────────────────────────────────────────────

export interface SchemeBreakdown {
  three_point_match: number;
  pace_match: number;
  usage_match: number;
  rim_attack_match: number;
  ball_movement_match: number;
}

export interface RoleFitBreakdown {
  projected_minutes: number;
  confidence_interval: [number, number];
  starter_probability: number;
  depth_chart_position: number;
}

export interface GapMatchBreakdown {
  archetype_needed: boolean;
  position_depth_score: number;
  uniqueness_bonus: number;
  redundancy_penalty: number;
}

export interface ProgramFitBreakdown {
  nil_score: number;
  geographic_score: number;
  academic_score: number;
  cultural_score: number;
  nil_budget_alignment: number;
}

export interface FitBreakdown {
  scheme: SchemeBreakdown;
  role_fit: RoleFitBreakdown;
  gap: GapMatchBreakdown;
  program_fit: ProgramFitBreakdown;
}

export interface FitScoreResponse {
  player_id: string; // string, not number — see PlayerProfile.player_id comment
  school_id: number;
  overall_fit: number;
  gap_match: number;
  scheme_fit: number;
  role_fit: number;
  program_fit: number;
  breakdown: FitBreakdown;
  weights_used: FitWeights;
  computed_at: string;
  model_version: string;
  cache_hit: boolean;
}

// ── Team Rating Projection ────────────────────────────────────────────────────

export interface TeamRatingProjectionResponse {
  player_id: string; // string, not number — see PlayerProfile.player_id comment
  school_id: number;
  current_adjEM: number;
  projected_adjEM: number;
  delta_adjEM: number;
  confidence_interval: [number, number];
  national_percentile: number;
  conference_rank: number;
  context: string;
  expected_minutes_input: number;
  model_version: string;
}

// ── Predictions ───────────────────────────────────────────────────────────────

export type PredictedRole = 'starter' | 'rotation' | 'bench' | 'reserve';

export interface SimilarTransfer {
  player_name: string;
  season: string;
  from_school: string;
  to_school: string;
  per_before: number;
  per_after: number;
  per_change: number;
  minutes_before: number;
  minutes_after: number;
  outcome_score: number;
}

export interface SHAPExplanation {
  feature: string;
  impact: number;
  description: string;
}

export interface PredictionResponse {
  player_id: string; // string, not number — see PlayerProfile.player_id comment
  school_id: number;
  predicted_per_change: number;
  predicted_minutes: number;
  predicted_role: PredictedRole;
  confidence: number;
  similar_transfers: SimilarTransfer[];
  shap_explanations: SHAPExplanation[];
  model_version: string;
}

// ── Comparison ────────────────────────────────────────────────────────────────

export interface ComparisonPlayerEntry {
  player: PlayerBase;
  fit_score: FitScoreResponse;
  prediction: PredictionResponse;
}

export interface ComparisonMatrix {
  overall_fit: Record<string, number>;
  gap_match: Record<string, number>;
  scheme_fit: Record<string, number>;
  role_fit: Record<string, number>;
  program_fit: Record<string, number>;
}

export interface TradeOff {
  factor: string;
  description: string;
  best_player_name: string;
  best_player_id: string; // string, not number — see PlayerProfile.player_id comment
}

export interface CompareRequest {
  program_id: number;
  player_ids: string[]; // backend's Pydantic list[int] coerces incoming JSON strings to full-precision ints
}

export interface CompareResponse {
  program_id: number;
  players: ComparisonPlayerEntry[];
  comparison_matrix: ComparisonMatrix;
  trade_offs: TradeOff[];
  generated_at: string;
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
  player_id: string; // string, not number — see PlayerProfile.player_id comment
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

// ── Shortlist / Pipeline ──────────────────────────────────────────────────────

export interface ShortlistItem {
  player_id: string; // string, not number — see PlayerProfile.player_id comment
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
