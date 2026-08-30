import { useMemo, useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  useAllSessions,
  useAdminQueue,
  useClusterMetrics,
  useForceTerminate,
  useSetQueuePriority,
  useSessionsStream,
} from '@/api/hooks/useMonitor';
import { Table, TableToolbar, Pagination, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useConfirm } from '@/components/ConfirmDialog';
import { useBulkTerminateSessions } from '@/api/hooks/useSessions';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useAuthStore } from '@/auth/authStore';
import { useProjects } from '@/api/hooks/useGroups';
import type { components } from '@/api/schema';
import { PageHeader } from '@/components/PageHeader';
import { DisabledReason } from '@/components/Field';
import { usePrompt } from '@/components/PromptDialog';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatVram, sessionStatusLabel } from '@/lib/format';
import { Cube } from '@/components/icons';
import { StatusPill } from '@/components/StatusPill';
import { useSessionTimeline } from '@/api/hooks/useSessions';
import { Figure } from '@/components/Figure';
import { Tabs } from '@/components/Tabs';
import { SessionMonitorOverlay } from '@/features/admin/SessionMonitorDetail';

// Session and scheduler monitoring.
// Live updates come from SSE (/sessions/events); on disconnect useAllSessions and useAdminQueue fall
// back to polling via livePaused.

export interface SessionRow {
  id: string;
  name: string;
  status: string;
  owner_user_id?: string;
  owner_name?: string;
  group_id?: string;
  group_name?: string | null;
  org_id?: string | null;
  org_name?: string | null;
  resource_class?: string;
  mode?: string;
  gpu_mem_mb?: number;
  gpu_cores?: number;
  gpu_model?: string | null;
  status_reason?: string | null;
  cpu?: number;
  mem_gb?: number;
  disk_gb?: number;
  created_at?: string;
  started_at?: string | null;
  terminated_at?: string | null;
  status_changed_at?: string | null;
}
type QueueRow = components['schemas']['QueueEntryView'];

const MONITOR_PAGE = 25;

