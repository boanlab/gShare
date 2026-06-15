import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useSession, sessionKeys, useStopSession, useStartSession, useRestartSession } from '@/api/hooks/useSessions';
import { subscribeSessionEvents } from '@/lib/sse';
import { formatVram, sessionStatusLabel } from '@/lib/format';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, type ApiError } from '@/lib/errors';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { CopyableId } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { TableSkeleton } from '@/components/EmptyState';
import { EmptyState } from '@/components/EmptyState';

interface SessionEvent {
  type?: string;
  phase?: string;
  message?: string;
  ts?: string;
}

// Session detail. Live updates arrive over SSE (/sessions/{id}/events); if the connection fails,
// useSession falls back to polling every 4 seconds.
export function SessionDetail() {
  const { t } = useTranslation();
  const { id = '' } = useParams();
  const qc = useQueryClient();
  const { data: session, isLoading, isFetching, refetch, dataUpdatedAt } = useSession(id);
  const pushToast = useUiStore((s) => s.pushToast);
  const stop = useStopSession();
  const start = useStartSession();
  const restart = useRestartSession();
  const lifecycleBusy = stop.isPending || start.isPending || restart.isPending;
  const [live, setLive] = useState(false);
  const [lastEvent, setLastEvent] = useState<SessionEvent | null>(null);

  // Subscribe to SSE and invalidate the detail query on each event so the view updates immediately.
  // On failure, polling takes over.
  useEffect(() => {
    if (!id) return;
    const unsubscribe = subscribeSessionEvents<SessionEvent>(id, {
      onOpen: () => setLive(true),
      onMessage: (data) => {
        setLastEvent(data);
        qc.invalidateQueries({ queryKey: sessionKeys.detail(id) });
      },
      onError: () => setLive(false),
    });
    return () => {
      setLive(false);
      unsubscribe();
    };
  }, [id, qc]);

  if (isLoading) return <div className="gs-card"><TableSkeleton rows={4} columns={2} /></div>;
  if (!session) {
    return (
      <EmptyState
        icon="?"
        title={t('session.notFound')}
        description={t('session.notFoundHint')}
        action={<Link to="/sessions" className="gs-btn gs-btn-primary">{t('session.backToList')}</Link>}
      />
    );
  }

  const running = session.status === 'running';
  const paused = session.status === 'paused';

  const runAction = (
    m: { mutate: (id: string, o: { onSuccess: () => void; onError: (e: unknown) => void }) => void },
    label: string,
  ) =>
    m.mutate(id, {
      onSuccess: () => pushToast('success', t('session.actionDone', { action: label })),
      onError: (e) => pushToast('error', humanizeError(e as ApiError)),
    });

  return (
    <div>
      <PageHeader
        title={session.name || session.id}
        crumbs={[{ label: t('session.title'), to: '/sessions' }, { label: session.name || session.id }]}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        description={
          <span className="flex items-center gap-2 flex-wrap">
            {t('session.statusPrefix', { status: sessionStatusLabel(session.status) })}
            <span
              className={`inline-flex items-center gap-1 text-[11px] ${live ? 'text-free' : 'text-muted'}`}
              title={live ? t('session.liveConnected') : t('session.pollingMode')}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${live ? 'bg-free' : 'bg-border'}`} aria-hidden="true" />
              {live ? t('session.live') : t('session.polling')}
            </span>
          </span>
        }
        actions={
          <>
            <BackLink to="/sessions" label={t('session.backToList')} />
            {running && (
              <button type="button" className="gs-btn disabled:opacity-50" disabled={lifecycleBusy} onClick={() => runAction(stop, t('session.pause'))}>
                {t('session.pause')}
              </button>
            )}
            {paused && (
              <button type="button" className="gs-btn disabled:opacity-50" disabled={lifecycleBusy} onClick={() => runAction(start, t('session.resume'))}>
                {t('session.resume')}
              </button>
            )}
            {(running || paused) && (
              <button type="button" className="gs-btn disabled:opacity-50" disabled={lifecycleBusy} onClick={() => runAction(restart, t('session.restart'))}>
                {t('session.restart')}
              </button>
            )}
            {running ? (
              <Link to={`/sessions/${id}/connect`} className="gs-btn gs-btn-primary">{t('session.connect')}</Link>
            ) : (
              // A disabled button with no reason is a dead end; the title says what would enable it.
              <button
                type="button"
                className="gs-btn gs-btn-primary disabled:opacity-50"
                disabled
                title={t('session.connectOnlyWhenRunning', { status: sessionStatusLabel(session.status) })}
              >
                {t('session.connect')}
              </button>
            )}
          </>
        }
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="gs-card">
          <h2 className="font-bold mb-3">{t('session.resources')}</h2>
          <dl className="text-[13px] space-y-1.5">
            <div className="flex justify-between"><dt className="text-muted">{t('session.class')}</dt><dd>{session.resource_class}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">{t('session.sharing')}</dt><dd>{session.resource_class === 'gpu' ? (session.mode === 'mig' ? 'MIG' : t(session.mode === 'exclusive' ? 'session.modeExclusive' : 'session.modeShared')) : '-'}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">VRAM</dt><dd>{session.mode === 'exclusive' ? t('session.exclusiveOneCard') : formatVram(session.gpu_mem_mb)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">{t('session.cores')}</dt><dd>{session.mode === 'exclusive' ? '100%' : session.gpu_cores != null ? `${session.gpu_cores}%` : '-'}</dd></div>
          </dl>
        </div>
        <div className="gs-card">
          <h2 className="font-bold mb-3">{t('session.meta')}</h2>
          <dl className="text-[13px] space-y-1.5">
            <div className="flex justify-between gap-2"><dt className="text-muted">ID</dt><dd className="min-w-0"><CopyableId value={session.id} /></dd></div>
            <div className="flex justify-between gap-2"><dt className="text-muted">{t('common.created')}</dt><dd><Timestamp value={session.created_at} /></dd></div>
            {session.started_at && (
              <div className="flex justify-between gap-2"><dt className="text-muted">{t('session.startedAt')}</dt><dd><Timestamp value={session.started_at} /></dd></div>
            )}
            {session.bound_gpu_uuid && (
              <div className="flex justify-between gap-2"><dt className="text-muted">{t('session.boundGpu')}</dt><dd className="min-w-0"><CopyableId value={session.bound_gpu_uuid} /></dd></div>
            )}
            {lastEvent && (
              <div className="flex justify-between">
                <dt className="text-muted">{t('session.lastEvent')}</dt>
                <dd>{lastEvent.phase ?? lastEvent.type ?? lastEvent.message ?? '-'}</dd>
              </div>
            )}
          </dl>
        </div>
      </div>

    </div>
  );
}
