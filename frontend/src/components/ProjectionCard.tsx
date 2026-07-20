import {
  Box,
  Chip,
  LinearProgress,
  Paper,
  Tooltip,
  Typography,
  Divider,
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import type { PlayerProjectionResponse } from '../types/api';
import { scoreColor } from './FitScoreBar';
import { BOX_SCORE, SKILLS, VALUE_PER_100, DESTINATION_VALUE_PER_100 } from '../constants/definitions';
import DefinitionTooltip from './DefinitionTooltip';

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

// Neutral mode reports pace-neutral per-40 rates; destination mode reports
// per-game numbers instead, scaled to real expected minutes at that program
// (destination_projection.py renames pts_per_40 -> pts_per_game etc. when it
// rescales) — different key set, not just a naming variant.
const BOX_SCORE_LABELS: Record<string, string> = {
  pts_per_40: 'PTS/40',
  reb_per_40: 'REB/40',
  ast_per_40: 'AST/40',
  stl_per_40: 'STL/40',
  blk_per_40: 'BLK/40',
  tov_per_40: 'TOV/40',
};

const BOX_SCORE_LABELS_PER_GAME: Record<string, string> = {
  pts_per_game: 'PTS/G',
  reb_per_game: 'REB/G',
  ast_per_game: 'AST/G',
  stl_per_game: 'STL/G',
  blk_per_game: 'BLK/G',
  tov_per_game: 'TOV/G',
};

function fmt(n: number, decimals = 1): string {
  return n.toFixed(decimals);
}

function ordinal(n: number): string {
  const rounded = Math.round(n);
  const mod100 = rounded % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${rounded}th`;
  switch (rounded % 10) {
    case 1: return `${rounded}st`;
    case 2: return `${rounded}nd`;
    case 3: return `${rounded}rd`;
    default: return `${rounded}th`;
  }
}

interface ValueDriver {
  feature: string;
  component: string;
  total_value_contribution: number;
}

export default function ProjectionCard({ projection }: { projection: PlayerProjectionResponse }) {
  const {
    value_per_100, value_ci_lower, value_ci_upper, projected_box_score,
    skill_states, skill_percentiles, explanation,
  } = projection;
  const isDestination = projection.projection_mode === 'destination';

  const valueDrivers = explanation?.value_drivers as
    | { top_positive?: ValueDriver[]; top_negative?: ValueDriver[] }
    | undefined;

  return (
    <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            Player Projection
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {isDestination ? 'Adjusted for fit at this program' : 'Context-neutral talent value'} · {projection.season} season
          </Typography>
        </Box>
        <Tooltip
          title={
            isDestination
              ? 'Backed by a real model — Destination Projection (Playing Time + Cross-Season blend)'
              : 'Backed by a real model — Cross-Season Kalman forecast'
          }
        >
          <Chip label="Live" size="small" color="success" sx={{ height: 18, fontSize: '0.65rem', fontWeight: 700 }} />
        </Tooltip>
      </Box>

      <Divider sx={{ mb: 2 }} />

      {/* Headline value + CI — always visible, the "so what" of this card */}
      <Box sx={{ mb: 2.5 }}>
        <DefinitionTooltip title={isDestination ? DESTINATION_VALUE_PER_100.short : VALUE_PER_100.short} placement="bottom">
          <Typography variant="caption" color="text.secondary">
            Value per 100 possessions
          </Typography>
        </DefinitionTooltip>
        <Typography variant="h4" sx={{ fontWeight: 800 }}>
          {value_per_100 >= 0 ? '+' : ''}{fmt(value_per_100)}
        </Typography>
        {value_ci_lower !== null && value_ci_upper !== null && (
          <Typography variant="caption" color="text.secondary">
            90% CI: {fmt(value_ci_lower)} to {fmt(value_ci_upper)}
          </Typography>
        )}
      </Box>

      {/* Projected box score — key convention differs by mode (see labels maps above) */}
      {projected_box_score && (
        <Box sx={{ mb: 2.5, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Projected Box Score {isDestination ? '(per game)' : '(per 40 min)'}
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(72px, 1fr))' }}>
            {Object.entries(isDestination ? BOX_SCORE_LABELS_PER_GAME : BOX_SCORE_LABELS).map(([key, label]) =>
              projected_box_score[key] !== undefined ? (
                <Box key={key} sx={{ textAlign: 'center', p: 1 }}>
                  <Typography variant="body1" sx={{ fontWeight: 700 }}>
                    {fmt(projected_box_score[key])}
                  </Typography>
                  <DefinitionTooltip title={BOX_SCORE[key]?.short ?? ''} placement="top">
                    <Typography variant="caption" color="text.secondary">
                      {label}
                    </Typography>
                  </DefinitionTooltip>
                </Box>
              ) : null,
            )}
          </Box>
        </Box>
      )}

      {/* Skill percentiles — the longest section, collapsed behind an accordion */}
      {skill_percentiles && (
        <Accordion
          variant="outlined"
          disableGutters
          sx={{ mb: valueDrivers ? 2.5 : 0, '&:before': { display: 'none' } }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>
            <Typography variant="subtitle2" color="text.secondary">
              Skill Percentiles (vs. season population)
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
              {Object.entries(skill_percentiles).map(([key, pct]) => {
                const stateVal = skill_states?.[key];
                return (
                  <Box key={key}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
                      <DefinitionTooltip title={SKILLS[key]?.short ?? ''} placement="top">
                        <Typography variant="caption" color="text.secondary">
                          {SKILL_LABELS[key] ?? key}
                        </Typography>
                      </DefinitionTooltip>
                      <Typography variant="caption" sx={{ fontWeight: 700 }}>
                        {stateVal !== undefined ? `${fmt(stateVal, 3)} · ` : ''}
                        {ordinal(pct)}
                      </Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={pct}
                      color={scoreColor(pct)}
                      sx={{ height: 6, borderRadius: 1 }}
                    />
                  </Box>
                );
              })}
            </Box>
          </AccordionDetails>
        </Accordion>
      )}

      {/* Top value drivers */}
      {valueDrivers && (valueDrivers.top_positive?.length || valueDrivers.top_negative?.length) ? (
        <Box sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1 }}>
          <Typography variant="subtitle2" color="text.secondary" gutterBottom>
            Top Value Drivers
          </Typography>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {valueDrivers.top_positive?.map((d) => {
              const skillKey = d.feature.replace('skill_', '');
              return (
                <Tooltip key={d.feature} title={SKILLS[skillKey]?.short ?? ''}>
                  <Chip
                    size="small"
                    color="success"
                    variant="outlined"
                    label={`${SKILL_LABELS[skillKey] ?? d.feature} +${fmt(d.total_value_contribution, 2)}`}
                  />
                </Tooltip>
              );
            })}
            {valueDrivers.top_negative?.map((d) => {
              const skillKey = d.feature.replace('skill_', '');
              return (
                <Tooltip key={d.feature} title={SKILLS[skillKey]?.short ?? ''}>
                  <Chip
                    size="small"
                    color="error"
                    variant="outlined"
                    label={`${SKILL_LABELS[skillKey] ?? d.feature} ${fmt(d.total_value_contribution, 2)}`}
                  />
                </Tooltip>
              );
            })}
          </Box>
        </Box>
      ) : null}
    </Paper>
  );
}
