import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { logout as apiLogout } from '../api/auth';

interface AuthState {
  token: string | null;
  userId: number | null;
  schoolId: number | null;
}

interface AuthContextValue extends AuthState {
  isAuthenticated: boolean;
  setSession: (token: string, userId: number, schoolId: number | null) => void;
  setSchoolId: (schoolId: number) => void;
  clearSession: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readStorage(): AuthState {
  const token = localStorage.getItem('pp_token');
  const rawUserId = localStorage.getItem('pp_user_id');
  const rawSchoolId = localStorage.getItem('pp_school_id');
  const userId = rawUserId ? parseInt(rawUserId, 10) : null;
  const schoolId = rawSchoolId ? parseInt(rawSchoolId, 10) : null;
  return { token, userId, schoolId };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>(readStorage);

  const setSession = useCallback((token: string, userId: number, schoolId: number | null) => {
    localStorage.setItem('pp_token', token);
    localStorage.setItem('pp_user_id', String(userId));
    if (schoolId !== null) {
      localStorage.setItem('pp_school_id', String(schoolId));
    } else {
      localStorage.removeItem('pp_school_id');
    }
    setState({ token, userId, schoolId });
  }, []);

  const setSchoolId = useCallback((schoolId: number) => {
    localStorage.setItem('pp_school_id', String(schoolId));
    setState((prev) => ({ ...prev, schoolId }));
  }, []);

  const clearSession = useCallback(() => {
    apiLogout();
    localStorage.removeItem('pp_token');
    localStorage.removeItem('pp_user_id');
    localStorage.removeItem('pp_school_id');
    setState({ token: null, userId: null, schoolId: null });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      isAuthenticated: state.token !== null,
      setSession,
      setSchoolId,
      clearSession,
    }),
    [state, setSession, setSchoolId, clearSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
