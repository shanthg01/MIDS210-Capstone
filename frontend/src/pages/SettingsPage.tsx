import { useEffect, useState } from 'react';
import {
  Box,
  Typography,
  Paper,
  Slider,
  Alert,
  Skeleton,
  Button,
  CircularProgress,
  Chip,
  Divider,
  Autocomplete,
  TextField,
  Stack,
} from '@mui/material';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getPreferences, updatePreferences } from '../api/users';
import { useAuth } from '../context/AuthContext';
import type { FitWeights, ImportanceWeights, UserFilters } from '../types/api';

// Mirrors schemas/school.py Region enum — no /api/schools listing endpoint exists yet to fetch this from.
const REGIONS = ['Northeast', 'Southeast', 'Mid-Atlantic', 'Midwest', 'Southwest', 'West', 'Pacific'];

const POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C'];

// Mirrors modeling/player_clustering.py ARCHETYPE_LABELS — kept in sync manually, same as FitScoreBar's LABEL_MAP.
const ARCHETYPES = [
  'Lead Scoring Playmaker',
  'High-Usage Frontcourt Creator',
  'Skilled Stretch Forward',
  'Post Scoring Big',
  'Two-Way Perimeter Guard',
  'Pressure Connector Guard',
  'Active Connector Forward',
  'Two-Way Spacing Wing',
  'Interior Star Big',
];

const DEFAULT_FILTERS: UserFilters = {
  recruiting_regions: [],
  conferences: [],
  positions: [],
  target_archetypes: [],
  nil_budget_min: null,
  nil_budget_max: null,
  min_stats: null,
};

// ── Local state shape (percentages for display) ───────────────────────────────

interface FitWeightsPct {
  gap: number;
  scheme: number;
  role_fit: number;
  program_fit: number;
}

const FIT_WEIGHT_FIELDS: Array<{ key: keyof FitWeightsPct; label: string }> = [
  { key: 'gap', label: 'Gap Match' },
  { key: 'scheme', label: 'Scheme Fit' },
  { key: 'role_fit', label: 'Role Fit' },
  { key: 'program_fit', label: 'Program Fit' },
];

const IMPORTANCE_FIELDS: Array<{ key: keyof ImportanceWeights; label: string; description: string }> = [
  { key: 'scheme_fit', label: 'Scheme Fit', description: 'How much player style must match your system' },
  { key: 'role_fit', label: 'Role Fit', description: 'Importance of minutes availability' },
  { key: 'gap_match', label: 'Gap Match', description: 'Priority of filling roster archetype holes' },
  { key: 'program_fit', label: 'Program Fit', description: 'NIL, geography, and academic alignment' },
];

const DEFAULT_FIT: FitWeightsPct = { gap: 20, scheme: 30, role_fit: 25, program_fit: 25 };
const DEFAULT_IMPORTANCE: ImportanceWeights = { scheme_fit: 7, role_fit: 5, gap_match: 5, program_fit: 5 };

// ── Slider row component ──────────────────────────────────────────────────────

