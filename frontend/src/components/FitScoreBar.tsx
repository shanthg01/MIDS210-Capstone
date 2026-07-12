import { Box, Chip, LinearProgress, Tooltip, Typography } from '@mui/material';

const LABEL_MAP: Record<string, string> = {
  gap_match: 'Gap Match',
  scheme_fit: 'Scheme Fit',
  role_fit: 'Role Fit',
  program_fit: 'Program Fit',    // FitScorePage breakdown (stubbed, descoped)
  team_impact_fit: 'Team Impact', // RecommendationCard — M6 delta_adjEM normalized
};

export function scoreColor(v: number): 'success' | 'warning' | 'error' {
  if (v >= 75) return 'success';
  if (v >= 50) return 'warning';
  return 'error';
}

// Real models: scheme_fit (scheme-cos-v3), gap_match (gap-cos-v4), role_fit
// (playing-time-rotation-v2, Issue #25). program_fit remains a 50.0 stub
// (descoped 2026-07-11 — no NIL/geography/academic proxy data yet).
export const LIVE_COMPONENTS = new Set(['scheme_fit', 'gap_match', 'role_fit']);

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
