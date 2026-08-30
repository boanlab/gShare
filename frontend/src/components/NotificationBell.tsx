import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useNotifications, useMarkRead, useMarkAllRead, useDeleteNotification, useDeleteAllNotifications, notificationKeys, type Notification } from '@/api/hooks/useNotifications';
import { subscribeNotifications } from '@/lib/sse';
import { useTranslation } from 'react-i18next';
import { Bell, X } from './icons';
import { useAnchoredMenu } from '@/hooks/useAnchoredMenu';
import { formatDateTime } from '@/lib/format';

// Maps a notification's type and ref to the screen it should open.
function hrefFor(n: Notification): string | null {
  const ref = (n.ref ?? {}) as Record<string, string>;
  const t = n.type ?? '';
  if (ref.session_id) return `/sessions/${ref.session_id}`;
  if (t.startsWith('credit_allocation')) {
    // The requester (approved or rejected) goes to their wallet; the approver (a request arrived)
    // goes to credit allocation.
    return t.endsWith('approved') || t.endsWith('rejected') ? '/wallet' : '/admin/allocations';
  }
  // Budget alerts have no dedicated console screen yet; wallet is the closest actionable view.
  if (t.startsWith('budget')) return '/wallet';
  if (t.startsWith('credit_topup')) return t.endsWith('request') ? '/admin/allocations' : '/wallet';
  // Everything credit/balance-flavoured lands on the wallet: low_balance, credit_exhausted,
  // credit_refill, credit_transaction previously dead-ended as non-clickable rows.
  if (t === 'low_balance' || t.startsWith('credit')) return '/wallet';
  if (t.startsWith('volume')) return '/data';
  if (t === 'cluster_health') return '/admin/clusters';
  if (t === 'node_health') return '/admin/nodes';
  if (t.startsWith('membership') || t === 'org_admin_added') return '/account';
  // Boards: an answered inquiry goes to MY support thread; a new/reopened one to the admin inbox;
  // a posted notice to the notice board.
  if (t === 'inquiry_answered') return '/support';
  if (t === 'inquiry_created') return '/admin/inquiries';
  if (t === 'notice_posted') return '/notices';
  return null;
}

// Backend notifications store English fallback text plus structured params; the console renders
// its own locale template notif.<type>.title/body (context = reason/scope variant) and falls back
// to the stored text for unknown or legacy types.
function useLocalizedNotification() {
  const { t } = useTranslation();
  return (n: Notification): { title: string; body?: string } => {
    const p = (n.params ?? {}) as Record<string, unknown>;
    const ctx =
      (typeof p.reason === 'string' && p.reason) ||
      (typeof p.scope === 'string' && p.scope) ||
      (p.escalated ? 'escalated' : undefined) || undefined;
    // Legacy rows predate params: their templates would render literal {{placeholders}} —
    // any unresolved placeholder means "fall back to the stored English text".
    const or = (rendered: string, stored: string | undefined) =>
      rendered.includes('{{') ? (stored ?? rendered) : rendered;
    const title = or(t(`notif.${n.type}.title`, { ...p, context: ctx, defaultValue: n.title }), n.title);
    let body: string | undefined =
      or(t(`notif.${n.type}.body`, { ...p, context: ctx, defaultValue: n.body ?? '' }), n.body) || undefined;
    // The operator's raw error message is untranslatable free text — append it as-is.
    if (n.type === 'session_error' && p.message && body) body = `${body} (${String(p.message)})`;
    return { title, body };
  };
}

