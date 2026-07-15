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
import { getPlayer, getPlayerProjection } from '../api/players';
import { useAuth } from '../context/AuthContext';
import { scoreColor, DataStatusChip, LIVE_COMPONENTS } from '../components/FitScoreBar';
import FitRadarChart, { RadarLegend } from '../components/FitRadarChart';
import ProjectionCard from '../components/ProjectionCard';
import DefinitionTooltip from '../components/DefinitionTooltip';
import { FIT_COMPONENTS, SUB_METRICS, GAP_FEATURES } from '../constants/definitions';
import { buildFitInsight, buildProjectionInsight } from '../utils/fitInsights';

// Mirrors modeling/gap_matching.py GAP_FEATURES — kept in sync manually, same
// convention as SettingsPage's STAT_LABELS / ProjectionCard's SKILL_LABELS.
const GAP_FEATURE_LABELS: Record<string, string> = {
  usage_rate: 'Usage Rate',
  true_shooting_pct: 'True Shooting %',
  assist_rate: 'Assist Rate',
  tov_pct_inverse: 'Turnover Avoidance',
  off_reb_pct: 'Off. Rebound %',
  def_reb_pct: 'Def. Rebound %',
  block_pct: 'Block %',
  steal_pct: 'Steal %',
  free_throw_rate: 'Free Throw Rate',
  three_point_rate: '3PT Rate',
  rim_rate: 'Rim Rate',
  mid_range_rate: 'Mid-Range Rate',
  fg3_pct: '3PT %',
  rim_pct: 'Rim %',
};

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
          <DefinitionTooltip title={FIT_COMPONENTS[component as keyof typeof FIT_COMPONENTS]?.short ?? ''}>
            <Typography variant="h6" fontWeight={700}>
              {label}
            </Typography>
          </DefinitionTooltip>
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

