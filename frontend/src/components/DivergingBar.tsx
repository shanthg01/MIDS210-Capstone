import { Box, Typography } from '@mui/material';

// Fixed per-player colors so a given player's bar color stays consistent
// across every metric row on the page (not re-picked per row).
export const DIVERGING_BAR_COLORS = ['#FF6B35', '#4A90E2', '#4CAF50', '#FF9800'];

export interface DivergingBarEntry {
  id: string;
  label: string;
  value: number;
}

const LABEL_WIDTH = 88;

/** 2 players: a single margin bar (leader minus other), not two half-bars off
 * a mean — with n=2 the mean always sits at the midpoint, so "deviation from
 * mean" carried no information beyond "who's bigger" while looking more
 * complex than it is.
 *
 * No per-row name captions on the bar itself — the table's own column
 * headers are the only place player identity is shown. entries[0] is always
 * the left table column and entries[1] the right one, so the bar's left/right
 * lean reads directly off those headers; a previous version put its own
 * "Wilkins"/"Brankovic" captions at the bar's ends with a fixed left/right
 * assignment that didn't track the actual column order, which is what made
 * the lean look backwards relative to the headers above it. */
function TwoPlayerMarginBar({ entries }: { entries: DivergingBarEntry[] }) {
  const [left, right] = entries;
  const diff = right.value - left.value;
  const margin = Math.abs(diff);
  const halfWidthPct = Math.min(50, (margin / 100) * 50);
  const leansRight = diff >= 0;
  const leader = leansRight ? right : left;
  const color = DIVERGING_BAR_COLORS[leansRight ? 1 : 0];

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
      <Box sx={{ position: 'relative', flex: 1, height: 14, minWidth: 80 }}>
        <Box
          sx={{
            position: 'absolute', left: '50%', top: 0, bottom: 0, width: '1px',
            bgcolor: 'divider', transform: 'translateX(-0.5px)',
          }}
        />
        {margin > 0 && (
          <Box
            sx={{
              position: 'absolute',
              top: 2,
              bottom: 2,
              left: leansRight ? '50%' : `${50 - halfWidthPct}%`,
              width: `${halfWidthPct}%`,
              bgcolor: color,
              borderRadius: 0.5,
              transition: 'width 0.2s, left 0.2s',
            }}
          />
        )}
      </Box>
      <Typography variant="caption" noWrap sx={{ fontWeight: 700, width: 80, flexShrink: 0, textAlign: 'right' }}>
        {margin === 0 ? 'Even' : `${leader.label} +${margin.toFixed(0)}`}
      </Typography>
    </Box>
  );
}

/** 3-4 players: plain small-multiple absolute bars on a shared 0-100 scale,
 * one per player, stacked. A "diverging from center" layout doesn't
 * generalize past 2 players (there's no single natural "other side" to lean
 * away from), so this scales to N instead of forcing a mean-centered story. */
function GroupedAbsoluteBars({ entries }: { entries: DivergingBarEntry[] }) {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, width: '100%' }}>
      {entries.map((e, i) => {
        const widthPct = Math.min(100, Math.max(0, e.value));
        const color = DIVERGING_BAR_COLORS[i % DIVERGING_BAR_COLORS.length];
        return (
          <Box key={e.id} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography
              variant="caption"
              noWrap
              sx={{ width: LABEL_WIDTH, flexShrink: 0, color: 'text.secondary', textAlign: 'right' }}
            >
              {e.label}
            </Typography>
            <Box sx={{ position: 'relative', flex: 1, height: 14, minWidth: 80, bgcolor: 'action.hover', borderRadius: 0.5 }}>
              <Box
                sx={{
                  position: 'absolute',
                  top: 2,
                  bottom: 2,
                  left: 0,
                  width: `${widthPct}%`,
                  bgcolor: color,
                  borderRadius: 0.5,
                  transition: 'width 0.2s',
                }}
              />
            </Box>
            <Typography variant="caption" sx={{ fontWeight: 700, width: 30, flexShrink: 0 }}>
              {e.value.toFixed(0)}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
}

export default function DivergingBar({ entries }: { entries: DivergingBarEntry[] }) {
  if (entries.length === 0) return null;
  if (entries.length === 2) return <TwoPlayerMarginBar entries={entries} />;
  return <GroupedAbsoluteBars entries={entries} />;
}
