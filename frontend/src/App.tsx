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
import PlaceholderPage from './pages/PlaceholderPage';

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
      main: '#4A90E2',
      light: '#76acea',
      dark: '#2d6bbf',
      contrastText: '#FFFFFF',
    },
    background: {
      default: '#0D1B2A',
      paper: '#1A2E42',
    },
    text: {
      primary: '#FFFFFF',
      secondary: '#B0C4DE',
      disabled: 'rgba(255, 255, 255, 0.38)',
    },
    success: { main: '#4CAF50', contrastText: '#fff' },
    error:   { main: '#F44336', contrastText: '#fff' },
    warning: { main: '#FF9800', contrastText: '#fff' },
    info:    { main: '#4A90E2', contrastText: '#fff' },
    divider: 'rgba(255, 255, 255, 0.12)',
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
    body2: { color: '#B0C4DE' },
    caption: { color: '#B0C4DE', fontSize: '0.7rem' },
    overline: { color: '#B0C4DE', letterSpacing: '0.1em' },
  },
  shape: { borderRadius: 4 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: '#0D1B2A',
          scrollbarWidth: 'thin',
          scrollbarColor: 'rgba(255,255,255,0.2) transparent',
          '&::-webkit-scrollbar': { width: 6 },
          '&::-webkit-scrollbar-track': { background: 'transparent' },
          '&::-webkit-scrollbar-thumb': { background: 'rgba(255,255,255,0.2)', borderRadius: 3 },
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#0D1B2A',
          backgroundImage: 'none',
          boxShadow: 'none',
          borderBottom: '1px solid rgba(255, 255, 255, 0.10)',
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: '#0D1B2A',
          backgroundImage: 'none',
          borderRight: '1px solid rgba(255, 255, 255, 0.10)',
        },
      },
    },
    MuiPaper: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: { backgroundImage: 'none' },
        outlined: { borderColor: 'rgba(255, 255, 255, 0.12)' },
      },
    },
    MuiCard: {
      defaultProps: { variant: 'outlined' },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          borderColor: 'rgba(255, 255, 255, 0.12)',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: 600,
          borderRadius: 4,
          letterSpacing: 0,
        },
        containedPrimary: {
          backgroundColor: '#FF6B35',
          '&:hover': { backgroundColor: '#e55a24' },
        },
        outlinedPrimary: {
          borderColor: '#FF6B35',
          color: '#FF6B35',
          '&:hover': { borderColor: '#e55a24', backgroundColor: 'rgba(255,107,53,0.08)' },
        },
        outlinedSecondary: {
          borderColor: '#4A90E2',
          color: '#4A90E2',
          '&:hover': { backgroundColor: 'rgba(74,144,226,0.08)' },
        },
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
          backgroundColor: 'rgba(74, 144, 226, 0.18)',
          color: '#4A90E2',
          border: '1px solid rgba(74,144,226,0.4)',
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: {
          backgroundColor: 'rgba(255, 255, 255, 0.12)',
          borderRadius: 2,
        },
        barColorSuccess: { backgroundColor: '#4CAF50' },
        barColorWarning: { backgroundColor: '#FF9800' },
        barColorError:   { backgroundColor: '#F44336' },
      },
    },
    MuiTableHead: {
      styleOverrides: {
        root: {
          '& .MuiTableCell-head': {
            backgroundColor: 'rgba(255, 255, 255, 0.04)',
            fontWeight: 700,
            fontSize: '0.7rem',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: '#B0C4DE',
            borderBottom: '1px solid rgba(255, 255, 255, 0.12)',
          },
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderBottom: '1px solid rgba(255, 255, 255, 0.07)' },
      },
    },
    MuiTableRow: {
      styleOverrides: {
        root: {
          '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.03)' },
        },
      },
    },
    MuiSlider: {
      styleOverrides: {
        root: { color: '#FF6B35' },
        rail: { backgroundColor: 'rgba(255, 255, 255, 0.18)' },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: { borderRadius: 4 },
        standardInfo:    { backgroundColor: 'rgba(74,144,226,0.12)',  color: '#4A90E2' },
        standardSuccess: { backgroundColor: 'rgba(76,175,80,0.12)',   color: '#4CAF50' },
        standardWarning: { backgroundColor: 'rgba(255,152,0,0.12)',   color: '#FF9800' },
        standardError:   { backgroundColor: 'rgba(244,67,54,0.12)',   color: '#F44336' },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            '& fieldset': { borderColor: 'rgba(255, 255, 255, 0.23)' },
            '&:hover fieldset': { borderColor: 'rgba(255, 255, 255, 0.4)' },
            '&.Mui-focused fieldset': { borderColor: '#FF6B35' },
          },
          '& label.Mui-focused': { color: '#FF6B35' },
        },
      },
    },
    MuiCheckbox: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.35)',
          '&.Mui-checked': { color: '#FF6B35' },
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 4,
          '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.05)' },
        },
      },
    },
    MuiDivider: {
      styleOverrides: {
        root: { borderColor: 'rgba(255, 255, 255, 0.10)' },
      },
    },
    MuiLink: {
      styleOverrides: {
        root: { color: '#4A90E2' },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: '#1A2E42',
          border: '1px solid rgba(255,255,255,0.12)',
          fontSize: '0.72rem',
          color: '#B0C4DE',
        },
      },
    },
    MuiBreadcrumbs: {
      styleOverrides: {
        separator: { color: 'rgba(255,255,255,0.3)' },
      },
    },
    MuiSkeleton: {
      styleOverrides: {
        root: { backgroundColor: 'rgba(255, 255, 255, 0.08)' },
      },
    },
    MuiInputAdornment: {
      styleOverrides: {
        root: { color: '#B0C4DE' },
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
                  <Route path="/dashboard" element={<DashboardPage />} />
                  <Route path="/players/search" element={<PlayerSearchPage />} />
                  <Route path="/players/:id" element={<PlayerProfilePage />} />
                  <Route path="/pipeline" element={<PipelinePage />} />
                  <Route path="/fit/:player_id" element={<FitScorePage />} />
                  <Route path="/compare" element={<ComparePage />} />
                  <Route path="/settings" element={<SettingsPage />} />
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
