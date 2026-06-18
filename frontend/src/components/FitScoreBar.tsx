import { Box, Chip, LinearProgress, Tooltip, Typography } from '@mui/material';

const LABEL_MAP: Record<string, string> = {
  gap_match: 'Gap Match',
  scheme_fit: 'Scheme Fit',
  role_fit: 'Role Fit',
  program_fit: 'Program Fit',
};

export function scoreColor(v: number): 'success' | 'warning' | 'error' {
  if (v >= 75) return 'success';
  if (v >= 50) return 'warning';
  return 'error';
}

// Components backed by a real model (M3 scheme-cos-v2, gap-cos-v1). Everything
// else (role_fit, program_fit) is a 50.0 stub with a seeded-random breakdown
// until Model 4 and the Program Fit calculator are built.
export const LIVE_COMPONENTS = new Set(['scheme_fit', 'gap_match']);

export function DataStatusChip({ component }: { component: string }) {
  const isLive = LIVE_COMPONENTS.has(component);
  return (
    <Tooltip
      title={
        isLive
          ? 'Backed by a real model — scores reflect actual data'
          : 'Placeholder — model not built yet, value is a fixed stub'
      }
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
        <Typography variant="caption" color="text.secondary">
          {LABEL_MAP[label] ?? label}
        </Typography>
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
