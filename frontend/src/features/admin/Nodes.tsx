import { useCallback, useMemo, useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  useNodes,
  useNode,
  useGpuDevices,
  useSetDeviceMode,
  useCordonNode,
  useDrainNode,
  type GpuNode,
  type GpuDevice,
  type NodeStatus,
} from '@/api/hooks/useNodes';
import {
  useNodePools,
  useCreateNodePool,
  useDeleteNodePool,
  useSetNodePool,
  useGrantPool,
  useRevokePoolGrant,
  type NodePool,
  type NodePoolGrant,
  type PoolKind,
  type PoolGrantScope,
} from '@/api/hooks/useNodePools';
import { useClusters } from '@/api/hooks/useClusters';
import { useAllSessions } from '@/api/hooks/useMonitor';
import { sessionStatusLabel } from '@/lib/format';
import { useOrganizations, useProjects, type Organization, type Project } from '@/api/hooks/useGroups';
import { useAuthStore } from '@/auth/authStore';
import { Table, TableToolbar, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { Timestamp } from '@/components/Timestamp';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader } from '@/components/PageHeader';
import { Field, DisabledReason } from '@/components/Field';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatVram } from '@/lib/format';
import { HardDrives, Plus } from '@/components/icons';
import { StatusPill } from '@/components/StatusPill';
import { Tabs } from '@/components/Tabs';
import { Figure } from '@/components/Figure';

// Nodes and devices: inventory, GPU capacity and health, cordon and drain.
// Nodes are reported and managed by the cluster operator's inventory controller, so the console
// never registers one by hand.



// ── Node pools ──
// A pool is a named set of nodes in one cluster. A dedicated pool serves only the organizations and
// groups holding a grant; a shared pool (and every unassigned node) serves everyone. super_admin
// creates pools, assigns nodes, and grants; an org_admin sees the pools granted to their
// organization and sub-assigns them to its groups. CPU-only sessions and lending idle dedicated
// cards to the shared pool are out of scope here.

