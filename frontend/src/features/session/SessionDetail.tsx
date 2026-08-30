import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useSession, sessionKeys, useSessionTimeline, useStopSession, useStartSession, useRestartSession, useTerminateSession, useOwnSessionUsage } from '@/api/hooks/useSessions';
import { SessionUsagePanel, type UsageRange } from '@/components/SessionUsagePanel';
import { subscribeSessionEvents } from '@/lib/sse';
import { formatCredit, formatDuration, formatVram, hoursElapsed, sessionStatusLabel } from '@/lib/format';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, type ApiError, asApiError } from '@/lib/errors';
import { PageHeader } from '@/components/PageHeader';
import { CopyableId } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { TableSkeleton, ErrorState } from '@/components/EmptyState';
import { EmptyState } from '@/components/EmptyState';
import { Question } from '@/components/icons';
import { useConfirm } from '@/components/ConfirmDialog';
import { StatusPill } from '@/components/StatusPill';
import { Meter } from '@/components/Meter';

interface SessionEvent {
  type?: string;
  phase?: string;
  message?: string;
  ts?: string;
}

// Session detail. Live updates arrive over SSE (/sessions/{id}/events); if the connection fails,
// useSession falls back to polling every 4 seconds.
type SessWithMounts = {
  mounts?: { volume_id: string; name?: string | null; quota_gb?: number | null; mount_path: string; mode: string }[];
};

