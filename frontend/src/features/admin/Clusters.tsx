import { useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useClusters, useCluster, useRegisterCluster, useUpdateCluster, useDeleteCluster } from '@/api/hooks/useClusters';
import { Table, TableToolbar, type Column } from '@/components/Table';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Field, DisabledReason } from '@/components/Field';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import type { components } from '@/api/schema';

type Cluster = components['schemas']['ClusterRead'];

// Cluster management: register, edit, switch, and delete, all keyed off a kubeconfig.
export function AdminClusters() {
  const { t } = useTranslation();
  const { data, isLoading, isFetching, refetch, dataUpdatedAt } = useClusters();
  const remove = useDeleteCluster();
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();
  const table = useTableState('', { sort: 'name', dir: 'asc' });

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
    { key: 'name', header: t('admin.clusters.colCluster'), sortBy: (c) => c.name },
    { key: 'role', header: t('admin.clusters.colRole'), sortBy: (c) => c.role, hideOnMobile: true, render: (c) => <span className="gs-pill bg-surface-2">{c.role}</span> },
    {
      key: 'api_server',
      header: t('admin.clusters.colApiServer'),
      sortBy: (c) => c.api_server ?? '',
      hideOnMobile: true,
      render: (c) => (c.api_server
        ? (
          <span className="inline-flex items-center gap-1 min-w-0">
            <code className="font-mono text-[12px] truncate max-w-[200px]" title={c.api_server}>{c.api_server}</code>
            <CopyButton value={c.api_server} label={t('admin.clusters.copyApiServer')} />
          </span>
        )
        : <span className="text-muted">-</span>),
    },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (c) => c.status,
      render: (c) => (
        <span className={`gs-pill ${c.status === 'connected' ? 'bg-free-soft text-free' : 'bg-warn-soft text-warn'}`}>{c.status}</span>
      ),
    },
    { key: 'node_count', header: t('admin.clusters.colNodes'), sortBy: (c) => c.node_count ?? 0, align: 'right' },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (c) => (
        <div className="flex gap-2 justify-end">
          <Link to={`/admin/clusters/${c.id}/edit`} className="gs-btn gs-btn-sm">{t('common.edit')}</Link>
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
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={<Link to="/admin/clusters/new" className="gs-btn gs-btn-primary">{t('admin.clusters.register')}</Link>}
      />
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.clusters.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
      />
      <div className="gs-card p-0 overflow-hidden">
        {isLoading ? (
          <div className="p-4"><TableSkeleton rows={3} columns={5} /></div>
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : (
              <EmptyState
                icon="◇"
                title={t('admin.clusters.empty')}
                description={t('admin.clusters.emptyDescription')}
                action={<Link to="/admin/clusters/new" className="gs-btn gs-btn-primary">{t('admin.clusters.register')}</Link>}
              />
            )
        ) : (
          <div className="p-1">
            <Table
              caption={t('admin.clusters.title')}
              columns={columns}
              rows={rows}
              rowKey={(c) => c.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// Cluster editing, on its own page at /admin/clusters/:clusterId/edit.
export function EditClusterPage() {
  const { t } = useTranslation();
  const { clusterId = '' } = useParams();
  const navigate = useNavigate();
  const update = useUpdateCluster();
  const pushToast = useUiStore((s) => s.pushToast);
  const { data: cluster, isLoading } = useCluster(clusterId);
  const [name, setName] = useState('');
  const [role, setRole] = useState('primary');
  const [syncedId, setSyncedId] = useState<string | null>(null);
  if (cluster && cluster.id !== syncedId) {
    setSyncedId(cluster.id);
    setName(cluster.name);
    setRole(cluster.role);
  }

  const dirty = !!cluster && (name.trim() !== cluster.name || role !== cluster.role);
  useUnsavedGuard(dirty && !update.isPending);

  const submit = () => {
    if (!cluster) return;
    update.mutate(
      { id: cluster.id, name: name.trim() || cluster.name, role },
      {
        onSuccess: () => { pushToast('success', t('admin.clusters.updated')); navigate('/admin/clusters'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={`${t('admin.clusters.editTitle')}${cluster ? ` — ${cluster.name}` : ''}`}
        crumbs={[{ label: t('admin.clusters.title'), to: '/admin/clusters' }, { label: t('admin.clusters.editTitle') }]}
        actions={<BackLink to="/admin/clusters" />}
      />
      {isLoading && !cluster ? <TableSkeleton rows={3} columns={2} /> : !cluster ? (
        <EmptyState
          icon="?"
          title={t('admin.clusters.notFound')}
          action={<Link to="/admin/clusters" className="gs-btn gs-btn-primary">{t('common.back')}</Link>}
        />
      ) : (
        <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="space-y-3">
            <Field label={t('admin.clusters.nameLabel')} required>
              {(ids) => <input {...ids} className="gs-input w-full" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoFocus autoComplete="off" />}
            </Field>
            <Field label={t('admin.clusters.colRole')} hint={t('admin.clusters.roleHint')}>
              {(ids) => (
                <select {...ids} className="gs-input w-full" value={role} onChange={(e) => setRole(e.target.value)}>
                  <option value="primary">primary</option>
                  <option value="standby">standby</option>
                </select>
              )}
            </Field>
            <p className="text-muted text-[11px]">{t('admin.clusters.rotateNote')}</p>
          </div>
          <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
            <DisabledReason reasons={name.trim() ? (dirty ? [] : [t('account.noChanges')]) : [t('admin.clusters.nameLabel')]} />
            <button type="button" className="gs-btn" onClick={() => navigate('/admin/clusters')}>{t('common.cancel')}</button>
            <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={update.isPending || !name.trim() || !dirty}>
              {update.isPending ? t('admin.clusters.saving') : t('common.save')}</button>
          </div>
        </form>
      )}
    </div>
  );
}

// Cluster registration, on its own page at /admin/clusters/new.
export function NewClusterPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
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
        onSuccess: () => { pushToast('success', t('admin.clusters.registered')); navigate('/admin/clusters'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.clusters.registerTitle')}
        crumbs={[{ label: t('admin.clusters.title'), to: '/admin/clusters' }, { label: t('admin.clusters.registerTitle') }]}
        actions={<BackLink to="/admin/clusters" />}
      />
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="space-y-3">
          <p className="text-muted text-[12px]"><Trans i18nKey="admin.clusters.registerNote" components={{ 1: <b /> }} /></p>
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
                className="w-full h-48 px-3 py-2 border border-border rounded-lg bg-surface-2 font-mono text-[12px]"
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
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/clusters')}>{t('common.cancel')}</button>
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={register.isPending || !name.trim() || !kubeconfig.trim() || !!kubeconfigError}>
            {register.isPending ? t('admin.clusters.registering') : t('admin.clusters.register')}
          </button>
        </div>
      </form>
    </div>
  );
}
