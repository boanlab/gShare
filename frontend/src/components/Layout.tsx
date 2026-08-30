import { useEffect, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { atLeast, type Role } from '@/lib/rbac';
import { formatCredit, roleLabel } from '@/lib/format';
import { useWallet } from '@/api/hooks/useWallet';
import { useAllocationRequests } from '@/api/hooks/useAllocations';
import { useTopupRequests } from '@/api/hooks/useBilling';
import { useResourceRequests } from '@/api/hooks/useResourceRequests';
import {
  ArrowLeft,
  Coins,
  Cube,
  Database,
  Gauge,
  Gear,
  GraphicsCard,
  Hourglass,
  List,
  SquaresFour,
  UserCircle,
  Megaphone,
  ChatCircleText,
} from './icons';
import { ThemeToggle } from './ThemeToggle';
import { LanguageToggle } from './LanguageToggle';
import { NotificationBell } from './NotificationBell';
import { AccountMenu } from './AccountMenu';

interface NavItem {
  to: string;
  /** Key into the pending-approval counts, when this destination has an inbox. */
  badge?: 'credits' | 'quota';
  /** Translation key under `nav.user` or `nav.admin`. */
  labelKey: string;
  /** Phosphor glyph, so a destination is recognisable before the label is read. */
  icon?: typeof SquaresFour;
  minRole?: Role;     // absent means every authenticated user
  exactGlobal?: Role; // a global role that has to match exactly, such as super_admin
}

const USER_NAV: NavItem[] = [
  { to: '/', labelKey: 'nav.user.dashboard', icon: Gauge },
  { to: '/sessions', labelKey: 'nav.user.sessions', icon: Cube },
  { to: '/queue', labelKey: 'nav.user.queue', icon: Hourglass },
  { to: '/data', labelKey: 'nav.user.data', icon: Database },
  { to: '/wallet', labelKey: 'nav.user.wallet', icon: Coins },
  { to: '/notices', labelKey: 'nav.user.notices', icon: Megaphone },
  { to: '/support', labelKey: 'nav.user.support', icon: ChatCircleText },
];

// Eleven flat rows read as a wall; the admin nav is grouped by concern instead. Routes and
// labels are unchanged — only the presentation is grouped. A group disappears entirely when the
// role can see none of its items.
const ADMIN_DASHBOARD: NavItem = { to: '/admin', labelKey: 'nav.admin.dashboard', minRole: 'group_admin' };
const ADMIN_GROUPS: { labelKey: string; items: NavItem[] }[] = [
  {
    labelKey: 'nav.adminGroup.tenancy',
    items: [
      { to: '/admin/orgs', labelKey: 'nav.admin.orgs', exactGlobal: 'super_admin' },
      { to: '/admin/groups', labelKey: 'nav.admin.groups', minRole: 'group_admin' },
      { to: '/admin/users', labelKey: 'nav.admin.users', minRole: 'group_admin' },
    ],
  },
  {
    labelKey: 'nav.adminGroup.infra',
    items: [
      { to: '/admin/clusters', labelKey: 'nav.admin.clusters', exactGlobal: 'super_admin' },
      { to: '/admin/nodes', labelKey: 'nav.admin.nodes', minRole: 'org_admin' },
      { to: '/admin/gpus', labelKey: 'nav.admin.gpus', minRole: 'org_admin' },
    ],
  },
  {
    labelKey: 'nav.adminGroup.resources',
    items: [
      { to: '/admin/resources', labelKey: 'nav.admin.resources', exactGlobal: 'super_admin' },
      // Group admins decide their members' quota requests (policy.create is scoped to them),
      // so hiding this page from them left the approval inbox unreachable in the console.
      { to: '/admin/policies', labelKey: 'nav.admin.policies', minRole: 'group_admin', badge: 'quota' },
      { to: '/admin/images', labelKey: 'nav.admin.images', exactGlobal: 'super_admin' },
      { to: '/admin/volumes', labelKey: 'nav.admin.volumes', exactGlobal: 'super_admin' },
      { to: '/admin/allocations', labelKey: 'nav.admin.allocations', minRole: 'group_admin', badge: 'credits' },
    ],
  },
  {
    labelKey: 'nav.adminGroup.ops',
    items: [
      { to: '/admin/monitor', labelKey: 'nav.admin.monitor', minRole: 'group_admin' },
      { to: '/admin/monitoring', labelKey: 'nav.admin.monitoring', exactGlobal: 'super_admin' },
      { to: '/admin/audit', labelKey: 'nav.admin.audit', minRole: 'group_admin' },
      { to: '/admin/notices', labelKey: 'nav.admin.notices', minRole: 'group_admin' },
      { to: '/admin/inquiries', labelKey: 'nav.admin.inquiries', minRole: 'group_admin' },
    ],
  },
];
const ADMIN_NAV: NavItem[] = [ADMIN_DASHBOARD, ...ADMIN_GROUPS.flatMap((g) => g.items)];

function canSee(item: NavItem, globalRole?: string | null, membershipRole?: string): boolean {
  const effective = globalRole ?? membershipRole;
  if (item.exactGlobal) return globalRole === item.exactGlobal || effective === item.exactGlobal;
  if (item.minRole) return atLeast(effective, item.minRole);
  return true;
}

/** Active is marked by an accent rule and full-strength text, not a filled chip: at this density a
 *  solid block per item turns the sidebar into stripes. */
function navLinkClass({ isActive }: { isActive: boolean }): string {
  return [
    'relative flex items-center gap-2.5 pl-3 pr-2.5 py-[7px] rounded-ctl text-sm cursor-pointer',
    'transition-colors duration-150',
    'before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:w-[2px] before:rounded-full',
    isActive
      ? 'bg-surface-2 text-text font-semibold before:h-4 before:bg-primary'
      : 'text-muted font-medium hover:bg-surface-2 hover:text-text before:h-0',
  ].join(' ');
}

/** One nav row: glyph plus label, sized so the icon column stays aligned. */
function NavRow({ item, onNavigate, end, count }: {
  item: NavItem; onNavigate: () => void; end?: boolean; count?: number;
}) {
  const { t } = useTranslation();
  const Glyph = item.icon;
  return (
    <NavLink to={item.to} end={end} className={navLinkClass} onClick={onNavigate}>
      {Glyph ? <Glyph size={17} className="shrink-0 opacity-90" /> : <span className="w-[17px] shrink-0" />}
      <span className="truncate">{t(item.labelKey)}</span>
      {count ? (
        // Waiting-for-you count: it only clears when the request is decided, never on visiting.
        <span
          className="ml-auto shrink-0 min-w-[1.25rem] px-1 rounded-tag bg-warn-soft text-warn text-2xs font-bold text-center"
          aria-label={t('nav.pendingCount', { count })}
        >
          {count > 9 ? '9+' : count}
        </span>
      ) : null}
    </NavLink>
  );
}

/** Pending approvals addressed to the viewer, per inbox. Polls with the underlying queries. */
function usePendingApprovals(isAdminConsole: boolean) {
  // The credit screen has TWO inboxes (allocation requests and top-up requests); the badge counts
  // what that screen actually asks the admin to decide.
  const allocs = useAllocationRequests('incoming');
  const topups = useTopupRequests({ status: 'pending' });
  const quota = useResourceRequests('incoming');
  if (!isAdminConsole) return { credits: 0, quota: 0 };
  const pending = (rows: { status?: string }[] | undefined) =>
    (rows ?? []).filter((r) => (r.status ?? 'pending') === 'pending').length;
  const topupRows = (topups.data as { data?: { status?: string }[] } | undefined)?.data;
  return { credits: pending(allocs.data) + pending(topupRows), quota: pending(quota.data) };
}

/** Overline above a nav section: labels the console the sidebar is showing. The `nav` element
 *  already announces the same name, so this is visual only. */
function NavSectionLabel({ text }: { text: string }) {
  return (
    <div className="px-3 pb-1.5 text-2xs font-semibold uppercase tracking-[0.08em] text-muted" aria-hidden="true">
      {text}
    </div>
  );
}

/** Available credits at a glance, one click from the wallet. Quiet chip; while the wallet is
 *  loading or failed it renders nothing rather than a spinner in the topbar. */
function WalletChip() {
  const { t } = useTranslation();
  const { data: wallet, isLoading, isError } = useWallet();
  const available = wallet?.available != null ? Number(wallet.available) : null;
  if (isLoading || isError || available == null || Number.isNaN(available)) return null;
  return (
    <Link
      to="/wallet"
      data-wallet-chip
      title={t('nav.walletChip')}
      aria-label={t('nav.walletChip')}
      className="inline-flex items-center gap-1.5 h-[34px] max-md:h-11 px-2.5 rounded-ctl border border-border
                 bg-surface text-sm font-semibold text-text whitespace-nowrap shrink-0
                 hover:bg-surface-2 transition-colors duration-150"
    >
      <Coins size={14} className="text-muted" aria-hidden="true" />
      <span className="gs-num">{formatCredit(available)} C</span>
    </Link>
  );
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

  const showAdmin = ADMIN_NAV.some((i) => canSee(i, claims.global_role, membershipRole));

  const pending = usePendingApprovals(isAdminConsole);
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

      <div className="gs-brand flex items-center gap-2.5 px-3 md:px-5 border-b md:border-r border-border bg-surface">
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
          <List size={18} aria-hidden="true" />
        </button>
        {/* The wordmark is the console's home button: user shell → /, admin shell → /admin. */}
        <Link
          to={isAdminConsole ? '/admin' : '/'}
          className="flex items-center gap-2.5 min-w-0 rounded-ctl focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
          aria-label={t('nav.brandHome')}
          title={t('nav.brandHome')}
        >
          <span className="w-[22px] h-[22px] rounded-ctl bg-primary grid place-items-center shrink-0" aria-hidden="true">
            <GraphicsCard size={14} weight="bold" className="text-on-primary" />
          </span>
          <span className="font-bold text-md tracking-[-0.02em]">gShare</span>
        </Link>
        {isAdminConsole && (
          <span className="gs-pill bg-primary-soft text-primary text-2xs tracking-wide">ADMIN</span>
        )}
      </div>

      {/* Topbar */}
      <header className="gs-topbar flex items-center gap-2 px-3 md:px-5 bg-surface border-b border-border overflow-x-auto">
        <div className="ml-auto flex items-center gap-2">
          {/* Who is signed in, and under which group - so a shared lab machine never leaves the
              current account ambiguous. Opens the account menu. */}
          <AccountMenu />
          {/* Mode switch: users holding any administrative role can toggle between the consoles. */}
          {isAdminConsole ? (
            <button type="button" className="gs-btn" onClick={() => navigate('/')} title={t('nav.switchToUser')}>
              <UserCircle size={16} aria-hidden="true" />
              {t('nav.switchToUser')}
            </button>
          ) : (
            adminRole && (
              <button
                type="button"
                className="gs-btn"
                onClick={() => navigate('/admin')}
                title={`${t('nav.switchToAdmin')} (${roleLabel(adminRole)})`}
              >
                <Gear size={16} aria-hidden="true" />
                {t('nav.switchToAdmin')}
              </button>
            )
          )}
          {!isAdminConsole && <WalletChip />}
          <NotificationBell />
          <LanguageToggle />
          <ThemeToggle />

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
          'gs-sidenav bg-surface border-r border-border px-2.5 py-4 overflow-y-auto flex flex-col',
          'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:w-[240px] max-md:z-40 max-md:pt-16 max-md:transition-transform',
          // `invisible` as well as the transform: an off-screen drawer stays in the tab order.
          navOpen ? 'max-md:translate-x-0' : 'max-md:-translate-x-full max-md:invisible',
        ].join(' ')}
      >
        {!isAdminConsole && (
          <>
            <NavSectionLabel text={t('nav.userSection')} />
            <div className="flex flex-col gap-0.5">
              {USER_NAV.map((i) => (
                <NavRow key={i.to} item={i} end={i.to === '/'} onNavigate={closeNav} />
              ))}
            </div>
            {showAdmin && (
              <NavLink
                to="/admin"
                onClick={closeNav}
                className="mt-auto flex items-center gap-2.5 px-3 py-2 rounded-ctl text-sm font-semibold
                           text-muted border border-border hover:text-text hover:bg-surface-2 transition-colors duration-150"
              >
                <Gear size={16} className="shrink-0" aria-hidden="true" />
                {t('nav.adminConsole')}
              </NavLink>
            )}
          </>
        )}
        {isAdminConsole && (
          <>
            <NavSectionLabel text={t('nav.adminSection')} />
            <div className="flex flex-col gap-0.5">
              {canSee(ADMIN_DASHBOARD, claims.global_role, membershipRole) && (
                <NavRow item={ADMIN_DASHBOARD} end onNavigate={closeNav} />
              )}
            </div>
            {ADMIN_GROUPS.map((g) => {
              const visible = g.items.filter((i) => canSee(i, claims.global_role, membershipRole));
              if (visible.length === 0) return null;
              return (
                <div key={g.labelKey} className="mt-3">
                  <NavSectionLabel text={t(g.labelKey)} />
                  <div className="flex flex-col gap-0.5">
                    {visible.map((i) => (
                      <NavRow key={i.to} item={i} onNavigate={closeNav}
                        count={i.badge ? pending[i.badge] : undefined} />
                    ))}
                  </div>
                </div>
              );
            })}
            <NavLink
              to="/"
              end
              onClick={closeNav}
              className="mt-auto flex items-center gap-2.5 px-3 py-2 rounded-ctl text-sm font-semibold
                         text-muted border border-border hover:text-text hover:bg-surface-2 transition-colors duration-150"
            >
              <ArrowLeft size={16} className="shrink-0" aria-hidden="true" />
              {t('nav.userConsole')}
            </NavLink>
          </>
        )}
      </nav>

      <main id="gs-main" tabIndex={-1} className="gs-main overflow-y-auto bg-bg flex flex-col">
        {/* flex column + mt-auto footer: a short page pins the footer to the bottom of the
            viewport, a long page pushes it below the content — never floating mid-screen. */}
        <div className="mx-auto w-full max-w-[1440px] 2xl:max-w-[1800px] min-[2200px]:max-w-[2000px] px-4 py-5 md:px-8 md:py-7 flex-1">{children}</div>
        <AppFooter />
      </main>
    </div>
  );
}


/** The lab footer, at the bottom of every console screen (and the login page). */
export function AppFooter() {
  return (
    <footer className="mt-auto pt-10 pb-6 text-center text-2xs text-muted leading-relaxed">
      <div>
        Licensed under the{' '}
        <a href="https://www.apache.org/licenses/LICENSE-2.0" target="_blank" rel="noreferrer noopener" className="underline hover:text-text">
          Apache License, Version 2.0
        </a>.
      </div>
      <div>Dankook University · Networked Systems and Security Lab.</div>
    </footer>
  );
}