function CreatePoolForm({ clusters, onDone }: { clusters: Array<{ id: string; name: string }>; onDone: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const create = useCreateNodePool();
  const [clusterId, setClusterId] = useState('');
  const [name, setName] = useState('');
  const [kind, setKind] = useState<PoolKind>('dedicated');
  const [description, setDescription] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);
  const effectiveCluster = clusterId || clusters[0]?.id || '';
  const blockers: string[] = [];
  if (!effectiveCluster) blockers.push(t('admin.nodes.pools.blockerCluster'));
  if (!name.trim()) blockers.push(t('admin.nodes.pools.blockerName'));

  const submit = () => {
    if (blockers.length) return;
    setServerError(null);
    create.mutate(
      { cluster_id: effectiveCluster, name: name.trim(), kind, description: description.trim() || undefined },
      {
        onSuccess: (p) => { pushToast('success', t('admin.nodes.pools.created', { name: p?.name ?? name })); onDone(); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );
  };

  return (
    <form className="gs-card mb-4" noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="text-xs text-muted font-semibold mb-3">{t('admin.nodes.pools.newTitle')}</div>
      <div className="grid grid-cols-2 gap-3 max-[640px]:grid-cols-1">
        <Field label={t('common.cluster')} required>
          {(ids) => (
            <Select {...ids} className="gs-input w-full" value={effectiveCluster} onChange={(e) => setClusterId(e.target.value)}>
              {clusters.length === 0 && <option value="">-</option>}
              {clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </Select>
          )}
        </Field>
        <Field label={t('admin.nodes.pools.name')} required>
          {(ids) => <input {...ids} className="gs-input w-full" value={name} maxLength={64} autoComplete="off" onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label={t('admin.nodes.pools.kind')} hint={t('admin.nodes.pools.kindHint')}>
          {(ids) => (
            <Select {...ids} className="gs-input w-full" value={kind} onChange={(e) => setKind(e.target.value as PoolKind)}>
              <option value="dedicated">{t('admin.nodes.pools.kindDedicated')}</option>
              <option value="shared">{t('admin.nodes.pools.kindShared')}</option>
            </Select>
          )}
        </Field>
        <Field label={t('admin.nodes.pools.description')}>
          {(ids) => <input {...ids} className="gs-input w-full" value={description} maxLength={200} autoComplete="off" onChange={(e) => setDescription(e.target.value)} />}
        </Field>
      </div>
      {serverError && <p role="alert" className="text-danger text-xs mt-3">{serverError}</p>}
      <p className="text-muted text-xs mt-3">{t('admin.nodes.pools.afterCreateNote')}</p>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={blockers} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={blockers.length > 0 || create.isPending}>
          {t('admin.nodes.pools.create')}
        </button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

// Inline grant form for one pool. super_admin may target an organization or a group; an org_admin
// only a group in an organization that already holds a grant on this pool.
function GrantForm({ pool, isSuper, orgs, groups, onDone }: {
  pool: NodePool;
  isSuper: boolean;
  orgs: Organization[];
  groups: Project[];
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const grant = useGrantPool();
  const [scope, setScope] = useState<PoolGrantScope>(isSuper ? 'org' : 'group');
  const [scopeId, setScopeId] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);
  const granted = new Set(pool.grants.map((g) => `${g.scope}:${g.scope_id}`));
  // An org_admin sub-assigns within the organizations granted on this pool.
  const grantedOrgIds = new Set(pool.grants.filter((g) => g.scope === 'org').map((g) => g.scope_id));
  const targets = scope === 'org'
    ? orgs.filter((o) => !granted.has(`org:${o.id}`)).map((o) => ({ id: o.id, label: o.name }))
    : groups
      .filter((g) => (isSuper || grantedOrgIds.has(g.org_id)) && !granted.has(`group:${g.id}`))
      .map((g) => ({ id: g.id, label: g.org_name ? `${g.org_name} / ${g.name}` : g.name }));
  const effectiveId = scopeId || targets[0]?.id || '';
  const blockers = effectiveId ? [] : [t('admin.nodes.pools.blockerTarget')];

  const submit = () => {
    if (blockers.length) return;
    setServerError(null);
    grant.mutate(
      { poolId: pool.id, scope, scope_id: effectiveId },
      {
        onSuccess: () => { pushToast('success', t('admin.nodes.pools.granted', { pool: pool.name })); onDone(); },
        onError: (e) => { const m = humanizeError(asApiError(e)); setServerError(m); pushToast('error', m); },
      },
    );
  };

  return (
    <form className="mt-1.5 flex flex-wrap items-center gap-1.5" noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
      {isSuper && (
        <label className="text-xs text-muted">
          {t('admin.nodes.pools.grantScope')}
          <Select className="gs-input gs-input-sm w-auto ml-2" value={scope} onChange={(e) => { setScope(e.target.value as PoolGrantScope); setScopeId(''); }}>
            <option value="org">{t('enum.scope.org')}</option>
            <option value="group">{t('enum.scope.group')}</option>
          </Select>
        </label>
      )}
      <label className="text-xs text-muted">
        {t('admin.nodes.pools.grantTarget')}
        <Select className="gs-input gs-input-sm w-auto ml-2" value={effectiveId} onChange={(e) => setScopeId(e.target.value)}>
          {targets.length === 0 && <option value="">{t('admin.nodes.pools.noTarget')}</option>}
          {targets.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
        </Select>
      </label>
      <button type="submit" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" disabled={blockers.length > 0 || grant.isPending}>
        {t('admin.nodes.pools.grantAdd')}
      </button>
      <button type="button" className="gs-btn gs-btn-sm" onClick={onDone}>{t('common.cancel')}</button>
      {serverError && <p role="alert" className="text-danger text-xs w-full">{serverError}</p>}
    </form>
  );
}

function NodePoolsPanel({ isSuper }: { isSuper: boolean }) {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const pushToast = useUiStore((s) => s.pushToast);
  const { data: pools, isLoading, isError, error, refetch } = useNodePools();
  // cluster.read is super_admin only; an org_admin never creates a pool, so the list is skipped.
  const clusters = useClusters({ enabled: isSuper }).data ?? [];
  const orgs = useOrganizations().data ?? [];
  const groups = useProjects().data ?? [];
  const revoke = useRevokePoolGrant();
  const remove = useDeleteNodePool();
  const [creating, setCreating] = useState(false);
  const [grantFor, setGrantFor] = useState<string | null>(null);

  const onRevoke = useCallback(async (pool: NodePool, g: NodePoolGrant) => {
    const ok = await confirm({
      title: t('admin.nodes.pools.confirmRevokeTitle', { name: g.name }),
      body: t('admin.nodes.pools.confirmRevokeBody', { pool: pool.name }),
      confirmLabel: t('admin.nodes.pools.revoke'),
      destructive: true,
    });
    if (!ok) return;
    revoke.mutate({ poolId: pool.id, grantId: g.id }, {
      onSuccess: () => pushToast('success', t('admin.nodes.pools.revoked', { name: g.name })),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  }, [confirm, revoke, pushToast, t]);

  const onDelete = useCallback(async (pool: NodePool) => {
    const ok = await confirm({
      title: t('admin.nodes.pools.confirmDeleteTitle', { name: pool.name }),
      body: t('admin.nodes.pools.confirmDeleteBody'),
      consequences: [
        t('admin.nodes.pools.consequenceNodes', { count: pool.node_count }),
        t('admin.nodes.pools.consequenceGrants', { count: pool.grants.length }),
      ],
      confirmLabel: t('common.delete'),
      confirmText: pool.name,
      destructive: true,
    });
    if (!ok) return;
    remove.mutate(pool.id, {
      onSuccess: () => pushToast('success', t('admin.nodes.pools.deleted', { name: pool.name })),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  }, [confirm, remove, pushToast, t]);

  const columns: Column<NodePool>[] = [
    {
      key: 'name',
      header: t('admin.nodes.pools.name'),
      render: (p) => (
        <div className="min-w-0">
          <div className="font-semibold">{p.name}</div>
          {p.description && <div className="text-muted text-2xs truncate">{p.description}</div>}
        </div>
      ),
    },
    { key: 'cluster', header: t('common.cluster'), hideOnMobile: true, render: (p) => <span className="gs-tag">{p.cluster_name ?? p.cluster_id}</span> },
    { key: 'kind', header: t('admin.nodes.pools.kind'), render: (p) => <span className="gs-tag">{t(`admin.nodes.pools.kind_${p.kind}`)}</span> },
    {
      key: 'nodes',
      header: t('admin.nodes.pools.nodes'),
      align: 'right',
      render: (p) => <span title={p.nodes.map((n) => n.hostname).join(', ')}>{t('admin.nodes.pools.nodeCount', { count: p.node_count })}</span>,
    },
    {
      key: 'grants',
      header: t('admin.nodes.pools.grants'),
      sortable: false,
      render: (p) => (
        <div className="min-w-0">
          <div className="flex flex-wrap gap-1.5">
            {p.grants.length === 0 && <span className="text-muted text-xs">{t('admin.nodes.pools.noGrants')}</span>}
            {p.grants.map((g) => {
              // An org_admin revokes only group grants; organization grants are super_admin's.
              const canRevoke = isSuper || g.scope === 'group';
              return (
                <span key={g.id} className="gs-tag inline-flex items-center gap-1">
                  <span className="text-muted">{t(`enum.scope.${g.scope}`)}</span>
                  {g.name}
                  {canRevoke && (
                    <button type="button" className="rounded-ctl px-0.5 leading-none text-muted hover:text-danger hover:bg-surface-2" aria-label={t('admin.nodes.pools.revokeAria', { name: g.name })}
                      disabled={revoke.isPending} onClick={() => onRevoke(p, g)}>
                      ×
                    </button>
                  )}
                </span>
              );
            })}
            {grantFor !== p.id && (
              <button type="button" className="text-primary text-xs font-semibold hover:underline self-center" onClick={() => setGrantFor(p.id)}>
                {t('admin.nodes.pools.grantAdd')}
              </button>
            )}
          </div>
          {grantFor === p.id && (
            <GrantForm pool={p} isSuper={isSuper} orgs={orgs} groups={groups} onDone={() => setGrantFor(null)} />
          )}
        </div>
      ),
    },
    ...(isSuper ? [{
      key: 'actions',
      header: t('admin.nodes.colQuickActions'),
      sortable: false,
      align: 'right',
      render: (p: NodePool) => (
        <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={remove.isPending} onClick={() => onDelete(p)}>
          {t('common.delete')}
        </button>
      ),
    } as Column<NodePool>] : []),
  ];

  const rows = pools ?? [];
  return (
    <div>
      <div className="flex items-start justify-between gap-3 mb-4 flex-wrap">
        <p className="text-muted text-xs max-w-2xl">{t('admin.nodes.pools.hint')}</p>
        {isSuper && !creating && (
          <button type="button" className="gs-btn gs-btn-primary" onClick={() => setCreating(true)}>
            <Plus size={15} weight="bold" aria-hidden="true" />
            {t('admin.nodes.pools.new')}
          </button>
        )}
      </div>
      {creating && (
        <CreatePoolForm clusters={clusters.map((c) => ({ id: c.id, name: c.name }))} onDone={() => setCreating(false)} />
      )}
      <div className="gs-card">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <TableSkeleton rows={3} columns={5} />
        ) : rows.length === 0 ? (
          <EmptyState icon={<HardDrives size={26} />} title={t('admin.nodes.pools.empty')} description={t(isSuper ? 'admin.nodes.pools.emptyDescription' : 'admin.nodes.pools.emptyDescriptionOrg')} />
        ) : (
          <Table caption={t('admin.nodes.pools.title')} columns={columns} rows={rows} rowKey={(p) => p.id} />
        )}
      </div>
    </div>
  );
}

// Per-row pool selector on the node table: the pools in the node's cluster plus "shared
// (unassigned)". super_admin only; everyone else sees the pool name as text.
function NodePoolCell({ node, pools, canManage }: { node: GpuNode; pools: NodePool[]; canManage: boolean }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const setPool = useSetNodePool();
  // A pool dedicates GPUs to a tenant; a node with no GPU (control plane, storage) has nothing to
  // place, so pool membership is meaningless — show a dash, never a selector.
  if ((node.device_count ?? 0) === 0) {
    return <span className="text-muted" aria-label={t('admin.nodes.pools.noGpu')}>-</span>;
  }
  if (!canManage) {
    return node.pool_name
      ? <span className="gs-tag">{node.pool_name}</span>
      : <span className="gs-tag">{t('admin.nodes.pools.sharedUnassigned')}</span>;
  }
  const options = pools.filter((p) => p.cluster_id === node.cluster_id);
  return (
    <Select
      className="gs-input gs-input-sm w-auto text-xs"
      aria-label={t('admin.nodes.pools.colPool')}
      value={node.pool_id ?? ''}
      disabled={setPool.isPending}
      onChange={(e) =>
        setPool.mutate({ nodeId: node.id, pool_id: e.target.value || null }, {
          onSuccess: () => pushToast('success', t('admin.nodes.pools.nodeMoved', { node: node.hostname })),
          onError: (err) => pushToast('error', humanizeError(asApiError(err))),
        })}
    >
      <option value="">{t('admin.nodes.pools.sharedUnassigned')}</option>
      {/* No pools yet: the only choice is shared. Point the admin at the create panel below. */}
      {options.length === 0 && <option value="" disabled>{t('admin.nodes.pools.noPoolsYet')}</option>}
      {options.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </Select>
  );
}

export function AdminNodes() {
  const { t } = useTranslation();
  const table = useTableState('', { sort: 'hostname', dir: 'asc' });
  const [params, setParams] = useSearchParams();
  const claims = useAuthStore((s) => s.claims);
  const isSuper = claims.global_role === 'super_admin';
  // Page tab (?tab=nodes|pools). node.read is super_admin only, so an org_admin lands on the pools
  // tab and never sees the inventory. The status filter is its own key (?status=).
  const tab: 'nodes' | 'pools' = !isSuper || params.get('tab') === 'pools' ? 'pools' : 'nodes';
  const setTab = (v: 'nodes' | 'pools') => setParams((p) => {
    const next = new URLSearchParams(p);
    if (v === 'nodes') next.delete('tab'); else next.set('tab', v);
    return next;
  }, { replace: true });
  const statusFilter = (params.get('status') ?? '') as NodeStatus | '';
  const setStatusFilter = (v: string) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (v) next.set('status', v); else next.delete('status');
    return next;
  }, { replace: true });
  const confirm = useConfirm();

  const { data: nodes, isLoading, isError, error, refetch } = useNodes(statusFilter ? { status: statusFilter } : {}, { enabled: isSuper });
  const poolsData = useNodePools(undefined, { enabled: isSuper }).data;
  const pools = useMemo(() => poolsData ?? [], [poolsData]);
  const cordon = useCordonNode();
  const pushToast = useUiStore((s) => s.pushToast);

  const summary = useMemo(() => {
    const list = nodes ?? [];
    return {
      total: list.length,
      ready: list.filter((n) => n.status === 'ready' || n.status === 'busy').length,
      cordoned: list.filter((n) => n.status === 'cordoned').length,
      availGpus: list.filter((n) => n.status === 'ready').reduce((sum, n) => sum + (n.device_count ?? 0), 0),
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
        onSuccess: () => pushToast('success', t(next ? 'admin.nodes.cordonedToast' : 'admin.nodes.uncordonedToast', { name: n.hostname })),
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
        <span className="gs-tag">{n.cluster_name ?? n.cluster_id ?? '-'}</span>
      ),
    },
    {
      key: 'role',
      header: t('admin.nodes.colRole'),
      sortBy: (n) => n.role ?? '',
      render: (n) => (n.role ? <span className="gs-tag">{t(`enum.nodeRole.${n.role}`, { defaultValue: n.role })}</span> : '-'),
    },
    {
      key: 'hostname',
      header: t('admin.nodes.colNode'),
      sortBy: (n) => n.hostname,
      render: (n) => (
        // Region is a manual-registration-only label and is empty for every operator-reported
        // node, so it is not surfaced here; the API still carries it.
        <Link to={`/admin/nodes/${n.id}/devices`} className="font-semibold text-primary truncate hover:underline">
          {n.hostname}
        </Link>
      ),
    },
    {
      key: 'pool',
      header: t('admin.nodes.pools.colPool'),
      sortBy: (n) => n.pool_name ?? '',
      render: (n) => <NodePoolCell node={n} pools={pools} canManage={isSuper} />,
    },
    { key: 'gpu_mode', header: t('admin.nodes.colMode'), hideOnMobile: true, sortBy: (n) => n.gpu_mode ?? '', render: (n) => <span className="gs-tag">{t(`enum.deviceMode.${n.gpu_mode}`, { defaultValue: n.gpu_mode })}</span> },
    { key: 'device_count', header: t('admin.nodes.colGpu'), align: 'right', sortBy: (n) => n.device_count ?? 0, render: (n) => t('admin.nodes.gpuCount', { count: n.device_count }) },
    { key: 'running_sessions', header: t('admin.nodes.colRunning'), align: 'right', hideOnMobile: true, sortBy: (n) => n.running_sessions ?? 0, render: (n) => <span className="gs-num">{n.running_sessions ?? 0}</span> },
    { key: 'cpu', header: t('admin.nodes.colCpuMem'), hideOnMobile: true, sortBy: (n) => n.cpu ?? 0, render: (n) => `${n.cpu} core · ${n.mem_gb} GiB · ${n.disk_gb ?? 0} GiB` },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (n) => n.status,
      render: (n) => <StatusPill kind={n.status} label={t(`enum.nodeStatus.${n.status}`)} />,
    },
    {
      key: 'heartbeat_at',
      header: t('admin.nodes.colHeartbeat'),
      align: 'right',
      sortBy: (n) => (n.heartbeat_at ? new Date(n.heartbeat_at).getTime() : 0),
      render: (n) => <Timestamp value={n.heartbeat_at} compact className="text-muted text-xs" />,
    },
    {
      key: 'actions',
      header: t('admin.nodes.colQuickActions'),
      sortable: false,
      align: 'right',
      render: (n) => (
        // gShare cordon/drain act on the GPU placement ledger — a GPU-less node has
        // nothing to place, so the actions are no-ops and only invite mistakes.
        (n.device_count ?? 0) === 0 ? <span className="text-muted text-xs">-</span> : (
        <div className="flex gap-2 justify-end">
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => toggleCordon(n)} disabled={cordon.isPending}>
            {n.status === 'cordoned' ? t('admin.nodes.uncordon') : t('admin.nodes.cordon')}
          </button>
          <Link to={`/admin/nodes/${n.id}/drain`} className="gs-btn gs-btn-sm gs-btn-danger">{t('admin.nodes.drain')}</Link>
        </div>
        )
      ),
    },
  ], [t, cordon.isPending, toggleCordon, pools, isSuper]);

  const all = nodes ?? [];
  const matched = all.filter((n) => {
    const q = table.query.trim().toLowerCase();
    return !q || n.hostname.toLowerCase().includes(q) || (n.cluster_name ?? '').toLowerCase().includes(q) || (n.pool_name ?? '').toLowerCase().includes(q);
  });
  const rows = sortRows(matched, sortAccessor(columns, table.sort), table.dir);

  return (
    <div>
      <PageHeader
        title={t('admin.nodes.title')}
        description={t('admin.nodes.subtitle')}
      />

      {isSuper && (
        <Tabs
          ariaLabel={t('admin.nodes.title')}
          items={[
            { key: 'nodes', label: t('admin.nodes.tabNodes') },
            { key: 'pools', label: t('admin.nodes.pools.title') },
          ]}
          active={tab}
          onChange={(k) => setTab(k as 'nodes' | 'pools')}
        />
      )}

      {tab === 'pools' ? <NodePoolsPanel isSuper={isSuper} /> : (
      <>
      <section className="gs-panel grid md:grid-cols-4 mb-4">
        <Figure label={t('admin.nodes.nodesUp')} value={summary.ready} unit={`/ ${summary.total}`} />
        <Figure
          label={t('admin.nodes.availGpus')}
          value={summary.availGpus}
          unit={`/ ${summary.devices}`}
          foot={t('admin.nodes.availGpusFoot')}
        />
        <Figure label={t('enum.nodeStatus.cordoned')} value={summary.cordoned} />
        <Figure label={t('enum.nodeStatus.offline')} value={summary.offline} />
      </section>

      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.nodes.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={() => { table.clear(); setStatusFilter(''); }}
      >
        <label className="gs-sr-only" htmlFor="gs-node-status">{t('admin.nodes.statusFilter')}</label>
        <Select id="gs-node-status" className="gs-input w-auto" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">{t('common.all')}</option>
          <option value="ready">{t('enum.nodeStatus.ready')}</option>
          <option value="busy">{t('enum.nodeStatus.busy')}</option>
          <option value="cordoned">{t('enum.nodeStatus.cordoned')}</option>
          <option value="offline">{t('enum.nodeStatus.offline')}</option>
        </Select>
      </TableToolbar>

      <div className="gs-card">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <TableSkeleton rows={4} columns={6} />
        ) : rows.length === 0 ? (
          (table.isFiltered || statusFilter)
            ? <NoResults query={table.query} onClear={() => { table.clear(); setStatusFilter(''); }} />
            : <EmptyState icon={<HardDrives size={26} />} title={t('admin.nodes.empty')} description={t('admin.nodes.emptyDescription')} />
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
        <p className="text-muted text-2xs mt-3">
          {t('admin.nodes.hint')}
        </p>
      </div>
      </>
      )}
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
  const confirm = useConfirm();

  const submit = async () => {
    if (!node) return;
    if (mode === 'force_terminate') {
      const ok = await confirm({
        title: t('admin.nodes.confirmDrainKillTitle', { node: node.hostname }),
        body: t('admin.nodes.confirmDrainKill'),
        confirmLabel: t('admin.nodes.drainRun'),
        destructive: true,
        confirmText: node.hostname,
      });
      if (!ok) return;
    }
    drain.mutate(
      { nodeId: node.id, mode },
      {
        onSuccess: (res) => {
          const r = res as unknown as { affected_sessions: string[]; rescheduled?: string[]; parked?: string[]; terminated?: string[]; failed?: string[] };
          pushToast('success', t('admin.nodes.drainDone', {
            node: node.hostname,
            rescheduled: r.rescheduled?.length ?? 0,
            parked: r.parked?.length ?? 0,
            terminated: r.terminated?.length ?? 0,
            failed: r.failed?.length ?? 0,
          }));
          navigate('/admin/nodes');
        },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.nodes.drainTitle') + (node ? ' - ' + (node.hostname) : '')}
        crumbs={[{ label: t('admin.nodes.title'), to: '/admin/nodes' }, { label: t('admin.nodes.drainTitle') }]}
      />
      {!node ? <p className="text-muted">{t('admin.nodes.notFound')}</p> : (
      <div className="gs-card">
      <div className="grid gap-3">
        <label className="text-sm font-semibold">
          {t('admin.nodes.mode')}
          <Select className="gs-input mt-1 w-full" value={mode} onChange={(e) => setMode(e.target.value as 'reschedule' | 'force_terminate')}>
            <option value="reschedule">{t('admin.nodes.modeReschedule')}</option>
            <option value="force_terminate">{t('admin.nodes.modeForceTerminate')}</option>
          </Select>
        </label>
        <p className="text-muted text-xs">
          {t('admin.nodes.drainNote')}
        </p>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={[]} />
        <button type="button" className="gs-btn gs-btn-primary disabled:opacity-50" onClick={submit} disabled={drain.isPending}>{t('admin.nodes.drainRun')}</button>
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/nodes')}>{t('common.cancel')}</button>
      </div>
      </div>
      )}
    </div>
  );
}

// One host-compute figure for the node devices page: allocated vs capacity with a meter.
function HostStat({ label, used, total, unit }: { label: string; used: number; total: number; unit: string }) {
  const pctv = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return <Figure label={label} value={used} unit={`/ ${total} ${unit}`} bar={{ value: pctv, variant: 'warn' }} />;
}

// GPU device view, on its own page at /admin/nodes/:nodeId/devices.
export function NodeDevicesPage() {
  const { t } = useTranslation();
  const { nodeId = '' } = useParams();
  const node = useNode(nodeId).data;
  const { data: devices, isLoading, isError, error, refetch } = useGpuDevices(nodeId);
  const setDeviceMode = useSetDeviceMode();
  const pushToast = useUiStore((s) => s.pushToast);
  // Sessions placed on THIS node: matched by node_id (inventory link) or the reported hostname —
  // the hostname path is what CPU sessions have.
  const { data: allSessions } = useAllSessions({});
  type NodeSessionRow = {
    id: string; name?: string | null; status: string; owner_name?: string | null;
    owner_user_id?: string | null; resource_class: string; mode?: string | null;
    gpu_mem_mb?: number | null; gpu_cores?: number | null; cpu?: number | null; mem_gb?: number | null;
    node_id?: string | null; node_hostname?: string | null;
  };
  const ACTIVE = ['pending', 'preparing', 'running', 'paused', 'terminating'];
  const nodeSessions = ((allSessions ?? []) as unknown as NodeSessionRow[]).filter((x) =>
    ACTIVE.includes(x.status)
    && (x.node_id === nodeId || (!!node?.hostname && x.node_hostname === node.hostname)));

  const columns: Column<GpuDevice>[] = [
    { key: 'model', header: t('admin.nodes.colModel'), render: (d) => <b>{d.model}</b> },
    {
      key: 'mode',
      header: t('admin.nodes.colMode'),
      render: (d) => (
        <span className="flex items-center gap-1.5">
          <span className="gs-tag">{t(`enum.deviceMode.${d.mode}`, { defaultValue: d.mode })}</span>
          {d.mode_state && d.mode_state !== 'ready' && (
            <span title={t('admin.nodes.modeStateHint', { target: d.desired_mode ?? '-' })}>
              <StatusPill kind={d.mode_state} label={t(`admin.nodes.modeState.${d.mode_state}`, { defaultValue: d.mode_state })} />
            </span>
          )}
        </span>
      ),
    },
    {
      key: 'pool',
      header: t('admin.nodes.colPool'),
      sortable: false,
      render: (d) => (
        <Select
          className="gs-input gs-input-sm w-auto text-xs"
          aria-label={t('admin.nodes.colPool')}
          value={d.desired_mode ?? d.mode}
          disabled={setDeviceMode.isPending}
          onChange={(e) =>
            setDeviceMode.mutate(
              { deviceId: d.id, desired_mode: e.target.value as GpuDevice['mode'] },
              {
                onSuccess: () => pushToast('success', t('admin.nodes.poolChangeQueued')),
                onError: (err) => pushToast('error', humanizeError(asApiError(err))),
              },
            )
          }
        >
          <option value="fractional">{t('enum.deviceMode.fractional')}</option>
          <option value="exclusive">{t('enum.deviceMode.exclusive')}</option>
          {/* MIG is hidden from selection (unimplemented); an already-mig card still displays. */}
          {(d.desired_mode ?? d.mode) === 'mig' && <option value="mig" disabled>{t('enum.deviceMode.mig')}</option>}
        </Select>
      ),
    },
    {
      key: 'mem',
      header: t('admin.nodes.colVram'),
      render: (d) => `${formatVram(d.used_mem_mb)} / ${formatVram(d.total_mem_mb)}`,
    },
    { key: 'cores', header: t('admin.nodes.colCores'), render: (d) => `${d.used_cores}% / ${d.total_cores}%` },
    { key: 'status', header: t('common.status'), render: (d) => <StatusPill kind={d.status} label={t(`enum.nodeStatus.${d.status}`, { defaultValue: d.status })} /> },
    { key: 'bound', header: t('admin.nodes.colBoundSessions'), render: (d) => d.bound_sessions.length },
  ];

  return (
    <div className="w-full">
      <PageHeader
        title={node?.hostname ?? t('admin.nodes.devicesTitle')}
        crumbs={[{ label: t('admin.nodes.title'), to: '/admin/nodes' }, { label: node?.hostname ?? t('admin.nodes.devicesTitle') }]}
      />
      {/* Host compute on this node: what sessions hold vs capacity. GPU-attributed sessions only -
          CPU-class pods are placed by the k8s scheduler and are not pinned to a node here. */}
      {node && (
        <section className="gs-panel grid md:grid-cols-3 mb-4">
          <HostStat label="CPU" used={node.alloc_cpu ?? 0} total={node.cpu} unit="vCPU" />
          <HostStat label={t('admin.nodes.hostMem')} used={node.alloc_mem_gb ?? 0} total={node.mem_gb} unit="GiB" />
          <HostStat label={t('admin.nodes.hostDisk')} used={node.alloc_disk_gb ?? 0} total={node.disk_gb ?? 0} unit="GB" />
        </section>
      )}
      <div className="gs-card">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <p className="text-muted">{t('common.loading')}</p>
        ) : (
          <Table columns={columns} rows={devices ?? []} rowKey={(d) => d.id} empty={t('admin.nodes.noDevices')} />
        )}
      </div>

      {/* WHAT RUNS HERE: the active sessions placed on this node (GPU-bound or, for CPU
          sessions, operator-reported). Row click opens the monitor's overlay via deep link. */}
      <div className="gs-card mt-4">
        <h2 className="font-bold mb-3">{t('admin.nodes.sessionsTitle')}</h2>
        {nodeSessions.length === 0 ? (
          <p className="text-muted text-sm">{t('admin.nodes.noSessions')}</p>
        ) : (
          <Table
            columns={[
              { key: 'name', header: t('common.name'), render: (x: NodeSessionRow) => (
                <Link to={`/admin/monitor?session=${x.id}`} className="font-semibold text-primary hover:underline">{x.name || x.id}</Link>
              ) },
              { key: 'owner', header: t('admin.monitor.colOwner'), render: (x: NodeSessionRow) => x.owner_name ?? x.owner_user_id ?? '-' },
              { key: 'status', header: t('common.status'), render: (x: NodeSessionRow) => <StatusPill kind={x.status} label={sessionStatusLabel(x.status)} /> },
              { key: 'slice', header: t('admin.nodes.colSlice'), sortable: false, render: (x: NodeSessionRow) => (
                <span className="gs-num text-xs">
                  {x.resource_class === 'gpu'
                    ? [x.mode, x.gpu_mem_mb ? formatVram(x.gpu_mem_mb) : null, x.gpu_cores != null ? `${x.gpu_cores}%` : null].filter(Boolean).join(' · ')
                    : ['CPU', x.cpu != null ? `${x.cpu}c` : null, x.mem_gb != null ? `${x.mem_gb}GiB` : null].filter(Boolean).join(' · ')}
                </span>
              ) },
            ]}
            rows={nodeSessions}
            rowKey={(x) => x.id}
          />
        )}
      </div>
    </div>
  );
}
