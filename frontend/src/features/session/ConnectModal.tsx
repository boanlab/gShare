import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useSession, useSessionConnections } from '@/api/hooks/useSessions';
import { countdown } from '@/lib/format';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { CopyButton, CopyableId } from '@/components/CopyButton';
import { EmptyState, TableSkeleton } from '@/components/EmptyState';

// Connection details, on their own page at /sessions/:id/connect. Each short-lived connection token
// (10 minutes by default) is shown as a card with a countdown.
export function ConnectPage() {
  const { t } = useTranslation();
  const { id = '' } = useParams();
  // Connection tokens exist only for a running session; the request waits until it is up.
  const { data: session } = useSession(id);
  const running = session?.status === 'running';
  const { data: connections, isLoading, isFetching, refetch, dataUpdatedAt } = useSessionConnections(id, running);
  // Ticks the expiry countdown.
  const [, tick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const expired = (iso?: string | null) => !!iso && new Date(iso).getTime() <= Date.now();

  return (
    <div className="w-full">
      <PageHeader
        title={t('connect.title')}
        crumbs={[
          { label: t('session.title'), to: '/sessions' },
          { label: session?.name || t('session.title'), to: `/sessions/${id}` },
          { label: t('connect.title') },
        ]}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={
          <>
            <button type="button" className="gs-btn" onClick={() => refetch()}>{t('connect.reissue')}</button>
            <BackLink to={`/sessions/${id}`} label={t('connect.backToSession')} />
          </>
        }
      />
      <div className="gs-card">
        <p className="text-[12px] text-muted flex items-center gap-1 mb-3">
          {t('connect.sessionIdLabel')} <CopyableId value={id} />
        </p>
        {running && isLoading && <TableSkeleton rows={2} columns={2} />}
        <div className="space-y-3">
          {connections?.map((c, i) => {
            const dead = expired(c.expires_at);
            return (
              <div key={i} className={`border rounded-xl p-3 ${dead ? 'border-border opacity-60' : 'border-border'}`}>
                <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
                  <strong className="uppercase text-[12px]">{c.kind}</strong>
                  {c.expires_at && (
                    <span className={`text-[12px] ${dead ? 'text-danger font-semibold' : 'text-muted'}`} title={new Date(c.expires_at).toLocaleString()}>
                      {dead ? t('connect.expired') : t('connect.expiresIn', { time: countdown(c.expires_at) })}
                    </span>
                  )}
                </div>
                {c.url && (
                  <div className="flex items-center gap-2">
                    <a className="text-primary font-mono text-[12px] break-all block min-w-0" href={c.url} target="_blank" rel="noreferrer noopener">
                      {c.url} <span aria-hidden="true">↗</span>
                      <span className="gs-sr-only">{t('connect.opensNewTab')}</span>
                    </a>
                    <CopyButton value={c.url} label={t('connect.copyUrl')} />
                  </div>
                )}
                {c.command && (
                  <div className="flex items-start gap-2 mt-1">
                    <pre className="flex-1 min-w-0 font-mono text-[12px] text-muted whitespace-pre-wrap break-all">{c.command}</pre>
                    <CopyButton value={c.command} label={t('connect.copyCommand')} />
                  </div>
                )}
                {c.connection_token && (
                  <div className="mt-2">
                    <span className="text-[11px] font-semibold text-muted">{t('connect.oneTimeToken')}</span>
                    <div className="flex items-center gap-2 mt-1">
                      <code className="flex-1 min-w-0 font-mono text-[11px] bg-surface-2 border border-border rounded px-2 py-1 break-all">{c.connection_token}</code>
                      <CopyButton value={c.connection_token} label={t('connect.copyToken')} className="shrink-0" />
                    </div>
                  </div>
                )}
              </div>
            );
          })}
          {!running && session && (
            <EmptyState
              icon="⚯"
              title={t('connect.notRunning', { status: session.status })}
              description={t('connect.notRunningHint')}
              action={<Link to={`/sessions/${id}`} className="gs-btn gs-btn-primary">{t('connect.backToSession')}</Link>}
            />
          )}
          {running && !isLoading && connections?.length === 0 && (
            <EmptyState
              icon="⚯"
              title={t('connect.empty')}
              description={t('connect.emptyHint')}
              action={<Link to={`/sessions/${id}`} className="gs-btn">{t('connect.backToSession')}</Link>}
            />
          )}
        </div>
        <p className="text-muted text-[12px] mt-3">{t('connect.expiredHint')}</p>
      </div>
    </div>
  );
}
