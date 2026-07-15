import { Box, Chip, LinearProgress, Tooltip, Typography } from '@mui/material';
import { DATA_STATUS, FIT_COMPONENTS } from '../constants/definitions';
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

// Components backed by a real model (M3 scheme-cos-v2, gap-cos-v1). Everything
// else (role_fit, program_fit) is a 50.0 stub with a seeded-random breakdown
// until Model 4 and the Program Fit calculator are built.
export const LIVE_COMPONENTS = new Set(['scheme_fit', 'gap_match', 'team_impact_fit']);

export function DataStatusChip({ component }: { component: string }) {
  const isLive = LIVE_COMPONENTS.has(component);
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
