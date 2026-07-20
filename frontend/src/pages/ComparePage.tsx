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
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Checkbox,
  FormControlLabel,
  Divider,
} from '@mui/material';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { getShortlist } from '../api/users';
import { comparePlayers } from '../api/compare';
import { useAuth } from '../context/AuthContext';
import { scoreColor, DataStatusChip } from '../components/FitScoreBar';
import DefinitionTooltip from '../components/DefinitionTooltip';
import { FIT_COMPONENTS, OVERALL_FIT } from '../constants/definitions';
import { buildVerdict } from '../utils/compareInsights';
import type { CompareResponse, ComparisonMatrix } from '../types/api';

// ── Matrix helpers ────────────────────────────────────────────────────────────

// overall_fit has no single component key, so it gets no component status chip.
const METRICS: Array<{ key: keyof ComparisonMatrix; label: string }> = [
  { key: 'overall_fit', label: 'Overall Fit' },
  { key: 'scheme_fit', label: 'Scheme Fit' },
  { key: 'gap_match', label: 'Gap Match' },
  { key: 'role_fit', label: 'Role Fit' },
  { key: 'team_impact_fit', label: 'Team Impact' },
];

function maxNameInRow(row: Record<string, number>): string {
  return Object.entries(row).reduce(
    (best, [name, val]) => (val > row[best] ? name : best),
    Object.keys(row)[0] ?? '',
  );
}

// ── Comparison results ────────────────────────────────────────────────────────

