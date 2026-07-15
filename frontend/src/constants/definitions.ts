// Single source of truth for hover-tooltip and glossary copy. Display labels
// stay in each file's own local label map (FitScoreBar's LABEL_MAP, ProjectionCard's
// SKILL_LABELS/BOX_SCORE_LABELS, FitScorePage's GAP_FEATURE_LABELS) — this file only
// centralizes the descriptions, keyed the same way, so Glossary/tooltips never drift.

export interface Definition {
  label: string;
  short: string;
}

export const FIT_COMPONENTS: Record<
  'gap_match' | 'scheme_fit' | 'role_fit' | 'program_fit' | 'team_impact_fit',
  Definition
> = {
  gap_match: {
    label: 'Gap Match',
    short: 'How well this player fills a statistical hole on your current roster (e.g. rebounding, shot creation) at their position.',
  },
  scheme_fit: {
    label: 'Scheme Fit',
    short: "How closely this player's tendencies (pace, 3PT rate, usage, rim attacks, ball movement) match your program's system.",
  },
  role_fit: {
    label: 'Role Fit',
    short: 'Projected minutes, starter probability, and depth-chart position if this player joined your roster.',
  },
  program_fit: {
    label: 'Program Fit',
    short: 'Off-court alignment — NIL budget, geography, academics, and culture.',
  },
  team_impact_fit: {
    label: 'Team Impact',
    short: "Projected effect on your program's overall rating (AdjEM) if this player joins, from the Team Rating Projection model.",
  },
};

export const OVERALL_FIT: Definition = {
  label: 'Overall Fit',
  short: 'A weighted blend of Gap Match, Scheme Fit, Role Fit, and Program Fit, using your Settings weights.',
};

export const SKILLS: Record<string, Definition> = {
  shooting_3p: { label: '3PT Shooting', short: 'Percentile for 3-point shooting ability vs. the season population.' },
  shooting_2p_finishing: { label: '2PT Finishing', short: 'Percentile for finishing at the rim and mid-range vs. the season population.' },
  free_throw_touch: { label: 'Free Throw Touch', short: 'Percentile for free-throw shooting touch vs. the season population.' },
  shot_creation_usage: { label: 'Shot Creation', short: 'Percentile for self-created shot volume/difficulty vs. the season population.' },
  passing_creation: { label: 'Passing / Creation', short: 'Percentile for playmaking and shot creation for others vs. the season population.' },
  turnover_avoidance: { label: 'Turnover Avoidance', short: 'Percentile for protecting the ball vs. the season population.' },
  offensive_rebounding: { label: 'Off. Rebounding', short: 'Percentile for offensive rebounding rate vs. the season population.' },
  defensive_rebounding: { label: 'Def. Rebounding', short: 'Percentile for defensive rebounding rate vs. the season population.' },
  steal_disruption: { label: 'Steal Disruption', short: 'Percentile for creating steals/deflections vs. the season population.' },
  block_rim_protection: { label: 'Rim Protection', short: 'Percentile for shot-blocking and rim deterrence vs. the season population.' },
  foul_discipline: { label: 'Foul Discipline', short: 'Percentile for avoiding fouls vs. the season population.' },
};

export const BOX_SCORE: Record<string, Definition> = {
  pts_per_40: { label: 'PTS/40', short: 'Projected points per 40 minutes.' },
  reb_per_40: { label: 'REB/40', short: 'Projected rebounds per 40 minutes.' },
  ast_per_40: { label: 'AST/40', short: 'Projected assists per 40 minutes.' },
  stl_per_40: { label: 'STL/40', short: 'Projected steals per 40 minutes.' },
  blk_per_40: { label: 'BLK/40', short: 'Projected blocks per 40 minutes.' },
  tov_per_40: { label: 'TOV/40', short: 'Projected turnovers per 40 minutes.' },
};

