import { Navigate, Outlet } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { atLeast, type Role } from '@/lib/rbac';

// Passes on either the global role (super_admin) or the active group's membership role.
export function RequireRole({
  role,
  min,
  children,
}: {
  role?: Role;
  min?: Role;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const claims = useAuthStore((s) => s.claims);
  const membershipRole = useAuthStore((s) => s.membershipRole);
  const meLoaded = useAuthStore((s) => s.meLoaded);
  const effective = claims.global_role ?? membershipRole;

  // super_admin passes every gate, exact-match ones included, matching the backend's rbac_allows.
  if (claims.global_role === 'super_admin') {
    return <>{children ?? <Outlet />}</>;
  }

  // The token carries no memberships: until /auth/me answers, the role is unknown rather than
  // absent, and deciding early would redirect a legitimate administrator to 403.
  if (!meLoaded && !claims.global_role) {
    return <p role="status" className="text-muted p-2">{t('common.loading')}</p>;
  }

  const ok = role ? effective === role : min ? atLeast(effective, min) : true;
  if (!ok) return <Navigate to="/403" replace />;
  return <>{children ?? <Outlet />}</>;
}
