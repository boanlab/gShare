import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  useNodes,
  useNode,
  useGpuDevices,
  useCordonNode,
  useDrainNode,
  type GpuNode,
  type GpuDevice,
  type NodeStatus,
} from '@/api/hooks/useNodes';
import { Table, TableToolbar, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { DisabledReason } from '@/components/Field';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatVram } from '@/lib/format';

// Nodes and devices: inventory, GPU capacity and health, cordon and drain.
// Nodes are reported and managed by the cluster operator's inventory controller, so the console
// never registers one by hand.

const STATUS_PILL: Record<NodeStatus, string> = {
  ready: 'bg-free-soft text-free',
  busy: 'bg-primary-soft text-primary',
  cordoned: 'bg-warn-soft text-warn',
  offline: 'bg-danger-soft text-danger',
};

export function AdminNodes() {
  const { t } = useTranslation();
  const table = useTableState('', { sort: 'hostname', dir: 'asc' });
  const statusFilter = (table.tab ?? '') as NodeStatus | '';
  const confirm = useConfirm();

  const { data: nodes, isLoading, isFetching, refetch, dataUpdatedAt } = useNodes(statusFilter ? { status: statusFilter } : {});
  const cordon = useCordonNode();
  const pushToast = useUiStore((s) => s.pushToast);

  const summary = useMemo(() => {
    const list = nodes ?? [];
    return {
      total: list.length,
      ready: list.filter((n) => n.status === 'ready' || n.status === 'busy').length,
      cordoned: list.filter((n) => n.status === 'cordoned').length,
      offline: list.filter((n) => n.status === 'offline').length,
      devices: list.reduce((s, n) => s + n.device_count, 0),
    };
  }, [nodes]);

  // Cordon confirms; uncordon does not.
  const toggleCordon = useCallback(async (n: GpuNode) => {
    const next = n.status !== 'cordoned';
    if (next) {
      const ok = await confirm({
        title: t('admin.nodes.confirmCordonTitle', { name: n.hostname }),
        body: t('admin.nodes.confirmCordonBody'),
        consequences: [t('admin.nodes.consequenceCordon', { count: n.device_count })],
        confirmLabel: t('admin.nodes.cordon'),
      });
      if (!ok) return;
    }
    cordon.mutate(
      { nodeId: n.id, cordon: next },
      {
        onSuccess: () => pushToast('success', `${n.hostname} ${next ? 'cordon' : 'uncordon'}`),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  }, [cordon, confirm, pushToast, t]);

  const columns: Column<GpuNode>[] = useMemo(() => [
    {
      key: 'cluster',
      header: t('admin.nodes.colCluster'),
      hideOnMobile: true,
      sortBy: (n) => n.cluster_name ?? n.cluster_id ?? '',
      render: (n) => (
        <span className="gs-pill bg-surface-2 text-muted">{n.cluster_name ?? n.cluster_id ?? '—'}</span>
      ),
    },
    {
      key: 'hostname',
      header: t('admin.nodes.colNode'),
      sortBy: (n) => n.hostname,
      render: (n) => (
        <div className="min-w-0">
          <Link to={`/admin/nodes/${n.id}/devices`} className="text-left block font-bold text-primary">{n.hostname}</Link>
          <div className="flex items-center gap-1 text-muted text-[12px]">
            <span className="font-mono">{n.region}</span>
            <CopyButton value={n.hostname} label={t('admin.nodes.copyHostname')} />
          </div>
        </div>
      ),
    },
    { key: 'gpu_mode', header: t('admin.nodes.colMode'), hideOnMobile: true, sortBy: (n) => n.gpu_mode ?? '', render: (n) => <span className="gs-pill bg-surface-2 text-muted">{n.gpu_mode}</span> },
    { key: 'device_count', header: t('admin.nodes.colGpu'), align: 'right', sortBy: (n) => n.device_count ?? 0, render: (n) => t('admin.nodes.gpuCount', { count: n.device_count }) },
    { key: 'cpu', header: 'CPU/MEM', hideOnMobile: true, sortBy: (n) => n.cpu ?? 0, render: (n) => `${n.cpu} core · ${n.mem_gb} GiB` },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (n) => n.status,
      render: (n) => <span className={`gs-pill ${STATUS_PILL[n.status]}`}>{n.status}</span>,
    },
    {
      key: 'heartbeat_at',
      header: t('admin.nodes.colHeartbeat'),
      align: 'right',
      sortBy: (n) => (n.heartbeat_at ? new Date(n.heartbeat_at).getTime() : 0),
      render: (n) => <Timestamp value={n.heartbeat_at} className="text-muted text-[12px]" />,
    },
    {
      key: 'actions',
      header: t('admin.nodes.colQuickActions'),
      sortable: false,
      align: 'right',
      render: (n) => (
        <div className="flex gap-2 justify-end">
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => toggleCordon(n)} disabled={cordon.isPending}>
            {n.status === 'cordoned' ? t('admin.nodes.uncordon') : t('admin.nodes.cordon')}
          </button>
          <Link to={`/admin/nodes/${n.id}/drain`} className="gs-btn gs-btn-sm gs-btn-danger">{t('admin.nodes.drain')}</Link>
        </div>
      ),
    },
  ], [t, cordon.isPending, toggleCordon]);

  const all = nodes ?? [];
  const matched = all.filter((n) => {
    const q = table.query.trim().toLowerCase();
    return !q || n.hostname.toLowerCase().includes(q) || (n.region ?? '').toLowerCase().includes(q) || (n.cluster_name ?? '').toLowerCase().includes(q);
  });
  const rows = sortRows(matched, sortAccessor(columns, table.sort), table.dir);

  return (
    <div>
      <PageHeader
        title={t('admin.nodes.title')}
        description={t('admin.nodes.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <div className="gs-card">
          <div className="text-[12px] text-muted font-semibold">{t('admin.nodes.nodesUp')}</div>
          <div className="text-2xl font-extrabold mt-1">{summary.ready} <small className="text-muted text-[13px]">/ {summary.total}</small></div>
        </div>
        <div className="gs-card">
          <div className="text-[12px] text-muted font-semibold">{t('admin.nodes.gpuDevices')}</div>
          <div className="text-2xl font-extrabold mt-1">{summary.devices}</div>
        </div>
        <div className="gs-card">
          <div className="text-[12px] text-muted font-semibold">cordoned</div>
          <div className="text-2xl font-extrabold mt-1 text-warn">{summary.cordoned}</div>
        </div>
        <div className="gs-card">
          <div className="text-[12px] text-muted font-semibold">offline</div>
          <div className="text-2xl font-extrabold mt-1 text-danger">{summary.offline}</div>
        </div>
      </div>

      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.nodes.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      >
        <label className="gs-sr-only" htmlFor="gs-node-status">{t('admin.nodes.statusFilter')}</label>
        <select id="gs-node-status" className="gs-input w-auto" value={statusFilter} onChange={(e) => table.setTab(e.target.value || null)}>
          <option value="">{t('common.all')}</option>
          <option value="ready">ready</option>
          <option value="busy">busy</option>
          <option value="cordoned">cordoned</option>
          <option value="offline">offline</option>
        </select>
      </TableToolbar>

      <div className="gs-card">
        {isLoading ? (
          <TableSkeleton rows={4} columns={6} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon="▦" title={t('admin.nodes.empty')} description={t('admin.nodes.emptyDescription')} />
        ) : (
          <Table
            caption={t('admin.nodes.title')}
            columns={columns}
            rows={rows}
            rowKey={(n) => n.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
        <p className="text-muted text-[11.5px] mt-3">
          {t('admin.nodes.hint')}
        </p>
      </div>
    </div>
  );
}

// Node drain, on its own page at /admin/nodes/:nodeId/drain.
export function DrainNodePage() {
  const { t } = useTranslation();
  const { nodeId = '' } = useParams();
  const navigate = useNavigate();
  const drain = useDrainNode();
  const pushToast = useUiStore((s) => s.pushToast);
  const node = useNode(nodeId).data;
  const [mode, setMode] = useState<'reschedule' | 'force_terminate'>('reschedule');

  const submit = () => {
    if (!node) return;
    drain.mutate(
      { nodeId: node.id, mode },
      {
        onSuccess: (res) => { pushToast('success', t('admin.nodes.drainStarted', { node: node.hostname, count: res.affected_sessions.length })); navigate('/admin/nodes'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.nodes.drainTitle') + (node ? ' — ' + (node.hostname) : '')}
        crumbs={[{ label: t('admin.nodes.title'), to: '/admin/nodes' }, { label: t('admin.nodes.drainTitle') }]}
        actions={<BackLink to={'/admin/nodes'} />}
      />
      {!node ? <p className="text-muted">{t('admin.nodes.notFound')}</p> : (
      <div className="gs-card">
      <div className="grid gap-3">
        <label className="text-[13px] font-semibold">
          {t('admin.nodes.mode')}
          <select className="gs-input mt-1 w-full" value={mode} onChange={(e) => setMode(e.target.value as 'reschedule' | 'force_terminate')}>
            <option value="reschedule">{t('admin.nodes.modeReschedule')}</option>
            <option value="force_terminate">{t('admin.nodes.modeForce')}</option>
          </select>
        </label>
        <p className="text-muted text-[12px]">
          {t('admin.nodes.drainNote')}
        </p>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={[]} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/nodes')}>{t('common.cancel')}</button>
        <button type="button" className="gs-btn gs-btn-primary disabled:opacity-50" onClick={submit} disabled={drain.isPending}>{t('admin.nodes.drainRun')}</button>
      </div>
      </div>
      )}
    </div>
  );
}

// GPU device view, on its own page at /admin/nodes/:nodeId/devices.
export function NodeDevicesPage() {
  const { t } = useTranslation();
  const { nodeId = '' } = useParams();
  const node = useNode(nodeId).data;
  const { data: devices, isLoading } = useGpuDevices(nodeId);

  const columns: Column<GpuDevice>[] = [
    { key: 'model', header: t('admin.nodes.colModel'), render: (d) => <b>{d.model}</b> },
    { key: 'mode', header: t('admin.nodes.colMode'), render: (d) => <span className="gs-pill bg-surface-2 text-muted">{d.mode}</span> },
    {
      key: 'mem',
      header: 'VRAM (used/total)',
      render: (d) => `${formatVram(d.used_mem_mb)} / ${formatVram(d.total_mem_mb)}`,
    },
    { key: 'cores', header: t('admin.nodes.colCores'), render: (d) => `${d.used_cores}% / ${d.total_cores}%` },
    { key: 'status', header: t('common.status'), render: (d) => <span className="gs-pill bg-surface-2 text-muted">{d.status}</span> },
    { key: 'bound', header: t('admin.nodes.colBoundSessions'), render: (d) => d.bound_sessions.length },
  ];

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.nodes.devicesTitle') + (node ? ' — ' + node.hostname : '')}
        crumbs={[{ label: t('admin.nodes.title'), to: '/admin/nodes' }, { label: t('admin.nodes.devicesTitle') }]}
        actions={<BackLink to="/admin/nodes" label={t('admin.nodes.backToList')} />}
      />
      <div className="gs-card">
        {isLoading ? (
          <p className="text-muted">{t('common.loading')}</p>
        ) : (
          <Table columns={columns} rows={devices ?? []} rowKey={(d) => d.id} empty={t('admin.nodes.noDevices')} />
        )}
      </div>
    </div>
  );
}
