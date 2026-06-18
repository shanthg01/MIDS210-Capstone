import {
  Box,
  Typography,
  Paper,
  Chip,
  Alert,
  Skeleton,
  Divider,
  LinearProgress,
  Breadcrumbs,
  Link,
  Tooltip,
} from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getFitScore, getTeamRatingProjection } from '../api/fitScores';
import { getPlayer } from '../api/players';
import { useAuth } from '../context/AuthContext';
import { scoreColor, DataStatusChip, LIVE_COMPONENTS } from '../components/FitScoreBar';

// ── Small helpers ─────────────────────────────────────────────────────────────

function fmt1(n: number): string {
  return n.toFixed(1);
}

function fmtScore(n: number): string {
  return n.toFixed(0);
}

function fmtAdjEM(n: number): string {
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}`;
}

function ScoreHeader({
  label,
  score,
  weight,
  component,
}: {
  label: string;
  score: number;
  weight: string;
  component: string;
}) {
  const color = scoreColor(score);
  return (
    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 1.5 }}>
      <Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography variant="h6" fontWeight={700}>
            {label}
          </Typography>
          <DataStatusChip component={component} />
        </Box>
        <Typography variant="caption" color="text.secondary">
          {weight} weight
        </Typography>
      </Box>
      <Typography variant="h4" fontWeight={800} color={`${color}.main`}>
        {fmtScore(score)}
        <Typography component="span" variant="body2" color="text.secondary" fontWeight={400}>
          /100
        </Typography>
      </Typography>
    </Box>
  );
}

function SubBar({ label, value }: { label: string; value: number }) {
  const color = scoreColor(value);
  return (
    <Box sx={{ mb: 1.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
        <Typography variant="body2">{label}</Typography>
        <Typography variant="body2" fontWeight={600}>
          {fmtScore(value)}
        </Typography>
      </Box>
      <LinearProgress
        variant="determinate"
        value={value}
        color={color}
        sx={{ height: 6, borderRadius: 3 }}
      />
    </Box>
  );
}

function SectionPaper({ children }: { children: React.ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 2.5, mb: 2 }}>
      {children}
    </Paper>
  );
}

// ── Component sections ────────────────────────────────────────────────────────

const SCORE_TINT: Record<'success' | 'warning' | 'error', string> = {
  success: 'rgba(76, 175, 80, 0.10)',
  warning: 'rgba(255, 152, 0, 0.10)',
  error:   'rgba(244, 67, 54, 0.10)',
};

function OverallPanel({
  overall,
  gap,
  scheme,
  role,
  program,
}: {
  overall: number;
  gap: number;
  scheme: number;
  role: number;
  program: number;
}) {
  const color = scoreColor(overall);
  return (
    <Paper
      variant="outlined"
      sx={{ p: 3, mb: 3, bgcolor: SCORE_TINT[color], borderColor: `${color}.main` }}
    >
      <Typography variant="overline" color="text.secondary" gutterBottom>
        Overall Fit Score
      </Typography>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 2 }}>
        <Typography variant="h2" fontWeight={900} color={`${color}.main`}>
          {fmtScore(overall)}
        </Typography>
        <Typography variant="h5" color="text.secondary">
          /100
        </Typography>
      </Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1 }}>
        {[
          { label: 'Gap Match', value: gap, component: 'gap_match' },
          { label: 'Scheme', value: scheme, component: 'scheme_fit' },
          { label: 'Role Fit', value: role, component: 'role_fit' },
          { label: 'Program Fit', value: program, component: 'program_fit' },
        ].map(({ label, value, component }) => {
          const c = scoreColor(value);
          const isLive = LIVE_COMPONENTS.has(component);
          return (
            <Box key={label} sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <Box
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    bgcolor: isLive ? 'success.main' : 'grey.400',
                  }}
                />
                <Typography variant="body2" color="text.secondary">
                  {label}
                </Typography>
              </Box>
              <Typography variant="body2" fontWeight={700} color={`${c}.main`}>
                {fmtScore(value)}
              </Typography>
            </Box>
          );
        })}
      </Box>
    </Paper>
  );
}

// ── Projection panel ──────────────────────────────────────────────────────────

function ProjectionPanel({
  data,
}: {
  data: {
    current_adjEM: number;
    projected_adjEM: number;
    delta_adjEM: number;
    confidence_interval: [number, number];
    national_percentile: number;
    conference_rank: number;
    context: string;
    expected_minutes_input: number;
  };
}) {
  const deltaPos = data.delta_adjEM >= 0;
  return (
    <SectionPaper>
      <Typography variant="h6" fontWeight={700} gutterBottom>
        Team Rating Projection
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
        Expected impact on program AdjEM if player joins and plays {fmt1(data.expected_minutes_input)} MPG
      </Typography>

      {/* AdjEM delta */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          mb: 2,
          flexWrap: 'wrap',
        }}
      >
        <Box sx={{ textAlign: 'center' }}>
          <Typography variant="caption" color="text.secondary">
            Current AdjEM
          </Typography>
          <Typography variant="h5" fontWeight={700}>
            {fmtAdjEM(data.current_adjEM)}
          </Typography>
        </Box>

        <ArrowForwardIcon color="action" />

        <Box sx={{ textAlign: 'center' }}>
          <Typography variant="caption" color="text.secondary">
            Projected AdjEM
          </Typography>
          <Typography variant="h5" fontWeight={700}>
            {fmtAdjEM(data.projected_adjEM)}
          </Typography>
        </Box>

        <Chip
          label={`${deltaPos ? '+' : ''}${fmt1(data.delta_adjEM)} AdjEM`}
          color={deltaPos ? 'success' : 'error'}
          size="small"
        />
      </Box>

      <Divider sx={{ my: 1.5 }} />

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 2, mb: 2 }}>
        <Box>
          <Typography variant="caption" color="text.secondary">
            80% Confidence Interval
          </Typography>
          <Typography variant="body2" fontWeight={600}>
            {fmtAdjEM(data.confidence_interval[0])} to {fmtAdjEM(data.confidence_interval[1])}
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">
            National Percentile
          </Typography>
          <Typography variant="body2" fontWeight={600}>
            {data.national_percentile}th
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">
            Conference Rank
          </Typography>
          <Typography variant="body2" fontWeight={600}>
            #{data.conference_rank}
          </Typography>
        </Box>
      </Box>

      <Alert severity="info" sx={{ py: 0.5 }}>
        {data.context}
      </Alert>
    </SectionPaper>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function FitScorePage() {
  const { player_id } = useParams<{ player_id: string }>();
  const playerId = Number(player_id);
  const { userId } = useAuth();
  const navigate = useNavigate();

  // school_id: use userId as proxy — stub seeds with player_id * 1000 + school_id,
  // so any consistent integer gives stable deterministic results
  const schoolId = userId ?? 1;

  const playerQuery = useQuery({
    queryKey: ['player', playerId],
    queryFn: () => getPlayer(playerId),
    enabled: !Number.isNaN(playerId),
  });

  const fitQuery = useQuery({
    queryKey: ['fitScore', playerId, schoolId],
    queryFn: () => getFitScore(playerId, schoolId),
    enabled: !Number.isNaN(playerId),
  });

  const projQuery = useQuery({
    queryKey: ['projection', playerId, schoolId],
    queryFn: () => getTeamRatingProjection(playerId, schoolId),
    enabled: !Number.isNaN(playerId),
  });

  const isLoading = playerQuery.isLoading || fitQuery.isLoading || projQuery.isLoading;
  const hasError = playerQuery.isError || fitQuery.isError;

  if (isLoading) {
    return (
      <Box maxWidth={720}>
        <Skeleton width={300} height={28} sx={{ mb: 2 }} />
        <Skeleton width={200} height={44} sx={{ mb: 1 }} />
        <Skeleton variant="rectangular" height={140} sx={{ mb: 2, borderRadius: 1 }} />
        <Skeleton variant="rectangular" height={200} sx={{ mb: 2, borderRadius: 1 }} />
        <Skeleton variant="rectangular" height={160} sx={{ borderRadius: 1 }} />
      </Box>
    );
  }

  if (hasError || !fitQuery.data) {
    return (
      <Alert severity="error">
        Failed to load fit score data.{' '}
        <Link component={RouterLink} to="/dashboard">Back to dashboard</Link>
      </Alert>
    );
  }

  const fit = fitQuery.data;
  const proj = projQuery.data ?? null;
  const playerName = playerQuery.data?.full_name ?? `Player #${playerId}`;
  const position = playerQuery.data?.position ?? '';

  return (
    <Box maxWidth={720}>
      <Breadcrumbs sx={{ mb: 2 }}>
        <Link
          component="button"
          variant="body2"
          onClick={() => navigate('/dashboard')}
          underline="hover"
          color="inherit"
        >
          Dashboard
        </Link>
        <Link
          component="button"
          variant="body2"
          onClick={() => navigate(`/players/${playerId}`)}
          underline="hover"
          color="inherit"
        >
          {playerName}
        </Link>
        <Typography variant="body2" color="text.primary">
          Fit Score
        </Typography>
      </Breadcrumbs>

      {/* Player name */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3, flexWrap: 'wrap' }}>
        <Box>
          <Typography variant="h4" fontWeight={800}>
            {playerName}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
            {position && <Chip label={position} color="primary" size="small" />}
            <Chip label={`Model ${fit.model_version}`} size="small" variant="outlined" />
            {fit.cache_hit && <Chip label="Cached" size="small" variant="outlined" />}
          </Box>
        </Box>
      </Box>

      {/* Overall */}
      <OverallPanel
        overall={fit.overall_fit}
        gap={fit.gap_match}
        scheme={fit.scheme_fit}
        role={fit.role_fit}
        program={fit.program_fit}
      />

      {/* Scheme Fit breakdown */}
      <SectionPaper>
        <ScoreHeader label="Scheme Fit" score={fit.scheme_fit} weight="30%" component="scheme_fit" />
        <Divider sx={{ mb: 2 }} />
        <SubBar label="3-Point Match" value={fit.breakdown.scheme.three_point_match} />
        <SubBar label="Pace Match" value={fit.breakdown.scheme.pace_match} />
        <SubBar label="Usage Match" value={fit.breakdown.scheme.usage_match} />
        <SubBar label="Rim Attack" value={fit.breakdown.scheme.rim_attack_match} />
        <SubBar label="Ball Movement" value={fit.breakdown.scheme.ball_movement_match} />
      </SectionPaper>

      {/* Role Fit breakdown */}
      <SectionPaper>
        <ScoreHeader label="Role Fit" score={fit.role_fit} weight="25%" component="role_fit" />
        <Divider sx={{ mb: 2 }} />
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 2 }}>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Projected MPG
            </Typography>
            <Typography variant="h6" fontWeight={700}>
              {fmt1(fit.breakdown.role_fit.projected_minutes)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              80% CI: {fmt1(fit.breakdown.role_fit.confidence_interval[0])} –{' '}
              {fmt1(fit.breakdown.role_fit.confidence_interval[1])} min
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Starter Probability
            </Typography>
            <Typography variant="h6" fontWeight={700}>
              {(fit.breakdown.role_fit.starter_probability * 100).toFixed(0)}%
            </Typography>
          </Box>
          <Tooltip title="Lower number = higher on depth chart" placement="top">
            <Box sx={{ cursor: 'help' }}>
              <Typography variant="caption" color="text.secondary">
                Depth Chart Position
              </Typography>
              <Typography variant="h6" fontWeight={700}>
                #{fit.breakdown.role_fit.depth_chart_position}
              </Typography>
            </Box>
          </Tooltip>
        </Box>
      </SectionPaper>

      {/* Gap Match breakdown */}
      <SectionPaper>
        <ScoreHeader label="Gap Match" score={fit.gap_match} weight="20%" component="gap_match" />
        <Divider sx={{ mb: 2 }} />
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 2, mb: 2 }}>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Archetype Needed
            </Typography>
            <Chip
              label={fit.breakdown.gap.archetype_needed ? 'Yes' : 'No'}
              color={fit.breakdown.gap.archetype_needed ? 'success' : 'default'}
              size="small"
              sx={{ mt: 0.5 }}
            />
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Position Depth Score
            </Typography>
            <Typography variant="h6" fontWeight={700}>
              {fmtScore(fit.breakdown.gap.position_depth_score)}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Uniqueness Bonus
            </Typography>
            <Typography
              variant="h6"
              fontWeight={700}
              color={fit.breakdown.gap.uniqueness_bonus > 0 ? 'success.main' : 'text.secondary'}
            >
              +{fmtScore(fit.breakdown.gap.uniqueness_bonus)}
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Redundancy Penalty
            </Typography>
            <Typography
              variant="h6"
              fontWeight={700}
              color={fit.breakdown.gap.redundancy_penalty > 0 ? 'error.main' : 'text.secondary'}
            >
              -{fmtScore(fit.breakdown.gap.redundancy_penalty)}
            </Typography>
          </Box>
        </Box>
      </SectionPaper>

      {/* Program Fit breakdown */}
      <SectionPaper>
        <ScoreHeader label="Program Fit" score={fit.program_fit} weight="25%" component="program_fit" />
        <Divider sx={{ mb: 2 }} />
        <SubBar label="NIL Score" value={fit.breakdown.program_fit.nil_score} />
        <SubBar label="Geographic Fit" value={fit.breakdown.program_fit.geographic_score} />
        <SubBar label="Academic Fit" value={fit.breakdown.program_fit.academic_score} />
        <SubBar label="Cultural Fit" value={fit.breakdown.program_fit.cultural_score} />
        <SubBar label="NIL Budget Alignment" value={fit.breakdown.program_fit.nil_budget_alignment} />
      </SectionPaper>

      {/* Team Rating Projection */}
      {proj && <ProjectionPanel data={proj} />}
    </Box>
  );
}
