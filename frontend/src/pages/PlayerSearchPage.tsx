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

export default function PlayerSearchPage() {
  const navigate = useNavigate();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    const id = setTimeout(() => setQuery(input.trim()), 300);
    return () => clearTimeout(id);
  }, [input]);

  const { data, isLoading, error } = useQuery({
    queryKey: ['playerSearch', query],
    queryFn: () => searchPlayers(query),
    enabled: query.length >= 2,
  });

  const showResults = query.length >= 2;

  return (
    <Box maxWidth={680}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" fontWeight={700} gutterBottom>
          Player Search
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Search 4,500+ players — type at least 2 characters
        </Typography>
      </Box>

      <TextField
        fullWidth
        placeholder="Search by name…"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        autoFocus
        InputProps={{
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
                            <Typography fontWeight={600}>{player.full_name}</Typography>
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
        <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ mt: 4 }}>
          Start typing to search players
        </Typography>
      )}
    </Box>
  );
}
