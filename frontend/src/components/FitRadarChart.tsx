import { Box, Typography } from '@mui/material';
import { scoreColor, LIVE_COMPONENTS } from './FitScoreBar';

const SCORE_HEX: Record<'success' | 'warning' | 'error', string> = {
  success: '#4caf50',
  warning: '#ff9800',
  error: '#f44336',
};

interface RadarDatum {
  label: string;
  value: number; // 0-100
  component: string;
}

interface Props {
  data: RadarDatum[];
  size?: number;
}

// Hand-rolled SVG radar — no D3 needed for a static N-axis polygon, avoids
// pulling in a new dependency for something this small.
export default function FitRadarChart({ data, size = 240 }: Props) {
  const n = data.length;
  const center = size / 2;
  const radius = size * 0.34;
  const labelRadius = radius + 28;

  const angleFor = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const pointFor = (i: number, r: number) => {
    const a = angleFor(i);
    return [center + r * Math.cos(a), center + r * Math.sin(a)] as const;
  };

  const ringLevels = [25, 50, 75, 100];
  const scorePoints = data.map((d, i) => pointFor(i, (Math.max(0, Math.min(100, d.value)) / 100) * radius));
  const scorePath = scorePoints.map((p) => p.join(',')).join(' ');

  // Wider side margin than a middle-anchored label would need — labels now
  // anchor outward (start/end, not middle), so the full label width extends
  // to one side of its axis point, not split evenly across both.
  const margin = 70;

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center' }}>
      <svg width={size + margin * 2} height={size + 16} viewBox={`0 0 ${size + margin * 2} ${size + 16}`}>
        <g transform={`translate(${margin}, 8)`}>
          {/* Grid rings */}
          {ringLevels.map((level) => {
            const pts = data.map((_, i) => pointFor(i, (level / 100) * radius).join(',')).join(' ');
            return (
              <polygon
                key={level}
                points={pts}
                fill="none"
                stroke="#e0e0e0"
                strokeWidth={1}
              />
            );
          })}

          {/* Axis lines */}
          {data.map((_, i) => {
            const [x, y] = pointFor(i, radius);
            return <line key={i} x1={center} y1={center} x2={x} y2={y} stroke="#e0e0e0" strokeWidth={1} />;
          })}

          {/* Score polygon */}
          <polygon points={scorePath} fill="rgba(25, 118, 210, 0.15)" stroke="#1976d2" strokeWidth={2} />

          {/* Vertex markers — filled = live model, open = placeholder stub */}
          {data.map((d, i) => {
            const [x, y] = scorePoints[i];
            const hex = SCORE_HEX[scoreColor(d.value)];
            const isLive = LIVE_COMPONENTS.has(d.component);
            return (
              <circle
                key={i}
                cx={x}
                cy={y}
                r={5}
                fill={isLive ? hex : '#fff'}
                stroke={hex}
                strokeWidth={2}
              />
            );
          })}

          {/* Axis labels — anchor points outward (away from center) instead of
              centering on the axis point, otherwise longer labels like
              long component labels overlap back into the chart on the left/right axes. */}
          {data.map((d, i) => {
            const [x, y] = pointFor(i, labelRadius);
            const dx = x - center;
            const textAnchor = dx > 5 ? 'start' : dx < -5 ? 'end' : 'middle';
            return (
              <text
                key={i}
                x={x}
                y={y}
                textAnchor={textAnchor}
                dominantBaseline="middle"
                fontSize={12}
                fontWeight={600}
                fill="#616161"
              >
                {d.label}
              </text>
            );
          })}
        </g>
      </svg>
    </Box>
  );
}

export function RadarLegend() {
  return (
    <Box sx={{ display: 'flex', gap: 2, justifyContent: 'center', mt: 0.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: 'success.main' }} />
        <Typography variant="caption" color="text.secondary">Live model</Typography>
      </Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
        <Box
          sx={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            bgcolor: 'common.white',
            border: '2px solid',
            borderColor: 'success.main',
          }}
        />
        <Typography variant="caption" color="text.secondary">Placeholder stub</Typography>
      </Box>
    </Box>
  );
}
