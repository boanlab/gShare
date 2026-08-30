import { useMemo, useState } from 'react';
import { Select } from '@/components/Select';
import { Trans, useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useClusters, useRegisterCluster, useUpdateCluster, useDeleteCluster } from '@/api/hooks/useClusters';
import { useNodes, type GpuNode } from '@/api/hooks/useNodes';
import { useAllSessions } from '@/api/hooks/useMonitor';
import { Table, TableToolbar, type Column } from '@/components/Table';
import { PageHeader } from '@/components/PageHeader';
import { Dialog } from '@/components/Dialog';
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Field, DisabledReason } from '@/components/Field';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { StatusPill } from '@/components/StatusPill';
import { Timestamp } from '@/components/Timestamp';
import type { components } from '@/api/schema';
import { Cloud } from '@/components/icons';

type Cluster = components['schemas']['ClusterRead'];

// Cluster management: register, edit, and delete, all keyed off a kubeconfig. The list carries
// operational facts (GPUs, running sessions, heartbeat); the API address moved into the row's
// detail panel — it is a registration detail, not something scanned per row.
export function AdminClusters() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = useClusters();
  const remove = useDeleteCluster();
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();
  const table = useTableState('', { sort: 'name', dir: 'asc' });
  const [registerOpen, setRegisterOpen] = useState(false);
  const [editCluster, setEditCluster] = useState<Cluster | null>(null);
  // Row click: the cluster's nodes unfold below (the org→dept / dept→member pattern).
  const [selCluster, setSelCluster] = useState<Cluster | null>(null);

  // The node inventory feeds the per-cluster GPU totals and the detail panel.
  const nodes = ((useNodes().data ?? []) as GpuNode[]);
  const byCluster = useMemo(() => {
    const m: Record<string, GpuNode[]> = {};
    for (const n of nodes) {
      const cid = n.cluster_id ?? '';
      (m[cid] = m[cid] ?? []).push(n);
    }
    return m;
  }, [nodes]);
  // Running sessions per cluster, from the monitor's fleet query (super_admin screen).
  const { data: allSessions } = useAllSessions({});
  const runningByCluster = useMemo(() => {
    const m: Record<string, number> = {};
    for (const s of (allSessions ?? []) as { cluster_id?: string; status?: string }[]) {
      if (s.status === 'running' && s.cluster_id) m[s.cluster_id] = (m[s.cluster_id] ?? 0) + 1;
    }
    return m;
  }, [allSessions]);

  const onDelete = async (c: Cluster) => {
    // Removing a cluster orphans everything running on it, so the name has to be typed.
    const ok = await confirm({
      title: t('admin.clusters.confirmDeleteTitle', { name: c.name }),
      body: t('admin.clusters.confirmDelete', { name: c.name }),
      consequences: [
        t('admin.clusters.consequenceNodes', { count: c.node_count ?? 0 }),
        t('admin.clusters.consequenceSessions'),
      ],
      confirmLabel: t('common.delete'),
      destructive: true,
      confirmText: c.name,
    });
    if (!ok) return;
    remove.mutate(c.id, {
      onSuccess: () => pushToast('success', t('admin.clusters.deleted')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const columns: Column<Cluster>[] = [
    { key: 'name', header: t('admin.clusters.colCluster'), sortBy: (c) => c.name, render: (c) => <b>{c.name}</b> },
    { key: 'role', header: t('admin.clusters.colRole'), sortBy: (c) => c.role, hideOnMobile: true, render: (c) => <span className="gs-tag">{c.role}</span> },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (c) => c.status,
      render: (c) => <StatusPill kind={c.status} label={c.status} />,
    },
    { key: 'node_count', header: t('admin.clusters.colNodes'), sortBy: (c) => c.node_count ?? 0, align: 'right' },
    {
      key: 'gpus',
      header: t('admin.clusters.colGpus'),
      align: 'right',
      sortBy: (c) => (byCluster[c.id] ?? []).reduce((a, n) => a + (n.device_count ?? 0), 0),
      render: (c) => (byCluster[c.id] ?? []).reduce((a, n) => a + (n.device_count ?? 0), 0),
    },
    {
      key: 'running',
      header: t('admin.clusters.colRunning'),
      align: 'right',
      sortBy: (c) => runningByCluster[c.id] ?? 0,
      render: (c) => runningByCluster[c.id] ?? 0,
    },
    {
      key: 'heartbeat',
      header: t('admin.clusters.colHeartbeat'),
      align: 'right',
      hideOnMobile: true,
      sortBy: (c) => Math.max(0, ...(byCluster[c.id] ?? []).map((n) => (n.heartbeat_at ? new Date(n.heartbeat_at).getTime() : 0))),
      render: (c) => {
        const ts = Math.max(0, ...(byCluster[c.id] ?? []).map((n) => (n.heartbeat_at ? new Date(n.heartbeat_at).getTime() : 0)));
        return ts > 0 ? <Timestamp value={new Date(ts).toISOString()} className="text-muted text-xs" /> : <span className="text-muted">-</span>;
      },
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (c) => (
        <div className="flex gap-2 justify-end">
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => setEditCluster(c)}>{t('common.edit')}</button>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={remove.isPending} onClick={() => onDelete(c)}>
            {t('common.delete')}
          </button>
        </div>
      ),
    },
  ];

  const all = (data ?? []) as Cluster[];
  const matched = all.filter((c) => {
    const q = table.query.trim().toLowerCase();
    return !q || c.name.toLowerCase().includes(q) || (c.api_server ?? '').toLowerCase().includes(q);
  });
  const rows = sortRows(matched, columns.find((col) => col.key === table.sort)?.sortBy ?? null, table.dir);

  return (
    <div>
      <PageHeader
        title={t('admin.clusters.title')}
        description={t('admin.clusters.subtitle')}
        actions={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setRegisterOpen(true)}>{t('admin.clusters.register')}</button>}
      />
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.clusters.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      />
      <div className="gs-panel overflow-hidden">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <div className="p-4"><TableSkeleton rows={3} columns={5} /></div>
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} />
            : (
              <EmptyState
                icon={<Cloud size={26} />}
                title={t('admin.clusters.empty')}
                description={t('admin.clusters.emptyDescription')}
                action={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setRegisterOpen(true)}>{t('admin.clusters.register')}</button>}
              />
            )
        ) : (
          <Table
            caption={t('admin.clusters.title')}
            columns={columns}
            rows={rows}
            rowKey={(c) => c.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
            onRowClick={setSelCluster}
          />
        )}
      </div>

      {selCluster && (
        <ClusterNodesPanel
          cluster={selCluster}
          nodes={byCluster[selCluster.id] ?? []}
        />
      )}

      <Dialog open={registerOpen} wide title={t('admin.clusters.registerTitle')} onClose={() => setRegisterOpen(false)}>
        <NewClusterForm onDone={() => setRegisterOpen(false)} />
      </Dialog>
      <Dialog
        open={!!editCluster}
        title={`${t('admin.clusters.editTitle')}${editCluster ? ` - ${editCluster.name}` : ''}`}
        onClose={() => setEditCluster(null)}
      >
        {editCluster && <EditClusterForm cluster={editCluster} onDone={() => setEditCluster(null)} />}
      </Dialog>
    </div>
  );
}

