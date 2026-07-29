import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
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
  Select,
  MenuItem,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material';
import TuneIcon from '@mui/icons-material/Tune';
import FilterAltIcon from '@mui/icons-material/FilterAlt';
import DeleteIcon from '@mui/icons-material/Delete';
import AddIcon from '@mui/icons-material/Add';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  activateProfile,
  createProfile,
  deleteProfile,
  getPreferences,
  listProfiles,
  updatePreferences,
} from '../api/users';
import { getRosterGap, getSystemProfile, listSchools } from '../api/schools';
import { updateSchool } from '../api/users';
import { useAuth } from '../context/AuthContext';
import type { FitWeights, ImportanceWeights, SchoolListItem, StatKey, StatThreshold, UserFilters } from '../types/api';
import { FIT_COMPONENTS } from '../constants/definitions';

// Mirrors schemas/user.py StatKey — player_season_stats columns eligible for a hard min-value filter.
const STAT_OPTIONS: Array<{ key: StatKey; label: string }> = [
  { key: 'usage_rate', label: 'Usage %' },
  { key: 'fg3_pct', label: '3PT %' },
  { key: 'ft_pct', label: 'FT %' },
  { key: 'rim_pct', label: 'Rim %' },
  { key: 'assist_rate', label: 'Assist Rate' },
  { key: 'tov_pct', label: 'Turnover %' },
  { key: 'off_reb_pct', label: 'Off. Rebound %' },
  { key: 'def_reb_pct', label: 'Def. Rebound %' },
  { key: 'steal_pct', label: 'Steal %' },
  { key: 'block_pct', label: 'Block %' },
  { key: 'min_pct', label: 'Minutes % of Team' },
];
export const STAT_LABELS: Record<StatKey, string> = STAT_OPTIONS.reduce(
  (acc, o) => ({ ...acc, [o.key]: o.label }),
  {} as Record<StatKey, string>,
);

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

const FIT_WEIGHT_FIELDS: Array<{ key: keyof FitWeightsPct; label: string; description?: string }> = [
  { key: 'gap', label: 'Roster Fit' },
  { key: 'scheme', label: 'System Fit' },
  { key: 'role_fit', label: 'Role Fit' },
  { key: 'program_fit', label: 'Program Fit', description: FIT_COMPONENTS.program_fit.short },
];

const IMPORTANCE_FIELDS: Array<{ key: keyof ImportanceWeights; label: string; description: string }> = [
  { key: 'scheme_fit', label: 'System Fit', description: 'How much player style must match your system' },
  { key: 'role_fit', label: 'Role Fit', description: 'Importance of minutes availability' },
  { key: 'gap_match', label: 'Roster Fit', description: 'Priority of filling roster archetype holes' },
  { key: 'program_fit', label: 'Program Fit', description: FIT_COMPONENTS.program_fit.short },
];

const DEFAULT_FIT: FitWeightsPct = { gap: 30, scheme: 25, role_fit: 25, program_fit: 20 };
const DEFAULT_IMPORTANCE: ImportanceWeights = {
  scheme_fit: 7,
  role_fit: 5,
  gap_match: 5,
  program_fit: 5,
};

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
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {label}
          </Typography>
          {description && (
            <Typography variant="caption" color="text.secondary">
              {description}
            </Typography>
          )}
        </Box>
        <Typography variant="body2" sx={{ fontWeight: 700, minWidth: 40, textAlign: 'right' }}>
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

// ── Mechanism group header ──────────────────────────────────────────────────────
// Two different mechanisms live on this page: weights re-rank survivors, filters
// eliminate candidates outright before scoring ever runs. Visually separating
// them (icon + color accent) so that distinction isn't just implied by reading.

