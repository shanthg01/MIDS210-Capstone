import { useState } from 'react';
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
  Slider,
  CircularProgress,
  TextField,
  Button,
} from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getFitScore,
  getTeamRatingProjection,
  overrideTeamRating,
  upsertProgramFitUserInput,
} from '../api/fitScores';
import { getPlayer, getPlayerProjection, overridePlayingTime } from '../api/players';
import type {
  PlayingTimeOverrideResponse,
  TeamRatingOverrideResponse,
  TeamRatingProjectionResponse,
} from '../types/api';
import { useAuth } from '../context/AuthContext';
import { scoreColor, DataStatusChip, isComponentLive } from '../components/FitScoreBar';
import FitRadarChart, { RadarLegend } from '../components/FitRadarChart';
import ProjectionCard, { BOX_SCORE_LABELS_PER_GAME } from '../components/ProjectionCard';
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

// Guard against null/undefined/NaN — a real backend bug (NaN silently stored
// in a NOT NULL float column, then serialized as the bare JSON token `NaN`,
// which browsers can't parse) has reached these call sites in production;
// showing "—" beats a page-crashing TypeError regardless of the data's shape.
function isBadNumber(n: unknown): boolean {
  return n === null || n === undefined || typeof n !== 'number' || Number.isNaN(n);
}

function fmt1(n: number): string {
  return isBadNumber(n) ? '—' : n.toFixed(1);
}

function fmtScore(n: number): string {
  return isBadNumber(n) ? '—' : n.toFixed(0);
}