function ComparisonResults({
  result,
  onReset,
}: {
  result: CompareResponse;
  onReset: () => void;
}) {
  const navigate = useNavigate();
  const entries = result.players;
  const verdict = buildVerdict(result);

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Typography variant="h6" fontWeight={700}>
          Comparison Matrix
        </Typography>
        <Button size="small" startIcon={<RefreshIcon />} onClick={onReset}>
          New Comparison
        </Button>
      </Box>

      {/* Verdict — plain-language summary, leads before the detailed matrix */}
      <Alert severity="info" sx={{ mb: 3 }}>
        <Typography variant="body2" fontWeight={700}>
          {verdict.headline}
        </Typography>
        {verdict.bullets.map((b) => (
          <Typography key={b} variant="body2">
            • {b}
          </Typography>
        ))}
      </Alert>

      {/* Score matrix */}
      <Paper variant="outlined" sx={{ mb: 3, overflow: 'hidden' }}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontWeight: 700, width: 140 }}>Metric</TableCell>
                {entries.map((e) => (
                  <TableCell key={e.player.player_id} align="center">
                    <Typography
                      variant="body2"
                      fontWeight={700}
                      sx={{ cursor: 'pointer', '&:hover': { color: 'primary.main' } }}
                      onClick={() => navigate(`/players/${e.player.player_id}`)}
                    >
                      {e.player.full_name}
                    </Typography>
                    <Box sx={{ display: 'flex', gap: 0.5, justifyContent: 'center', mt: 0.5 }}>
                      <Chip label={e.player.position} size="small" />
                      <Chip
                        label={e.player.class_year.replace('_', ' ')}
                        size="small"
                        variant="outlined"
                      />
                    </Box>
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {METRICS.map(({ key, label }) => {
                const row = result.comparison_matrix[key];
                const bestName = maxNameInRow(row);
                return (
                  <TableRow key={key} hover>
                    <TableCell sx={{ color: 'text.secondary', fontWeight: 500 }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                        <DefinitionTooltip
                          title={key === 'overall_fit' ? OVERALL_FIT.short : FIT_COMPONENTS[key as keyof typeof FIT_COMPONENTS]?.short ?? ''}
                        >
                          <span>{label}</span>
                        </DefinitionTooltip>
                        {key !== 'overall_fit' && <DataStatusChip component={key} />}
                      </Box>
                    </TableCell>
                    {entries.map((e) => {
                      const val = row[e.player.full_name] ?? 0;
                      const isBest = e.player.full_name === bestName;
                      const color = scoreColor(val);
                      return (
                        <TableCell key={e.player.player_id} align="center">
                          <Typography
                            variant="h6"
                            fontWeight={isBest ? 800 : 500}
                            color={isBest ? `${color}.main` : 'text.primary'}
                            sx={isBest ? { textDecoration: 'underline dotted' } : {}}
                          >
                            {val.toFixed(0)}
                          </Typography>
                        </TableCell>
                      );
                    })}
                  </TableRow>
                );
              })}

              {/* Prediction row */}
              <TableRow hover>
                <TableCell sx={{ color: 'text.secondary', fontWeight: 500 }}>
                  Predicted MPG
                </TableCell>
                {entries.map((e) => (
                  <TableCell key={e.player.player_id} align="center">
                    <Typography variant="body2" fontWeight={600}>
                      {e.prediction.predicted_minutes.toFixed(1)}
                    </Typography>
                    <Chip
                      label={e.prediction.predicted_role}
                      size="small"
                      variant="outlined"
                      sx={{ mt: 0.25 }}
                    />
                  </TableCell>
                ))}
              </TableRow>
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {/* Trade-offs */}
      <Typography variant="h6" fontWeight={700} gutterBottom>
        Trade-offs
      </Typography>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        {result.trade_offs.map((t) => (
          <Paper key={t.factor} variant="outlined" sx={{ p: 2 }}>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', mb: 0.5 }}>
              <Typography variant="subtitle2" fontWeight={700} color="primary">
                {t.factor}
              </Typography>
              <Chip label={t.best_player_name} size="small" color="success" variant="outlined" />
            </Box>
            <Typography variant="body2" color="text.secondary">
              {t.description}
            </Typography>
          </Paper>
        ))}
      </Box>
    </Box>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ComparePage() {
  const { userId } = useAuth();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data: shortlist, isLoading: loadingPipeline } = useQuery({
    queryKey: ['shortlist', userId],
    queryFn: () => getShortlist(userId!),
    enabled: !!userId,
  });

  const { mutate, data: result, isPending, error: mutError, reset } = useMutation({
    mutationFn: comparePlayers,
  });

  function togglePlayer(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < 4) {
        next.add(id);
      }
      return next;
    });
  }

  function handleCompare() {
    if (!userId || selected.size < 2) return;
    mutate({ program_id: userId, player_ids: [...selected] });
  }

  function handleReset() {
    reset();
    setSelected(new Set());
  }

  if (loadingPipeline) {
    return (
      <Box>
        <Skeleton width={200} height={36} sx={{ mb: 3 }} />
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} height={48} sx={{ mb: 1 }} />
        ))}
      </Box>
    );
  }

  // Show results if mutation succeeded
  if (result) {
    return (
      <Box maxWidth={900}>
        <Typography variant="h5" fontWeight={700} gutterBottom>
          Compare Players
        </Typography>
        <ComparisonResults result={result} onReset={handleReset} />
      </Box>
    );
  }

  const players = shortlist?.players ?? [];

  return (
    <Box maxWidth={700}>
      <Box sx={{ mb: 3 }}>
        <Typography variant="h5" fontWeight={700} gutterBottom>
          Compare Players
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Select 2–4 players from your pipeline to compare side-by-side
        </Typography>
      </Box>

      {players.length < 2 ? (
        <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
          <CompareArrowsIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 2 }} />
          <Typography variant="h6" color="text.secondary" gutterBottom>
            Need at least 2 players in your pipeline
          </Typography>
          <Box sx={{ display: 'flex', gap: 2, justifyContent: 'center', mt: 2 }}>
            <Button variant="contained" onClick={() => navigate('/pipeline')}>
              Go to Pipeline
            </Button>
            <Button variant="outlined" onClick={() => navigate('/dashboard')}>
              View Recommendations
            </Button>
          </Box>
        </Paper>
      ) : (
        <>
          <Paper variant="outlined" sx={{ mb: 3 }}>
            <Box
              sx={{
                px: 2,
                py: 1.5,
                borderBottom: 1,
                borderColor: 'divider',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <Typography variant="subtitle2" color="text.secondary">
                Your Pipeline ({players.length} players)
              </Typography>
              <Chip
                label={`${selected.size}/4 selected`}
                color={selected.size >= 2 ? 'primary' : 'default'}
                size="small"
              />
            </Box>

            {players.map((p, idx) => {
              const isSelected = selected.has(p.player_id);
              const isDisabled = !isSelected && selected.size >= 4;
              const color = p.overall_fit !== null ? scoreColor(p.overall_fit) : undefined;
              return (
                <Box key={p.player_id}>
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      px: 2,
                      py: 1,
                      opacity: isDisabled ? 0.45 : 1,
                      bgcolor: isSelected ? 'action.selected' : 'transparent',
                      cursor: isDisabled ? 'not-allowed' : 'pointer',
                      '&:hover': isDisabled ? {} : { bgcolor: 'action.hover' },
                    }}
                    onClick={() => !isDisabled && togglePlayer(p.player_id)}
                  >
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={isSelected}
                          disabled={isDisabled}
                          size="small"
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => !isDisabled && togglePlayer(p.player_id)}
                        />
                      }
                      label=""
                      sx={{ mr: 0 }}
                    />
                    <Box sx={{ flex: 1 }}>
                      <Typography variant="body2" fontWeight={700}>
                        {p.player_name}
                      </Typography>
                      <Chip label={p.position} size="small" sx={{ mt: 0.25 }} />
                    </Box>
                    {p.overall_fit !== null ? (
                      <Typography
                        variant="h6"
                        fontWeight={800}
                        color={color ? `${color}.main` : undefined}
                      >
                        {p.overall_fit.toFixed(0)}
                      </Typography>
                    ) : (
                      <Typography variant="body2" color="text.disabled">
                        —
                      </Typography>
                    )}
                  </Box>
                  {idx < players.length - 1 && <Divider />}
                </Box>
              );
            })}
          </Paper>

          {mutError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              Comparison failed. Check API server.
            </Alert>
          )}

          <Button
            variant="contained"
            size="large"
            startIcon={
              isPending ? (
                <CircularProgress size={18} color="inherit" />
              ) : (
                <CompareArrowsIcon />
              )
            }
            disabled={selected.size < 2 || isPending}
            onClick={handleCompare}
          >
            {isPending ? 'Comparing…' : `Compare ${selected.size < 2 ? '(select 2+)' : `${selected.size} Players`}`}
          </Button>
        </>
      )}
    </Box>
  );
}
