import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { atLeast, type Role } from '@/lib/rbac';
import { roleLabel } from '@/lib/format';
import { ThemeToggle } from './ThemeToggle';
import { LanguageToggle } from './LanguageToggle';
import { NotificationBell } from './NotificationBell';

interface NavItem {
  to: string;
  /** Translation key under `nav.user` or `nav.admin`. */
  labelKey: string;
  minRole?: Role;     // absent means every authenticated user
  exactGlobal?: Role; // a global role that has to match exactly, such as super_admin
}

const USER_NAV: NavItem[] = [
  { to: '/', labelKey: 'nav.user.dashboard' },
  { to: '/sessions', labelKey: 'nav.user.sessions' },
  { to: '/queue', labelKey: 'nav.user.queue' },
  { to: '/data', labelKey: 'nav.user.data' },
  { to: '/wallet', labelKey: 'nav.user.wallet' },
  { to: '/account', labelKey: 'nav.user.account' },
];

const ADMIN_NAV: NavItem[] = [
  { to: '/admin', labelKey: 'nav.admin.dashboard', minRole: 'group_admin' },
  { to: '/admin/orgs', labelKey: 'nav.admin.orgs', exactGlobal: 'super_admin' },
  { to: '/admin/groups', labelKey: 'nav.admin.groups', minRole: 'group_admin' },
  { to: '/admin/users', labelKey: 'nav.admin.users', minRole: 'group_admin' },
  { to: '/admin/clusters', labelKey: 'nav.admin.clusters', exactGlobal: 'super_admin' },
  { to: '/admin/nodes', labelKey: 'nav.admin.nodes', exactGlobal: 'super_admin' },
  { to: '/admin/resources', labelKey: 'nav.admin.resources', exactGlobal: 'super_admin' },
  { to: '/admin/images', labelKey: 'nav.admin.images', exactGlobal: 'super_admin' },
  { to: '/admin/allocations', labelKey: 'nav.admin.allocations', minRole: 'group_admin' },
  { to: '/admin/monitor', labelKey: 'nav.admin.monitor', minRole: 'group_admin' },
  { to: '/admin/audit', labelKey: 'nav.admin.audit', minRole: 'group_admin' },
];

function canSee(item: NavItem, globalRole?: string | null, membershipRole?: string): boolean {
  const effective = globalRole ?? membershipRole;
  if (item.exactGlobal) return globalRole === item.exactGlobal || effective === item.exactGlobal;
  if (item.minRole) return atLeast(effective, item.minRole);
  return true;
}

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return [
    'flex items-center gap-2.5 px-3 py-2 rounded-[9px] font-semibold cursor-pointer',
    isActive ? 'bg-primary-soft text-primary' : 'text-muted hover:bg-surface-2 hover:text-text',
  ].join(' ');
}

