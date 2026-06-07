import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { createTheme, ThemeProvider, CssBaseline } from '@mui/material';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AuthProvider } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import AppLayout from './components/AppLayout';
import LoginPage from './pages/LoginPage';
import SignupPage from './pages/SignupPage';
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
                  <Route path="/dashboard" element={
                    <PlaceholderPage
                      title="Dashboard"
                      description="Top-20 portal player recommendations for your program. Coming in the next session."
                    />
                  } />
                  <Route path="/players/search" element={
                    <PlaceholderPage
                      title="Player Search"
                      description="Search all 4,500+ portal players and view detailed profiles."
                    />
                  } />
                  <Route path="/pipeline" element={
                    <PlaceholderPage
                      title="Recruiting Pipeline"
                      description="Players your program has shortlisted."
                    />
                  } />
                  <Route path="/compare" element={
                    <PlaceholderPage
                      title="Compare Players"
                      description="Side-by-side fit score and stat comparison for 2-4 players."
                    />
                  } />
                  <Route path="/settings" element={
                    <PlaceholderPage
                      title="Program Settings"
                      description="Adjust fit weight sliders and recruiting filters."
                    />
                  } />
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