export const GAP_FEATURES: Record<string, Definition> = {
  usage_rate: { label: 'Usage Rate', short: 'Share of team possessions used while on the floor.' },
  true_shooting_pct: { label: 'True Shooting %', short: 'Shooting efficiency accounting for 2s, 3s, and free throws.' },
  assist_rate: { label: 'Assist Rate', short: 'Share of teammate field goals this player assisted.' },
  tov_pct_inverse: { label: 'Turnover Avoidance', short: 'Inverse of turnover rate — higher is better ball security.' },
  off_reb_pct: { label: 'Off. Rebound %', short: 'Share of available offensive rebounds grabbed.' },
  def_reb_pct: { label: 'Def. Rebound %', short: 'Share of available defensive rebounds grabbed.' },
  block_pct: { label: 'Block %', short: 'Share of opponent 2-point attempts blocked.' },
  steal_pct: { label: 'Steal %', short: 'Share of opponent possessions ending in a steal by this player.' },
  free_throw_rate: { label: 'Free Throw Rate', short: 'Free throw attempts relative to field goal attempts.' },
  three_point_rate: { label: '3PT Rate', short: 'Share of field goal attempts from 3-point range.' },
  rim_rate: { label: 'Rim Rate', short: 'Share of field goal attempts at the rim.' },
  mid_range_rate: { label: 'Mid-Range Rate', short: 'Share of field goal attempts from mid-range.' },
  fg3_pct: { label: '3PT %', short: '3-point field goal percentage.' },
  rim_pct: { label: 'Rim %', short: 'Field goal percentage at the rim.' },
};

export const SUB_METRICS: Record<string, Definition> = {
  three_point_match: { label: '3-Point Match', short: "How closely this player's 3PT shot rate matches your program's system." },
  pace_match: { label: 'Pace Match', short: "How closely this player's playing pace matches your program's system." },
  usage_match: { label: 'Usage Match', short: "How closely this player's usage rate matches the role your system asks of that position." },
  rim_attack_match: { label: 'Rim Attack', short: "How closely this player's rim-attack rate matches your program's system." },
  ball_movement_match: { label: 'Ball Movement', short: "How closely this player's passing/assist behavior matches your program's ball-movement style." },
  nil_score: { label: 'NIL Score', short: "Fit between this player's NIL expectations and your program's NIL market." },
  geographic_score: { label: 'Geographic Fit', short: "How close this player's home/current location is to your program, a factor in transfer likelihood." },
  academic_score: { label: 'Academic Fit', short: "Alignment between this player's academic profile and your program." },
  cultural_score: { label: 'Cultural Fit', short: "Estimated alignment with your program's culture and identity." },
  nil_budget_alignment: { label: 'NIL Budget Alignment', short: "Whether this player's NIL price point fits your program's NIL budget." },
  depth_chart_position: { label: 'Depth Chart Position', short: 'Lower number = higher on the depth chart at this position.' },
  gap_reliability: {
    label: 'Gap Confidence',
    short: "Confidence in the gap score itself — blends how reliable this player's position assignment, sample size, and stat features are.",
  },
  archetype_needed: { label: 'Archetype Needed', short: 'Whether this player matches a playing-style archetype your roster is missing.' },
  position_depth_score: { label: 'Position Depth Score', short: 'How thin your roster currently is at this position (higher = more open opportunity).' },
  projected_minutes: { label: 'Projected MPG', short: "Projected minutes per game for this player in your program's rotation." },
  starter_probability: { label: 'Starter Probability', short: 'Modeled probability this player starts games in your program.' },
};

export const VALUE_PER_100: Definition = {
  label: 'Value per 100 possessions',
  short: "This player's context-neutral talent value, independent of any specific program's scheme or roster — not one of the 4 fit components.",
};

export const CONFIDENCE_INTERVAL: Definition = {
  label: 'Confidence Interval',
  short: 'The range this estimate is expected to fall within, given modeling uncertainty.',
};

export const DATA_STATUS = {
  live: {
    label: 'Live',
    short: 'Backed by a real model — scores reflect actual data.',
  } as Definition,
  placeholder: {
    label: 'Placeholder',
    short: 'Placeholder — model not built yet, value is a fixed stub.',
  } as Definition,
};