export function Layout({ children, variant = 'user' }: { children: ReactNode; variant?: 'user' | 'admin' }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const isAdminConsole = variant === 'admin';
  // Below `md` the sidebar is a drawer, closed by navigation, Escape or the dimmer.
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => { setNavOpen(false); }, [location.pathname]);
  // Also closes when the destination is the current one, where the pathname does not change.
  const closeNav = () => setNavOpen(false);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setNavOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [navOpen]);
  const { claims, membershipRole, memberships, activeProjectId } = useAuthStore();
  const logout = useAuthStore((s) => s.logout);

  const showAdmin = ADMIN_NAV.some((i) => canSee(i, claims.global_role, membershipRole));
  const isSuperAdmin = claims.global_role === 'super_admin';
  const activeProject = memberships.find((m) => m.group_id === activeProjectId);

  // The highest administrative role the user holds, used to label the admin-mode button.
  const memRole = activeProject?.role ?? membershipRole;
  const adminRole: Role | null = isSuperAdmin
    ? 'super_admin'
    : claims.global_role === 'org_admin' || atLeast(memRole, 'org_admin')
      ? 'org_admin'
      : atLeast(memRole, 'group_admin')
        ? 'group_admin'
        : null;

  return (
    <div className="gs-shell grid h-full">
      {/* Skip past the sidebar. */}
      <a href="#gs-main" className="gs-skip-link">{t('common.skipToContent')}</a>

      <div className="gs-brand flex items-center gap-2 px-3 md:px-[18px] border-b md:border-r border-border bg-surface font-extrabold">
        <button
          type="button"
          className="md:hidden gs-btn gs-btn-sm min-w-11 justify-center"
          aria-expanded={navOpen}
          aria-controls="gs-nav"
          aria-label={navOpen ? t('nav.closeMenu') : t('nav.openMenu')}
          title={navOpen ? t('nav.closeMenu') : t('nav.openMenu')}
          data-sidebar-toggle
          onClick={() => setNavOpen((v) => !v)}
        >
          <span aria-hidden="true">☰</span>
        </button>
        <span className="w-[22px] h-[22px] rounded-md bg-primary inline-block" />
        GShare {isAdminConsole && <span className="text-[11px] font-bold text-primary bg-primary-soft px-1.5 py-0.5 rounded">ADMIN</span>}
      </div>

      {/* Topbar */}
      <header className="gs-topbar flex items-center gap-3.5 px-3 md:px-[18px] bg-surface border-b border-border overflow-x-auto">
        <div className="ml-auto flex items-center gap-2.5">
          {/* Mode switch: users holding any administrative role can toggle between the consoles. */}
          {isAdminConsole ? (
            <button type="button" className="gs-btn" onClick={() => navigate('/')} title={t('nav.switchToUser')}>
              👤 {t('nav.switchToUser')}
            </button>
          ) : (
            adminRole && (
              <button type="button" className="gs-btn gs-btn-primary" onClick={() => navigate('/admin')} title={t('nav.switchToAdmin')}>
                ⚙ {t('nav.switchToAdmin')} · {roleLabel(adminRole)}
              </button>
            )
          )}
          <NotificationBell />
          <LanguageToggle />
          <ThemeToggle />
          <button
            type="button"
            className="gs-btn"
            onClick={async () => {
              await logout();
              navigate('/login');
            }}
          >
            {t('common.logout')}
          </button>
        </div>
      </header>

      {/* Dimmer behind the drawer. */}
      {navOpen && (
        <div className="md:hidden fixed inset-0 z-30 bg-black/40" onClick={() => setNavOpen(false)} aria-hidden="true" />
      )}

      {/* Sidebar navigation. The user console (/) and the admin console (/admin) are separate. */}
      <nav
        id="gs-nav"
        aria-label={isAdminConsole ? t('nav.adminSection') : t('nav.userSection')}
        className={[
          'gs-sidenav bg-surface border-r border-border p-3 overflow-y-auto flex flex-col',
          'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:w-[240px] max-md:z-40 max-md:pt-16 max-md:transition-transform',
          // `invisible` as well as the transform: an off-screen drawer stays in the tab order.
          navOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full max-md:invisible',
        ].join(' ')}
      >
        {!isAdminConsole && (
          <>
            <div className="text-[11px] text-muted font-bold uppercase tracking-wider px-3 mb-2">{t('nav.userSection')}</div>
            <div className="flex flex-col gap-0.5">
              {USER_NAV.map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === '/'} className={navLinkClass} onClick={closeNav}>
                  {t(i.labelKey)}
                </NavLink>
              ))}
            </div>
            {showAdmin && (
              <a href="/admin" className="mt-auto flex items-center gap-2.5 px-3 py-2 rounded-[9px] font-semibold text-primary bg-primary-soft">
                ⚙ {t('nav.adminConsole')} →
              </a>
            )}
          </>
        )}
        {isAdminConsole && (
          <>
            <div className="text-[11px] text-muted font-bold uppercase tracking-wider px-3 mb-2">{t('nav.adminSection')}</div>
            <div className="flex flex-col gap-0.5">
              {ADMIN_NAV.filter((i) => canSee(i, claims.global_role, membershipRole)).map((i) => (
                <NavLink key={i.to} to={i.to} end={i.to === '/admin'} className={navLinkClass} onClick={closeNav}>
                  {t(i.labelKey)}
                </NavLink>
              ))}
            </div>
            <a href="/" className="mt-auto flex items-center gap-2.5 px-3 py-2 rounded-[9px] font-semibold text-muted hover:bg-surface-2 hover:text-text">
              ← {t('nav.userConsole')}
            </a>
          </>
        )}
      </nav>

      <main id="gs-main" tabIndex={-1} className="gs-main overflow-y-auto p-4 md:p-6 bg-bg">
        {children}
      </main>
    </div>
  );
}