function SliderRow({
  label,
  description,
  value,
  min,
  max,
  step,
  suffix,
  onChange,
}: {
  label: string;
  description?: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
  onChange: (v: number) => void;
}) {
  return (
    <Box sx={{ mb: 2.5 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
        <Box>
          <Typography variant="body2" fontWeight={600}>
            {label}
          </Typography>
          {description && (
            <Typography variant="caption" color="text.secondary">
              {description}
            </Typography>
          )}
        </Box>
        <Typography variant="body2" fontWeight={700} minWidth={40} textAlign="right">
          {value}{suffix}
        </Typography>
      </Box>
      <Slider
        value={value}
        min={min}
        max={max}
        step={step}
        size="small"
        onChange={(_, v) => onChange(v as number)}
        sx={{ py: 0.5 }}
      />
    </Box>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { userId } = useAuth();
  const qc = useQueryClient();

  const { data: prefs, isLoading } = useQuery({
    queryKey: ['preferences', userId],
    queryFn: () => getPreferences(userId!),
    enabled: !!userId,
  });

  const [fitWeights, setFitWeights] = useState<FitWeightsPct>(DEFAULT_FIT);
  const [importance, setImportance] = useState<ImportanceWeights>(DEFAULT_IMPORTANCE);
  const [filters, setFilters] = useState<UserFilters>(DEFAULT_FILTERS);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [isDirty, setIsDirty] = useState(false);

  useEffect(() => {
    if (prefs) {
      setFitWeights({
        gap: Math.round(prefs.fit_weights.gap * 100),
        scheme: Math.round(prefs.fit_weights.scheme * 100),
        role_fit: Math.round(prefs.fit_weights.role_fit * 100),
        program_fit: Math.round(prefs.fit_weights.program_fit * 100),
      });
      setImportance({ ...prefs.importance_weights });
      setFilters({ ...DEFAULT_FILTERS, ...prefs.filters });
      setIsDirty(false);
    }
  }, [prefs]);

  const fitTotal = fitWeights.gap + fitWeights.scheme + fitWeights.role_fit + fitWeights.program_fit;
  const fitValid = fitTotal === 100;

  function updateFit(key: keyof FitWeightsPct, val: number) {
    setFitWeights((prev) => ({ ...prev, [key]: val }));
    setIsDirty(true);
    setSaveSuccess(false);
  }

  function updateImportance(key: keyof ImportanceWeights, val: number) {
    setImportance((prev) => ({ ...prev, [key]: val }));
    setIsDirty(true);
    setSaveSuccess(false);
  }

  function updateFilters<K extends keyof UserFilters>(key: K, val: UserFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: val }));
    setIsDirty(true);
    setSaveSuccess(false);
  }

  function handleReset() {
    setFitWeights(DEFAULT_FIT);
    setImportance(DEFAULT_IMPORTANCE);
    setFilters(DEFAULT_FILTERS);
    setIsDirty(true);
    setSaveSuccess(false);
  }

  async function handleSave() {
    if (!userId || !fitValid) return;
    setSaving(true);
    setSaveError('');
    setSaveSuccess(false);
    try {
      const fitWeightsApi: FitWeights = {
        gap: fitWeights.gap / 100,
        scheme: fitWeights.scheme / 100,
        role_fit: fitWeights.role_fit / 100,
        program_fit: fitWeights.program_fit / 100,
      };
      await updatePreferences(userId, {
        fit_weights: fitWeightsApi,
        importance_weights: importance,
        filters,
      });
      await qc.invalidateQueries({ queryKey: ['preferences', userId] });
      setSaveSuccess(true);
      setIsDirty(false);
    } catch {
      setSaveError('Failed to save. Check API server.');
    } finally {
      setSaving(false);
    }
  }

  if (isLoading) {
    return (
      <Box maxWidth={600}>
        <Skeleton width={200} height={36} sx={{ mb: 3 }} />
        <Skeleton variant="rectangular" height={280} sx={{ mb: 2, borderRadius: 1 }} />
        <Skeleton variant="rectangular" height={260} sx={{ borderRadius: 1 }} />
      </Box>
    );
  }

  return (
    <Box maxWidth={600}>
      <Typography variant="h5" fontWeight={700} gutterBottom>
        Program Settings
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Customize how fit scores are calculated for your program
      </Typography>

      {/* Fit component weights */}
      <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Box>
            <Typography variant="h6" fontWeight={700}>
              Fit Component Weights
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Controls what percentage each component contributes to overall fit
            </Typography>
          </Box>
          <Chip
            label={`Total: ${fitTotal}%`}
            color={fitValid ? 'success' : 'warning'}
            size="small"
          />
        </Box>

        <Divider sx={{ mb: 2 }} />

        {FIT_WEIGHT_FIELDS.map(({ key, label }) => (
          <SliderRow
            key={key}
            label={label}
            value={fitWeights[key]}
            min={0}
            max={60}
            step={5}
            suffix="%"
            onChange={(v) => updateFit(key, v)}
          />
        ))}

        {!fitValid && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            Weights must sum to 100%. Current total: {fitTotal}%
          </Alert>
        )}
      </Paper>

      {/* Priority/importance weights */}
      <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6" fontWeight={700}>
            Priority Weights
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Program-stated priorities (1–10) used to weight Program Fit sub-components
          </Typography>
        </Box>

        <Divider sx={{ mb: 2 }} />

        {IMPORTANCE_FIELDS.map(({ key, label, description }) => (
          <SliderRow
            key={key}
            label={label}
            description={description}
            value={importance[key]}
            min={1}
            max={10}
            step={1}
            onChange={(v) => updateImportance(key, v)}
          />
        ))}
      </Paper>

      {/* Recruiting filters */}
      <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6" fontWeight={700}>
            Recruiting Filters
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Eliminate candidates outside these criteria, before fit scoring ranks the rest
          </Typography>
        </Box>

        <Divider sx={{ mb: 2 }} />

        <Stack spacing={2.5}>
          <Autocomplete
            multiple
            size="small"
            options={REGIONS}
            value={filters.recruiting_regions}
            onChange={(_, val) => updateFilters('recruiting_regions', val)}
            renderInput={(params) => (
              <TextField {...params} label="Recruiting regions" placeholder="Any region" />
            )}
          />

          <Autocomplete
            multiple
            freeSolo
            size="small"
            options={[]}
            value={filters.conferences}
            onChange={(_, val) => updateFilters('conferences', val)}
            renderInput={(params) => (
              <TextField {...params} label="Conferences" placeholder="Type a conference and press Enter" />
            )}
          />

          <Autocomplete
            multiple
            size="small"
            options={POSITIONS}
            value={filters.positions}
            onChange={(_, val) => updateFilters('positions', val)}
            renderInput={(params) => (
              <TextField {...params} label="Positions" placeholder="Any position" />
            )}
          />

          <Autocomplete
            multiple
            size="small"
            options={ARCHETYPES}
            value={filters.target_archetypes}
            onChange={(_, val) => updateFilters('target_archetypes', val)}
            renderInput={(params) => (
              <TextField {...params} label="Target archetypes" placeholder="Any archetype" />
            )}
          />

          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField
              label="NIL budget min ($)"
              type="number"
              size="small"
              fullWidth
              value={filters.nil_budget_min ?? ''}
              onChange={(e) =>
                updateFilters('nil_budget_min', e.target.value === '' ? null : Number(e.target.value))
              }
            />
            <TextField
              label="NIL budget max ($)"
              type="number"
              size="small"
              fullWidth
              value={filters.nil_budget_max ?? ''}
              onChange={(e) =>
                updateFilters('nil_budget_max', e.target.value === '' ? null : Number(e.target.value))
              }
            />
          </Box>

          <Alert severity="info" sx={{ mt: 0.5 }}>
            NIL budget filtering won't affect results yet — NIL valuation data isn't populated. Saved here so it
            applies automatically once it is.
          </Alert>
        </Stack>
      </Paper>

      {/* Save actions */}
      {saveSuccess && (
        <Alert severity="success" sx={{ mb: 2 }}>
          Preferences saved successfully.
        </Alert>
      )}
      {saveError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {saveError}
        </Alert>
      )}

      <Box sx={{ display: 'flex', gap: 2 }}>
        <Button
          variant="contained"
          disabled={saving || !fitValid || !isDirty}
          onClick={handleSave}
          startIcon={saving ? <CircularProgress size={16} color="inherit" /> : undefined}
        >
          {saving ? 'Saving…' : 'Save Changes'}
        </Button>
        <Button variant="outlined" onClick={handleReset} disabled={saving}>
          Reset to Defaults
        </Button>
      </Box>
    </Box>
  );
}
