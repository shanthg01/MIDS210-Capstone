import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { createTheme, ThemeProvider, CssBaseline } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AuthProvider } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import AppLayout from './components/AppLayout';
import LoginPage from './pages/LoginPage';
import SignupPage from './pages/SignupPage';
import DashboardPage from './pages/DashboardPage';
import PlayerSearchPage from './pages/PlayerSearchPage';
import PlayerProfilePage from './pages/PlayerProfilePage';
import PipelinePage from './pages/PipelinePage';
import FitScorePage from './pages/FitScorePage';
import ComparePage from './pages/ComparePage';
import SettingsPage from './pages/SettingsPage';
import OverviewPage from './pages/OverviewPage';
import GlossaryPage from './pages/GlossaryPage';
import RosterImpactPage from './pages/RosterImpactPage';
import AgentActivityPage from './pages/AgentActivityPage';

// Soft slate-navy: lifted from near-black so tables/cards read clearly while
// keeping the dark sports-analytics feel and the orange/blue accents.
const BG = '#1B2838';
const PAPER = '#263849';
const TEXT_MUTED = '#A8B9CC';
const BORDER = 'rgba(255, 255, 255, 0.14)';
const CREAM = '#f3e5d0'; // warm accent from slide deck

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#FF6B35',
      light: '#ff8c61',
      dark: '#cc4e1f',
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: '#5BA3E8',
      light: '#8bbef0',
      dark: '#3d7fc4',
      contrastText: '#FFFFFF',
    },
    background: {
      default: BG,
      paper: PAPER,
    },
    text: {
      primary: '#F2F6FA',
      secondary: TEXT_MUTED,
      disabled: 'rgba(242, 246, 250, 0.38)',
    },
    success: { main: '#4CAF50', contrastText: '#fff' },
    error:   { main: '#F44336', contrastText: '#fff' },
    warning: { main: '#FF9800', contrastText: '#fff' },
    info:    { main: '#5BA3E8', contrastText: '#fff' },
    divider: BORDER,
  },
  typography: {
    fontFamily: '"Inter", -apple-system, BlinkMacSystemFont, sans-serif',
    fontWeightLight: 300,
    fontWeightRegular: 400,
    fontWeightMedium: 600,
    fontWeightBold: 700,
    h1: { fontWeight: 900, fontStyle: 'italic' },
    h2: { fontWeight: 900, fontStyle: 'italic' },
    h3: { fontWeight: 700 },
    h4: { fontWeight: 700 },
    h5: { fontWeight: 700 },
    h6: { fontWeight: 600 },
    body2: { color: TEXT_MUTED },
    caption: { color: TEXT_MUTED, fontSize: '0.7rem' },
    overline: { color: TEXT_MUTED, letterSpacing: '0.1em' },
  },
  shape: { borderRadius: 4 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: BG,
          scrollbarWidth: 'thin',
          scrollbarColor: 'rgba(243,229,208,0.35) transparent',
          '&::selection': { backgroundColor: CREAM, color: '#1B2838' },
          '&::-webkit-scrollbar': { width: 6 },
          '&::-webkit-scrollbar-track': { background: 'transparent' },
          '&::-webkit-scrollbar-thumb': { background: 'rgba(243,229,208,0.35)', borderRadius: 3 },
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: BG,
          backgroundImage: 'none',
          boxShadow: 'none',
          borderBottom: `1px solid ${BORDER}`,
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: BG,
          backgroundImage: 'none',
          borderRight: `1px solid ${BORDER}`,
        },
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: { backgroundImage: 'none' },
        outlined: { borderColor: BORDER },
      },
    },
    MuiCard: {
      defaultProps: { variant: 'outlined' },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          backgroundColor: PAPER,
          borderColor: BORDER,
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        // Per-color/variant classKey overrides (containedPrimary, outlinedPrimary, ...) were
        // removed from this MUI version's override types — ownerState-based root function is
        // the current supported pattern for the same conditional styling.
        root: ({ ownerState }) => ({
          textTransform: 'none',
          fontWeight: 600,
          borderRadius: 4,
          letterSpacing: 0,
          ...(ownerState.variant === 'contained' && ownerState.color === 'primary' && {
            backgroundColor: '#FF6B35',
            '&:hover': { backgroundColor: '#e55a24' },
          }),
          ...(ownerState.variant === 'outlined' && ownerState.color === 'primary' && {
            borderColor: '#FF6B35',
            color: '#FF6B35',
            '&:hover': { borderColor: '#e55a24', backgroundColor: 'rgba(255,107,53,0.08)' },
          }),
          ...(ownerState.variant === 'outlined' && ownerState.color === 'secondary' && {
            borderColor: '#5BA3E8',
            color: '#5BA3E8',
            '&:hover': { backgroundColor: 'rgba(91,163,232,0.08)' },
          }),
        }),
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 4,
          fontWeight: 600,
          fontSize: '0.7rem',
        },
        colorPrimary: {
          backgroundColor: 'rgba(255, 107, 53, 0.18)',
          color: '#FF6B35',
          border: '1px solid rgba(255,107,53,0.4)',
        },
        colorSecondary: {
          backgroundColor: 'rgba(91, 163, 232, 0.18)',
          color: '#5BA3E8',
          border: '1px solid rgba(91,163,232,0.4)',
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        // barColorSuccess/Warning/Error classKeys were removed from this MUI version's
        // override types — target the bar element by class inside an ownerState-based root.
        root: ({ ownerState }) => ({
          backgroundColor: 'rgba(255, 255, 255, 0.14)',
          borderRadius: 2,
          ...(ownerState.color === 'success' && { '& .MuiLinearProgress-bar': { backgroundColor: '#4CAF50' } }),
          ...(ownerState.color === 'warning' && { '& .MuiLinearProgress-bar': { backgroundColor: '#FF9800' } }),
          ...(ownerState.color === 'error' && { '& .MuiLinearProgress-bar': { backgroundColor: '#F44336' } }),
        }),
      },
    },
    MuiTableHead: {
      styleOverrides: {
        root: {
          '& .MuiTableCell-head': {
            backgroundColor: 'rgba(255, 255, 255, 0.07)',
            fontWeight: 700,
            fontSize: '0.7rem',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: TEXT_MUTED,
            borderBottom: `1px solid ${BORDER}`,
          },
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderBottom: '1px solid rgba(255, 255, 255, 0.09)' },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.05)' },
        },
      },
    },
    MuiSlider: {
      styleOverrides: {
        root: { color: '#FF6B35' },
        rail: { backgroundColor: 'rgba(255, 255, 255, 0.22)' },
      },
    },
    MuiAlert: {
      styleOverrides: {
        // standardInfo/Success/Warning/Error classKeys were removed from this MUI version's
        // override types — ownerState-based root function is the current supported pattern.
        root: ({ ownerState }) => ({
          borderRadius: 4,
          ...(ownerState.variant === 'standard' && ownerState.severity === 'info' && { backgroundColor: 'rgba(91,163,232,0.14)', color: '#5BA3E8' }),
          ...(ownerState.variant === 'standard' && ownerState.severity === 'success' && { backgroundColor: 'rgba(76,175,80,0.12)', color: '#4CAF50' }),
          ...(ownerState.variant === 'standard' && ownerState.severity === 'warning' && { backgroundColor: 'rgba(255,152,0,0.12)', color: '#FF9800' }),
          ...(ownerState.variant === 'standard' && ownerState.severity === 'error' && { backgroundColor: 'rgba(244,67,54,0.12)', color: '#F44336' }),
        }),
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            '& fieldset': { borderColor: 'rgba(255, 255, 255, 0.28)' },
            '&:hover fieldset': { borderColor: 'rgba(255, 255, 255, 0.45)' },
            '&.Mui-focused fieldset': { borderColor: '#FF6B35' },
          },
          '& label.Mui-focused': { color: '#FF6B35' },
        },
      },
    },
    MuiCheckbox: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.4)',
          '&.Mui-checked': { color: '#FF6B35' },
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 4,
          '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.06)' },
        },
      },
    },
    MuiDivider: {
      styleOverrides: {
        root: { borderColor: BORDER },
      },
    },
    MuiLink: {
      styleOverrides: {
        root: { color: '#5BA3E8' },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: PAPER,
          border: `1px solid ${BORDER}`,
          fontSize: '0.72rem',
          color: TEXT_MUTED,
        },
      },
    },
    MuiBreadcrumbs: {
      styleOverrides: {
        separator: { color: 'rgba(255,255,255,0.35)' },
      },
    },
    MuiSkeleton: {
      styleOverrides: {
        root: { backgroundColor: 'rgba(255, 255, 255, 0.1)' },
      },
    },
    MuiInputAdornment: {
      styleOverrides: {
        root: { color: TEXT_MUTED },
      },
    },
  },
});

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
});

export default function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              {/* Public */}
              <Route path="/login" element={<LoginPage />} />
              <Route path="/signup" element={<SignupPage />} />

              {/* Protected — share AppLayout shell */}
              <Route element={<ProtectedRoute />}>
                <Route element={<AppLayout />}>
                  <Route path="/overview" element={<OverviewPage />} />
                  <Route path="/glossary" element={<GlossaryPage />} />
                  <Route path="/dashboard" element={<DashboardPage />} />
                  <Route path="/players/search" element={<PlayerSearchPage />} />
                  <Route path="/players/:id" element={<PlayerProfilePage />} />
                  <Route path="/pipeline" element={<PipelinePage />} />
                  <Route path="/fit/:player_id" element={<FitScorePage />} />
                  <Route path="/compare" element={<ComparePage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="/roster-impact" element={<RosterImpactPage />} />
                  <Route path="/agent-activity" element={<AgentActivityPage />} />
                </Route>
              </Route>

              {/* Fallback */}
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
