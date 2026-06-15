import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useSessions, useTerminateSession, useBulkTerminateSessions } from '@/api/hooks/useSessions';
import { formatCredit, formatDuration, hoursElapsed, sessionStatusLabel } from '@/lib/format';
import { PageHeader } from '@/components/PageHeader';
import { Table, TableToolbar, Pagination, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUiStore } from '@/store/uiStore';
import type { Session } from '@/api/types';
import i18n from '@/i18n';

// The session list, as an operational tool: filter and search by state, GPU specification,
// occupancy, uptime, and estimated cost, with inline connect and terminate.
const ACTIVE = ['pending', 'preparing', 'running', 'paused', 'terminating'];
const PAGE_SIZE = 25;
const TABS = [
  { key: 'active', labelKey: 'session.tabActive' },
  { key: 'running', labelKey: 'session.tabRunning' },
  { key: 'terminated', labelKey: 'session.tabTerminated' },
  { key: 'all', labelKey: 'session.tabAll' },
] as const;

const STATUS_CLS: Record<string, string> = {
  running: 'bg-free-soft text-free', pending: 'bg-warn-soft text-warn', preparing: 'bg-warn-soft text-warn',
  paused: 'bg-surface-2 text-muted', terminating: 'bg-surface-2 text-muted',
  terminated: 'bg-surface-2 text-muted', error: 'bg-danger-soft text-danger',
};
function modeLabel(s: Session): string {
  if (s.resource_class !== 'gpu') return 'CPU';
  if (s.mode === 'mig') return 'MIG';
  return i18n.t(s.mode === 'exclusive' ? 'session.modeExclusive' : 'session.modeShared');
}
function vramGb(mb?: number | null): string {
  if (!mb) return '—';
  return mb % 1024 === 0 ? `${mb / 1024}GB` : `${(mb / 1024).toFixed(1)}GB`;
}

