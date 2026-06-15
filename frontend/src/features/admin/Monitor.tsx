import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  useAllSessions,
  useAdminQueue,
  useNodes,
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
import { CopyButton, CopyableId } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useAuthStore } from '@/auth/authStore';
import { useProjects } from '@/api/hooks/useGroups';
import type { components } from '@/api/schema';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { DisabledReason } from '@/components/Field';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatVram, sessionStatusLabel } from '@/lib/format';

// Session and scheduler monitoring.
// Live updates come from SSE (/sessions/events); on disconnect useAllSessions and useAdminQueue fall
// back to polling via livePaused.

interface SessionRow {
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
  sharing_mode?: string;
  gpu_mem_mb?: number;
  created_at?: string;
}
type QueueRow = components['schemas']['QueueEntryView'];
interface NodeRow {
  id: string;
  hostname: string;
  cluster_id?: string | null;
  cluster_name?: string | null;
  status: string;
  region?: string;
  gpu_mode?: string;
  device_count?: number;
  heartbeat_at?: string;
}

const SESSION_PILL: Record<string, string> = {
  pending: 'bg-warn-soft text-warn',
  preparing: 'bg-warn-soft text-warn',
  running: 'bg-free-soft text-free',
  paused: 'bg-surface-2 text-muted',
  terminated: 'bg-surface-2 text-muted',
  error: 'bg-danger-soft text-danger',
};
const MONITOR_PAGE = 25;

const NODE_PILL: Record<string, string> = {
  ready: 'bg-free-soft text-free',
  busy: 'bg-primary-soft text-primary',
  cordoned: 'bg-warn-soft text-warn',
  offline: 'bg-danger-soft text-danger',
};

