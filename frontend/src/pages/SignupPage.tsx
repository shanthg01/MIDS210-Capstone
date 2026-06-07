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
import { signup } from '../api/auth';
import { useAuth } from '../context/AuthContext';
import { isAxiosError } from 'axios';

export default function SignupPage() {
  const { setSession } = useAuth();
  const navigate = useNavigate();

  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const { access_token, user_id } = await signup({ email, password, full_name: fullName });
      setSession(access_token, user_id);
      navigate('/dashboard', { replace: true });
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 409) {
        setError('An account with this email already exists.');
      } else if (isAxiosError(err) && err.response?.status === 422) {
        setError('Please check your inputs and try again.');
      } else {
        setError('Something went wrong. Try again.');
      }
    } finally {
      setLoading(false);
    }
  }

  const valid = fullName.trim().length > 0 && email.length > 0 && password.length >= 8;

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
            Create a program account
          </Typography>

          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} noValidate>
            <TextField
              label="Full name"
              fullWidth
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              sx={{ mb: 2 }}
              autoComplete="name"
              autoFocus
            />
            <TextField
              label="Email"
              type="email"
              fullWidth
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              sx={{ mb: 2 }}
              autoComplete="email"
            />
            <TextField
              label="Password"
              type="password"
              fullWidth
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              helperText="Minimum 8 characters"
              sx={{ mb: 3 }}
              autoComplete="new-password"
            />
            <Button
              type="submit"
              variant="contained"
              fullWidth
              size="large"
              disabled={loading || !valid}
            >
              {loading ? <CircularProgress size={22} color="inherit" /> : 'Create account'}
            </Button>
          </Box>

          <Typography variant="body2" mt={2} textAlign="center">
            Already have an account?{' '}
            <Link component={RouterLink} to="/login">
              Sign in
            </Link>
          </Typography>
        </CardContent>
      </Card>
    </Box>
  );
}