export function AdminMonitor() {
  const { t } = useTranslation();
  const [detail, setDetail] = useState<SessionRow | null>(null);
  // Status, search and sort in the URL, so a view survives Back and travels in a link.
  const table = useTableState('', { sort: 'started', dir: 'desc' });
  // Sessions vs queue as page tabs (?view=): the queue stays a first-class screen because its
  // per-row priority action reorders waiting sessions — the list's '대기중' rows cannot do that.
  const [viewParams, setViewParams] = useSearchParams();
  const view = viewParams.get('view') === 'queue' ? 'queue' : 'sessions';
  const setView = (v: string) => setViewParams((prev) => {
    const next = new URLSearchParams(prev);
    if (v === 'sessions') next.delete('view'); else next.set('view', v);
    return next;
  }, { replace: true });
  // Selection for bulk force-terminate.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const bulkTerm = useBulkTerminateSessions();
  const confirm = useConfirm();
  const queueTable = useTableState('q', { sort: 'position', dir: 'asc' });
  const statusFilter = table.tab ?? '';
  const setStatusFilter = (v: string) => table.setTab(v || null);

  const { connected } = useSessionsStream({ scope: 'all' });
  const livePaused = !connected; // a dropped SSE connection turns polling back on

  // /metrics/cluster and /nodes are super_admin only, so other roles never call them.
  const isSuper = useAuthStore((s) => s.claims.global_role === 'super_admin');
  const metricsQ = useClusterMetrics({}, { enabled: isSuper });
  const sessionsQ = useAllSessions({ status: statusFilter || undefined }, livePaused);
  const queueQ = useAdminQueue({ status: 'queued' }, livePaused);

  const sessions = useMemo(() => (sessionsQ.data ?? []) as SessionRow[], [sessionsQ.data]);
  // Row click opens the detail drawer: the row itself can never carry WHY a session errored.
  // GET /queue has no status filter server-side; count the actually-queued entries here so the
  // heading does not include promoted/expired rows.
  const queueAll = queueQ.data ?? [];
  const queue = queueAll.filter((q: { status?: string }) => !q.status || q.status === 'queued');

  // Resolve group ids to names. Monitoring requires group_admin or above, so the accessible groups
  // are fetched here.
  const projects = useProjects().data ?? [];
  const groupName = (id?: string) => (id ? projects.find((p) => p.id === id)?.name ?? id : undefined);

  const metrics = metricsQ.data as
    | {
        nodes?: Record<string, number>;
        gpu?: { device_total?: number; vram_used_mb?: number; vram_total_mb?: number; vram_load_pct?: number; avg_utilization_pct?: number; empty_gpu_count?: number };
        sessions?: { running?: number; queued?: number };
        credit?: { consumed_last_24h?: string; active_holds?: string };
      }
    | undefined;

  const setPriority = useSetQueuePriority();
  const pushToast = useUiStore((s) => s.pushToast);
  const promptDialog = usePrompt();

  const onPriority = async (q: QueueRow) => {
    const raw = await promptDialog({
      title: t('admin.monitor.priorityPrompt', { current: q.priority }),
      defaultValue: String(q.priority),
      inputType: 'number',
      required: true,
    });
    if (raw == null) return;
    const priority = Number(raw);
    if (Number.isNaN(priority)) {
      pushToast('error', t('admin.monitor.priorityInvalid'));
      return;
    }
    setPriority.mutate(
      { entryId: q.id, priority },
      {
        onSuccess: () => pushToast('success', t('admin.monitor.priorityChanged', { id: q.session_id, priority })),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  const sessionColumns: Column<SessionRow>[] = useMemo(
    () => [
      {
        key: 'name',
        header: t('admin.monitor.colSession'),
        sortBy: (s) => s.name ?? s.id,
        render: (s) => (
          <div className="min-w-0">
            <b>{s.name}</b>
            <div className="flex items-center gap-1 text-muted text-xs">
              <code className="font-mono truncate max-w-[150px]" title={s.id}>{s.id}</code>
              <CopyButton value={s.id} label={t('admin.monitor.copySessionId')} />
            </div>
          </div>
        ),
      },
      {
        key: 'status',
        header: t('common.status'),
        sortBy: (s) => s.status,
        render: (s) => <StatusPill kind={s.status} label={sessionStatusLabel(s.status)} />,
      },
      // Name only: the raw user id doubled the column width for a value nobody reads here — it
      // lives in the title (hover) and on the user admin screen.
      { key: 'owner_id', header: t('admin.monitor.colOwner'), sortBy: (s) => s.owner_name ?? s.owner_user_id ?? '', render: (s) => s.owner_name
          ? <span className="truncate" title={s.owner_user_id ?? undefined}>{s.owner_name}</span>
          : <span className="font-mono text-xs">{s.owner_user_id ?? '-'}</span> },
      {
        key: 'org',
        header: t('common.organization'),
        render: (s) => s.org_name
          ? <span className="text-xs">{s.org_name}</span>
          : <span className="text-muted text-xs">-</span>,
      },
      {
        key: 'group',
        header: t('common.group'),
        render: (s) => {
          const name = s.group_name ?? groupName(s.group_id);
          return name ? <span className="text-xs">{name}</span> : <span className="text-muted text-xs">-</span>;
        },
      },
      {
        key: 'resource',
        header: t('admin.monitor.colResource'),
        render: (s) => (
          <span className="inline-flex flex-col leading-tight text-xs">
            <span>
              {s.resource_class ?? '-'}
              {s.mode ? ` · ${s.mode}` : ''}
              {s.gpu_mem_mb ? ` · ${formatVram(s.gpu_mem_mb)}` : ''}
              {s.gpu_mem_mb && s.gpu_cores != null ? <span className="text-muted gs-num"> ({s.gpu_cores}%)</span> : null}
            </span>
            {(s.cpu != null || s.mem_gb != null || s.disk_gb != null) && (
              <span className="text-muted gs-num">
                {[s.cpu != null ? `${s.cpu}c` : null, s.mem_gb != null ? `${s.mem_gb}GiB` : null, s.disk_gb != null ? `${s.disk_gb}GB` : null].filter(Boolean).join(' · ')}
              </span>
            )}
          </span>
        ),
      },
      {
        // When the status last changed: running → started, error/paused/terminated → when it
        // happened. Falls back to older rows' nearest timestamp.
        key: 'status_changed_at',
        header: t('admin.monitor.colLastChange'),
        sortBy: (s) => {
          const v = s.status_changed_at ?? s.terminated_at ?? s.started_at ?? s.created_at;
          return v ? new Date(v).getTime() : 0;
        },
        align: 'right',
        render: (s) => (
          <Timestamp
            value={s.status_changed_at ?? s.terminated_at ?? s.started_at ?? s.created_at}
            className="text-muted"
          />
        ),
      },
      {
        key: 'actions',
        header: t('admin.monitor.colActions'),
        sortable: false,
        align: 'right',
        render: (s) =>
          s.status === 'terminated' ? (
            <span className="text-muted text-xs">-</span>
          ) : s.status === 'error' ? (
            // An error session is already dead (pod and CR gone, credit settled); what is left for
            // an admin is clearing any residue and filing the row — that is not a "terminate".
            <Link
              to={`/admin/monitor/sessions/${s.id}/terminate`}
              className="gs-btn gs-btn-sm"
              title={t('admin.monitor.cleanupHint')}
            >
              {t('admin.monitor.cleanup')}
            </Link>
          ) : (
            <Link to={`/admin/monitor/sessions/${s.id}/terminate`} className="gs-btn gs-btn-sm gs-btn-danger">{t('admin.monitor.forceTerminate')}</Link>
          ),
      },
    ],
    [projects], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const matchedSessions = useMemo(() => {
    const q = table.query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((r) => `${r.name ?? ''} ${r.id} ${r.owner_name ?? ''}`.toLowerCase().includes(q));
  }, [sessions, table.query]);
  const STATUS_RANK: Record<string, number> = { running: 0, preparing: 1, pending: 2, paused: 3, terminating: 4, error: 5, terminated: 6 };
  const sessionRows = useMemo(() => {
    const acc = sortAccessor(sessionColumns, table.sort);
    if (acc) return sortRows(matchedSessions, acc, table.dir);
    // Default order: running first, then the rest by recency — the fleet operator's reading order.
    return [...matchedSessions].sort((a, b) =>
      (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9)
      || new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime());
  }, [matchedSessions, sessionColumns, table.sort, table.dir]);  // eslint-disable-line react-hooks/exhaustive-deps
  const pagedSessions = useMemo(
    () => sessionRows.slice((table.page - 1) * MONITOR_PAGE, table.page * MONITOR_PAGE),
    [sessionRows, table.page],
  );
  const selectedLive = useMemo(
    () => sessionRows.filter((s) => selected.has(s.id) && !['terminated', 'error'].includes(s.status)),
    [sessionRows, selected],
  );

  const terminateSelected = async () => {
    const ok = await confirm({
      title: t('admin.monitor.confirmBulkTitle', { count: selectedLive.length }),
      body: t('admin.monitor.confirmBulkBody'),
      consequences: selectedLive.slice(0, 6).map((s) => `${s.name ?? s.id} - ${s.owner_name ?? s.owner_user_id ?? ''}`),
      confirmLabel: t('admin.monitor.forceTerminate'),
      destructive: true,
      // Force-terminating other people's work is not a click to make casually.
      confirmText: String(selectedLive.length),
    });
    if (!ok) return;
    bulkTerm.mutate(selectedLive.map((s) => s.id), {
      onSuccess: () => { pushToast('success', t('admin.monitor.bulkTerminated', { count: selectedLive.length })); setSelected(new Set()); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const queueColumns: Column<QueueRow>[] = useMemo(
    () => [
      { key: 'position', header: '#', sortBy: (q) => q.position ?? 0, align: 'right', render: (q) => <b>{q.position ?? '-'}</b> },
      {
        key: 'session_id',
        header: t('admin.monitor.colSession'),
        render: (q) => (
          <span className="inline-flex items-center gap-1 min-w-0">
            <code className="font-mono text-xs truncate max-w-[160px]" title={q.session_id}>{q.session_id}</code>
            <CopyButton value={q.session_id} label={t('admin.monitor.copySessionId')} />
          </span>
        ),
      },
      { key: 'priority', header: t('admin.monitor.colPriority'), sortBy: (q) => q.priority ?? 0, align: 'right', render: (q) => <b>{q.priority}</b> },
      { key: 'enqueued_at', header: t('admin.monitor.colEnqueued'), sortBy: (q) => (q.enqueued_at ? new Date(q.enqueued_at).getTime() : 0), align: 'right', render: (q) => <Timestamp value={q.enqueued_at} className="text-muted" /> },
      {
        key: 'actions',
        header: t('admin.monitor.colActions'),
        render: (q) =>
          q.status === 'queued' ? (
            <button type="button" className="gs-btn gs-btn-sm" onClick={() => onPriority(q)} disabled={setPriority.isPending}>
              {t('admin.monitor.priority')}
            </button>
          ) : (
            <span className="text-muted text-xs">-</span>
          ),
      },
    ],
    [setPriority.isPending], // eslint-disable-line react-hooks/exhaustive-deps
  );

  return (
    <div>
      <PageHeader
        title={t('admin.monitor.title')}
        description={t('admin.monitor.subtitle')}
        actions={
          <StatusPill
            kind={connected ? 'live' : 'polling'}
            label={connected ? t('admin.monitor.live') : t('admin.monitor.pollingFallback')}
          />
        }
      />

      {isSuper && (
        <section className="gs-panel grid md:grid-cols-4 mb-5">
          <Figure label={t('admin.monitor.runningSessions')} value={metrics?.sessions?.running ?? '-'} />
          <Figure label={t('admin.monitor.queuedSessions')} value={metrics?.sessions?.queued ?? '-'} />
          <Figure
            label={t('admin.monitor.vramPacking')}
            value={metrics?.gpu?.vram_load_pct != null ? `${metrics.gpu.vram_load_pct}%` : '-'}
            foot={metrics?.gpu ? `${formatVram(metrics.gpu.vram_used_mb)} / ${formatVram(metrics.gpu.vram_total_mb)}` : undefined}
          />
          <Figure
            label={t('admin.monitor.consumed24h')}
            value={metrics?.credit?.consumed_last_24h ?? '-'}
            foot={metrics?.credit?.active_holds ? t('admin.monitor.heldAmount', { amount: metrics.credit.active_holds }) : undefined}
          />
        </section>
      )}

      <Tabs
        ariaLabel={t('admin.monitor.title')}
        items={[
          { key: 'sessions', label: t('admin.monitor.tabSessions'), count: sessions.length },
          { key: 'queue', label: t('admin.monitor.tabQueue'), count: queue.length },
        ]}
        active={view}
        onChange={setView}
      />

      {view === 'sessions' && (
      <div className="gs-card mb-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-bold">{t('admin.monitor.sessionsHeading', { count: sessions.length })}</h2>
          <label className="gs-sr-only" htmlFor="gs-monitor-status">{t('admin.monitor.allStatuses')}</label>
          <Select id="gs-monitor-status" data-url-state className="gs-input w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">{t('admin.monitor.allStatuses')}</option>
            <option value="running">{t('enum.sessionStatus.running')}</option>
            <option value="preparing">{t('enum.sessionStatus.preparing')}</option>
            <option value="pending">{t('enum.sessionStatus.pending')}</option>
            <option value="paused">{t('enum.sessionStatus.paused')}</option>
            <option value="error">{t('enum.sessionStatus.error')}</option>
          </Select>
        </div>
        <TableToolbar
          query={table.query}
          onQueryChange={table.setQuery}
          placeholder={t('admin.monitor.searchPlaceholder')}
          total={sessions.length}
          shown={matchedSessions.length}
          onClear={table.clear}
        >
          {selectedLive.length > 0 && (
            <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={bulkTerm.isPending} onClick={terminateSelected}>
              {t('admin.monitor.terminateSelected', { count: selectedLive.length })}
            </button>
          )}
        </TableToolbar>
        {sessionsQ.isLoading ? (
          <TableSkeleton rows={5} columns={5} />
        ) : sessionRows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon={<Cube size={26} />} title={t('admin.monitor.emptySessions')} />
        ) : (
          <>
            <Table
              caption={t('admin.monitor.sessionsHeading', { count: sessions.length })}
              columns={sessionColumns}
              rows={pagedSessions}
              rowKey={(s) => s.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
              selected={selected}
              onSelectedChange={setSelected}
              selectable={(s) => !['terminated', 'error'].includes(s.status)}
              onRowClick={setDetail}
            />
            <Pagination page={table.page} pageSize={MONITOR_PAGE} total={sessionRows.length} onPage={table.setPage} />
          </>
        )}
      </div>
      )}

      {view === 'queue' && (
      <div className="gs-card mb-5">
        <h2 className="font-bold mb-3">{t('admin.monitor.queueHeading', { count: queue.length })}</h2>
        {queueQ.isLoading ? (
          <TableSkeleton rows={3} columns={4} />
        ) : (
          <Table
            caption={t('admin.monitor.queueHeading', { count: queue.length })}
            columns={queueColumns}
            rows={sortRows(queue, sortAccessor(queueColumns, queueTable.sort), queueTable.dir)}
            rowKey={(q) => q.id}
            empty={t('admin.monitor.emptyQueue')}
            sort={queueTable.sort}
            dir={queueTable.dir}
            onSort={queueTable.toggleSort}
          />
        )}
        <p className="text-muted text-2xs mt-3">{t('admin.monitor.reorderNote')}</p>
      </div>
      )}

      {detail && <SessionMonitorOverlay sessionId={detail.id} onClose={() => setDetail(null)} />}
    </div>
  );
}

// Force-terminating a session, at /admin/monitor/sessions/:sessionId/terminate.
export function ForceTerminatePage() {
  const { t } = useTranslation();
  const { sessionId = '' } = useParams();
  const navigate = useNavigate();
  const [reason, setReason] = useState('');
  const term = useForceTerminate();
  const pushToast = useUiStore((s) => s.pushToast);
  const session = (useAllSessions({}).data as SessionRow[] | undefined)?.find((s) => s.id === sessionId);

  const isCleanup = session?.status === 'error';
  const submit = () => {
    term.mutate(
      { sessionId, reason: reason.trim() },
      {
        onSuccess: () => { pushToast('success', t('admin.monitor.terminateRequested', { session: session?.name ?? sessionId })); navigate('/admin/monitor'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={(isCleanup ? t('admin.monitor.cleanupTitle') : t('admin.monitor.terminateTitle')) + (session ? ' - ' + (session.name) : '')}
        crumbs={[
          { label: t('admin.monitor.title'), to: '/admin/monitor' },
          { label: isCleanup ? t('admin.monitor.cleanupTitle') : t('admin.monitor.terminateTitle') },
        ]}
      />
      <div className="gs-card">
        <div className="grid gap-3">
          <p className="text-muted text-xs font-mono">{sessionId}</p>
          <p className="text-sm">{isCleanup ? t('admin.monitor.cleanupWarning') : t('admin.monitor.terminateWarning')}</p>
          <label className="text-sm font-semibold">
            {t('common.reason')} <span className="text-danger">*</span>
            <input className="gs-input mt-1 w-full" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t('admin.monitor.reasonPlaceholder')} autoComplete="off" />
          </label>
          <p className="text-muted text-xs">{t('admin.monitor.reasonNote')}</p>
        </div>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={reason.trim().length === 0 ? [t('common.reason')] : []} />
          <button type="button" className="gs-btn gs-btn-primary disabled:opacity-50" onClick={submit} disabled={reason.trim().length === 0 || term.isPending}>
            {term.isPending ? t('admin.monitor.terminating') : (isCleanup ? t('admin.monitor.cleanup') : t('admin.monitor.forceTerminate'))}
          </button>
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/monitor')}>{t('common.cancel')}</button>
        </div>
      </div>
    </div>
  );
}


/** The session's lifecycle events, under the facts: what happened, in order, without leaving the
 *  monitor. The drawer's lower half was empty space until now. */
export function DrawerTimeline({ sessionId, flat = false }: { sessionId: string; flat?: boolean }) {
  const { t } = useTranslation();
  const { data: events = [], isLoading } = useSessionTimeline(sessionId);
  return (
    <section className={flat ? '' : 'mt-5 border-t border-border pt-4'}>
      <h3 className="text-xs font-semibold text-muted mb-2">{t('session.eventsTitle')}</h3>
      {isLoading ? (
        <p className="text-muted text-xs">{t('common.loading')}</p>
      ) : events.length === 0 ? (
        <p className="text-muted text-xs">{t('session.noEvents')}</p>
      ) : (
        <ol className="space-y-1.5 max-h-[300px] overflow-y-auto pr-1">
          {events.map((e) => (
            <li key={e.id} className="flex items-baseline gap-2 text-xs">
              <Timestamp value={e.at} className="gs-num text-2xs text-muted shrink-0" />
              <span className="font-semibold">{t(`enum.sessionEvent.${e.kind}`, { defaultValue: e.kind })}</span>
              {e.reason && (
                <span className="text-muted truncate" title={e.reason}>
                  {t(`enum.statusReason.${e.reason}`, { defaultValue: e.reason })}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
