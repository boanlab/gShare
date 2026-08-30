import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  useSessions,
  useTerminateSession,
  useBulkTerminateSessions,
  useStopSession,
  useStartSession,
} from '@/api/hooks/useSessions';
import { formatCredit, formatDuration, hoursElapsed, sessionStatusLabel } from '@/lib/format';
import { PageHeader } from '@/components/PageHeader';
import { Table, TableToolbar, Pagination, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUiStore } from '@/store/uiStore';
import { asApiError, humanizeError } from '@/lib/errors';
import type { Session } from '@/api/types';
import i18n from '@/i18n';
import { Cube, Plus } from '@/components/icons';
import { StatusPill } from '@/components/StatusPill';
import { Tabs } from '@/components/Tabs';

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

function modeLabel(s: Session): string {
  if (s.resource_class !== 'gpu') return 'CPU';
  if (s.mode === 'mig') return 'MIG';
  return i18n.t(s.mode === 'exclusive' ? 'session.modeExclusive' : 'session.modeShared');
}
function vramGb(mb?: number | null): string {
  if (!mb) return '-';
  return mb % 1024 === 0 ? `${mb / 1024}GB` : `${(mb / 1024).toFixed(1)}GB`;
}

export function SessionList() {
  const { t } = useTranslation();
  const { data, isLoading, isError, refetch } = useSessions();
  const term = useTerminateSession();
  const bulkTerm = useBulkTerminateSessions();
  const stop = useStopSession();
  const start = useStartSession();
  const lifecycleBusy = stop.isPending || start.isPending;
  const confirm = useConfirm();
  const pushToast = useUiStore((s) => s.pushToast);
  const table = useTableState('', { sort: 'live', dir: 'desc', tab: 'active' });
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
      uptime: (s) => (s.started_at ? new Date(s.started_at).getTime() : Number.POSITIVE_INFINITY),
      cost: costOf,
      status: (s) => s.status,
      created: (s) => (s.created_at ? new Date(s.created_at).getTime() : 0),
    };
    // Default ordering ("live"): running first, then the other active states, then finished —
    // newest first within each band. Clicking a column header replaces it entirely.
    if ((table.sort ?? 'live') === 'live') {
      const rank = (s: Session) => (s.status === 'running' ? 0 : ACTIVE.includes(s.status) ? 1 : 2);
      const at = (s: Session) => (s.created_at ? new Date(s.created_at).getTime() : 0);
      return [...matched].sort((a, b) => rank(a) - rank(b) || at(b) - at(a));
    }
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

  const cleanupOne = (sess: Session) =>
    term.mutate(sess.id, {
      onSuccess: () => pushToast('success', t('session.cleanedUp', { name: sess.name || sess.id })),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });

  // Pause frees the GPU and stops compute billing; resume re-acquires one, so it can queue.
  const pauseOne = (sess: Session) =>
    stop.mutate(sess.id, {
      onSuccess: () => pushToast('success', t('session.pausedToast', { name: sess.name || sess.id })),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  const resumeOne = (sess: Session) =>
    start.mutate(sess.id, {
      onSuccess: () => pushToast('success', t('session.resumedToast', { name: sess.name || sess.id })),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });

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
          <div className="flex items-center gap-1 text-muted text-2xs">
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
        <span className="text-xs gs-num">
          {vramGb(s.gpu_mem_mb)} <span className="text-muted">· {s.gpu_cores ?? '-'}%</span>
          {s.gpu_model && (
            <span
              className="block text-muted text-2xs truncate max-w-[130px] font-sans"
              title={s.bound_gpu_uuid ?? undefined}
            >
              {s.gpu_model}
            </span>
          )}
        </span>
      ) : <span className="text-muted">-</span>),
    },
    {
      key: 'compute',
      header: t('session.colCompute'),
      hideOnMobile: true,
      render: (s) => (
        (s.cpu ?? s.mem_gb ?? s.disk_gb) != null ? (
          <span className="text-xs gs-num" title={t('session.computeHint')}>
            {s.cpu ?? '-'}<span className="text-muted">C</span>
            <span className="text-muted"> · </span>{s.mem_gb ?? '-'}<span className="text-muted">G</span>
            <span className="text-muted"> · </span>{s.disk_gb ?? '-'}<span className="text-muted">G</span>
          </span>
        ) : <span className="text-muted">-</span>
      ),
    },
    {
      key: 'uptime',
      header: t('session.colUptime'),
      sortBy: (s) => (s.started_at ? new Date(s.started_at).getTime() : Number.POSITIVE_INFINITY),
      align: 'right',
      render: (s) => {
        const started = s.started_at ?? s.created_at ?? null;
        if (s.status === 'pending') return <Timestamp value={s.created_at} className="text-xs text-muted" />;
        if (!started) return <span className="text-muted">-</span>;
        const dur = s.status === 'terminated' && s.terminated_at
          ? formatDuration(started, new Date(s.terminated_at).getTime())
          : ACTIVE.includes(s.status) ? formatDuration(started, now) : '-';
        return <span className="text-xs gs-num" title={new Date(started).toLocaleString()}>{dur}</span>;
      },
    },
    {
      key: 'cost',
      header: t('session.colCost'),
      sortBy: costOf,
      align: 'right',
      render: (s) => (s.credit_per_hour_snapshot
        ? <span className="text-xs gs-num font-semibold">{formatCredit(Math.round(costOf(s) * 100) / 100)} C</span>
        : <span className="text-xs text-muted">{t('session.free')}</span>),
    },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (s) => s.status,
      render: (s) => <StatusPill kind={s.status} label={sessionStatusLabel(s.status)} />,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (s) => (
        // Three fixed slots so the controls never move between rows: the state-dependent primary
        // action, an optional secondary, then terminate. Only the primary is ever rendered
        // disabled (with the reason), because "why can I not connect yet" is the common question;
        // every other inapplicable action is simply absent rather than greyed out.
        <div className="flex justify-end items-center gap-1 whitespace-nowrap">
          <span className="inline-flex justify-end min-w-[64px]">
            {s.status === 'running' && (
              <Link to={`/sessions/${s.id}/connect`} className="gs-btn gs-btn-sm gs-btn-primary">
                {t('session.connect')}
              </Link>
            )}
            {s.status === 'paused' && (
              <button
                type="button"
                className="gs-btn gs-btn-sm gs-btn-primary"
                disabled={lifecycleBusy}
                onClick={() => resumeOne(s)}
              >
                {t('session.resume')}
              </button>
            )}
            {(s.status === 'pending' || s.status === 'preparing') && (
              <button
                type="button"
                className="gs-btn gs-btn-sm gs-btn-primary"
                disabled
                title={t('session.connectOnlyWhenRunning', { status: sessionStatusLabel(s.status) })}
              >
                {t('session.connect')}
              </button>
            )}
          </span>
          <span className="inline-flex justify-end min-w-[62px]">
            {s.status === 'running' && (
              <button type="button" className="gs-btn gs-btn-sm" disabled={lifecycleBusy} onClick={() => pauseOne(s)}>
                {t('session.pause')}
              </button>
            )}
          </span>
          <span className="inline-flex justify-end min-w-[52px]">
            {ACTIVE.includes(s.status) && s.status !== 'terminating' && (
              <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={term.isPending} onClick={() => terminateOne(s)}>
                {t('session.terminate')}
              </button>
            )}
            {/* An errored session is already dead; what is left is reclaiming any leftover hold and
                filing the row — the same cleanup admins have, for the owner's own row. */}
            {s.status === 'error' && (
              <button
                type="button"
                className="gs-btn gs-btn-sm"
                title={t('session.cleanupHint')}
                disabled={term.isPending}
                onClick={() => cleanupOne(s)}
              >
                {t('session.cleanup')}
              </button>
            )}
          </span>
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t('session.title')}
        description={t('session.subtitle')}
        actions={
          <Link to="/sessions/new" className="gs-btn gs-btn-primary">
            <Plus size={15} weight="bold" aria-hidden="true" />
            {t('session.new')}
          </Link>
        }
      />

      <Tabs
        ariaLabel={t('session.title')}
        items={TABS.map((tab_) => ({ key: tab_.key, label: t(tab_.labelKey), count: counts[tab_.key] }))}
        active={tab}
        onChange={table.setTab}
      />

      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('session.searchPlaceholder')}
        total={inTab.length}
        shown={matched.length}
      />

      {/* Bulk toolbar: appears only while rows are selected, directly above the table it acts on. */}
      {selectedActive.length > 0 && (
        <div
          data-bulk-toolbar
          role="toolbar"
          aria-label={t('session.selectedCount', { count: selectedActive.length })}
          className="gs-panel border-primary bg-primary-soft flex items-center gap-3 flex-wrap px-4 py-2 mb-3"
        >
          <span className="text-sm font-semibold">{t('session.selectedCount', { count: selectedActive.length })}</span>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" onClick={terminateSelected} disabled={bulkTerm.isPending}>
            {t('session.terminateSelected', { count: selectedActive.length })}
          </button>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-ghost ml-auto" onClick={() => setSelected(new Set())}>
            {t('session.clearSelection')}
          </button>
        </div>
      )}

      {isError && (
        <p role="alert" className="text-danger mb-2">
          {t('session.loadFailed')}{' '}
          <button type="button" className="underline font-semibold" onClick={() => refetch()}>{t('common.retry')}</button>
        </p>
      )}

      <div className="gs-panel overflow-hidden">
        {isLoading ? (
          <div className="p-4"><TableSkeleton rows={6} columns={5} /></div>
        ) : sorted.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : (
              <EmptyState
                icon={<Cube size={26} />}
                title={t('session.emptyTitle')}
                description={t('session.emptyDescription')}
                action={<Link to="/sessions/new" className="gs-btn gs-btn-primary"><Plus size={15} weight="bold" aria-hidden="true" />{t('session.new')}</Link>}
              />
            )
        ) : (
          <div>
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
