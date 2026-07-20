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
import type { TeamRatingProjectionResponse } from '../types/api';
import { useAuth } from '../context/AuthContext';
import { scoreColor, DataStatusChip, isComponentLive } from '../components/FitScoreBar';
import FitRadarChart, { RadarLegend } from '../components/FitRadarChart';
import ProjectionCard from '../components/ProjectionCard';
import DefinitionTooltip from '../components/DefinitionTooltip';
import { FIT_COMPONENTS, SUB_METRICS, GAP_FEATURES, HE_PLAY_TYPES, PLAY_TYPE_MATCH } from '../constants/definitions';
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
  modelVersion,
  headlineNote,
}: {
  label: string;
  score: number;
  weight: string;
  component: string;
  modelVersion?: string;
  headlineNote?: string;
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
          <DataStatusChip component={component} modelVersion={modelVersion} />
        </Box>
        <Typography variant="caption" color="text.secondary">
          {weight} weight
        </Typography>
        {headlineNote && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {headlineNote}
          </Typography>
        )}
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

function SubBar({
  label,
  value,
  metricKey,
  description: descriptionProp,
}: {
  label: string;
  value: number;
  metricKey?: string;
  description?: string;
}) {
  const color = scoreColor(value);
  const description = descriptionProp ?? (metricKey ? SUB_METRICS[metricKey]?.short : undefined);
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

// Category header for Scheme Fit's 3-way hierarchy (Pace / Shot Distribution /
// Play Type). `note` is only passed for categories whose score is a cosine
// similarity of multiple sub-dimensions — it explains, right next to the
// number it's about, why that score doesn't average the bars below it.
function SchemeCategoryHeader({ label, score, note }: { label: string; score: number; note?: string }) {
  return (
    <Box sx={{ mb: 1 }}>
      <Typography variant="subtitle2" fontWeight={700}>
        {label} — {fmtScore(score)}/100
      </Typography>
      {note && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          {note}
        </Typography>
      )}
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
  personalized,
  confidence,
  modelVersion,
}: {
  overall: number;
  gap: number;
  scheme: number;
  role: number;
  program: number;
  personalized: number | null;
  confidence: number;
  modelVersion: string;
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
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Score confidence: {Math.round(confidence * 100)}%
        {personalized !== null && ` · Personalized Fit: ${Math.round(personalized)}/100`}
      </Typography>
      <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'center' }}>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1, flex: '1 1 200px' }}>
          {[
            { label: 'Gap Match', value: gap, component: 'gap_match' },
            { label: 'Scheme', value: scheme, component: 'scheme_fit' },
            { label: 'Role Fit', value: role, component: 'role_fit' },
            { label: 'Program Fit', value: program, component: 'program_fit' },
          ].map(({ label, value, component }) => {
            const c = scoreColor(value);
            const isLive = isComponentLive(component, modelVersion);
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

// Keys present in the M6 explanation JSONB payload and their display labels.
const EXPLANATION_LABELS: Record<string, string> = {
  candidate_off_contribution: 'Offense talent',
  candidate_def_contribution: 'Defense talent',
  spacing_delta:              '3PT / spacing',
  rim_protection_delta:       'Rim protection',
  rebounding_delta:           'Rebounding',
  bench_depth_delta:          'Bench depth',
  continuity_delta:           'Roster continuity',
};

function ProjectionPanel({ data }: { data: TeamRatingProjectionResponse }) {
  const deltaPos = data.delta_adjEM >= 0;

  // Top 3 explanation drivers by absolute magnitude.
  const drivers: { label: string; value: number }[] = [];
  if (data.explanation) {
    for (const [key, label] of Object.entries(EXPLANATION_LABELS)) {
      const v = data.explanation[key];
      if (typeof v === 'number' && Math.abs(v) > 0.001) {
        drivers.push({ label, value: v });
      }
    }
    drivers.sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
    drivers.splice(3); // keep top 3
  }

  return (
    <SectionPaper>
      <Typography variant="h6" fontWeight={700} gutterBottom>
        Team Rating Projection
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
        Expected AdjEM impact if player joins and plays {fmt1(data.expected_minutes_input)} MPG
        {data.candidate_usage_role && (
          <Chip
            label={data.candidate_usage_role}
            size="small"
            variant="outlined"
            sx={{ ml: 1, height: 18, fontSize: '0.65rem' }}
          />
        )}
      </Typography>

      {/* AdjEM delta */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
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

      {/* Offense / Defense split when available */}
      {(data.baseline_adj_o != null || data.baseline_adj_d != null) && (
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5, mb: 2 }}>
          {data.baseline_adj_o != null && data.projected_adj_o != null && (
            <Box sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
              <Typography variant="caption" color="text.secondary" display="block">
                Offense (AdjO)
              </Typography>
              <Typography variant="body2" fontWeight={600}>
                {fmtAdjEM(data.baseline_adj_o)} → {fmtAdjEM(data.projected_adj_o)}
              </Typography>
            </Box>
          )}
          {data.baseline_adj_d != null && data.projected_adj_d != null && (
            <Box sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
              <Typography variant="caption" color="text.secondary" display="block">
                Defense (AdjD) ↓ better
              </Typography>
              <Typography variant="body2" fontWeight={600}>
                {fmtAdjEM(data.baseline_adj_d)} → {fmtAdjEM(data.projected_adj_d)}
              </Typography>
            </Box>
          )}
        </Box>
      )}

      <Divider sx={{ my: 1.5 }} />

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 2, mb: 2 }}>
        <Box>
          <Typography variant="caption" color="text.secondary">
            80% CI
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

      {/* Explanation driver chips */}
      {drivers.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
            Top impact drivers
          </Typography>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {drivers.map((d) => (
              <Chip
                key={d.label}
                size="small"
                variant="outlined"
                color={d.value >= 0 ? 'success' : 'error'}
                label={`${d.value >= 0 ? '+' : ''}${d.value.toFixed(2)} ${d.label}`}
              />
            ))}
          </Box>
        </Box>
      )}

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

  // Destination-adjusted — school-specific, unlike PlayerProfilePage's neutral
  // call. 404 (no destination row yet for this pair) is expected, not retried.
  const playerProjectionQuery = useQuery({
    queryKey: ['playerProjection', playerId, schoolId],
    queryFn: () => getPlayerProjection(playerId, schoolId!),
    enabled: !!playerId && schoolId !== null,
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

  // The calibrated Scheme score is the canonical headline. Keep the raw HE
  // play-type score in the explanation below; it is not on the calibrated scale.
  const hasPlayType = fit.breakdown.scheme.he_scheme_fit != null;
  const schemeDisplay = fit.scheme_fit;

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
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
        <Box>
          <Typography variant="h4" fontWeight={800}>
            {playerName}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 0.5, flexWrap: 'wrap' }}>
            {position && <Chip label={position} color="primary" size="small" />}
            {fit.is_portal_candidate && (
              <Chip label="In Portal" color="success" size="small" />
            )}
            {fit.is_current_school && (
              <Chip label="Current Roster" color="warning" size="small" />
            )}
            <Chip label={`Model ${fit.model_version}`} size="small" variant="outlined" />
            {fit.cache_hit && <Chip label="Cached" size="small" variant="outlined" />}
          </Box>
        </Box>
      </Box>

      {fit.is_current_school && (
        <Alert severity="info" sx={{ mb: 2 }}>
          This player is already on your roster — scores reflect current fit, not a recruit evaluation.
        </Alert>
      )}

      {fit.scheme_fit_stale && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Coaching change detected — scheme fit scores may not reflect the current system.
          {fit.scheme_fit_stale_reason && ` (${fit.scheme_fit_stale_reason})`}
        </Alert>
      )}

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
        scheme={schemeDisplay}
        role={fit.role_fit}
        program={fit.program_fit}
        personalized={fit.personalized_fit}
        confidence={fit.overall_confidence}
        modelVersion={fit.model_version}
      />

      {/* Scheme Fit breakdown — headline is calibrated; the category scores
          below remain raw model diagnostics. */}
      <SectionPaper>
        <ScoreHeader
          label="Scheme Fit"
          score={schemeDisplay}
          weight="25%"
          component="scheme_fit"
          headlineNote={
            hasPlayType
              ? 'Calibrated shot-distribution fit; Play Type Match is supporting context below.'
              : 'Calibrated shot-distribution fit; no Play Type data available for this pair.'
          }
        />
        <Divider sx={{ mb: 2 }} />

        <SchemeCategoryHeader label="Pace Match" score={fit.breakdown.scheme.pace_match} />

        <Divider sx={{ my: 2 }} />

        <SchemeCategoryHeader
          label="Shot Distribution Match"
          score={fit.raw_components.scheme_fit}
          note="Cosine similarity of overall shot-location style — the bars below show closeness on each dimension individually; they don't average to this number."
        />
        <SubBar label="3-Point Match" value={fit.breakdown.scheme.three_point_match} metricKey="three_point_match" />
        <SubBar label="Rim Attack" value={fit.breakdown.scheme.rim_attack_match} metricKey="rim_attack_match" />
        <SubBar label="Mid-Range Match" value={fit.breakdown.scheme.mid_range_match} metricKey="mid_range_match" />

        <Divider sx={{ my: 2 }} />

        {hasPlayType ? (
          <>
            <SchemeCategoryHeader
              label="Play Type Match"
              score={fit.breakdown.scheme.he_scheme_fit!}
              note="Cosine similarity of overall play-type style — the bars below show closeness on each play type individually; they don't average to this number."
            />
            {Object.entries(fit.breakdown.scheme.he_breakdown ?? {}).map(([feat, value]) => (
              <SubBar
                key={feat}
                label={HE_PLAY_TYPES[feat]?.label ?? feat}
                value={value}
                description={HE_PLAY_TYPES[feat]?.short}
              />
            ))}
          </>
        ) : (
          <Alert severity="info">
            No play-type data available for this pair — {PLAY_TYPE_MATCH.short}
          </Alert>
        )}
      </SectionPaper>

      {/* Role Fit breakdown */}
      <SectionPaper>
        <ScoreHeader
          label="Role Fit"
          score={fit.role_fit}
          weight="25%"
          component="role_fit"
          modelVersion={fit.model_version}
        />
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
        <ScoreHeader label="Gap Match" score={fit.gap_match} weight="30%" component="gap_match" />
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

      {/* Program Fit — not live yet, see FIT_COMPONENTS.program_fit in definitions.ts */}
      <SectionPaper>
        <ScoreHeader label="Program Fit" score={fit.program_fit} weight="20%" component="program_fit" />
        <Divider sx={{ mb: 2 }} />
        <Alert severity="info">{FIT_COMPONENTS.program_fit.short}</Alert>
      </SectionPaper>

      {/* Player Projection — adjusted for this program, not part of the 4-component score above */}
      {playerProjectionQuery.data && (
        <Box sx={{ mb: 3 }}>
          <Alert severity="info" sx={{ mb: 1.5 }}>
            The projection below is <strong>adjusted for fit at this program</strong> — this
            player's projected talent/value here specifically, incorporating role, usage, and
            roster context. It is a separate signal from the 4 fit components above, not folded
            into Overall Fit.
          </Alert>
          <ProjectionCard projection={playerProjectionQuery.data} />
        </Box>
      )}

      {/* Team Rating Projection */}
      {proj && <ProjectionPanel data={proj} />}
    </Box>
  );
}
