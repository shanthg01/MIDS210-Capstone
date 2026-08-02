import { Box, Chip, LinearProgress, Tooltip, Typography } from '@mui/material';
import { DATA_STATUS, FIT_COMPONENTS, ROLE_FIT_LIVE_MODEL_VERSION } from '../constants/definitions';
import DefinitionTooltip from './DefinitionTooltip';

const LABEL_MAP: Record<string, string> = {
  gap_match: 'Roster Fit',
  scheme_fit: 'System Fit',
  role_fit: 'Role Fit',
  program_fit: 'Program Fit',    // real per-user grade once program_fit_user_inputs exists for this pair
  team_impact_fit: 'Team Rating', // RecommendationCard — M6 delta_adjEM normalized
};

export function scoreColor(v: number): 'success' | 'warning' | 'error' {
  if (v >= 75) return 'success';
  if (v >= 50) return 'warning';
  return 'error';
}

// Components backed by a real model for every row: scheme_fit (scheme-cos-v3),
// gap_match (gap-cos-v4), team_impact_fit (M6 delta_adjEM normalized).
// role_fit and program_fit are NOT in this set — unlike the others, their
// liveness is per-row/per-user (see isComponentLive below), not a fixed
// property of the component.
export const LIVE_COMPONENTS = new Set(['scheme_fit', 'gap_match', 'team_impact_fit']);

// role_fit is real only on rows run_playing_time.py has actually synced
// (model_version === ROLE_FIT_LIVE_MODEL_VERSION); everywhere else it's still
// the 50.0 stub baked in by gap_matching.py. program_fit is real only once
// this user has entered their own qualitative grade (program_fit_user_inputs,
// 2026-07-21 design decision) — per-user, never a property of the row itself.
// Every other component's liveness is a fixed property, so it falls back to
// the static LIVE_COMPONENTS check.
export function isComponentLive(component: string, modelVersion?: string, isGraded?: boolean): boolean {
  if (component === 'role_fit') return modelVersion === ROLE_FIT_LIVE_MODEL_VERSION;
  if (component === 'program_fit') return !!isGraded;
  return LIVE_COMPONENTS.has(component);
}

export function DataStatusChip({
  component,
  modelVersion,
  isGraded,
}: {
  component: string;
  modelVersion?: string;
  isGraded?: boolean;
}) {
  const isLive = isComponentLive(component, modelVersion, isGraded);
  return (
    <Tooltip
      title={isLive ? DATA_STATUS.live.short : DATA_STATUS.placeholder.short}
    >
      <Chip
        label={isLive ? 'Live' : 'Placeholder'}
        size="small"
        color={isLive ? 'success' : 'default'}
        variant={isLive ? 'filled' : 'outlined'}
        sx={{ height: 18, fontSize: '0.65rem', fontWeight: 700 }}
      />
    </Tooltip>
  );
}

interface Props {
  label: string;
  value: number;
}

export default function FitScoreBar({ label, value }: Props) {
  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.25 }}>
        <DefinitionTooltip title={FIT_COMPONENTS[label as keyof typeof FIT_COMPONENTS]?.short ?? ''} placement="top">
          <Typography variant="caption" color="text.secondary">
            {LABEL_MAP[label] ?? label}
          </Typography>
        </DefinitionTooltip>
        <Typography variant="caption" sx={{ fontWeight: 700 }}>
          {Math.round(value)}
        </Typography>
      </Box>
      <LinearProgress
        variant="determinate"
        value={value}
        color={scoreColor(value)}
        sx={{ height: 6, borderRadius: 1 }}
      />
    </Box>
  );
}