export function SessionList() {
  const { t } = useTranslation();
  const { data, isLoading, isError, isFetching, refetch, dataUpdatedAt } = useSessions();
  const term = useTerminateSession();
  const bulkTerm = useBulkTerminateSessions();
  const confirm = useConfirm();
  const pushToast = useUiStore((s) => s.pushToast);
  const table = useTableState('', { sort: 'created', dir: 'desc', tab: 'active' });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const sessions = useMemo(() => (data ?? []) as Session[], [data]);
  const tab = table.tab ?? 'active';
  const counts = useMemo(
    () => ({
      active: sessions.filter((s) => ACTIVE.includes(s.status)).length,
      running: sessions.filter((s) => s.status === 'running').length,
      terminated: sessions.filter((s) => s.status === 'terminated' || s.status === 'error').length,
      all: sessions.length,
    }),
    [sessions],
  );

  // Estimated cost = rate (credits per hour) x occupancy x uptime in hours, measured from
  // started_at. Without a start time the cost is zero.
  const costOf = useMemo(() => (s: Session): number => {
    const rate = s.credit_per_hour_snapshot ?? 0;
    if (!rate || !s.started_at) return 0;
    const occ = s.occupancy ?? 1;
    // Only a running session accumulates live; anything else is frozen at terminated_at, or at
    // started_at when there is none.
    const endMs = s.status === 'running'
      ? now
      : (s.terminated_at ? new Date(s.terminated_at).getTime() : new Date(s.started_at).getTime());
    return rate * occ * hoursElapsed(s.started_at, endMs);
  }, [now]);

  const inTab = useMemo(() => {
    if (tab === 'running') return sessions.filter((s) => s.status === 'running');
    if (tab === 'active') return sessions.filter((s) => ACTIVE.includes(s.status));
    if (tab === 'terminated') return sessions.filter((s) => s.status === 'terminated' || s.status === 'error');
    return sessions;
  }, [sessions, tab]);

  const matched = useMemo(() => {
    const query = table.query.trim().toLowerCase();
    if (!query) return inTab;
    return inTab.filter((s) => (s.name ?? '').toLowerCase().includes(query) || s.id.toLowerCase().includes(query));
  }, [inTab, table.query]);

  const sorted = useMemo(() => {
    const by: Record<string, (s: Session) => unknown> = {
      name: (s) => s.name || s.id,
      resource: (s) => `${s.resource_class}-${s.mode ?? ''}`,
      occupancy: (s) => s.occupancy ?? -1,
      uptime: (s) => (s.started_at ? new Date(s.started_at).getTime() : Number.POSITIVE_INFINITY),
      cost: costOf,
      status: (s) => s.status,
      created: (s) => (s.created_at ? new Date(s.created_at).getTime() : 0),
    };
    return sortRows(matched, by[table.sort ?? 'created'] ?? null, table.dir);
  }, [matched, table.sort, table.dir, costOf]);

  const pageRows = useMemo(
    () => sorted.slice((table.page - 1) * PAGE_SIZE, table.page * PAGE_SIZE),
    [sorted, table.page],
  );

  const selectedActive = useMemo(
    () => sorted.filter((s) => selected.has(s.id) && ACTIVE.includes(s.status)),
    [sorted, selected],
  );

  const terminateOne = async (s: Session) => {
    const ok = await confirm({
      title: t('session.confirmTerminateTitle', { name: s.name || s.id }),
      body: t('session.confirmTerminateBody'),
      consequences: [
        t('session.consequenceCredit', { amount: formatCredit(Math.round(costOf(s) * 100) / 100) }),
        t('session.consequenceData'),
      ],
      confirmLabel: t('session.terminate'),
      destructive: true,
    });
    if (ok) term.mutate(s.id);
  };

  const terminateSelected = async () => {
    const ok = await confirm({
      title: t('session.confirmBulkTitle', { count: selectedActive.length }),
      body: t('session.confirmBulkBody'),
      consequences: selectedActive.slice(0, 6).map((s) => s.name || s.id),
      confirmLabel: t('session.terminate'),
      destructive: true,
      confirmText: selectedActive.length >= 5 ? String(selectedActive.length) : undefined,
    });
    if (!ok) return;
    bulkTerm.mutate(selectedActive.map((s) => s.id), {
      onSuccess: () => { pushToast('success', t('session.bulkTerminated', { count: selectedActive.length })); setSelected(new Set()); },
      onError: () => pushToast('error', t('session.bulkFailed')),
    });
  };

  const columns: Column<Session>[] = [
    {
      key: 'name',
      header: t('session.colName'),
      sortBy: (s) => s.name || s.id,
      render: (s) => (
        <div className="min-w-0">
          <Link to={`/sessions/${s.id}`} className="text-primary font-semibold">{s.name || s.id}</Link>
          <div className="flex items-center gap-1 text-muted text-[11px]">
            <code className="font-mono truncate max-w-[150px]" title={s.id}>{s.id}</code>
            <CopyButton value={s.id} label={t('session.copyId')} />
          </div>
        </div>
      ),
    },
    {
      key: 'resource',
      header: t('session.colResource'),
      sortBy: (s) => `${s.resource_class}-${s.mode ?? ''}`,
      hideOnMobile: true,
      render: (s) => (
        <>
          <span className="font-semibold">{s.resource_class === 'gpu' ? 'GPU' : 'CPU'}</span>
          <span className="text-muted"> · {modeLabel(s)}</span>
        </>
      ),
    },
    {
      key: 'spec',
      header: t('session.colGpuSpec'),
      hideOnMobile: true,
      render: (s) => (s.resource_class === 'gpu' ? (
        <span className="text-[12px]">
          {vramGb(s.gpu_mem_mb)} <span className="text-muted">· {s.gpu_cores ?? '—'}%</span>
          {s.bound_gpu_uuid && (
            <span className="block text-muted text-[10.5px] font-mono truncate max-w-[130px]" title={s.bound_gpu_uuid}>
              {s.bound_gpu_uuid.replace('GPU-', '').slice(0, 12)}…
            </span>
          )}
        </span>
      ) : <span className="text-muted">—</span>),
    },
    {
      key: 'occupancy',
      header: t('session.colOccupancy'),
      sortBy: (s) => s.occupancy ?? -1,
      hideOnMobile: true,
      render: (s) => {
        const occ = s.occupancy != null ? Math.round(s.occupancy * 100) : null;
        if (occ == null) return <span className="text-muted">—</span>;
        return (
          <div className="flex items-center gap-2" title={t('session.occupancyHint')}>
            <div className="h-1.5 w-16 rounded bg-surface-2 overflow-hidden">
              <div className="h-full bg-primary" style={{ width: `${occ}%` }} />
            </div>
            <span className="text-[12px] text-muted tabular-nums">{occ}%</span>
          </div>
        );
      },
    },
    {
      key: 'uptime',
      header: t('session.colUptime'),
      sortBy: (s) => (s.started_at ? new Date(s.started_at).getTime() : Number.POSITIVE_INFINITY),
      align: 'right',
      render: (s) => {
        const started = s.started_at ?? s.created_at ?? null;
        if (s.status === 'pending') return <Timestamp value={s.created_at} className="text-[12px] text-muted" />;
        if (!started) return <span className="text-muted">—</span>;
        const dur = s.status === 'terminated' && s.terminated_at
          ? formatDuration(started, new Date(s.terminated_at).getTime())
          : ACTIVE.includes(s.status) ? formatDuration(started, now) : '—';
        return <span className="text-[12px] tabular-nums" title={new Date(started).toLocaleString()}>{dur}</span>;
      },
    },
    {
      key: 'cost',
      header: t('session.colCost'),
      sortBy: costOf,
      align: 'right',
      render: (s) => (s.credit_per_hour_snapshot
        ? <span className="text-[12px] tabular-nums font-semibold">{formatCredit(Math.round(costOf(s) * 100) / 100)} C</span>
        : <span className="text-[12px] text-muted">{t('session.free')}</span>),
    },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (s) => s.status,
      render: (s) => (
        <span className={`gs-pill ${STATUS_CLS[s.status] ?? 'bg-surface-2 text-muted'}`}>{sessionStatusLabel(s.status)}</span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (s) => (
        <div className="flex justify-end gap-1 whitespace-nowrap">
          {s.status === 'running' && (
            <Link to={`/sessions/${s.id}/connect`} className="gs-btn gs-btn-sm gs-btn-primary">{t('session.connect')}</Link>
          )}
          {ACTIVE.includes(s.status)
            ? <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={term.isPending} onClick={() => terminateOne(s)}>{t('session.terminate')}</button>
            : <span className="text-muted text-[12px]">—</span>}
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t('session.title')}
        description={t('session.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={<Link to="/sessions/new" className="gs-btn gs-btn-primary">{t('session.new')}</Link>}
      />

      <div className="flex gap-1 mb-3 flex-wrap" role="tablist" aria-label={t('session.title')}>
        {TABS.map((tab_) => (
          <button
            key={tab_.key}
            type="button"
            role="tab"
            aria-selected={tab === tab_.key}
            onClick={() => table.setTab(tab_.key)}
            className={`px-3 py-2 rounded-lg text-[13px] font-semibold transition min-h-[40px] ${
              tab === tab_.key ? 'bg-primary text-white' : 'bg-surface-2 text-muted hover:text-text'
            }`}
          >
            {t(tab_.labelKey)} <span className="opacity-70 tabular-nums">{counts[tab_.key]}</span>
          </button>
        ))}
      </div>

      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('session.searchPlaceholder')}
        total={inTab.length}
        shown={matched.length}
      >
        {selectedActive.length > 0 && (
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" onClick={terminateSelected} disabled={bulkTerm.isPending}>
            {t('session.terminateSelected', { count: selectedActive.length })}
          </button>
        )}
      </TableToolbar>

      {isError && (
        <p role="alert" className="text-danger mb-2">
          {t('session.loadFailed')}{' '}
          <button type="button" className="underline font-semibold" onClick={() => refetch()}>{t('common.retry')}</button>
        </p>
      )}

      <div className="gs-card p-0 overflow-hidden">
        {isLoading ? (
          <div className="p-4"><TableSkeleton rows={6} columns={5} /></div>
        ) : sorted.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : (
              <EmptyState
                icon="▷"
                title={t('session.emptyTitle')}
                description={t('session.emptyDescription')}
                action={<Link to="/sessions/new" className="gs-btn gs-btn-primary">{t('session.new')}</Link>}
              />
            )
        ) : (
          <div className="p-1">
            <Table
              caption={t('session.title')}
              columns={columns}
              rows={pageRows}
              rowKey={(s) => s.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
              selected={selected}
              onSelectedChange={setSelected}
              selectable={(s) => ACTIVE.includes(s.status)}
            />
          </div>
        )}
      </div>
      <Pagination page={table.page} pageSize={PAGE_SIZE} total={sorted.length} onPage={table.setPage} />
    </div>
  );
}