function fmtAdjEM(n: number): string {
  return isBadNumber(n) ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}`;
}

function ScoreHeader({
  label,
  score,
  weight,
  component,
  modelVersion,
  isGraded,
  headlineNote,
}: {
  label: string;
  score: number;
  weight: string;
  component: string;
  modelVersion?: string;
  isGraded?: boolean;
  headlineNote?: string;
}) {
  const color = scoreColor(score);
  return (
    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', mb: 1.5 }}>
      <Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <DefinitionTooltip title={FIT_COMPONENTS[component as keyof typeof FIT_COMPONENTS]?.short ?? ''}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              {label}
            </Typography>
          </DefinitionTooltip>
          <DataStatusChip component={component} modelVersion={modelVersion} isGraded={isGraded} />
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
      <Typography variant="h4" color={`${color}.main`} sx={{ fontWeight: 800 }}>
        {fmtScore(score)}
        <Typography component="span" variant="body2" color="text.secondary" sx={{ fontWeight: 400 }}>
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
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
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
      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
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
  isProgramFitGraded,
}: {
  overall: number;
  gap: number;
  scheme: number;
  role: number;
  program: number;
  personalized: number | null;
  confidence: number;
  modelVersion: string;
  isProgramFitGraded: boolean;
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
        <Typography variant="h2" color={`${color}.main`} sx={{ fontWeight: 900 }}>
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
            { label: 'Roster Fit', value: gap, component: 'gap_match' },
            { label: 'System', value: scheme, component: 'scheme_fit' },
            { label: 'Role Fit', value: role, component: 'role_fit' },
            { label: 'Program Fit', value: program, component: 'program_fit' },
          ].map(({ label, value, component }) => {
            const c = scoreColor(value);
            const isLive = isComponentLive(
              component,
              modelVersion,
              component === 'program_fit' ? isProgramFitGraded : undefined,
            );
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
                <Typography variant="body2" color={`${c}.main`} sx={{ fontWeight: 700 }}>
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
              { label: 'Roster Fit', value: gap, component: 'gap_match' },
              { label: 'System', value: scheme, component: 'scheme_fit' },
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
      <Typography variant="h6" sx={{ fontWeight: 700 }} gutterBottom>
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
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            {fmtAdjEM(data.current_adjEM)}
          </Typography>
        </Box>

        <ArrowForwardIcon color="action" />

        <Box sx={{ textAlign: 'center' }}>
          <Typography variant="caption" color="text.secondary">
            Projected AdjEM
          </Typography>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
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
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                Offense (AdjO)
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {fmtAdjEM(data.baseline_adj_o)} → {fmtAdjEM(data.projected_adj_o)}
              </Typography>
            </Box>
          )}
          {data.baseline_adj_d != null && data.projected_adj_d != null && (
            <Box sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                Defense (AdjD) ↓ better
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
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
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {data.confidence_interval
              ? `${fmtAdjEM(data.confidence_interval[0])} to ${fmtAdjEM(data.confidence_interval[1])}`
              : '—'}
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">
            National Percentile
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {data.national_percentile}th
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">
            Conference Rank
          </Typography>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
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

// ── Program Fit manual qualitative grade ("off the court" input) ──────────────

function ProgramFitInputEditor({
  playerId,
  schoolId,
  season,
  initialScore,
  initialNotes,
  onSaved,
}: {
  playerId: string;
  schoolId: number;
  season: number;
  initialScore: number | null;
  initialNotes: string | null;
  onSaved: () => void;
}) {
  const [score, setScore] = useState(initialScore ?? 50);
  const [notes, setNotes] = useState(initialNotes ?? '');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  async function handleSave() {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      await upsertProgramFitUserInput({
        player_id: playerId,
        school_id: schoolId,
        season,
        qualitative_score: score,
        notes: notes.trim() || null,
      });
      setSaved(true);
      onSaved();
    } catch {
      setError('Could not save this grade — try again.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <Box sx={{ mt: 2 }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        Your grade — how well does this player's off-the-court fit (culture, work ethic,
        coachability) match this program? This is personal to you, not shared or scored into
        Overall Fit — it only adjusts your Personalized Fit.
      </Typography>
      {/* pt leaves room for valueLabelDisplay="on"'s permanent label above the thumb,
          which otherwise overlaps the caption text above (it was clashing into it). */}
      <Box sx={{ px: 1.5, pt: 3, mb: 1 }}>
        <Slider
          value={score}
          min={0}
          max={100}
          step={1}
          valueLabelDisplay="on"
          onChange={(_, v) => setScore(v as number)}
        />
      </Box>
      <TextField
        fullWidth
        size="small"
        placeholder="Notes (optional)"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        multiline
        minRows={2}
        sx={{ mb: 1 }}
      />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Button variant="contained" size="small" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save grade'}
        </Button>
        {saved && !saving && (
          <Typography variant="caption" color="success.main">Saved</Typography>
        )}
        {error && <Typography variant="caption" color="error.main">{error}</Typography>}
      </Box>
    </Box>
  );
}

// ── Minutes override ("what if this player got more/fewer minutes?", issue #61) ──

function MinutesOverridePanel({
  playerId,
  schoolId,
  storedMinutes,
  teamRatingSeason,
}: {
  playerId: string;
  schoolId: number;
  storedMinutes: number;
  teamRatingSeason: number | null;
}) {
  const [minutes, setMinutes] = useState(storedMinutes);
  const [pending, setPending] = useState(false);
  const [roleError, setRoleError] = useState('');
  const [ratingError, setRatingError] = useState('');
  const [roleResult, setRoleResult] = useState<PlayingTimeOverrideResponse | null>(null);
  const [ratingResult, setRatingResult] = useState<TeamRatingOverrideResponse | null>(null);
  const [boxScoreResult, setBoxScoreResult] = useState<Record<string, number> | null>(null);

  // Independent try/catch per call (issue: a single shared catch couldn't say
  // which of role/rating/box-score failed — role_fit and team_rating_projections
  // are separate tables with separate coverage, so one can 404 while the other
  // succeeds, e.g. no playing_time_projections row for this pair yet, or its
  // 7-day TTL expired with no scheduled refresh in place).
  async function runOverride(value: number) {
    setPending(true);
    setRoleError('');
    setRatingError('');

    const rolePromise = overridePlayingTime(playerId, { school_id: schoolId, minutes_override: value })
      .then((role) => setRoleResult(role))
      .catch(() => {
        setRoleResult(null);
        setRoleError('Could not compute Role Fit for this scenario — no scored playing-time projection for this pair yet.');
      });

    const ratingPromise = teamRatingSeason !== null
      ? overrideTeamRating({
          player_id: playerId,
          school_id: schoolId,
          season: teamRatingSeason,
          minutes_override: value,
        })
          .then((rating) => setRatingResult(rating))
          .catch(() => {
            setRatingResult(null);
            setRatingError('Could not compute Team Rating impact for this scenario — no scored projection for this pair yet.');
          })
      : Promise.resolve(setRatingResult(null));

    await Promise.all([rolePromise, ratingPromise]);
    setPending(false);

    // Separate try/catch — a player can have a playing-time projection without
    // a destination box-score projection yet (different population scopes), and
    // that shouldn't blank out the role/rating results above.
    try {
      const projection = await getPlayerProjection(playerId, schoolId, value);
      setBoxScoreResult(projection.projected_box_score_at_minutes);
    } catch {
      setBoxScoreResult(null);
    }
  }

  const roleDelta = roleResult ? roleResult.override_role_fit - roleResult.stored_role_fit : null;

  return (
    <SectionPaper>
      <Typography variant="h6" sx={{ fontWeight: 700 }} gutterBottom>
        What if minutes changed?
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 3 }}>
        Drag to see how Role Fit and Team Rating impact change under a different minutes projection.
        This doesn't change the stored scores — it's a live scenario check.
      </Typography>

      <Box sx={{ px: 1.5, mb: 1 }}>
        <Slider
          value={minutes}
          min={0}
          max={40}
          step={1}
          marks={[
            { value: 0, label: '0' },
            { value: 20, label: '20' },
            { value: 40, label: '40' },
          ]}
          valueLabelDisplay="on"
          valueLabelFormat={(v) => `${v} MPG`}
          onChange={(_, v) => setMinutes(v as number)}
          onChangeCommitted={(_, v) => runOverride(v as number)}
          disabled={pending}
        />
      </Box>

      {roleError && <Alert severity="warning" sx={{ mt: 1 }}>{roleError}</Alert>}
      {ratingError && <Alert severity="warning" sx={{ mt: 1 }}>{ratingError}</Alert>}

      {pending && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1 }}>
          <CircularProgress size={16} />
          <Typography variant="caption" color="text.secondary">Recomputing…</Typography>
        </Box>
      )}

      {!pending && (roleResult || ratingResult) && (
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 2, mt: 2 }}>
          {roleResult && (
            <Box sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                Role Fit at {minutes} MPG
              </Typography>
              <Typography variant="h6" color={`${scoreColor(roleResult.override_role_fit)}.main`} sx={{ fontWeight: 700 }}>
                {roleResult.override_role_fit.toFixed(0)}
                {roleDelta !== null && (
                  <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                    ({roleDelta >= 0 ? '+' : ''}{roleDelta.toFixed(1)} vs. stored {roleResult.stored_role_fit.toFixed(0)})
                  </Typography>
                )}
              </Typography>
            </Box>
          )}
          {ratingResult && (
            <Box sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                Team AdjEM Impact at {minutes} MPG
              </Typography>
              <Typography
                variant="h6"
                color={ratingResult.delta_adj_em >= 0 ? 'success.main' : 'error.main'}
                sx={{ fontWeight: 700 }}
              >
                {ratingResult.delta_adj_em >= 0 ? '+' : ''}{ratingResult.delta_adj_em.toFixed(1)} AdjEM
              </Typography>
            </Box>
          )}
          {boxScoreResult && Object.keys(boxScoreResult).length > 0 && (
            <Box sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1, gridColumn: '1 / -1' }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                Projected Box Score at {minutes} MPG
              </Typography>
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(72px, 1fr))' }}>
                {Object.entries(BOX_SCORE_LABELS_PER_GAME).map(([key, label]) =>
                  boxScoreResult[key] !== undefined ? (
                    <Box key={key} sx={{ textAlign: 'center' }}>
                      <Typography variant="body1" sx={{ fontWeight: 700 }}>
                        {boxScoreResult[key].toFixed(1)}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {label}
                      </Typography>
                    </Box>
                  ) : null,
                )}
              </Box>
            </Box>
          )}
        </Box>
      )}
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
      <Box sx={{ width: '100%', maxWidth: { xs: '100%', xl: 1200 } }}>
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
    <Box sx={{ maxWidth: 720 }}>
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
          <Typography variant="h4" sx={{ fontWeight: 800 }}>
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
        <Typography variant="body2" sx={{ fontWeight: 700 }}>
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
        isProgramFitGraded={fit.program_fit_user_input !== null}
      />

      {/* System Fit breakdown — headline is calibrated; the category scores
          below remain raw model diagnostics. */}
      <SectionPaper>
        <ScoreHeader
          label="System Fit"
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
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
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
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              {(fit.breakdown.role_fit.starter_probability * 100).toFixed(0)}%
            </Typography>
          </Box>
          <Tooltip title={SUB_METRICS.depth_chart_position.short} placement="top">
            <Box sx={{ cursor: 'help' }}>
              <Typography variant="caption" color="text.secondary">
                Depth Chart Position
              </Typography>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                #{fit.breakdown.role_fit.depth_chart_position}
              </Typography>
            </Box>
          </Tooltip>
        </Box>
      </SectionPaper>

      <MinutesOverridePanel
        playerId={playerId}
        schoolId={schoolId!}
        storedMinutes={fit.breakdown.role_fit.projected_minutes}
        teamRatingSeason={proj?.season ?? null}
      />

      {/* Roster Fit breakdown */}
      <SectionPaper>
        <ScoreHeader label="Roster Fit" score={fit.gap_match} weight="30%" component="gap_match" />
        <Divider sx={{ mb: 2 }} />
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 2, mb: 2 }}>
          <Box>
            <DefinitionTooltip title={SUB_METRICS.archetype_needed.short}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
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
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              {fmtScore(fit.breakdown.gap.position_depth_score)}
            </Typography>
          </Box>
          <Tooltip title={SUB_METRICS.gap_reliability.short} placement="top">
            <Box sx={{ cursor: 'help' }}>
              <Typography variant="caption" color="text.secondary">
                Gap Confidence
              </Typography>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
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

      {/* Program Fit — real once you've graded this pair (program_fit_user_inputs),
          per-user: your grade shows here and feeds your Personalized Fit, but is
          never shared with other users or folded into the canonical Overall Fit. */}
      <SectionPaper>
        <ScoreHeader
          label="Program Fit"
          score={fit.program_fit}
          weight="20%"
          component="program_fit"
          isGraded={fit.program_fit_user_input !== null}
        />
        <Divider sx={{ mb: 2 }} />
        <Alert severity="info">
          {fit.program_fit_user_input !== null
            ? 'This is your own qualitative grade for this pair — personal to you, not shared with other users or folded into the canonical Overall Fit above.'
            : FIT_COMPONENTS.program_fit.short}
        </Alert>
        {schoolId !== null && (
          <ProgramFitInputEditor
            key={`${playerId}-${schoolId}`}
            playerId={playerId}
            schoolId={schoolId}
            season={fit.season}
            initialScore={fit.program_fit_user_input}
            initialNotes={fit.program_fit_user_input_notes}
            onSaved={() => fitQuery.refetch()}
          />
        )}
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