export function SessionDetail() {
  const { t } = useTranslation();
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: session, isLoading, isError, error, refetch } = useSession(id);
  const { data: timeline = [] } = useSessionTimeline(id);
  const pushToast = useUiStore((s) => s.pushToast);
  const stop = useStopSession();
  const start = useStartSession();
  const restart = useRestartSession();
  const terminate = useTerminateSession();
  const confirm = useConfirm();
  const [usageRange, setUsageRange] = useState<UsageRange>('15m');
  const lifecycleBusy = stop.isPending || start.isPending || restart.isPending || terminate.isPending;
  const [live, setLive] = useState(false);
  const [lastEvent, setLastEvent] = useState<SessionEvent | null>(null);
  // A one-second tick drives the estimated-spend line while the session is running.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Subscribe to SSE and invalidate the detail query on each event so the view updates immediately.
  // On failure, polling takes over.
  useEffect(() => {
    if (!id) return;
    if (session && ['terminated', 'error'].includes(session.status)) { setLive(false); return; }
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
  }, [id, qc, session?.status]);

  if (isLoading) return <div className="gs-card"><TableSkeleton rows={4} columns={2} /></div>;
  // A failed fetch is not "this session does not exist" - show the error and offer a retry;
  // only a clean 404 falls through to the not-found screen.
  if (isError && asApiError(error).status !== 404) {
    return <div className="gs-card"><ErrorState error={error} onRetry={() => refetch()} /></div>;
  }
  if (!session) {
    return (
      <EmptyState
        icon={<Question size={26} />}
        title={t('session.notFound')}
        description={t('session.notFoundHint')}
        action={<Link to="/sessions" className="gs-btn gs-btn-primary">{t('session.backToList')}</Link>}
      />
    );
  }

  const running = session.status === 'running';
  const paused = session.status === 'paused';
  // Not started yet: nothing to pause or resume, "terminate" reads as cancel.
  const pendingStart = session.status === 'pending' || session.status === 'preparing';
  const terminal = ['terminating', 'terminated', 'error'].includes(session.status);

  // Estimated spend so far: rate snapshot x occupancy x elapsed hours, the same arithmetic as the
  // list's cost column. Live only while running; anything else is frozen at terminated_at (or at
  // started_at when there is none). An estimate, labeled as one; settlement is the ledger's job.
  const rate = session.credit_per_hour_snapshot ?? 0;
  const estEndMs = session.status === 'running'
    ? now
    : session.terminated_at
      ? new Date(session.terminated_at).getTime()
      : session.started_at
        ? new Date(session.started_at).getTime()
        : now;
  const estSpend = rate && session.started_at
    ? rate * (session.occupancy ?? 1) * hoursElapsed(session.started_at, estEndMs)
    : null;

  // Scratch-disk gauge: a ~5-minute-stale kubelet reading the backend caches; shown only while
  // a recent sample exists. Warn at 80% (the backend notifies at the same threshold), danger at 95%.
  const diskUsed = session.disk_used_bytes;
  const diskLimit = session.disk_limit_bytes;
  const diskPct = diskUsed != null && diskLimit ? (diskUsed / diskLimit) * 100 : null;

  const runAction = (
    m: { mutate: (id: string, o: { onSuccess: () => void; onError: (e: unknown) => void }) => void },
    label: string,
  ) =>
    m.mutate(id, {
      onSuccess: () => pushToast('success', t('session.actionDone', { action: label })),
      onError: (e) => pushToast('error', humanizeError(e as ApiError)),
    });

  const terminateWithConfirm = async () => {
    const ok = await confirm({
      title: t('session.confirmTerminateTitle', { name: session.name || session.id }),
      body: t('session.confirmTerminateBody'),
      consequences: [t('session.consequenceData')],
      confirmLabel: pendingStart ? t('session.cancelPending') : t('session.terminate'),
      destructive: true,
    });
    if (!ok) return;
    const label = pendingStart ? t('session.cancelPending') : t('session.terminate');
    // A terminated session has nothing left to do here: settle back on the list, where it shows
    // under the ended tab, instead of leaving the user on a page whose only action is "back".
    terminate.mutate(id, {
      onSuccess: () => {
        pushToast('success', t('session.actionDone', { action: label }));
        navigate('/sessions', { replace: true });
      },
      onError: (e: unknown) => pushToast('error', humanizeError(e as ApiError)),
    });
  };

  return (
    <div>
      <PageHeader
        title={session.name || session.id}
        crumbs={[{ label: t('session.title'), to: '/sessions' }, { label: session.name || session.id }]}
        description={
          <span className="flex items-center gap-2 flex-wrap">
            <StatusPill kind={session.status} label={sessionStatusLabel(session.status)} />
            {session.status_reason && ['paused', 'terminating', 'terminated', 'error'].includes(session.status) && (
              <span
                className="gs-pill bg-warn-soft text-warn text-2xs"
                title={t(`enum.statusReason.${session.status_reason}Hint`, { defaultValue: '' })}
              >
                {t(`enum.statusReason.${session.status_reason}`, { defaultValue: session.status_reason })}
              </span>
            )}
            {!['terminated', 'error'].includes(session.status) && (
              <span
                className={`inline-flex items-center gap-1 text-2xs ${live ? 'text-free' : 'text-muted'}`}
                title={live ? t('session.liveConnected') : t('session.pollingMode')}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${live ? 'bg-free' : 'bg-border'}`} aria-hidden="true" />
                {live ? t('session.live') : t('session.polling')}
              </span>
            )}
          </span>
        }
        actions={
          // Per state, only what applies — one row: lifecycle + connect.
          <div className="flex gap-2 flex-wrap justify-end">
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
            {running && (
              <button type="button" className="gs-btn disabled:opacity-50" disabled={lifecycleBusy} onClick={() => runAction(restart, t('session.restart'))} title={t('session.restartHint')}>
                {t('session.restart')}
              </button>
            )}
            {!terminal && (
              <button type="button" className="gs-btn gs-btn-danger disabled:opacity-50" disabled={lifecycleBusy} onClick={terminateWithConfirm}>
                {pendingStart ? t('session.cancelPending') : t('session.terminate')}
              </button>
            )}
            {running ? (
              <Link to={`/sessions/${id}/connect`} className="gs-btn gs-btn-primary">{t('session.connect')}</Link>
            ) : !terminal ? (
              // A disabled button with no reason is a dead end; the title says what would enable it.
              <button
                type="button"
                className="gs-btn gs-btn-primary disabled:opacity-50"
                disabled
                title={t('session.connectOnlyWhenRunning', { status: sessionStatusLabel(session.status) })}
              >
                {t('session.connect')}
              </button>
            ) : null}
          </div>
        }
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="gs-card">
          <h2 className="font-bold mb-3">{t('session.resources')}</h2>
          <dl className="text-sm space-y-1.5">
            <div className="flex justify-between"><dt className="text-muted">{t('session.class')}</dt><dd>{session.resource_class}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">{t('session.sharing')}</dt><dd>{session.resource_class === 'gpu' ? (session.mode === 'mig' ? 'MIG' : t(session.mode === 'exclusive' ? 'session.modeExclusive' : 'session.modeShared')) : '-'}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">VRAM</dt><dd>{session.mode === 'exclusive' ? t('session.exclusiveOneCard') : formatVram(session.gpu_mem_mb)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">{t('session.cores')}</dt><dd>{session.mode === 'exclusive' ? '100%' : session.gpu_cores != null ? `${session.gpu_cores}%` : '-'}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">CPU</dt><dd>{session.cpu != null ? t('session.cpuCores', { count: session.cpu }) : '-'}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">RAM</dt><dd>{session.mem_gb != null ? `${session.mem_gb} GiB` : '-'}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">{t('session.disk')}</dt><dd>{session.disk_gb != null ? `${session.disk_gb} GiB` : '-'}</dd></div>
            {diskPct != null && diskLimit != null && (
              <div>
                <div className="flex justify-between">
                  <dt className="text-muted">{t('session.scratchDisk')}</dt>
                  <dd className="gs-num">
                    {t('session.scratchDiskEstimate', {
                      used: ((diskUsed ?? 0) / 2 ** 30).toFixed(1),
                      limit: Math.round(diskLimit / 2 ** 30),
                    })}
                  </dd>
                </div>
                <Meter
                  value={diskPct}
                  variant={diskPct >= 95 ? 'danger' : diskPct >= 80 ? 'warn' : 'free'}
                  className="mt-1"
                  label={t('session.scratchDisk')}
                />
              </div>
            )}
            {estSpend != null && (
              <div className="flex justify-between">
                <dt className="text-muted">{t('session.estimatedSpend')}</dt>
                <dd className="gs-num font-semibold">{t('session.estimatedSpendValue', { amount: formatCredit(Math.round(estSpend * 100) / 100) })}</dd>
              </div>
            )}
          </dl>
        </div>
        <div className="gs-card">
          <h2 className="font-bold mb-3">{t('session.meta')}</h2>
          <dl className="text-sm space-y-1.5">
            <div className="flex justify-between gap-2"><dt className="text-muted">ID</dt><dd className="min-w-0"><CopyableId value={session.id} /></dd></div>
            <div className="flex justify-between gap-2"><dt className="text-muted">{t('common.created')}</dt><dd><Timestamp value={session.created_at} /></dd></div>
            {((session as SessWithMounts).mounts ?? []).map((mnt) => (
              <div key={mnt.volume_id} className="flex justify-between gap-2">
                <dt className="text-muted">{t('session.volumesLabel')}</dt>
                <dd className="min-w-0 text-right">
                  <Link to="/data" className="text-primary hover:underline">{mnt.name || mnt.volume_id}</Link>
                  <span className="text-muted text-xs ml-1.5 gs-num">
                    {mnt.quota_gb != null ? `${mnt.quota_gb} GiB · ` : ''}{mnt.mode === 'ro' ? 'RO' : 'RW'} · {mnt.mount_path}
                  </span>
                </dd>
              </div>
            ))}
            {session.started_at && (
              <div className="flex justify-between gap-2"><dt className="text-muted">{t('session.startedAt')}</dt><dd><Timestamp value={session.started_at} /></dd></div>
            )}
            {(session.gpu_model || session.bound_gpu_uuid) && (
              <div className="flex justify-between gap-2">
                <dt className="text-muted">{t('session.boundGpu')}</dt>
                <dd className="min-w-0 truncate" title={session.bound_gpu_uuid ?? undefined}>
                  {session.gpu_model ?? session.bound_gpu_uuid}
                </dd>
              </div>
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

      {(session.started_at != null || running) && !terminal && (
        <div className="gs-card mt-4">
          <OwnUsagePanel session={session} running={running} range={usageRange} onRange={setUsageRange} />
        </div>
      )}

      {/* Lifecycle log: created → queued/preparing → running → … with reasons. Errors are readable
          here (OOM, disk limit, admission failures) instead of a separate message box. */}
      <div className="gs-card mt-4">
        <h2 className="font-bold mb-3">{t('session.eventsTitle')}</h2>
        {timeline.length === 0 ? (
          <p className="text-muted text-sm">{t('session.eventsEmpty')}</p>
        ) : (
          <ol className="space-y-0 max-h-[340px] overflow-y-auto pr-1">
            {[...timeline].reverse().map((e) => (
              <li key={e.id} className="flex items-baseline gap-3 py-1.5 border-b border-border/50 last:border-0 text-sm">
                <Timestamp value={e.at} className="gs-num text-xs text-muted shrink-0 w-56 whitespace-nowrap" />
                <span className={`shrink-0 font-semibold ${e.kind === 'error' ? 'text-danger' : e.kind === 'running' ? 'text-free' : ''}`}>
                  {t(`enum.sessionEvent.${e.kind}`, { defaultValue: e.kind })}
                </span>
                {e.reason && (
                  <span className="text-muted text-xs">
                    {t(`enum.statusReason.${e.reason}`, { defaultValue: e.reason })}
                  </span>
                )}
                {e.kind === 'promoted' && (() => {
                  // Realized queue wait: distance from the latest 'queued' event before this one.
                  const at = e.at;
                  const qa = at
                    ? timeline.filter((x) => x.kind === 'queued' && x.at != null && x.at <= at).pop()?.at
                    : undefined;
                  return at && qa ? (
                    <span className="text-muted text-xs">
                      {t('session.queueWaited', { duration: formatDuration(qa, new Date(at).getTime()) })}
                    </span>
                  ) : null;
                })()}
                {e.message && <span className="text-muted text-2xs truncate" title={e.message}>{e.message}</span>}
              </li>
            ))}
          </ol>
        )}
      </div>

    </div>
  );
}


// Data wiring for the shared usage panel on the user's own session page.
function OwnUsagePanel({ session, running, range, onRange }: {
  session: { id: string; resource_class?: string | null; cpu?: number | null; mem_gb?: number | null; gpu_mem_mb?: number | null; gpu_cores?: number | null };
  running: boolean;
  range: UsageRange;
  onRange: (r: UsageRange) => void;
}) {
  const { data: usage } = useOwnSessionUsage(running ? session.id : undefined);
  return (
    <SessionUsagePanel
      charts={false}
      limits={{
        isGpu: session.resource_class === 'gpu',
        cpu: session.cpu, mem_gb: session.mem_gb,
        gpu_mem_mb: session.gpu_mem_mb, gpu_cores: session.gpu_cores,
      }}
      usage={usage ?? undefined}
      range={range}
      onRange={onRange}
    />
  );
}
