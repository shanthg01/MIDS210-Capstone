import { useEffect, useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  Chip,
  Link,
} from '@mui/material';
import TuneIcon from '@mui/icons-material/Tune';
import FilterAltIcon from '@mui/icons-material/FilterAlt';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getPreferences } from '../api/users';
import { useAuth } from '../context/AuthContext';

const ONBOARDED_KEY_PREFIX = 'pp_onboarded_';

// `_DEFAULTS` in users.py applies silently when a program has never saved
// preferences — this surfaces that fact once per browser instead of letting
// a new program use the product without ever seeing its defaults exist.
export default function OnboardingWizard() {
  const { userId } = useAuth();
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState(true);

  const { data: prefs } = useQuery({
    queryKey: ['preferences', userId],
    queryFn: () => getPreferences(userId!),
    enabled: !!userId,
  });

  useEffect(() => {
    if (!userId) return;
    setDismissed(localStorage.getItem(`${ONBOARDED_KEY_PREFIX}${userId}`) === '1');
  }, [userId]);

  function dismiss() {
    if (userId) localStorage.setItem(`${ONBOARDED_KEY_PREFIX}${userId}`, '1');
    setDismissed(true);
  }

  function handleCustomize() {
    dismiss();
    navigate('/settings');
  }

  if (dismissed || !prefs) return null;

  const fitPct = (v: number) => Math.round(v * 100);

  return (
    <Dialog open onClose={dismiss} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontWeight: 700 }}>Welcome to PortalPoint</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
          PortalPoint scores transfer-portal players against your program using two things you
          control: what to prioritize, and what to rule out entirely. We've started you on
          sensible defaults — fine-tune both anytime from Settings.
        </Typography>

        <Box sx={{ display: 'flex', gap: 1.5, mb: 2 }}>
          <TuneIcon color="info" fontSize="small" sx={{ mt: 0.25 }} />
          <Box>
            <Typography variant="body2" fontWeight={700}>
              Prioritize
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Personalized weights that re-rank candidates. Default split: Gap {fitPct(prefs.fit_weights.gap)}% ·
              Scheme {fitPct(prefs.fit_weights.scheme)}% · Role {fitPct(prefs.fit_weights.role_fit)}% ·
              Program {fitPct(prefs.fit_weights.program_fit)}%
            </Typography>
          </Box>
        </Box>

        <Box sx={{ display: 'flex', gap: 1.5, mb: 2.5 }}>
          <FilterAltIcon color="warning" fontSize="small" sx={{ mt: 0.25 }} />
          <Box>
            <Typography variant="body2" fontWeight={700}>
              Eliminate
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Hard filters (region, conference, position, archetype, NIL budget). None set yet —
              every candidate is in scope by default.
            </Typography>
          </Box>
        </Box>

        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip
            label="You can change any of this later in Settings"
            size="small"
            variant="outlined"
            sx={{ fontSize: '0.7rem' }}
          />
          <Link component="button" variant="caption" onClick={() => navigate('/glossary')}>
            See how fit scoring works →
          </Link>
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2.5 }}>
        <Button onClick={dismiss}>Looks good — get started</Button>
        <Button variant="contained" onClick={handleCustomize}>
          Customize now
        </Button>
      </DialogActions>
    </Dialog>
  );
}
