import {
  Box,
  Typography,
  Paper,
  Chip,
  Alert,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  TextField,
  MenuItem,
  Tooltip,
} from '@mui/material';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import BarChartIcon from '@mui/icons-material/BarChart';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getTopRosterImpact } from '../api/fitScores';
import type { RosterImpactItem } from '../types/api';

const POSITIONS = ['All', 'PG', 'SG', 'SF', 'PF', 'C'];

function fmtAdjEM(n: number): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
}

function DeltaChip({ delta }: { delta: number }) {
  const pos = delta >= 0;
  return (
    <Chip
      label={fmtAdjEM(delta)}
      size="small"
      color={pos ? 'success' : 'error'}
      icon={pos ? <TrendingUpIcon style={{ fontSize: 12 }} /> : undefined}
    />
  );
}

function ImpactRow({ item, rank }: { item: RosterImpactItem; rank: number }) {
  const navigate = useNavigate();
  return (
    <TableRow
      hover
      sx={{ cursor: 'pointer' }}
      onClick={() => navigate(`/players/${item.player_id}`)}
    >
      <TableCell sx={{ color: 'text.secondary', width: 40 }}>{rank}</TableCell>
      <TableCell>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {item.player_name}
        </Typography>
      </TableCell>
      <TableCell>
        <Chip label={item.position || '—'} size="small" color="primary" />
      </TableCell>
      <TableCell>
        <DeltaChip delta={item.delta_adjEM} />
      </TableCell>
      <TableCell sx={{ color: 'text.secondary' }}>
        <Tooltip title={`${fmtAdjEM(item.confidence_interval[0])} to ${fmtAdjEM(item.confidence_interval[1])}`}>
          <span>{fmtAdjEM(item.current_adjEM)} → {fmtAdjEM(item.projected_adjEM)}</span>
        </Tooltip>
      </TableCell>
      <TableCell sx={{ color: 'text.secondary' }}>
        {item.expected_minutes_input.toFixed(1)} MPG
        {item.candidate_usage_role && (
          <Chip
            label={item.candidate_usage_role}
            size="small"
            variant="outlined"
            sx={{ ml: 0.75, height: 16, fontSize: '0.6rem' }}
          />
        )}
      </TableCell>
      <TableCell onClick={(e) => e.stopPropagation()}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<BarChartIcon />}
          onClick={() => navigate(`/fit/${item.player_id}`)}
          sx={{ whiteSpace: 'nowrap' }}
        >
          Fit
        </Button>
      </TableCell>
    </TableRow>
  );
}

export default function RosterImpactPage() {
  const [posFilter, setPosFilter] = useState('All');
  const [minDelta, setMinDelta] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['rosterImpact'],
    queryFn: () => getTopRosterImpact(2027, 50),
  });

  const players = (data?.players ?? []).filter((p) => {
    if (posFilter !== 'All' && p.position !== posFilter) return false;
    const threshold = parseFloat(minDelta);
    if (!isNaN(threshold) && p.delta_adjEM < threshold) return false;
    return true;
  });

  return (
    <Box>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          Roster Impact Rankings
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Portal candidates ranked by projected AdjEM delta — who moves the needle most for your program
          {data && ` · ${players.length} of ${data.total} players`}
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load roster impact data. Make sure the API server is running and team rating projections have been generated.
        </Alert>
      )}

      {data?.total === 0 && !error && (
        <Alert severity="info" sx={{ mb: 2 }}>
          No team rating projections found for your school yet. Run{' '}
          <code>scripts/run_team_rating_projection.py</code> to generate them.
        </Alert>
      )}

      {/* Filters */}
      <Paper variant="outlined" sx={{ p: 2, mb: 2, display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center' }}>
        <TextField
          select
          label="Position"
          value={posFilter}
          onChange={(e) => setPosFilter(e.target.value)}
          size="small"
          sx={{ minWidth: 110 }}
        >
          {POSITIONS.map((p) => (
            <MenuItem key={p} value={p}>{p}</MenuItem>
          ))}
        </TextField>
        <TextField
          label="Min ΔAdjEM"
          value={minDelta}
          onChange={(e) => setMinDelta(e.target.value)}
          size="small"
          placeholder="e.g. 0.5"
          sx={{ width: 130 }}
          slotProps={{ htmlInput: { inputMode: 'decimal' } }}
        />
        {(posFilter !== 'All' || minDelta !== '') && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => { setPosFilter('All'); setMinDelta(''); }}
          >
            Clear
          </Button>
        )}
      </Paper>

      <TableContainer component={Paper} variant="outlined">
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>#</TableCell>
              <TableCell>Player</TableCell>
              <TableCell>Pos</TableCell>
              <TableCell>ΔAdjEM</TableCell>
              <TableCell>AdjEM (Current → Projected)</TableCell>
              <TableCell>Role</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading
              ? Array.from({ length: 10 }).map((_, i) => (
                  <TableRow key={i}>
                    {Array.from({ length: 7 }).map((__, j) => (
                      <TableCell key={j}>
                        <Skeleton variant="text" width={j === 1 ? 120 : 60} />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : players.map((item, i) => (
                  <ImpactRow key={item.player_id} item={item} rank={i + 1} />
                ))}
          </TableBody>
        </Table>
      </TableContainer>

      {!isLoading && players.length === 0 && data && data.total > 0 && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2, textAlign: 'center' }}>
          No players match the current filters.
        </Typography>
      )}
    </Box>
  );
}
