import { Box, LinearProgress, Typography } from '@mui/material';

const LABEL_MAP: Record<string, string> = {
  gap_match: 'Gap Match',
  scheme_fit: 'Scheme Fit',
  role_fit: 'Role Fit',
  program_fit: 'Program Fit',
};

function scoreColor(v: number): 'success' | 'warning' | 'error' {
  if (v >= 75) return 'success';
  if (v >= 50) return 'warning';
  return 'error';
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
