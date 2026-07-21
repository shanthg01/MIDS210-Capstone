import { useEffect, useState } from 'react';
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
} from '@mui/material';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getNewsMonitoringEvents, getNewsMonitoringRun, startNewsMonitoringRun } from '../api/agent';

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

const STATUS_COLOR: Record<string, 'default' | 'warning' | 'success' | 'error'> = {
  running: 'warning',
  completed: 'success',
  failed: 'error',
};

export default function AgentActivityPage() {
  const qc = useQueryClient();
  const [runId, setRunId] = useState<string | null>(null);
  const [startError, setStartError] = useState('');

  const eventsQuery = useQuery({
    queryKey: ['agent-events'],
    queryFn: () => getNewsMonitoringEvents(undefined, 50),
  });

  const runQuery = useQuery({
    queryKey: ['agent-run', runId],
    queryFn: () => getNewsMonitoringRun(runId!),
    enabled: !!runId,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 4000 : false),
  });

  // Once a run finishes, refresh the events feed so new program_events show
  // up. Must live in an effect, not the render body — invalidateQueries()
  // triggers a refetch/re-render, and calling it unconditionally on every
  // render (as long as `finished` stays true) produced a refetch loop
  // (caught in review). Keyed on the run id + status transition so it fires
  // exactly once per completed run, not on every render while finished.
  const runStatus = runQuery.data?.status;
  useEffect(() => {
    if (runId && runStatus && runStatus !== 'running') {
      qc.invalidateQueries({ queryKey: ['agent-events'] });
    }
  }, [runId, runStatus, qc]);

  async function handleRunNow() {
    setStartError('');
    try {
      const accepted = await startNewsMonitoringRun({ dry_run: false });
      setRunId(accepted.run_id);
    } catch {
      setStartError('Failed to start agent run. Check API server / Tavily+Gemini credentials.');
    }
  }

  const events = eventsQuery.data?.events ?? [];

  return (
    <Box sx={{ maxWidth: 1000 }}>
      <Box sx={{ mb: 3, display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }} gutterBottom>
            Agent Activity
          </Typography>
          <Typography variant="body2" color="text.secondary">
            News-monitoring agent — portal entries, commitments, and coaching changes it has detected.
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={runQuery.data?.status === 'running' ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
          onClick={handleRunNow}
          disabled={runQuery.data?.status === 'running'}
        >
          {runQuery.data?.status === 'running' ? 'Running…' : 'Run now'}
        </Button>
      </Box>

      {startError && <Alert severity="error" sx={{ mb: 2 }}>{startError}</Alert>}

      {runQuery.data && (() => {
        const reviewNeeded = runQuery.data.summary?.review_needed ?? [];
        const hasReviewItems = runQuery.data.status === 'completed' && reviewNeeded.length > 0;
        const severity =
          runQuery.data.status === 'failed' ? 'error' :
          runQuery.data.status === 'running' ? 'info' :
          hasReviewItems ? 'warning' : 'success';
        return (
          <Alert severity={severity} sx={{ mb: 2 }}>
            {runQuery.data.status === 'running' && 'Agent run in progress — searching news, classifying events…'}
            {runQuery.data.status === 'completed' && runQuery.data.summary && (
              <Box>
                <Typography variant="body2">
                  Run complete: {runQuery.data.summary.events_detected} event(s) detected,{' '}
                  {runQuery.data.summary.portal_updates} portal update(s) applied.
                </Typography>
                {hasReviewItems && (
                  <Box sx={{ mt: 1 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {reviewNeeded.length} item{reviewNeeded.length !== 1 ? 's' : ''} need manual review —
                      no confident player match found in the database:
                    </Typography>
                    <Box component="ul" sx={{ mt: 0.5, mb: 0, pl: 2.5 }}>
                      {reviewNeeded.map((item, i) => (
                        <Typography component="li" variant="body2" key={i}>
                          "{item.queried_name ?? 'unknown'}"{item.school_from ? ` from ${item.school_from}` : ''}
                          {' — '}{item.status === 'ambiguous' ? 'multiple possible matches' : 'no matching player found'}
                        </Typography>
                      ))}
                    </Box>
                  </Box>
                )}
              </Box>
            )}
            {runQuery.data.status === 'failed' && (runQuery.data.error || 'Run failed — see server logs.')}
          </Alert>
        );
      })()}

      {eventsQuery.isLoading ? (
        <Paper variant="outlined" sx={{ p: 3 }}>
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} height={32} sx={{ mb: 1 }} />
          ))}
        </Paper>
      ) : eventsQuery.error ? (
        <Alert severity="error">Failed to load agent activity. Check API server.</Alert>
      ) : events.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 6, textAlign: 'center' }}>
          <SmartToyIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 2 }} />
          <Typography variant="h6" color="text.secondary" gutterBottom>
            No agent activity yet
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Run the agent manually above, or wait for the next scheduled run.
          </Typography>
        </Paper>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Event</TableCell>
                <TableCell>School</TableCell>
                <TableCell>Player / Coach</TableCell>
                <TableCell>Date</TableCell>
                <TableCell>Source</TableCell>
                <TableCell>Confidence</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Detected</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {events.map((e) => (
                <TableRow key={e.id} hover>
                  <TableCell>
                    <Chip label={e.event_type.replace(/_/g, ' ')} size="small" />
                  </TableCell>
                  <TableCell>{e.school_id ?? '—'}</TableCell>
                  <TableCell>{e.player_id ?? e.coach_id ?? '—'}</TableCell>
                  <TableCell>{e.event_date ?? '—'}</TableCell>
                  <TableCell>{e.source}</TableCell>
                  <TableCell>{e.confidence !== null ? `${(e.confidence * 100).toFixed(0)}%` : '—'}</TableCell>
                  <TableCell>
                    <Chip
                      label={e.match_status}
                      size="small"
                      color={STATUS_COLOR[e.match_status] ?? 'default'}
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell>{fmtDateTime(e.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
