import { Box, Typography } from '@mui/material';

// Fixed per-player colors so a given player's bar color stays consistent
// across every metric row on the page (not re-picked per row).
export const DIVERGING_BAR_COLORS = ['#FF6B35', '#4A90E2', '#4CAF50', '#FF9800'];

export interface DivergingBarEntry {
  id: string;
  label: string;
  value: number;
}

/** One metric's worth of per-player bars, diverging from the group mean —
 * centered in the middle, each bar leaning toward whichever player scores
 * higher (issue #61: "diverging bar charts centered in the middle, leaning
 * toward whichever player is higher on each metric"). */
export default function DivergingBar({ entries }: { entries: DivergingBarEntry[] }) {
  if (entries.length === 0) return null;
  const mean = entries.reduce((s, e) => s + e.value, 0) / entries.length;
  const deviations = entries.map((e) => e.value - mean);
  const maxDev = Math.max(1, ...deviations.map((d) => Math.abs(d)));

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, width: '100%' }}>
      {entries.map((e, i) => {
        const dev = deviations[i];
        const halfWidthPct = Math.min(50, (Math.abs(dev) / maxDev) * 50);
        const isPositive = dev >= 0;
        const color = DIVERGING_BAR_COLORS[i % DIVERGING_BAR_COLORS.length];
        return (
          <Box key={e.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography
              variant="caption"
              noWrap
              sx={{ width: 88, flexShrink: 0, color: 'text.secondary', textAlign: 'right' }}
            >
              {e.label}
            </Typography>
            <Box sx={{ position: 'relative', flex: 1, height: 14, minWidth: 80 }}>
              <Box
                sx={{
                  position: 'absolute', left: '50%', top: 0, bottom: 0, width: '1px',
                  bgcolor: 'divider', transform: 'translateX(-0.5px)',
                }}
              />
              <Box
                sx={{
                  position: 'absolute',
                  top: 2,
                  bottom: 2,
                  left: isPositive ? '50%' : `${50 - halfWidthPct}%`,
                  width: `${halfWidthPct}%`,
                  bgcolor: color,
                  borderRadius: 0.5,
                  transition: 'width 0.2s, left 0.2s',
                }}
              />
            </Box>
            <Typography variant="caption" fontWeight={700} sx={{ width: 30, flexShrink: 0 }}>
              {e.value.toFixed(0)}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}
