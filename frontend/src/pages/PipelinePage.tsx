import { useState } from 'react';
import {
  Box,
  Typography,
  Paper,
  Chip,
  Alert,
  Skeleton,
  Button,
  CircularProgress,
  Divider,
} from '@mui/material';
import PersonIcon from '@mui/icons-material/Person';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutlined';
import BarChartIcon from '@mui/icons-material/BarChart';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { getShortlist, removeFromShortlist } from '../api/users';
import { useAuth } from '../context/AuthContext';
import { scoreColor } from '../components/FitScoreBar';
import type { ShortlistItem } from '../types/api';

function sortPlayers(players: ShortlistItem[]): ShortlistItem[] {
  return [...players].sort((a, b) => {
    const af = a.overall_fit ?? -1;
    const bf = b.overall_fit ?? -1;
    if (bf !== af) return bf - af;
    return new Date(b.added_at).getTime() - new Date(a.added_at).getTime();
  });
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function PlayerRow({ player, userId }: { player: ShortlistItem; userId: number }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState('');

  async function handleRemove() {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setRemoving(true);
    setRemoveError('');
    try {
      await removeFromShortlist(userId, player.player_id);
      qc.invalidateQueries({ queryKey: ['shortlist', userId] });
    } catch {
      setRemoveError('Remove failed.');
      setConfirming(false);
    } finally {
      setRemoving(false);
    }
  }

  const color = player.overall_fit !== null ? scoreColor(player.overall_fit) : undefined;

  return (
    <Box>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          py: 2,
          px: 3,
          flexWrap: 'wrap',
        }}
      >
        {/* Name + meta */}
        <Box sx={{ flex: 1, minWidth: 200 }}>
          <Typography
            variant="body1"
            sx={{ fontWeight: 700, cursor: 'pointer', '&:hover': { color: 'primary.main' } }}
            onClick={() => navigate(`/players/${player.player_id}`)}
          >
            {player.player_name}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 0.5, flexWrap: 'wrap', alignItems: 'center' }}>
            <Chip label={player.position} size="small" />
            <Typography variant="caption" color="text.secondary">
              Added {fmtDate(player.added_at)}
            </Typography>
          </Box>
        </Box>

        {/* Overall fit */}
        <Box sx={{ textAlign: 'center', minWidth: 72 }}>
          {player.overall_fit !== null ? (
            <>
              <Typography
                variant="h5"
                color={`${color}.main`}
                sx={{ fontWeight: 800 }}
              >
                {player.overall_fit.toFixed(0)}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Overall Fit
              </Typography>
            </>
          ) : (
            <Typography variant="caption" color="text.secondary">
              No score
            </Typography>
          )}
        </Box>

        {/* Actions */}
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Button
            size="small"
            variant="outlined"
            startIcon={<BarChartIcon />}
            onClick={() => navigate(`/fit/${player.player_id}`)}
          >
            View Fit
          </Button>

          {confirming ? (
            <>
              <Button
                size="small"
                color="error"
                variant="contained"
                disabled={removing}
                onClick={handleRemove}
                startIcon={removing ? <CircularProgress size={14} color="inherit" /> : undefined}
              >
                Confirm
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => setConfirming(false)}
                disabled={removing}
              >
                Cancel
              </Button>
            </>
          ) : (
            <Button
              size="small"
              color="error"
              variant="outlined"
              startIcon={<DeleteOutlineIcon />}
              onClick={handleRemove}
            >
              Remove
            </Button>
          )}
        </Box>
      </Box>

      {removeError && (
        <Alert severity="error" sx={{ mx: 3, mb: 1 }}>
          {removeError}
        </Alert>
      )}
    </Box>
  );
}

export default function PipelinePage() {
  const { userId } = useAuth();
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery({
    queryKey: ['shortlist', userId],
    queryFn: () => getShortlist(userId!),
    enabled: !!userId,
  });

  if (isLoading) {
    return (
      <Box>
        <Skeleton width={200} height={36} sx={{ mb: 1 }} />
        <Skeleton width={280} height={20} sx={{ mb: 3 }} />
        <Paper variant="outlined">
          {[...Array(4)].map((_, i) => (
            <Box key={i} sx={{ px: 3, py: 2, borderBottom: i < 3 ? 1 : 0, borderColor: 'divider' }}>
              <Skeleton width={180} height={24} />
              <Skeleton width={120} height={16} sx={{ mt: 0.5 }} />
            </Box>
          ))}
        </Paper>
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error">Failed to load pipeline. Check API server.</Alert>;
  }

  const players = sortPlayers(data?.players ?? []);

  return (
    <Box sx={{ maxWidth: 800 }}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 700 }} gutterBottom>
          Recruiting Pipeline
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {players.length > 0
            ? `${players.length} player${players.length !== 1 ? 's' : ''} shortlisted — sorted by fit score`
            : 'Players you shortlist appear here'}
        </Typography>
      </Box>

      {players.length === 0 ? (
        <Paper
          variant="outlined"
          sx={{ p: 6, textAlign: 'center' }}
        >
          <PersonIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 2 }} />
          <Typography variant="h6" color="text.secondary" gutterBottom>
            Pipeline is empty
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Add players from recommendations or search
          </Typography>
          <Box sx={{ display: 'flex', gap: 2, justifyContent: 'center' }}>
            <Button variant="contained" onClick={() => navigate('/dashboard')}>
              View Recommendations
            </Button>
            <Button variant="outlined" onClick={() => navigate('/players/search')}>
              Search Players
            </Button>
          </Box>
        </Paper>
      ) : (
        <Paper variant="outlined">
          {players.map((player, idx) => (
            <Box key={player.player_id}>
              <PlayerRow player={player} userId={userId!} />
              {idx < players.length - 1 && <Divider />}
            </Box>
          ))}
        </Paper>
      )}
    </Box>
  );
}