// The selected cluster's nodes: name (deep link), role, status, GPU count — plus the API server,
// which left the list columns for this panel.
function ClusterNodesPanel({ cluster, nodes }: { cluster: Cluster; nodes: GpuNode[] }) {
  const { t } = useTranslation();
  return (
    <div className="gs-card mt-4">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <h2 className="font-bold">
          {t('admin.clusters.nodesPanelTitle', { name: cluster.name })}{' '}
          <span className="text-muted text-sm font-normal">{nodes.length}</span>
        </h2>
        {cluster.api_server && (
          <span className="inline-flex items-center gap-1 text-xs text-muted min-w-0">
            {t('admin.clusters.colApiServer')}
            <code className="font-mono truncate max-w-[280px]" title={cluster.api_server}>{cluster.api_server}</code>
            <CopyButton value={cluster.api_server} label={t('admin.clusters.copyApiServer')} />
          </span>
        )}
      </div>
      {nodes.length === 0 ? (
        <p className="text-muted text-sm">{t('admin.clusters.nodesPanelEmpty')}</p>
      ) : (
        <ul className="divide-y divide-border/60">
          {nodes.map((n) => (
            <li key={n.id} className="py-2 flex items-center gap-3 text-sm min-w-0">
              <Link to={`/admin/nodes/${n.id}/devices`} className="font-semibold text-primary hover:underline truncate">
                {n.hostname}
              </Link>
              {n.role && <span className="gs-tag shrink-0">{t(`enum.nodeRole.${n.role}`, { defaultValue: n.role })}</span>}
              <span className="text-muted text-xs gs-num shrink-0">{t('admin.nodes.gpuCount', { count: n.device_count ?? 0 })}</span>
              <span className="ml-auto shrink-0">
                <StatusPill kind={n.status} label={t(`enum.nodeStatus.${n.status}`, { defaultValue: n.status })} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Cluster editing (모달).
function EditClusterForm({ cluster, onDone }: { cluster: Cluster; onDone: () => void }) {
  const { t } = useTranslation();
  const update = useUpdateCluster();
  const pushToast = useUiStore((s) => s.pushToast);
  const [name, setName] = useState(cluster.name);
  const [role, setRole] = useState(cluster.role);

  const dirty = name.trim() !== cluster.name || role !== cluster.role;
  useUnsavedGuard(dirty && !update.isPending);

  const submit = () => {
    update.mutate(
      { id: cluster.id, name: name.trim() || cluster.name, role },
      {
        onSuccess: () => { pushToast('success', t('admin.clusters.updated')); onDone(); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="space-y-3">
        <Field label={t('admin.clusters.nameLabel')} required>
          {(ids) => <input {...ids} className="gs-input w-full" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoFocus autoComplete="off" />}
        </Field>
        <Field label={t('admin.clusters.colRole')} hint={t('admin.clusters.roleHint')}>
          {(ids) => (
            <Select {...ids} className="gs-input w-full" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="primary">{t('admin.clusters.rolePrimary')}</option>
              <option value="standby">{t('admin.clusters.roleStandby')}</option>
            </Select>
          )}
        </Field>
        {cluster.api_server && (
          <p className="text-muted text-2xs inline-flex items-center gap-1 min-w-0">
            {t('admin.clusters.colApiServer')}: <code className="font-mono truncate max-w-[260px]">{cluster.api_server}</code>
            <CopyButton value={cluster.api_server} label={t('admin.clusters.copyApiServer')} />
          </p>
        )}
        <p className="text-muted text-2xs">{t('admin.clusters.rotateNote')}</p>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={name.trim() ? (dirty ? [] : [t('account.noChanges')]) : [t('admin.clusters.nameLabel')]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={update.isPending || !name.trim() || !dirty}>
          {update.isPending ? t('admin.clusters.saving') : t('common.save')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

// Cluster registration (모달).
function NewClusterForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const register = useRegisterCluster();
  const pushToast = useUiStore((s) => s.pushToast);
  const [name, setName] = useState('');
  const [kubeconfig, setKubeconfig] = useState('');
  const [kubeTouched, setKubeTouched] = useState(false);
  // A kubeconfig without a clusters block is caught here rather than as a generic server error.
  const kubeconfigError = kubeTouched && kubeconfig.trim() && !/\bclusters\s*:/.test(kubeconfig)
    ? t('admin.clusters.kubeconfigInvalid')
    : null;
  useUnsavedGuard((!!name.trim() || !!kubeconfig.trim()) && !register.isPending);

  const submit = () => {
    register.mutate(
      { name: name.trim(), kubeconfig: kubeconfig.trim() },
      {
        onSuccess: () => { pushToast('success', t('admin.clusters.registered')); onDone(); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="space-y-3">
        <p className="text-muted text-xs"><Trans i18nKey="admin.clusters.registerNote" components={{ 1: <b /> }} /></p>
        <Field label={t('admin.clusters.nameLabel')} required hint={t('admin.clusters.nameHint')}>
          {(ids) => <input {...ids} className="gs-input w-full" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder={t('admin.clusters.namePlaceholder')} autoFocus autoComplete="off" />}
        </Field>
        <Field
          label={t('admin.clusters.kubeconfigLabel')}
          required
          hint={t('admin.clusters.kubeconfigNote')}
          error={kubeconfigError}
        >
          {(ids) => (
            <textarea
              {...ids}
              className="w-full h-48 px-3 py-2 border border-border rounded-ctl bg-surface-2 font-mono text-xs"
              value={kubeconfig}
              onChange={(e) => setKubeconfig(e.target.value)}
              onBlur={() => setKubeTouched(true)}
              placeholder="apiVersion: v1&#10;kind: Config&#10;clusters: …"
              spellCheck={false}
              maxLength={65536}
            />
          )}
        </Field>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={[
          !name.trim() && t('admin.clusters.nameLabel'),
          !kubeconfig.trim() && t('admin.clusters.kubeconfigLabel'),
        ].filter(Boolean) as string[]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={register.isPending || !name.trim() || !kubeconfig.trim() || !!kubeconfigError}>
          {register.isPending ? t('admin.clusters.registering') : t('admin.clusters.register')}
        </button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}
