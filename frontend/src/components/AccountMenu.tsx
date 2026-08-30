import { useEffect, useRef, useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { useAnchoredMenu } from '@/hooks/useAnchoredMenu';
import { roleLabel } from '@/lib/format';
import { CaretDown, LockKey, SignOut, UserCircle } from './icons';

/**
 * The signed-in identity, top right, as a menu rather than a link.
 *
 * It answers "who am I signed in as" at a glance (the trigger carries the name and group) and
 * "what can I do about it" on click: a short summary, then the full account screen, the password
 * screen, and sign-out. Account is not a work destination, so it does not belong in the sidebar
 * next to sessions and volumes.
 */
export function AccountMenu() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const MENU_WIDTH = 268;
  const pos = useAnchoredMenu(open, triggerRef, MENU_WIDTH);

  const { claims, memberships, activeProjectId, displayName, membershipRole } = useAuthStore();
  const logout = useAuthStore((s) => s.logout);

  const isSuperAdmin = claims.global_role === 'super_admin';
  const activeProject = memberships.find((m) => m.group_id === activeProjectId);
  const orgName = activeProject?.org_name ?? undefined;
  const groupName = activeProject?.project_name ?? undefined;
  // Same precedence the account screen uses: an explicit global role wins over membership rank.
  const role = isSuperAdmin
    ? roleLabel('super_admin')
    : roleLabel(activeProject?.role ?? membershipRole ?? '') || t('account.defaultRole');

  // Close on navigation, on Escape, and on a click outside the menu.
  useEffect(() => { setOpen(false); }, [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  const itemClass = 'flex items-center gap-2.5 px-4 py-2.5 text-sm text-left w-full hover:bg-surface-2 transition-colors duration-150';

  return (
    <div className="relative" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title={t('nav.accountInfo')}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 pl-2 pr-2 sm:pr-2.5 py-1.5 rounded-ctl border border-transparent
                   text-xs max-w-[420px] hover:border-border hover:bg-surface-2 transition-colors duration-150"
      >
        <UserCircle size={18} className="shrink-0 text-muted" aria-hidden="true" />
        <span className="hidden sm:flex items-center gap-2 min-w-0">
          <span className="truncate font-semibold">{displayName ?? claims.email ?? '-'}</span>
          {groupName && (
            <span className="text-muted whitespace-nowrap shrink-0 border-l border-border pl-2">{groupName}</span>
          )}
        </span>
        <CaretDown size={12} className="shrink-0 text-muted" aria-hidden="true" />
      </button>

      {open && (
        <div
          role="menu"
          aria-label={t('nav.accountInfo')}
          style={pos ? { top: pos.top, right: pos.right } : { visibility: 'hidden' }}
          className="fixed z-40 w-[268px] max-w-[calc(100vw-16px)] bg-surface border border-border
                     rounded-card shadow-raised overflow-hidden"
        >
          {/* Summary. The detail lives one click away, on the account screen. */}
          <div className="px-4 py-3 border-b border-border">
            <div className="font-semibold truncate">{displayName ?? '-'}</div>
            <div className="text-muted text-xs truncate">{claims.email ?? '-'}</div>
            <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {orgName && (
                <>
                  <dt className="text-muted">{t('common.organization')}</dt>
                  <dd className="truncate">{orgName}</dd>
                </>
              )}
              {groupName && (
                <>
                  <dt className="text-muted">{t('common.group')}</dt>
                  <dd className="truncate">{groupName}</dd>
                </>
              )}
              <dt className="text-muted">{t('account.roleLabel')}</dt>
              <dd className="truncate">{role}</dd>
            </dl>
          </div>

          <NavLink to="/account" role="menuitem" className={itemClass} onClick={() => setOpen(false)}>
            <UserCircle size={16} className="text-muted shrink-0" aria-hidden="true" />
            {t('nav.user.account')}
          </NavLink>
          <NavLink to="/account/password" role="menuitem" className={itemClass} onClick={() => setOpen(false)}>
            <LockKey size={16} className="text-muted shrink-0" aria-hidden="true" />
            {t('account.changePassword')}
          </NavLink>

          <div className="border-t border-border">
            <button
              type="button"
              role="menuitem"
              className={itemClass}
              onClick={async () => {
                setOpen(false);
                await logout();
                navigate('/login');
              }}
            >
              <SignOut size={16} className="text-muted shrink-0" aria-hidden="true" />
              {t('common.logout')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
