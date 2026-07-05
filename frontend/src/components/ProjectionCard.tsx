import { Box, Chip, LinearProgress, Paper, Tooltip, Typography, Divider } from '@mui/material';
import type { PlayerProjectionResponse } from '../types/api';
import { scoreColor } from './FitScoreBar';

// Mirrors modeling/player_projection.py SKILLS (master 11-skill list) — kept in
// sync manually, same convention as SettingsPage's ARCHETYPES/FitScoreBar's LABEL_MAP.
const SKILL_LABELS: Record<string, string> = {
  shooting_3p: '3PT Shooting',
  shooting_2p_finishing: '2PT Finishing',
  free_throw_touch: 'Free Throw Touch',
  shot_creation_usage: 'Shot Creation',
  passing_creation: 'Passing / Creation',
  turnover_avoidance: 'Turnover Avoidance',
  offensive_rebounding: 'Off. Rebounding',
  defensive_rebounding: 'Def. Rebounding',
  steal_disruption: 'Steal Disruption',
  block_rim_protection: 'Rim Protection',
  foul_discipline: 'Foul Discipline',
};

const BOX_SCORE_LABELS: Record<string, string> = {
  pts_per_40: 'PTS/40',
  reb_per_40: 'REB/40',
  ast_per_40: 'AST/40',
  stl_per_40: 'STL/40',
  blk_per_40: 'BLK/40',
  tov_per_40: 'TOV/40',
};

function fmt(n: number, decimals = 1): string {
  return n.toFixed(decimals);
}

interface ValueDriver {
  feature: string;
  component: string;
  total_value_contribution: number;
}

export default function ProjectionCard({ projection }: { projection: PlayerProjectionResponse }) {
  const { value_per_100, value_ci_lower, value_ci_upper, projected_box_score, skill_percentiles, explanation } =
    projection;

  const valueDrivers = explanation?.value_drivers as
    | { top_positive?: ValueDriver[]; top_negative?: ValueDriver[] }
    | undefined;

  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
        <Box>
          <Typography variant="h6" fontWeight={700}>
            Player Projection
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Context-neutral talent value · {projection.season} season
          </Typography>
        </Box>
        <Tooltip title="Backed by a real model — Cross-Season Kalman forecast">
          <Chip label="Live" size="small" color="success" sx={{ height: 18, fontSize: '0.65rem', fontWeight: 700 }} />
        </Tooltip>
      </Box>

      <Divider sx={{ mb: 2 }} />

      {/* Headline value + CI */}
      <Box sx={{ mb: 2.5 }}>
        <Typography variant="caption" color="text.secondary">
          Value per 100 possessions
        </Typography>
        <Typography variant="h4" fontWeight={800}>
          {value_per_100 >= 0 ? '+' : ''}{fmt(value_per_100)}
        </Typography>
        {value_ci_lower !== null && value_ci_upper !== null && (
          <Typography variant="caption" color="text.secondary">
            90% CI: {fmt(value_ci_lower)} to {fmt(value_ci_upper)}
          </Typography>
        )}
      </Box>

      {/* Projected box score */}
      {projected_box_score && (
        <Box sx={{ mb: 2.5 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Projected Box Score
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(72px, 1fr))' }}>
            {Object.entries(BOX_SCORE_LABELS).map(([key, label]) =>
              projected_box_score[key] !== undefined ? (
                <Box key={key} sx={{ textAlign: 'center', p: 1 }}>
                  <Typography variant="body1" fontWeight={700}>
                    {fmt(projected_box_score[key])}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {label}
                  </Typography>
                </Box>
              ) : null,
            )}
          </Box>
        </Box>
      )}

      {/* Skill percentiles */}
      {skill_percentiles && (
        <Box sx={{ mb: valueDrivers ? 2.5 : 0 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Skill Percentiles (vs. season population)
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
            {Object.entries(skill_percentiles).map(([key, pct]) => (
              <Box key={key}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">
                    {SKILL_LABELS[key] ?? key}
                  </Typography>
                  <Typography variant="caption" fontWeight={700}>
                    {Math.round(pct)}
                  </Typography>
                </Box>
                <LinearProgress
                  variant="determinate"
                  value={pct}
                  color={scoreColor(pct)}
                  sx={{ height: 6, borderRadius: 1 }}
                />
              </Box>
            ))}
          </Box>
        </Box>
      )}

      {/* Top value drivers */}
      {valueDrivers && (valueDrivers.top_positive?.length || valueDrivers.top_negative?.length) ? (
        <Box>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Top Value Drivers
          </Typography>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {valueDrivers.top_positive?.map((d) => (
              <Chip
                key={d.feature}
                size="small"
                color="success"
                variant="outlined"
                label={`${SKILL_LABELS[d.feature.replace('skill_', '')] ?? d.feature} +${fmt(d.total_value_contribution, 2)}`}
              />
            ))}
            {valueDrivers.top_negative?.map((d) => (
              <Chip
                key={d.feature}
                size="small"
                color="error"
                variant="outlined"
                label={`${SKILL_LABELS[d.feature.replace('skill_', '')] ?? d.feature} ${fmt(d.total_value_contribution, 2)}`}
              />
            ))}
          </Box>
        </Box>
      ) : null}
    </Paper>
  );
}
