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
  team_impact_fit: number;
}

export interface ImportanceWeights {
  scheme_fit: number;
  role_fit: number;
  gap_match: number;
  team_impact_fit: number;
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

export interface TeamSystemProfileResponse {
  school_id: number;
  season: number;
  system_label: string;
  offense_label: string | null;
  defense_label: string | null;
}

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
  rim_attack_match: number;
  mid_range_match: number;
  // Play-type match (HoopExplorer 6-dim cosine) — only present when both
  // player and team have HE coverage.
  he_scheme_fit?: number | null;
  he_breakdown?: Record<string, number> | null;
}

export interface RoleFitBreakdown {
  projected_minutes: number;
  confidence_interval: [number, number];
  starter_probability: number;
  depth_chart_position: number;
}

export interface GapFeatureGap {
  feature: string;
  gap: number;
}

export interface GapMatchBreakdown {
  archetype_needed: boolean;
  position_depth_score: number;
  gap_reliability: number;
  top_gap_features: GapFeatureGap[];
}

export interface TeamImpactBreakdown {
  available: boolean;
  delta_adj_em: number | null;
  current_adj_em: number | null;
  projected_adj_em: number | null;
  confidence_interval: [number, number] | null;
  national_percentile: number | null;
  conference_rank: number | null;
  model_version: string | null;
}

export interface FitBreakdown {
  scheme: SchemeBreakdown;
  role_fit: RoleFitBreakdown;
  gap: GapMatchBreakdown;
  team_impact: TeamImpactBreakdown;
}

export interface FitComponentValues {
  gap_match: number;
  scheme_fit: number;
  role_fit: number;
  team_impact_fit: number;
}

export interface FitScoreResponse {
  player_id: string; // string, not number — see PlayerProfile.player_id comment
  school_id: number;
  overall_fit: number;
  personalized_fit: number | null;
  gap_match: number;
  scheme_fit: number;
  role_fit: number;
  team_impact_fit: number;
  raw_components: FitComponentValues;
  component_confidences: FitComponentValues;
  overall_confidence: number;
  data_quality_flags: Record<string, boolean | string>;
  breakdown: FitBreakdown;
  weights_used: FitWeights;
  personalized_weights: FitWeights | null;
  computed_at: string;
  model_version: string;
  calibration_version: string | null;
  cache_hit: boolean;
  // PR #33 follow-ups — populated by backend, was silently dropped by FE
  is_portal_candidate: boolean;      // player has Entered/Committed portal event this season
  is_current_school: boolean;        // player already on this school's own roster
  is_roster_baseline_member: boolean; // player counts in shared roster baseline
  // Gate 7 (PR #50) — set when news-monitoring agent detects a coaching change
  scheme_fit_stale: boolean;
  scheme_fit_stale_reason: string | null;
}

// ── Roster Impact Ranking ─────────────────────────────────────────────────────

export interface RosterImpactItem {
  player_id: string;
  player_name: string;
  position: string;
  delta_adjEM: number;
  current_adjEM: number;
  projected_adjEM: number;
  confidence_interval: [number, number];
  expected_minutes_input: number;
  candidate_usage_role: string | null;
}

export interface RosterImpactResponse {
  school_id: number;
  season: number;
  players: RosterImpactItem[];
  total: number;
}

// ── Team Rating Projection ────────────────────────────────────────────────────

export interface TeamRatingProjectionResponse {
  player_id: string; // string, not number — see PlayerProfile.player_id comment
  school_id: number;
  season: number;
  current_adjEM: number;
  projected_adjEM: number;
  delta_adjEM: number;
  baseline_adj_o: number | null;
  baseline_adj_d: number | null;
  projected_adj_o: number | null;
  projected_adj_d: number | null;
  confidence_interval: [number, number];
  national_percentile: number;
  conference_rank: number;
  context: string;
  expected_minutes_input: number;
  candidate_usage_role: string | null;
  explanation: Record<string, unknown> | null;
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
  team_impact_fit: Record<string, number>;
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
  team_impact_fit: number; // M6 delta_adjEM → 0-100; replaces program_fit (descoped)
}

export interface RecommendationItem {
  rank: number;
  player_id: string; // string, not number — see PlayerProfile.player_id comment
  player_name: string;
  position: string;
  overall_fit: number;
  personalized_fit: number;
  components: FitComponents;
  reasoning: string;
  is_portal_candidate: boolean;
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