function SubBar({ label, value, metricKey }: { label: string; value: number; metricKey?: string }) {
  const color = scoreColor(value);
  const description = metricKey ? SUB_METRICS[metricKey]?.short : undefined;
  return (
    <Box sx={{ mb: 1.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
        {description ? (
          <DefinitionTooltip title={description}>
            <Typography variant="body2">{label}</Typography>
          </DefinitionTooltip>
        ) : (
          <Typography variant="body2">{label}</Typography>
        )}
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
      <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'center' }}>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1, flex: '1 1 200px' }}>
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

        <Box sx={{ flex: '1 1 240px' }}>
          <FitRadarChart
            size={180}
            data={[
              { label: 'Gap Match', value: gap, component: 'gap_match' },
              { label: 'Scheme', value: scheme, component: 'scheme_fit' },
              { label: 'Role Fit', value: role, component: 'role_fit' },
              { label: 'Program Fit', value: program, component: 'program_fit' },
            ]}
          />
          <RadarLegend />
        </Box>
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
  // Stays a string end-to-end — player_id is a 63-bit hash; Number() would
  // silently corrupt it past JS's 53-bit safe-integer limit (the original bug).
  const playerId = player_id ?? '';
  const { schoolId } = useAuth();
  const navigate = useNavigate();

  const playerQuery = useQuery({
    queryKey: ['player', playerId],
    queryFn: () => getPlayer(playerId),
    enabled: !!playerId,
  });

  const fitQuery = useQuery({
    queryKey: ['fitScore', playerId, schoolId],
    queryFn: () => getFitScore(playerId, schoolId!),
    enabled: !!playerId && schoolId !== null,
  });

  const projQuery = useQuery({
    queryKey: ['projection', playerId, schoolId],
    queryFn: () => getTeamRatingProjection(playerId, schoolId!),
    enabled: !!playerId && schoolId !== null,
  });

  // Context-neutral — independent of schoolId, unlike everything else on this
  // page. 404 (no projection row yet) is expected, not retried.
  const playerProjectionQuery = useQuery({
    queryKey: ['playerProjection', playerId],
    queryFn: () => getPlayerProjection(playerId),
    enabled: !!playerId,
    retry: false,
  });

  const isLoading = playerQuery.isLoading || fitQuery.isLoading || projQuery.isLoading;
  const hasError = playerQuery.isError || fitQuery.isError;

  if (schoolId === null) {
    return (
      <Alert severity="warning">
        No school set up for your account yet.{' '}
        <Link component={RouterLink} to="/settings">Set up your program in Settings</Link>{' '}
        to see fit scores.
      </Alert>
    );
  }

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

  const fitInsight = buildFitInsight(fit);
  if (playerProjectionQuery.data) {
    fitInsight.bullets.push(buildProjectionInsight(playerProjectionQuery.data).headline);
  }

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

      {/* Key insight — plain-language takeaway ahead of the detailed breakdowns */}
      <Alert severity="info" sx={{ mb: 2 }}>
        <Typography variant="body2" fontWeight={700}>
          {fitInsight.headline}
        </Typography>
        {fitInsight.bullets.map((b) => (
          <Typography key={b} variant="body2">
            • {b}
          </Typography>
        ))}
      </Alert>

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
        <SubBar label="3-Point Match" value={fit.breakdown.scheme.three_point_match} metricKey="three_point_match" />
        <SubBar label="Pace Match" value={fit.breakdown.scheme.pace_match} metricKey="pace_match" />
        <SubBar label="Usage Match" value={fit.breakdown.scheme.usage_match} metricKey="usage_match" />
        <SubBar label="Rim Attack" value={fit.breakdown.scheme.rim_attack_match} metricKey="rim_attack_match" />
        <SubBar label="Ball Movement" value={fit.breakdown.scheme.ball_movement_match} metricKey="ball_movement_match" />
      </SectionPaper>

      {/* Role Fit breakdown */}
      <SectionPaper>
        <ScoreHeader label="Role Fit" score={fit.role_fit} weight="25%" component="role_fit" />
        <Divider sx={{ mb: 2 }} />
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 2 }}>
          <Box>
            <DefinitionTooltip title={SUB_METRICS.projected_minutes.short}>
              <Typography variant="caption" color="text.secondary">
                Projected MPG
              </Typography>
            </DefinitionTooltip>
            <Typography variant="h6" fontWeight={700}>
              {fmt1(fit.breakdown.role_fit.projected_minutes)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              80% CI: {fmt1(fit.breakdown.role_fit.confidence_interval[0])} –{' '}
              {fmt1(fit.breakdown.role_fit.confidence_interval[1])} min
            </Typography>
          </Box>
          <Box>
            <DefinitionTooltip title={SUB_METRICS.starter_probability.short}>
              <Typography variant="caption" color="text.secondary">
                Starter Probability
              </Typography>
            </DefinitionTooltip>
            <Typography variant="h6" fontWeight={700}>
              {(fit.breakdown.role_fit.starter_probability * 100).toFixed(0)}%
            </Typography>
          </Box>
          <Tooltip title={SUB_METRICS.depth_chart_position.short} placement="top">
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
            <DefinitionTooltip title={SUB_METRICS.archetype_needed.short}>
              <Typography variant="caption" color="text.secondary">
                Archetype Needed
              </Typography>
            </DefinitionTooltip>
            <Chip
              label={fit.breakdown.gap.archetype_needed ? 'Yes' : 'No'}
              color={fit.breakdown.gap.archetype_needed ? 'success' : 'default'}
              size="small"
              sx={{ mt: 0.5 }}
            />
          </Box>
          <Box>
            <DefinitionTooltip title={SUB_METRICS.position_depth_score.short}>
              <Typography variant="caption" color="text.secondary">
                Position Depth Score
              </Typography>
            </DefinitionTooltip>
            <Typography variant="h6" fontWeight={700}>
              {fmtScore(fit.breakdown.gap.position_depth_score)}
            </Typography>
          </Box>
          <Tooltip title={SUB_METRICS.gap_reliability.short} placement="top">
            <Box sx={{ cursor: 'help' }}>
              <Typography variant="caption" color="text.secondary">
                Gap Confidence
              </Typography>
              <Typography variant="h6" fontWeight={700}>
                {(fit.breakdown.gap.gap_reliability * 100).toFixed(0)}%
              </Typography>
            </Box>
          </Tooltip>
        </Box>

        {fit.breakdown.gap.top_gap_features.length > 0 && (
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
              Top stat gaps this player fills
            </Typography>
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {fit.breakdown.gap.top_gap_features.map((f) => (
                <Tooltip key={f.feature} title={GAP_FEATURES[f.feature]?.short ?? ''}>
                  <Chip
                    size="small"
                    variant="outlined"
                    color="success"
                    label={`${GAP_FEATURE_LABELS[f.feature] ?? f.feature} (${f.gap.toFixed(2)})`}
                  />
                </Tooltip>
              ))}
            </Box>
          </Box>
        )}
      </SectionPaper>

      {/* Program Fit breakdown */}
      <SectionPaper>
        <ScoreHeader label="Program Fit" score={fit.program_fit} weight="25%" component="program_fit" />
        <Divider sx={{ mb: 2 }} />
        <SubBar label="NIL Score" value={fit.breakdown.program_fit.nil_score} metricKey="nil_score" />
        <SubBar label="Geographic Fit" value={fit.breakdown.program_fit.geographic_score} metricKey="geographic_score" />
        <SubBar label="Academic Fit" value={fit.breakdown.program_fit.academic_score} metricKey="academic_score" />
        <SubBar label="Cultural Fit" value={fit.breakdown.program_fit.cultural_score} metricKey="cultural_score" />
        <SubBar label="NIL Budget Alignment" value={fit.breakdown.program_fit.nil_budget_alignment} metricKey="nil_budget_alignment" />
      </SectionPaper>

      {/* Player Projection — context-neutral, not part of the 4-component score above */}
      {playerProjectionQuery.data && (
        <Box sx={{ mb: 3 }}>
          <Alert severity="info" sx={{ mb: 1.5 }}>
            The projection below is <strong>context-neutral</strong> — this player's intrinsic
            talent/value, independent of this school's scheme or roster. It is not one of the
            4 fit components above.
          </Alert>
          <ProjectionCard projection={playerProjectionQuery.data} />
        </Box>
      )}

      {/* Team Rating Projection */}
      {proj && <ProjectionPanel data={proj} />}
    </Box>
  );
}
