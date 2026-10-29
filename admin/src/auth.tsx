import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router';
import { api, ApiError, type Admin } from './api';

export const ME_KEY = ['me'] as const;

export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: () => api<Admin>('/auth/me'),
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 2,
    staleTime: 60_000,
  });
}

/** Renders children only for a signed-in admin; everyone else goes to the sign-in page. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <div className="center">Loading…</div>;
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <Navigate replace state={{ from: location.pathname }} to="/sign-in" />;
    }
    return <div className="center">Couldn’t reach the server. Refresh to try again.</div>;
  }
  return <>{children}</>;
}