// The top bar's notification bell: list, unread badge, mark-as-read, and deep links. Backed by
// useNotifications, which polls every 30 seconds.
export function NotificationBell() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const PANEL_WIDTH = 340;
  const pos = useAnchoredMenu(open, triggerRef, PANEL_WIDTH);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: items = [], isLoading } = useNotifications();
  const localize = useLocalizedNotification();
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
  const delOne = useDeleteNotification();
  const delAll = useDeleteAllNotifications();
  const unread = items.filter((n) => n.read_at == null).length;

  // Refresh the list the moment a push arrives; the 30-second poll stays as a fallback.
  useEffect(() => {
    const unsub = subscribeNotifications({
      onMessage: () => qc.invalidateQueries({ queryKey: notificationKeys.all }),
    });
    return unsub;
  }, [qc]);

  const onItemClick = (n: Notification) => {
    if (n.read_at == null) markRead.mutate(n.id);
    const href = hrefFor(n);
    setOpen(false);
    if (href) navigate(href);
  };

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        className="relative w-[34px] h-[34px] max-md:w-11 max-md:h-11 rounded-ctl border border-border bg-surface
                   text-muted grid place-items-center hover:text-text hover:bg-surface-2 transition-colors duration-150"
        title={unread > 0 ? t('notifications.unreadCount', { count: unread }) : t('notifications.title')}
        aria-label={unread > 0 ? t('notifications.unreadCount', { count: unread }) : t('notifications.title')}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Bell size={17} aria-hidden="true" />
        {unread > 0 && (
          <span
            aria-hidden="true"
            className="absolute -top-1 -right-1 min-w-[17px] h-[17px] px-1 rounded-full bg-danger text-white
                       text-2xs font-semibold grid place-items-center tabular-nums"
          >
            {unread}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          {/* Fixed to the viewport, not absolute in the topbar: the topbar is an overflow-x-auto
              scroll container, which clips absolutely-positioned children. Coordinates come from
              the bell itself so the panel hangs off the control that was clicked. */}
          <div
            style={pos ? { top: pos.top, right: pos.right } : { visibility: 'hidden' }}
            className="fixed z-40 w-[340px] max-w-[calc(100vw-16px)] max-h-[70vh] overflow-y-auto bg-surface border border-border rounded-card shadow-raised"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <span className="font-bold text-sm">{t('notifications.title')}</span>
              <span className="inline-flex items-center gap-3">
                <button
                  type="button"
                  className="text-primary text-xs font-semibold disabled:opacity-40"
                  disabled={unread === 0 || markAll.isPending}
                  onClick={() => markAll.mutate()}
                >
                  {t('notifications.markAllRead')}
                </button>
                <button
                  type="button"
                  className="text-danger text-xs font-semibold disabled:opacity-40"
                  disabled={items.length === 0 || delAll.isPending}
                  onClick={() => delAll.mutate()}
                >
                  {t('notifications.deleteAll')}
                </button>
              </span>
            </div>
            {isLoading ? (
              <p className="px-4 py-6 text-center text-muted text-sm">{t('common.loading')}</p>
            ) : items.length === 0 ? (
              <p className="px-4 py-6 text-center text-muted text-sm">{t('notifications.empty')}</p>
            ) : (
              <ul>
                {items.map((n) => (
                  <li
                    key={n.id}
                    className={`px-4 py-3 border-b border-border last:border-0 ${n.read_at == null ? 'bg-surface-2' : ''}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <button
                        type="button"
                        className="min-w-0 text-left flex-1 cursor-pointer"
                        onClick={() => onItemClick(n)}
                        title={hrefFor(n) ? t('notifications.openHint') : undefined}
                      >
                        {(() => { const loc = localize(n); return (<>
                          <div className="font-semibold text-sm truncate">{loc.title}</div>
                          {loc.body && <div className="text-muted text-xs mt-0.5">{loc.body}</div>}
                        </>); })()}
                        <div className="text-muted text-2xs mt-1">{formatDateTime(n.created_at)}</div>
                      </button>
                      <span className="shrink-0 inline-flex items-center gap-2">
                        {n.read_at == null && (
                          <button
                            type="button"
                            className="text-primary text-2xs font-semibold"
                            onClick={(e) => { e.stopPropagation(); markRead.mutate(n.id); }}
                          >
                            {t('notifications.markRead')}
                          </button>
                        )}
                        <button
                          type="button"
                          className="text-muted hover:text-danger p-0.5"
                          aria-label={t('notifications.deleteOne')}
                          title={t('notifications.deleteOne')}
                          onClick={(e) => { e.stopPropagation(); delOne.mutate(n.id); }}
                        >
                          <X size={13} aria-hidden="true" />
                        </button>
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
