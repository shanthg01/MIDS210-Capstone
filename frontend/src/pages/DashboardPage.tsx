import { useMemo, useState } from 'react';
import {
  Box,
  Typography,
  Skeleton,
  Alert,
  ToggleButtonGroup,
  ToggleButton,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Paper,
  Chip,
  Button,
} from '@mui/material';
import GridViewIcon from '@mui/icons-material/GridView';
import TableRowsIcon from '@mui/icons-material/TableRows';
import BarChartIcon from '@mui/icons-material/BarChart';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { getRecommendations } from '../api/recommendations';
import { useAuth } from '../context/AuthContext';
import RecommendationCard from '../components/RecommendationCard';
import { scoreColor } from '../components/FitScoreBar';
import type { RecommendationItem } from '../types/api';

type ViewMode = 'cards' | 'table';
type SortKey = 'rank' | 'overall_fit' | 'value_per_100' | 'projected_minutes';

const SORT_LABELS: Record<SortKey, string> = {
  rank: 'Rank',
  overall_fit: 'Overall Fit',
  value_per_100: 'Value / 100',
  projected_minutes: 'Projected MPG',
};

function sortValue(item: RecommendationItem, key: SortKey): number {
  if (key === 'rank') return item.rank;
  if (key === 'overall_fit') return item.overall_fit;
  const v = item[key];
  // Nulls (no destination projection row yet) sort last regardless of direction.
  return v === null ? -Infinity : v;
}

export default function DashboardPage() {
  const { userId } = useAuth();
  const navigate = useNavigate();
  const [view, setView] = useState<ViewMode>('cards');
  const [positionFilter, setPositionFilter] = useState<string>('all');
  const [sortKey, setSortKey] = useState<SortKey>('rank');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const { data, isLoading, error } = useQuery({
    queryKey: ['recommendations', userId],
    queryFn: () => getRecommendations(userId!),
    enabled: !!userId,
  });

  const positions = useMemo(() => {
    const set = new Set((data?.recommendations ?? []).map((r) => r.position));
    return ['all', ...Array.from(set).sort()];
  }, [data]);

  const rows = useMemo(() => {
    let items = data?.recommendations ?? [];
    if (positionFilter !== 'all') {
      items = items.filter((r) => r.position === positionFilter);
    }
    const sorted = [...items].sort((a, b) => {
      const diff = sortValue(a, sortKey) - sortValue(b, sortKey);
      return sortDir === 'asc' ? diff : -diff;
    });
    return sorted;
  }, [data, positionFilter, sortKey, sortDir]);

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'rank' ? 'asc' : 'desc');
    }
  }

  return (
    <Box>
      <Box sx={{ mb: 3, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h5" fontWeight={700}>
            Portal Recommendations
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Top matches ranked by composite fit score
            {data && ` · ${data.total} players`}
          </Typography>
        </Box>

        <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
          <FormControl size="small" sx={{ minWidth: 110 }}>
            <InputLabel id="position-filter-label">Position</InputLabel>
            <Select
              labelId="position-filter-label"
              label="Position"
              value={positionFilter}
              onChange={(e) => setPositionFilter(e.target.value)}
            >
              {positions.map((p) => (
                <MenuItem key={p} value={p}>{p === 'all' ? 'All positions' : p}</MenuItem>
              ))}
            </Select>
          </FormControl>

          <ToggleButtonGroup
            size="small"
            value={view}
            exclusive
            onChange={(_, v) => v && setView(v)}
          >
            <ToggleButton value="cards"><GridViewIcon fontSize="small" /></ToggleButton>
            <ToggleButton value="table"><TableRowsIcon fontSize="small" /></ToggleButton>
          </ToggleButtonGroup>
        </Box>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Failed to load recommendations. Make sure the API server is running.
        </Alert>
      )}

      {isLoading ? (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: '1fr 1fr 1fr' },
            gap: 2,
          }}
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} variant="rectangular" height={300} sx={{ borderRadius: 1 }} />
          ))}
        </Box>
      ) : view === 'cards' ? (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: '1fr 1fr 1fr' },
            gap: 2,
          }}
        >
          {rows.map((item) => (
            <RecommendationCard key={item.player_id} item={item} />
          ))}
        </Box>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sortDirection={sortKey === 'rank' ? sortDir : false}>
                  <TableSortLabel
                    active={sortKey === 'rank'}
                    direction={sortKey === 'rank' ? sortDir : 'asc'}
                    onClick={() => handleSort('rank')}
                  >
                    {SORT_LABELS.rank}
                  </TableSortLabel>
                </TableCell>
                <TableCell>Player</TableCell>
                <TableCell>Position</TableCell>
                <TableCell sortDirection={sortKey === 'overall_fit' ? sortDir : false}>
                  <TableSortLabel
                    active={sortKey === 'overall_fit'}
                    direction={sortKey === 'overall_fit' ? sortDir : 'asc'}
                    onClick={() => handleSort('overall_fit')}
                  >
                    {SORT_LABELS.overall_fit}
                  </TableSortLabel>
                </TableCell>
                <TableCell>Scheme</TableCell>
                <TableCell>Gap</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>Team Impact</TableCell>
                <TableCell sortDirection={sortKey === 'value_per_100' ? sortDir : false}>
                  <TableSortLabel
                    active={sortKey === 'value_per_100'}
                    direction={sortKey === 'value_per_100' ? sortDir : 'asc'}
                    onClick={() => handleSort('value_per_100')}
                  >
                    {SORT_LABELS.value_per_100}
                  </TableSortLabel>
                </TableCell>
                <TableCell sortDirection={sortKey === 'projected_minutes' ? sortDir : false}>
                  <TableSortLabel
                    active={sortKey === 'projected_minutes'}
                    direction={sortKey === 'projected_minutes' ? sortDir : 'asc'}
                    onClick={() => handleSort('projected_minutes')}
                  >
                    {SORT_LABELS.projected_minutes}
                  </TableSortLabel>
                </TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((item) => (
                <TableRow key={item.player_id} hover>
                  <TableCell>#{item.rank}</TableCell>
                  <TableCell>
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      sx={{ cursor: 'pointer', '&:hover': { color: 'primary.main' } }}
                      onClick={() => navigate(`/players/${item.player_id}`)}
                    >
                      {item.player_name}
                    </Typography>
                  </TableCell>
                  <TableCell><Chip label={item.position} size="small" /></TableCell>
                  <TableCell>
                    <Typography fontWeight={700} color={`${scoreColor(item.overall_fit)}.main`}>
                      {Math.round(item.overall_fit)}
                    </Typography>
                  </TableCell>
                  <TableCell>{Math.round(item.components.scheme_fit)}</TableCell>
                  <TableCell>{Math.round(item.components.gap_match)}</TableCell>
                  <TableCell>{Math.round(item.components.role_fit)}</TableCell>
                  <TableCell>{Math.round(item.components.team_impact_fit)}</TableCell>
                  <TableCell>
                    {item.value_per_100 !== null
                      ? `${item.value_per_100 >= 0 ? '+' : ''}${item.value_per_100.toFixed(1)}`
                      : '—'}
                  </TableCell>
                  <TableCell>
                    {item.projected_minutes !== null ? item.projected_minutes.toFixed(1) : '—'}
                  </TableCell>
                  <TableCell align="right">
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<BarChartIcon fontSize="small" />}
                      onClick={() => navigate(`/fit/${item.player_id}`)}
                    >
                      Fit
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
