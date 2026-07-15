import { Box, Chip, LinearProgress, Tooltip, Typography } from '@mui/material';
import { DATA_STATUS, FIT_COMPONENTS, ROLE_FIT_LIVE_MODEL_VERSION } from '../constants/definitions';
import DefinitionTooltip from './DefinitionTooltip';

const LABEL_MAP: Record<string, string> = {
  gap_match: 'Gap Match',
  scheme_fit: 'Scheme Fit',
  role_fit: 'Role Fit',
  program_fit: 'Program Fit',
  team_impact_fit: 'Team Impact',
};

export function scoreColor(v: number): 'success' | 'warning' | 'error' {
  if (v >= 75) return 'success';
  if (v >= 50) return 'warning';
  return 'error';
}

// Components backed by a real model (M3 scheme-cos-v2, gap-cos-v1) for every
// row. program_fit is always a 50.0 stub until the Program Fit calculator is
// built. role_fit is NOT in this set — unlike the others, its liveness is
// per-row (see isComponentLive below), not a fixed property of the component.
export const LIVE_COMPONENTS = new Set(['scheme_fit', 'gap_match', 'team_impact_fit']);

// role_fit is real only on rows run_playing_time.py has actually synced
// (model_version === ROLE_FIT_LIVE_MODEL_VERSION); everywhere else it's still
// the 50.0 stub baked in by gap_matching.py. Every other component's liveness
// is a fixed property, so it falls back to the static LIVE_COMPONENTS check.
export function isComponentLive(component: string, modelVersion?: string): boolean {
  if (component === 'role_fit') return modelVersion === ROLE_FIT_LIVE_MODEL_VERSION;
  return LIVE_COMPONENTS.has(component);
}

export function DataStatusChip({ component, modelVersion }: { component: string; modelVersion?: string }) {
  const isLive = isComponentLive(component, modelVersion);
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
        <Typography variant="caption" fontWeight={700}>
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
