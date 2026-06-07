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
    primary: { main: '#1a3a5c' },
    secondary: { main: '#e8622a' },
  },
  typography: {
    fontFamily: '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
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