function MechanismHeader({
  icon,
  title,
  description,
  color,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  color: string;
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, mb: 1.5, mt: 4 }}>
      <Box sx={{ color, display: 'flex', alignItems: 'center' }}>{icon}</Box>
      <Box>
        <Typography variant="subtitle1" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
          {title}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {description}
        </Typography>
      </Box>
    </Box>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { userId, schoolId, setSchoolId } = useAuth();
  const qc = useQueryClient();

  const { data: schoolsData } = useQuery({ queryKey: ['schools'], queryFn: listSchools });
  const schools = schoolsData?.schools ?? [];
  const currentSchool = schools.find((s) => s.school_id === schoolId) ?? null;
  const [selectedSchool, setSelectedSchool] = useState<SchoolListItem | null>(null);
  const [schoolSaving, setSchoolSaving] = useState(false);
  const [schoolSaveSuccess, setSchoolSaveSuccess] = useState(false);

  // 404 (no team_system_profiles row for this school/season yet) is expected, not retried.
  const { data: systemProfile } = useQuery({
    queryKey: ['systemProfile', schoolId],
    queryFn: getSystemProfile,
    enabled: !!schoolId,
    retry: false,
  });

  useEffect(() => {
    if (currentSchool) setSelectedSchool(currentSchool);
  }, [currentSchool]);

  async function handleSaveSchool() {
    if (!userId || !selectedSchool) return;
    setSchoolSaving(true);
    setSchoolSaveSuccess(false);
    try {
      await updateSchool(userId, selectedSchool.school_id);
      setSchoolId(selectedSchool.school_id);
      setSchoolSaveSuccess(true);
    } finally {
      setSchoolSaving(false);
    }
  }

  const { data: prefs, isLoading } = useQuery({
    queryKey: ['preferences', userId],
    queryFn: () => getPreferences(userId!),
    enabled: !!userId,
  });

  // 404 (no school / no roster snapshot yet) is expected, not retried.
  const { data: rosterGap } = useQuery({
    queryKey: ['rosterGap', userId],
    queryFn: getRosterGap,
    enabled: !!userId,
    retry: false,
  });

  const { data: profilesData } = useQuery({
    queryKey: ['preferenceProfiles', userId],
    queryFn: () => listProfiles(userId!),
    enabled: !!userId,
  });
  const profiles = profilesData?.profiles ?? [];
  const [activeProfileId, setActiveProfileId] = useState<number | ''>('');
  const [profileDialogOpen, setProfileDialogOpen] = useState(false);
  const [newProfileName, setNewProfileName] = useState('');
  const [profileSaving, setProfileSaving] = useState(false);

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

  const minStats = filters.min_stats ?? [];

  function addStatThreshold() {
    const used = new Set(minStats.map((t) => t.stat));
    const next = STAT_OPTIONS.find((o) => !used.has(o.key))?.key ?? STAT_OPTIONS[0].key;
    updateFilters('min_stats', [...minStats, { stat: next, min_value: 0 }]);
  }

  function updateStatThreshold(index: number, patch: Partial<StatThreshold>) {
    updateFilters(
      'min_stats',
      minStats.map((t, i) => (i === index ? { ...t, ...patch } : t)),
    );
  }

  function removeStatThreshold(index: number) {
    const next = minStats.filter((_, i) => i !== index);
    updateFilters('min_stats', next.length ? next : null);
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

  async function handleActivateProfile(profileId: number) {
    if (!userId) return;
    setActiveProfileId(profileId);
    await activateProfile(userId, profileId);
    await qc.invalidateQueries({ queryKey: ['preferences', userId] });
  }

  async function handleSaveAsProfile() {
    if (!userId || !newProfileName.trim() || !fitValid) return;
    setProfileSaving(true);
    try {
      await createProfile(userId, {
        name: newProfileName.trim(),
        fit_weights: {
          gap: fitWeights.gap / 100,
          scheme: fitWeights.scheme / 100,
          role_fit: fitWeights.role_fit / 100,
          program_fit: fitWeights.program_fit / 100,
        },
        importance_weights: importance,
        filters,
      });
      await qc.invalidateQueries({ queryKey: ['preferenceProfiles', userId] });
      setProfileDialogOpen(false);
      setNewProfileName('');
    } finally {
      setProfileSaving(false);
    }
  }

  async function handleDeleteProfile(profileId: number) {
    if (!userId) return;
    await deleteProfile(userId, profileId);
    if (activeProfileId === profileId) setActiveProfileId('');
    await qc.invalidateQueries({ queryKey: ['preferenceProfiles', userId] });
  }

  if (isLoading) {
    return (
      <Box sx={{ width: '100%', maxWidth: { xs: '100%', xl: 1200 } }}>
        <Skeleton width={200} height={36} sx={{ mb: 3 }} />
        <Skeleton variant="rectangular" height={280} sx={{ mb: 2, borderRadius: 1 }} />
        <Skeleton variant="rectangular" height={260} sx={{ borderRadius: 1 }} />
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%', maxWidth: { xs: '100%', xl: 1200 } }}>
      <Typography variant="h5" sx={{ fontWeight: 700 }} gutterBottom>
        Program Settings
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Customize your personalized candidate ranking; canonical Overall Fit stays fixed
      </Typography>

      <Paper variant="outlined" sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" sx={{ fontWeight: 700 }} gutterBottom>
          Program
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          System Fit, Roster Fit, and every other school-scoped score are computed against this school
        </Typography>
        <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
          <Autocomplete
            sx={{ flex: 1 }}
            size="small"
            options={schools}
            getOptionLabel={(o) => o.name}
            isOptionEqualToValue={(o, v) => o.school_id === v.school_id}
            value={selectedSchool}
            onChange={(_, val) => {
              setSelectedSchool(val);
              setSchoolSaveSuccess(false);
            }}
            renderInput={(params) => <TextField {...params} label="Your school" />}
          />
          <Button
            variant="contained"
            disabled={
              schoolSaving || !selectedSchool || selectedSchool.school_id === currentSchool?.school_id
            }
            onClick={handleSaveSchool}
            startIcon={schoolSaving ? <CircularProgress size={16} color="inherit" /> : undefined}
          >
            {schoolSaving ? 'Saving…' : 'Save'}
          </Button>
        </Box>
        {schoolSaveSuccess && (
          <Alert severity="success" sx={{ mt: 1.5 }}>
            Program updated to {selectedSchool?.name}.
          </Alert>
        )}
        {!schoolId && (
          <Alert severity="warning" sx={{ mt: 1.5 }}>
            No school set yet — fit scores can't be computed until you pick one.
          </Alert>
        )}
        {systemProfile && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              Your team's system (Model #2, {systemProfile.season})
            </Typography>
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {systemProfile.offense_label && (
                <Chip size="small" color="primary" variant="outlined" label={systemProfile.offense_label} />
              )}
              {systemProfile.defense_label && (
                <Chip size="small" color="secondary" variant="outlined" label={systemProfile.defense_label} />
              )}
            </Box>
          </Box>
        )}
      </Paper>

      <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', mb: 3 }}>
        <Select<number | ''>
          size="small"
          displayEmpty
          value={activeProfileId}
          onChange={(e) => {
            const id = e.target.value;
            if (typeof id === 'number') handleActivateProfile(id);
          }}
          sx={{ minWidth: 220 }}
          renderValue={(id) =>
            id === '' ? 'Current (unsaved) settings' : profiles.find((p) => p.id === id)?.name ?? ''
          }
        >
          <MenuItem value="" disabled>
            Saved profiles
          </MenuItem>
          {profiles.map((p) => (
            <MenuItem key={p.id} value={p.id} sx={{ display: 'flex', justifyContent: 'space-between', gap: 1 }}>
              {p.name}
              <IconButton
                size="small"
                edge="end"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDeleteProfile(p.id);
                }}
              >
                <DeleteIcon fontSize="inherit" />
              </IconButton>
            </MenuItem>
          ))}
        </Select>
        <Button size="small" startIcon={<AddIcon fontSize="small" />} onClick={() => setProfileDialogOpen(true)}>
          Save current as new…
        </Button>
      </Box>

      <Dialog open={profileDialogOpen} onClose={() => setProfileDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Save current settings as a profile</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            label="Profile name"
            placeholder="e.g. Wing search"
            value={newProfileName}
            onChange={(e) => setNewProfileName(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setProfileDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!newProfileName.trim() || profileSaving}
            onClick={handleSaveAsProfile}
          >
            {profileSaving ? 'Saving…' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      <MechanismHeader
        icon={<TuneIcon />}
        title="Prioritize"
        description="Re-ranks candidates who pass the filters below — doesn't remove anyone"
        color="info.main"
      />

      {/* Fit component weights */}
      <Paper variant="outlined" sx={{ p: 3, mb: 3, borderLeft: '4px solid', borderLeftColor: 'info.main' }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              Personalized Fit Weights
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Controls your Personalized Fit ranking; does not redefine canonical Overall Fit
            </Typography>
          </Box>
          <Chip
            label={`Total: ${fitTotal}%`}
            color={fitValid ? 'success' : 'warning'}
            size="small"
          />
        </Box>

        <Divider sx={{ mb: 2 }} />

        {FIT_WEIGHT_FIELDS.map(({ key, label, description }) => (
          <SliderRow
            key={key}
            label={label}
            description={description}
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
      <Paper variant="outlined" sx={{ p: 3, mb: 3, borderLeft: '4px solid', borderLeftColor: 'info.main' }}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            Priority Weights
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Relative priorities used when generating your Personalized Fit ranking
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

      <MechanismHeader
        icon={<FilterAltIcon />}
        title="Eliminate"
        description="Removes candidates outright before scoring ever runs — a hard cutoff, not a preference"
        color="warning.main"
      />

      {/* Recruiting filters */}
      <Paper variant="outlined" sx={{ p: 3, mb: 3, borderLeft: '4px solid', borderLeftColor: 'warning.main' }}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
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

          {rosterGap?.suggested_position && !filters.positions.includes(rosterGap.suggested_position) && (
            <Alert
              severity="info"
              action={
                <Button
                  size="small"
                  onClick={() =>
                    updateFilters('positions', [...filters.positions, rosterGap.suggested_position!])
                  }
                >
                  Add to filters
                </Button>
              }
            >
              Suggested: <strong>{rosterGap.suggested_position}</strong> — your biggest open-minutes hole
              ({rosterGap.suggested_open_minutes?.toFixed(1)}% of team minutes share)
            </Alert>
          )}

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

          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 1 }}>
              Stat thresholds
            </Typography>
            <Stack spacing={1.5}>
              {minStats.map((t, i) => (
                <Box key={i} sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                  <Select
                    size="small"
                    value={t.stat}
                    onChange={(e) => updateStatThreshold(i, { stat: e.target.value as StatKey })}
                    sx={{ minWidth: 180 }}
                  >
                    {STAT_OPTIONS.map((o) => (
                      <MenuItem key={o.key} value={o.key}>
                        {o.label}
                      </MenuItem>
                    ))}
                  </Select>
                  <TextField
                    size="small"
                    type="number"
                    label="Min value"
                    value={t.min_value}
                    onChange={(e) => updateStatThreshold(i, { min_value: Number(e.target.value) })}
                    sx={{ width: 120 }}
                  />
                  <IconButton size="small" onClick={() => removeStatThreshold(i)} aria-label="Remove threshold">
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Box>
              ))}
            </Stack>
            <Button
              size="small"
              startIcon={<AddIcon fontSize="small" />}
              onClick={addStatThreshold}
              sx={{ mt: minStats.length ? 1 : 0 }}
            >
              Add stat filter
            </Button>
          </Box>

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