export function AdminMonitor() {
  const { t } = useTranslation();
  // Status, search and sort in the URL, so a view survives Back and travels in a link.
  const table = useTableState('', { sort: 'started', dir: 'desc' });
  // Selection for bulk force-terminate.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const bulkTerm = useBulkTerminateSessions();
  const confirm = useConfirm();
  const queueTable = useTableState('q', { sort: 'position', dir: 'asc' });
  const nodeTable = useTableState('n', { sort: 'hostname', dir: 'asc' });
  const statusFilter = table.tab ?? '';
  const setStatusFilter = (v: string) => table.setTab(v || null);

  const { connected } = useSessionsStream({ scope: 'all' });
  const livePaused = !connected; // a dropped SSE connection turns polling back on

  // /metrics/cluster and /nodes are super_admin only, so other roles never call them.
  const isSuper = useAuthStore((s) => s.claims.global_role === 'super_admin');
  const metricsQ = useClusterMetrics({}, { enabled: isSuper });
  const sessionsQ = useAllSessions({ status: statusFilter || undefined }, livePaused);
  const queueQ = useAdminQueue({ status: 'queued' }, livePaused);
  const nodesQ = useNodes({}, { enabled: isSuper });

  const sessions = useMemo(() => (sessionsQ.data ?? []) as SessionRow[], [sessionsQ.data]);
  const queue = queueQ.data ?? [];
  const nodes = (nodesQ.data ?? []) as NodeRow[];

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

  const onPriority = (q: QueueRow) => {
    const raw = window.prompt(t('admin.monitor.priorityPrompt', { current: q.priority }), String(q.priority));
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
            <div className="flex items-center gap-1 text-muted text-[12px]">
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
        render: (s) => <span className={`gs-pill ${SESSION_PILL[s.status] ?? 'bg-surface-2 text-muted'}`}>{sessionStatusLabel(s.status)}</span>,
      },
      { key: 'owner_id', header: t('admin.monitor.colOwner'), sortBy: (s) => s.owner_name ?? s.owner_user_id ?? '', render: (s) => s.owner_name
          ? <span className="inline-flex items-center gap-1 min-w-0">{s.owner_name} <CopyableId value={s.owner_user_id ?? ''} /></span>
          : <span className="font-mono text-[12px]">{s.owner_user_id ?? '—'}</span> },
      {
        key: 'org',
        header: t('common.organization'),
        render: (s) => s.org_name
          ? <span className="text-[12px]">{s.org_name}</span>
          : <span className="text-muted text-[12px]">—</span>,
      },
      {
        key: 'group',
        header: t('common.group'),
        render: (s) => {
          const name = s.group_name ?? groupName(s.group_id);
          return name ? <span className="text-[12px]">{name}</span> : <span className="text-muted text-[12px]">—</span>;
        },
      },
      {
        key: 'resource',
        header: t('admin.monitor.colResource'),
        render: (s) => (
          <span className="text-[12px]">
            {s.resource_class ?? '—'}
            {s.sharing_mode ? ` · ${s.sharing_mode}` : ''}
            {s.gpu_mem_mb ? ` · ${formatVram(s.gpu_mem_mb)}` : ''}
          </span>
        ),
      },
      {
        key: 'actions',
        header: t('admin.monitor.colActions'),
        sortable: false,
        align: 'right',
        render: (s) =>
          ['terminated'].includes(s.status) ? (
            <span className="text-muted text-[12px]">—</span>
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
  const sessionRows = useMemo(
    () => sortRows(matchedSessions, sortAccessor(sessionColumns, table.sort), table.dir),
    [matchedSessions, sessionColumns, table.sort, table.dir],
  );
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
      consequences: selectedLive.slice(0, 6).map((s) => `${s.name ?? s.id} — ${s.owner_name ?? s.owner_user_id ?? ''}`),
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
      { key: 'position', header: '#', sortBy: (q) => q.position ?? 0, align: 'right', render: (q) => <b>{q.position ?? '—'}</b> },
      {
        key: 'session_id',
        header: t('admin.monitor.colSession'),
        render: (q) => (
          <span className="inline-flex items-center gap-1 min-w-0">
            <code className="font-mono text-[12px] truncate max-w-[160px]" title={q.session_id}>{q.session_id}</code>
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
            <span className="text-muted text-[12px]">—</span>
          ),
      },
    ],
    [setPriority.isPending], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const nodeColumns: Column<NodeRow>[] = useMemo(
    () => [
      {
        key: 'cluster',
        header: t('admin.monitor.colCluster'),
        sortBy: (n) => n.cluster_name ?? n.cluster_id ?? '',
        render: (n) => <span className="gs-pill bg-surface-2 text-muted">{n.cluster_name ?? n.cluster_id ?? '—'}</span>,
      },
      {
        key: 'hostname',
        header: t('admin.monitor.colNode'),
        sortBy: (n) => n.hostname,
        render: (n) => (
          <div>
            <b>{n.hostname}</b>
            <div className="text-muted text-[12px]">
              {n.region ?? '—'} · {n.gpu_mode ?? '—'} · GPU {n.device_count ?? 0}
            </div>
          </div>
        ),
      },
      {
        key: 'status',
        header: t('common.status'),
        sortBy: (n) => n.status,
        render: (n) => <span className={`gs-pill ${NODE_PILL[n.status] ?? 'bg-surface-2 text-muted'}`}>{n.status}</span>,
      },
      { key: 'heartbeat_at', header: 'heartbeat', sortBy: (n) => (n.heartbeat_at ? new Date(n.heartbeat_at).getTime() : 0), align: 'right', render: (n) => <Timestamp value={n.heartbeat_at} className="text-muted" /> },
    ],
    [t],
  );

  return (
    <div>
      <PageHeader
        title={t('admin.monitor.title')}
        description={t('admin.monitor.subtitle')}
        updatedAt={sessionsQ.dataUpdatedAt || null}
        onRefresh={() => { void sessionsQ.refetch(); void queueQ.refetch(); }}
        isFetching={sessionsQ.isFetching}
        actions={
          <span className={`gs-pill ${connected ? 'bg-free-soft text-free' : 'bg-warn-soft text-warn'}`}>
            {connected ? t('admin.monitor.live') : t('admin.monitor.pollingFallback')}
          </span>
        }
      />

      {isSuper && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
          <MetricCard label={t('admin.monitor.runningSessions')} value={metrics?.sessions?.running ?? '—'} />
          <MetricCard label={t('admin.monitor.queuedSessions')} value={metrics?.sessions?.queued ?? '—'} />
          <MetricCard
            label={t('admin.monitor.vramPacking')}
            value={metrics?.gpu?.vram_load_pct != null ? `${metrics.gpu.vram_load_pct}%` : '—'}
            sub={metrics?.gpu ? `${formatVram(metrics.gpu.vram_used_mb)} / ${formatVram(metrics.gpu.vram_total_mb)}` : undefined}
          />
          <MetricCard
            label={t('admin.monitor.consumed24h')}
            value={metrics?.credit?.consumed_last_24h ?? '—'}
            sub={metrics?.credit?.active_holds ? t('admin.monitor.heldAmount', { amount: metrics.credit.active_holds }) : undefined}
          />
        </div>
      )}

      <div className="gs-card mb-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-bold">{t('admin.monitor.sessionsHeading', { count: sessions.length })}</h2>
          <label className="gs-sr-only" htmlFor="gs-monitor-status">{t('admin.monitor.allStatuses')}</label>
          <select id="gs-monitor-status" data-url-state className="gs-input w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">{t('admin.monitor.allStatuses')}</option>
            <option value="running">{t('enum.sessionStatus.running')}</option>
            <option value="preparing">{t('enum.sessionStatus.preparing')}</option>
            <option value="pending">{t('enum.sessionStatus.pending')}</option>
            <option value="paused">{t('enum.sessionStatus.paused')}</option>
            <option value="error">{t('enum.sessionStatus.error')}</option>
          </select>
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
            : <EmptyState icon="▷" title={t('admin.monitor.emptySessions')} />
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
            />
            <Pagination page={table.page} pageSize={MONITOR_PAGE} total={sessionRows.length} onPage={table.setPage} />
          </>
        )}
      </div>

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
        <p className="text-muted text-[11.5px] mt-3">{t('admin.monitor.reorderNote')}</p>
      </div>

      {isSuper && (
        <div className="gs-card">
          <h2 className="font-bold mb-3">{t('admin.monitor.nodesHeading', { count: nodes.length })}</h2>
          {nodesQ.isLoading ? (
            <TableSkeleton rows={3} columns={4} />
          ) : (
            <Table
              caption={t('admin.monitor.nodesHeading', { count: nodes.length })}
              columns={nodeColumns}
              rows={sortRows(nodes, sortAccessor(nodeColumns, nodeTable.sort), nodeTable.dir)}
              rowKey={(n) => n.id}
              empty={t('admin.monitor.emptyNodes')}
              sort={nodeTable.sort}
              dir={nodeTable.dir}
              onSort={nodeTable.toggleSort}
            />
          )}
        </div>
      )}

    </div>
  );
}

function MetricCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="gs-card">
      <p className="text-muted text-[12px]">{label}</p>
      <p className="text-2xl font-extrabold mt-1">{value}</p>
      {sub && <p className="text-muted text-[11.5px] mt-0.5">{sub}</p>}
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
        title={t('admin.monitor.terminateTitle') + (session ? ' — ' + (session.name) : '')}
        crumbs={[{ label: t('admin.monitor.title'), to: '/admin/monitor' }, { label: t('admin.monitor.terminateTitle') }]}
        actions={<BackLink to={'/admin/monitor'} />}
      />
      <div className="gs-card">
        <div className="grid gap-3">
          <p className="text-muted text-[12px] font-mono">{sessionId}</p>
          <p className="text-[13px]">{t('admin.monitor.terminateWarning')}</p>
          <label className="text-[13px] font-semibold">
            {t('common.reason')} <span className="text-danger">*</span>
            <input className="gs-input mt-1 w-full" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t('admin.monitor.reasonPlaceholder')} autoComplete="off" />
          </label>
          <p className="text-muted text-[12px]">{t('admin.monitor.reasonNote')}</p>
        </div>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={[]} />
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/monitor')}>{t('common.cancel')}</button>
          <button type="button" className="gs-btn gs-btn-primary disabled:opacity-50" onClick={submit} disabled={reason.trim().length === 0 || term.isPending}>
            {term.isPending ? t('admin.monitor.terminating') : t('admin.monitor.forceTerminate')}
          </button>
        </div>
      </div>
    </div>
  );
}
