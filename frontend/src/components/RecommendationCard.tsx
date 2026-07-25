import { useState } from 'react';
import {
  Card,
  CardContent,
  Box,
  Typography,
  Chip,
  Button,
  CircularProgress,
  Alert,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import CheckIcon from '@mui/icons-material/Check';
import BarChartIcon from '@mui/icons-material/BarChart';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import type { RecommendationItem } from '../types/api';
import FitScoreBar from './FitScoreBar';
import ValueDriverChip from './ValueDriverChip';
import { addToShortlist } from '../api/users';
import { useAuth } from '../context/AuthContext';

interface Props {
  item: RecommendationItem;
}

function overallColor(v: number) {
  if (v >= 75) return 'success.main';
  if (v >= 50) return 'warning.main';
  return 'error.main';
}

export default function RecommendationCard({ item }: Props) {
  const { userId } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [added, setAdded] = useState(false);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');

  async function handleAdd() {
    if (!userId) return;
    setAdding(true);
    setAddError('');
    try {
      await addToShortlist(userId, item.player_id);
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

  const hasDrivers = item.biggest_strength || item.biggest_weakness;

  return (
    <Card variant="outlined" sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <CardContent sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
        {/* Header row */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
          <Box>
            <Typography
              variant="subtitle1"
              sx={{ fontWeight: 700, cursor: 'pointer', '&:hover': { color: 'primary.main' } }}
              onClick={() => navigate(`/players/${item.player_id}`)}
            >
              {item.player_name}
            </Typography>
            <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
              <Chip label={item.position} size="small" />
              <Chip label={`#${item.rank}`} size="small" variant="outlined" color="primary" />
              {item.is_portal_candidate && (
                <Chip label="In Portal" color="success" size="small" />
              )}
            </Box>
          </Box>

          <Box sx={{ textAlign: 'right', ml: 1, flexShrink: 0 }}>
            <Typography variant="h4" color={overallColor(item.personalized_fit)} sx={{ fontWeight: 800, lineHeight: 1 }}>
              {Math.round(item.personalized_fit)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Personalized Fit
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
              Overall {Math.round(item.overall_fit)}
            </Typography>
          </Box>
        </Box>

        {/* Biggest strength / weakness — same chips as ProjectionCard */}
        <Box sx={{ mb: 2, flexGrow: 1, minHeight: 40 }}>
          {hasDrivers ? (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
              {item.biggest_strength && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
                  <Typography variant="caption" color="text.secondary" sx={{ minWidth: 72 }}>
                    Strength
                  </Typography>
                  <ValueDriverChip driver={item.biggest_strength} polarity="positive" />
                </Box>
              )}
              {item.biggest_weakness && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
                  <Typography variant="caption" color="text.secondary" sx={{ minWidth: 72 }}>
                    Weakness
                  </Typography>
                  <ValueDriverChip driver={item.biggest_weakness} polarity="negative" />
                </Box>
              )}
            </Box>
          ) : (
            <Typography variant="body2" color="text.secondary">
              {item.reasoning}
            </Typography>
          )}
        </Box>

        {/* Component bars 2×2 grid */}
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5, mb: 2 }}>
          {Object.entries(item.components).map(([key, val]) => (
            <FitScoreBar key={key} label={key} value={val} />
          ))}
        </Box>

        {addError && (
          <Alert severity="error" sx={{ mb: 1, py: 0 }}>
            {addError}
          </Alert>
        )}

        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            size="small"
            variant={added ? 'contained' : 'outlined'}
            color={added ? 'success' : 'primary'}
            startIcon={
              adding
                ? <CircularProgress size={14} color="inherit" />
                : added
                  ? <CheckIcon />
                  : <AddIcon />
            }
            onClick={handleAdd}
            disabled={adding || added}
            sx={{ flex: 1 }}
          >
            {added ? 'In Pipeline' : 'Add to Pipeline'}
          </Button>
          <Button
            size="small"
            variant="outlined"
            startIcon={<BarChartIcon />}
            onClick={() => navigate(`/fit/${item.player_id}`)}
          >
            Fit
          </Button>
        </Box>
      </CardContent>
    </Card>
  );
}
