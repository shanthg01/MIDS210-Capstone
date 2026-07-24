import { useEffect, useState } from 'react';
import {
  Box,
  TextField,
  Typography,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Chip,
  CircularProgress,
  Alert,
  InputAdornment,
  Paper,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { searchPlayers } from '../api/players';
import { getPreferences } from '../api/users';
import { useAuth } from '../context/AuthContext';
import { STAT_LABELS } from './SettingsPage';

export default function PlayerSearchPage() {
  const navigate = useNavigate();
  const { userId } = useAuth();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    const id = setTimeout(() => setQuery(input.trim()), 300);
    return () => clearTimeout(id);
  }, [input]);

  // Saved Recruiting Filters' min_stats — the actual loop-closer: a stored
  // threshold only matters if it narrows real results, not just sits in Settings.
  const { data: prefs } = useQuery({
    queryKey: ['preferences', userId],
    queryFn: () => getPreferences(userId!),
    enabled: !!userId,
  });
  const minStats = prefs?.filters.min_stats ?? [];

  const { data, isLoading, error } = useQuery({
    queryKey: ['playerSearch', query, minStats],
    queryFn: () => searchPlayers(query, { minStats }),
    enabled: query.length >= 2,
  });

  const showResults = query.length >= 2;

  return (
    <Box sx={{ width: '100%', maxWidth: { xs: '100%', xl: 1200 } }}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 700 }} gutterBottom>
          Player Search
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Search 4,500+ players — type at least 2 characters
        </Typography>
        {minStats.length > 0 && (
          <Chip
            label={`Filtering: ${minStats.map((t) => `${STAT_LABELS[t.stat]} ≥ ${t.min_value}`).join(', ')} (from Settings)`}
            size="small"
            color="warning"
            variant="outlined"
            sx={{ mt: 1 }}
          />
        )}
      </Box>

      <TextField
        fullWidth
        placeholder="Search by name…"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        autoFocus
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon color="action" />
              </InputAdornment>
            ),
            endAdornment: isLoading ? (
              <InputAdornment position="end">
                <CircularProgress size={18} />
              </InputAdornment>
            ) : null,
          },
        }}
        sx={{ mb: 2 }}
      />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Search failed. Make sure the API server is running.
        </Alert>
      )}

      {showResults && data && (
        <Paper variant="outlined">
          {data.results.length === 0 ? (
            <Box sx={{ p: 3, textAlign: 'center' }}>
              <Typography color="text.secondary">No players found for "{query}"</Typography>
            </Box>
          ) : (
            <>
              <Box sx={{ px: 2, py: 1, borderBottom: 1, borderColor: 'divider' }}>
                <Typography variant="caption" color="text.secondary">
                  {data.total} result{data.total !== 1 ? 's' : ''}
                </Typography>
              </Box>
              <List disablePadding>
                {data.results.map((player, idx) => (
                  <ListItem
                    key={player.player_id}
                    disablePadding
                    divider={idx < data.results.length - 1}
                  >
                    <ListItemButton onClick={() => navigate(`/players/${player.player_id}`)}>
                      <ListItemText
                        primary={
                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Typography sx={{ fontWeight: 600 }}>{player.full_name}</Typography>
                            <Chip label={player.position} size="small" />
                            <Chip label={player.class_year} size="small" variant="outlined" />
                          </Box>
                        }
                        secondary={`${player.current_school}${player.hometown ? ` · ${player.hometown}` : ''}`}
                      />
                    </ListItemButton>
                  </ListItem>
                ))}
              </List>
            </>
          )}
        </Paper>
      )}

      {!showResults && (
        <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 4 }}>
          Start typing to search players
        </Typography>
      )}
    </Box>
  );
}
