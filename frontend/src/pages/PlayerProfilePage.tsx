import { useState } from 'react';
import {
  Box,
  Typography,
  Chip,
  Skeleton,
  Alert,
  Button,
  CircularProgress,
  Divider,
  Paper,
  Breadcrumbs,
  Link,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import CheckIcon from '@mui/icons-material/Check';
import BarChartIcon from '@mui/icons-material/BarChart';
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { getPlayer, getPlayerProjection } from '../api/players';
import { addToShortlist } from '../api/users';
import { useAuth } from '../context/AuthContext';
import type { PlayerStats } from '../types/api';
import ProjectionCard from '../components/ProjectionCard';
import { buildProjectionInsight } from '../utils/fitInsights';

function StatCell({ label, value }: { label: string; value: string | number }) {
  return (
    <Box sx={{ textAlign: 'center', p: 1.5 }}>
      <Typography variant="h6" sx={{ fontWeight: 700 }}>
        {value}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
    </Box>
  );
}

function fmt(n: number | null | undefined, decimals = 1): string {
  if (n === null || n === undefined) return '—';
  return n.toFixed(decimals);
}

function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return `${(n * 100).toFixed(1)}%`;
}

function fmtBpm(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}`;
}

function StatsSection({ stats }: { stats: PlayerStats }) {
  return (
    <Box>
      {/* Traditional */}
      <Typography variant="subtitle2" color="text.secondary" gutterBottom>
        Traditional ({stats.season} · {stats.games_played} GP)
      </Typography>
      <Paper variant="outlined" sx={{ mb: 2 }}>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(72px, 1fr))' }}>
          <StatCell label="PPG" value={fmt(stats.points_per_game)} />
          <StatCell label="RPG" value={fmt(stats.rebounds_per_game)} />
          <StatCell label="APG" value={fmt(stats.assists_per_game)} />
          <StatCell label="SPG" value={fmt(stats.steals_per_game)} />
          <StatCell label="BPG" value={fmt(stats.blocks_per_game)} />
          <StatCell label="TOV" value={fmt(stats.turnovers_per_game)} />
          <StatCell label="MPG" value={fmt(stats.minutes_per_game)} />
        </Box>
      </Paper>

      {/* Advanced */}
      <Typography variant="subtitle2" color="text.secondary" gutterBottom>
        Advanced
      </Typography>
      <Paper variant="outlined" sx={{ mb: 2 }}>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(80px, 1fr))' }}>
          <StatCell label="PER" value={fmt(stats.per)} />
          <StatCell label="TS%" value={fmtPct(stats.true_shooting_pct)} />
          <StatCell label="USG%" value={`${fmt(stats.usage_rate, 1)}%`} />
          <StatCell label="AST%" value={`${fmt(stats.assist_rate, 1)}%`} />
          <StatCell label="BPM" value={fmtBpm(stats.bpm)} />
        </Box>
      </Paper>

      {/* Shot distribution */}
      <Typography variant="subtitle2" color="text.secondary" gutterBottom>
        Shot Distribution
      </Typography>
      <Paper variant="outlined">
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)' }}>
          <StatCell label="3PT Rate" value={fmtPct(stats.three_point_rate)} />
          <StatCell label="Rim Rate" value={fmtPct(stats.rim_rate)} />
          <StatCell label="Mid Rate" value={fmtPct(stats.mid_range_rate)} />
        </Box>
      </Paper>
    </Box>
  );
}

export default function PlayerProfilePage() {
  const { id } = useParams<{ id: string }>();
  // Stays a string end-to-end — player_id is a 63-bit hash; Number() would
  // silently corrupt it past JS's 53-bit safe-integer limit (the original bug).
  const playerId = id ?? '';
  const { userId } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [added, setAdded] = useState(false);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');

  const { data: player, isLoading, error } = useQuery({
    queryKey: ['player', playerId],
    queryFn: () => getPlayer(playerId),
    enabled: !!playerId,
  });

  // 404 (no projection row for this player) is expected, not retried — handled via the `error` flag below.
  const { data: projection, isLoading: projectionLoading } = useQuery({
    queryKey: ['playerProjection', playerId],
    queryFn: () => getPlayerProjection(playerId),
    enabled: !!playerId,
    retry: false,
  });

  async function handleAdd() {
    if (!userId) return;
    setAdding(true);
    setAddError('');
    try {
      await addToShortlist(userId, playerId);
      setAdded(true);
      qc.invalidateQueries({ queryKey: ['shortlist', userId] });
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 409) {
        setAdded(true);
      } else {
        setAddError('Failed to add to pipeline.');
      }
    } finally {
      setAdding(false);
    }
  }

  if (isLoading) {
    return (
      <Box>
        <Skeleton width={240} height={28} sx={{ mb: 2 }} />
        <Skeleton width={180} height={40} sx={{ mb: 1 }} />
        <Skeleton width={320} height={20} sx={{ mb: 3 }} />
        <Skeleton variant="rectangular" height={120} sx={{ mb: 2, borderRadius: 1 }} />
        <Skeleton variant="rectangular" height={100} sx={{ borderRadius: 1 }} />
      </Box>
    );
  }

  if (error || !player) {
    return (
      <Alert severity="error">
        Player not found or failed to load. <Link component={RouterLink} to="/players/search">Back to search</Link>
      </Alert>
    );
  }

  return (
    <Box sx={{ maxWidth: 720 }}>
      <Breadcrumbs sx={{ mb: 2 }}>
        <Link
          component="button"
          variant="body2"
          onClick={() => navigate('/players/search')}
          underline="hover"
          color="inherit"
        >
          Player Search
        </Link>
        <Typography variant="body2" color="text.primary">
          {player.full_name}
        </Typography>
      </Breadcrumbs>

      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 800 }}>
            {player.full_name}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Chip label={player.position} color="primary" size="small" />
            <Chip label={player.class_year.replace('_', ' ')} size="small" variant="outlined" />
            {player.is_in_portal && (
              <Chip label="In Portal" color="success" size="small" />
            )}
            {player.archetype && (
              <Chip label={player.archetype.label} size="small" variant="outlined" color="secondary" />
            )}
          </Box>
        </Box>

        <Box sx={{ display: 'flex', gap: 1, flexShrink: 0, ml: 2 }}>
          <Button
            variant="outlined"
            startIcon={<BarChartIcon />}
            onClick={() => navigate(`/fit/${playerId}`)}
          >
            View Fit
          </Button>
          <Button
            variant={added ? 'contained' : 'outlined'}
            color={added ? 'success' : 'primary'}
            startIcon={
              adding
                ? <CircularProgress size={16} color="inherit" />
                : added
                  ? <CheckIcon />
                  : <AddIcon />
            }
            onClick={handleAdd}
            disabled={adding || added}
          >
            {added ? 'In Pipeline' : 'Add to Pipeline'}
          </Button>
        </Box>
      </Box>

      {/* Meta */}
      <Box sx={{ display: 'flex', gap: 3, mb: 3, flexWrap: 'wrap' }}>
        <Box>
          <Typography variant="caption" color="text.secondary">School</Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{player.current_school}</Typography>
        </Box>
        {player.hometown && (
          <Box>
            <Typography variant="caption" color="text.secondary">Hometown</Typography>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>{player.hometown}</Typography>
          </Box>
        )}
        {player.height_inches && (
          <Box>
            <Typography variant="caption" color="text.secondary">Height</Typography>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {Math.floor(player.height_inches / 12)}'{player.height_inches % 12}"
            </Typography>
          </Box>
        )}
        {player.archetype && (
          <Box>
            <Typography variant="caption" color="text.secondary">Archetype confidence</Typography>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {(player.archetype.confidence * 100).toFixed(0)}%
            </Typography>
          </Box>
        )}
      </Box>

      <Divider sx={{ mb: 3 }} />

      {addError && <Alert severity="error" sx={{ mb: 2 }}>{addError}</Alert>}

      {projectionLoading ? (
        <Skeleton variant="rectangular" height={220} sx={{ mb: 3, borderRadius: 1 }} />
      ) : projection ? (
        <>
          {(() => {
            const insight = buildProjectionInsight(projection);
            return (
              <Alert severity="info" sx={{ mb: 2 }}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>
                  {insight.headline}
                </Typography>
                {insight.bullets.map((b) => (
                  <Typography key={b} variant="body2">
                    • {b}
                  </Typography>
                ))}
              </Alert>
            );
          })()}
          <ProjectionCard projection={projection} />
        </>
      ) : null}

      {player.current_season_stats ? (
        <StatsSection stats={player.current_season_stats} />
      ) : (
        <Alert severity="info">No season stats available for this player.</Alert>
      )}
    </Box>
  );
}
