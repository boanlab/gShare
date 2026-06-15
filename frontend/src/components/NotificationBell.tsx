import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useNotifications, useMarkRead, useMarkAllRead, notificationKeys, type Notification } from '@/api/hooks/useNotifications';
import { subscribeNotifications } from '@/lib/sse';
import { useTranslation } from 'react-i18next';
import { formatDateTime } from '@/lib/format';

// Maps a notification's type and ref to the screen it should open.
function hrefFor(n: Notification): string | null {
  const ref = (n.ref ?? {}) as Record<string, string>;
  const t = n.type ?? '';
  if (ref.session_id) return `/sessions/${ref.session_id}`;
  if (t.startsWith('credit_allocation') || t.startsWith('budget')) {
    // The requester (approved or rejected) goes to their wallet; the approver (a request arrived)
    // goes to credit allocation.
    return t.endsWith('approved') || t.endsWith('rejected') ? '/wallet' : '/admin/allocations';
  }
  if (t.startsWith('credit_topup')) return t.endsWith('request') ? '/admin/allocations' : '/wallet';
  if (t.startsWith('volume')) return '/data';
  if (t === 'cluster_health') return '/admin/clusters';
  if (t === 'node_health') return '/admin/nodes';
  if (t.startsWith('membership')) return '/account';
  return null;
}

// The top bar's notification bell: list, unread badge, mark-as-read, and deep links. Backed by
// useNotifications, which polls every 30 seconds.
export function NotificationBell() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: items = [], isLoading } = useNotifications();
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();
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
        type="button"
        className="relative w-[34px] h-[34px] max-md:w-11 max-md:h-11 rounded-lg border border-border bg-surface-2 grid place-items-center"
        title={unread > 0 ? t('notifications.unreadCount', { count: unread }) : t('notifications.title')}
        aria-label={unread > 0 ? t('notifications.unreadCount', { count: unread }) : t('notifications.title')}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span aria-hidden="true">🔔</span>
        {unread > 0 && (
          <span
            aria-hidden="true"
            className="absolute -top-1 -right-1 min-w-4 h-4 px-1 rounded-full bg-danger text-white text-[10px] grid place-items-center"
          >
            {unread}
          </span>
        )}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-2 z-40 w-[340px] max-h-[420px] overflow-y-auto bg-surface border border-border rounded-xl shadow-card">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <span className="font-bold text-[13px]">{t('notifications.title')}</span>
              <button
                type="button"
                className="text-primary text-[12px] font-semibold disabled:opacity-40"
                disabled={unread === 0 || markAll.isPending}
                onClick={() => markAll.mutate()}
              >
                {t('notifications.markAllRead')}
              </button>
            </div>
            {isLoading ? (
              <p className="px-4 py-6 text-center text-muted text-[13px]">{t('common.loading')}</p>
            ) : items.length === 0 ? (
              <p className="px-4 py-6 text-center text-muted text-[13px]">{t('notifications.empty')}</p>
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
                        <div className="font-semibold text-[13px] truncate">{n.title}</div>
                        {n.body && <div className="text-muted text-[12px] mt-0.5">{n.body}</div>}
                        <div className="text-muted text-[11px] mt-1">{formatDateTime(n.created_at)}</div>
                      </button>
                      {n.read_at == null && (
                        <button
                          type="button"
                          className="shrink-0 text-primary text-[11px] font-semibold"
                          onClick={(e) => { e.stopPropagation(); markRead.mutate(n.id); }}
                        >
                          {t('notifications.markRead')}
                        </button>
                      )}
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
