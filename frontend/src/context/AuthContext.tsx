import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { logout as apiLogout } from '../api/auth';

interface AuthState {
  token: string | null;
  userId: number | null;
}

interface AuthContextValue extends AuthState {
  isAuthenticated: boolean;
  setSession: (token: string, userId: number) => void;
  clearSession: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readStorage(): AuthState {
  const token = localStorage.getItem('pp_token');
  const raw = localStorage.getItem('pp_user_id');
  const userId = raw ? parseInt(raw, 10) : null;
  return { token, userId };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(readStorage);

  const setSession = useCallback((token: string, userId: number) => {
    localStorage.setItem('pp_token', token);
    localStorage.setItem('pp_user_id', String(userId));
    setState({ token, userId });
  }, []);

  const clearSession = useCallback(() => {
    apiLogout();
    localStorage.removeItem('pp_token');
    localStorage.removeItem('pp_user_id');
    setState({ token: null, userId: null });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      isAuthenticated: state.token !== null,
      setSession,
      clearSession,
    }),
    [state, setSession, clearSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
