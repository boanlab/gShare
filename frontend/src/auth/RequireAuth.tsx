import { useEffect } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/auth/authStore';
import { api } from '@/api/client';
import { Layout } from '@/components/Layout';

// After login, load the membership context (/auth/me) into the store; it is the source of
// membershipRole and the active group.
function useLoadMembershipContext(isAuthed: boolean) {
  const setMeContext = useAuthStore((s) => s.setMeContext);
  const setMeLoaded = useAuthStore((s) => s.setMeLoaded);
  const setActiveProject = useAuthStore((s) => s.setActiveProject);
  useEffect(() => {
    if (!isAuthed) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.GET('/api/v1/auth/me');
        if (cancelled) return;
        if (!data) { setMeLoaded(); return; }
        const ms = data.memberships;
        setMeContext(ms, (data as { org_admin_orgs?: string[] }).org_admin_orgs ?? []);
        const cur = useAuthStore.getState().activeProjectId;
        if ((!cur || !ms.some((m) => m.group_id === cur)) && ms.length > 0) {
          setActiveProject(ms[0].group_id);
        }
      } catch {
        // Release the guards on failure rather than leaving them waiting.
        if (!cancelled) setMeLoaded();
      }
    })();
    return () => { cancelled = true; };
  }, [isAuthed, setMeContext, setMeLoaded, setActiveProject]);
}

// JWT guard plus the app shell. An unauthenticated visitor is sent to /login with the return URL
// preserved. variant selects the console: 'user' (/) or 'admin' (/admin), each with its own layout
// and navigation.
export function RequireAuth({ variant = 'user' }: { variant?: 'user' | 'admin' }) {
  const isAuthed = useAuthStore((s) => s.isAuthed);
  const mustChange = useAuthStore((s) => s.claims.must_change_password);
  const loc = useLocation();
  useLoadMembershipContext(isAuthed);
  if (!isAuthed) {
    return <Navigate to="/login" replace state={{ returnUrl: loc.pathname }} />;
  }
  // Force the first-login password change: until it is done, every page redirects to that screen.
  if (mustChange) {
    return <Navigate to="/change-password" replace />;
  }
  return (
    <Layout variant={variant}>
      <Outlet />
    </Layout>
  );
}
