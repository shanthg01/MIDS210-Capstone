import { useState } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  TextField,
  Typography,
  Alert,
  Link,
  CircularProgress,
} from '@mui/material';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import { login } from '../api/auth';
import { useAuth } from '../context/AuthContext';
import { isAxiosError } from 'axios';

export default function LoginPage() {
  const { setSession } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const { access_token, user_id } = await login({ email, password });
      setSession(access_token, user_id);
      navigate('/dashboard', { replace: true });
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 401) {
        setError('Invalid email or password.');
      } else {
        setError('Something went wrong. Try again.');
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        bgcolor: 'grey.100',
      }}
    >
      <Card sx={{ width: 400, p: 2 }} elevation={3}>
        <CardContent>
          <Typography variant="h5" fontWeight={700} gutterBottom>
            PortalPoint
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={3}>
            Sign in to your program account
          </Typography>

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} noValidate>
            <TextField
              label="Email"
              type="email"
              fullWidth
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              sx={{ mb: 2 }}
              autoComplete="email"
              autoFocus
            />
            <TextField
              label="Password"
              type="password"
              fullWidth
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              sx={{ mb: 3 }}
              autoComplete="current-password"
            />
            <Button
              type="submit"
              variant="contained"
              fullWidth
              size="large"
              disabled={loading || !email || !password}
            >
              {loading ? <CircularProgress size={22} color="inherit" /> : 'Sign in'}
            </Button>
          </Box>

          <Typography variant="body2" mt={2} textAlign="center">
            No account?{' '}
            <Link component={RouterLink} to="/signup">
              Create one
            </Link>
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
}
