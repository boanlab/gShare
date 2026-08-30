import { useEffect, useState, useRef } from 'react';
import { Select } from '@/components/Select';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  useVolumes, useVolume, useCreateVolume, useDeleteVolume, useStorageQuotaUsage,
  useVolumePermissions, useGrantPermission, useRevokePermission, useLeaveShare,
  useUpdateVolumeQuota,
  type CreateVolumeBody,
} from '@/api/hooks/useVolumes';
import { Table, TableToolbar, Pagination, type Column } from '@/components/Table';
import { PageHeader } from '@/components/PageHeader';
import { Dialog } from '@/components/Dialog';
import { StatusPill } from '@/components/StatusPill';
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { Field, DisabledReason } from '@/components/Field';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useAuthStore } from '@/auth/authStore';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatGiB, roleLabel, accessModeLabel, scopeLabel, sessionStatusLabel } from '@/lib/format';
import i18n from '@/i18n';
import { Database } from '@/components/icons';
import { BlockGauge } from '@/components/BlockGauge';

// The backend's VolumeRead: { id, scope, scope_id, type, access_mode, quota_gb, used_gb }.
type Vol = Record<string, unknown> & {
  owner_id?: string | null;
  owner_name?: string | null;
  id: string; name?: string; scope?: string; scope_id?: string; type?: string; access_mode?: string; quota_gb?: number; used_gb?: number;
};

// Volume types. The `value` is the code the API uses; the labels and descriptions are translated.
const VOLUME_TYPES = [
  { value: 'home', labelKey: 'volume.type.home', descKey: 'volume.type.homeDesc' },
  { value: 'group', labelKey: 'volume.type.group', descKey: 'volume.type.groupDesc' },
  { value: 'dataset', labelKey: 'volume.type.dataset', descKey: 'volume.type.datasetDesc' },
  { value: 'scratch', labelKey: 'volume.type.scratch', descKey: 'volume.type.scratchDesc' },
] as const;
function volumeTypeLabel(type?: string): string {
  if (!type) return '-';
  const spec = VOLUME_TYPES.find((v) => v.value === type);
  return spec ? i18n.t(spec.labelKey) : type;
}

export function VolumePage() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch } = useVolumes();
  const del = useDeleteVolume();
  const confirm = useConfirm();
  const table = useTableState('', { sort: 'id', dir: 'asc' });
  const pushToast = useUiStore((s) => s.pushToast);
  const userId = useAuthStore((s) => s.claims.sub) ?? '';
  const memberships = useAuthStore((s) => s.memberships);
  const projectName = (pid?: string) => memberships.find((m) => m.group_id === pid)?.project_name ?? pid ?? '-';
  const scopeText = (v: Vol) => {
    if (v.scope === 'group') return t('volume.scopeGroup', { name: projectName(v.scope_id) });
    if (v.scope === 'user') {
      if (v.scope_id === userId) return t('volume.scopeMine');
      return t('volume.scopeShared', { name: v.owner_name ?? v.scope_id });
    }
    return scopeLabel(v.scope);
  };

  // A user-scope volume someone else owns reached this list through a share; "delete" on it
  // must mean "leave the share", never the owner's data.
  const isSharedToMe = (v: Vol) => v.scope === 'user' && v.scope_id !== userId;
  const leave = useLeaveShare();
  const onLeave = async (v: Vol) => {
    const ok = await confirm({
      title: t('volume.confirmLeaveTitle', { name: v.name || v.id }),
      body: t('volume.confirmLeave'),
      confirmLabel: t('volume.leaveShare'),
    });
    if (!ok) return;
    leave.mutate({ volumeId: v.id, userId }, {
      onSuccess: () => pushToast('success', t('volume.leftShare')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const onDelete = async (v: Vol) => {
    const ok = await confirm({
      title: t('volume.confirmDeleteTitle', { name: v.name || v.id }),
      body: t('volume.confirmDelete'),
      consequences: [
        t('volume.consequenceData', { used: formatGiB(v.used_gb ?? 0) }),
        t('volume.consequenceSnapshots'),
      ],
      confirmLabel: t('common.delete'),
      destructive: true,
      // Shared volumes require the name to be typed.
      confirmText: v.access_mode === 'ROX' || v.scope === 'group' ? (v.name || v.id) : undefined,
    });
    if (!ok) return;
    del.mutate(v.id, {
      onSuccess: () => pushToast('success', t('volume.deleted')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const all = (data ?? []) as Vol[];
  const matched = all.filter((v) => {
    const q = table.query.trim().toLowerCase();
    if (!q) return true;
    return (v.name ?? '').toLowerCase().includes(q) || v.id.toLowerCase().includes(q) || (v.type ?? '').toLowerCase().includes(q);
  });
  const rows = sortRows(matched, {
    id: (v: Vol) => v.name || v.id,
    scope: (v: Vol) => scopeText(v),
    access_mode: (v: Vol) => v.access_mode ?? '',
    quota: (v: Vol) => v.quota_gb ?? 0,
    used: (v: Vol) => (v.quota_gb ? (v.used_gb ?? 0) / v.quota_gb : 0),
  }[table.sort ?? 'id'] ?? null, table.dir);
  const pageRows = rows.slice((table.page - 1) * 25, table.page * 25);

  const columns: Column<Vol>[] = [
    {
      key: 'id',
      header: t('volume.colVolume'),
      sortBy: (v) => v.name || v.id,
      // One line: name + type tag. The raw vol_ id means nothing to a user (admin surfaces and
      // the detail pages keep it); every action wires the id internally.
      render: (v) => (
        <span className="inline-flex items-center gap-2 min-w-0">
          <b className="truncate">{v.name || volumeTypeLabel(v.type)}</b>
          <span className="gs-tag shrink-0">{volumeTypeLabel(v.type)}</span>
          {((v as { shared_count?: number }).shared_count ?? 0) > 0 && (
            <span className="gs-tag shrink-0 text-primary" title={t('volume.sharedCountTitle', { count: (v as { shared_count?: number }).shared_count })}>
              {t('volume.sharedTag')}
            </span>
          )}
        </span>
      ),
    },
    { key: 'scope', header: t('volume.colScope'), sortBy: (v) => scopeText(v), hideOnMobile: true, render: (v) => scopeText(v) },
    { key: 'access_mode', header: t('volume.colAccessMode'), sortBy: (v) => v.access_mode ?? '', hideOnMobile: true, render: (v) => accessModeLabel(v.access_mode) },
    {
      key: 'quota',
      header: t('volume.colQuota'),
      sortBy: (v) => (v.quota_gb ? (v.used_gb ?? 0) / v.quota_gb : 0),
      align: 'right',
      render: (v) => {
        const pct = v.quota_gb ? Math.min(100, Math.round(((v.used_gb ?? 0) / v.quota_gb) * 100)) : 0;
        return (
          // The meter spans exactly the width of the figures above it — an 80px bar under a wider
          // number read as a wrong ratio.
          <span className="inline-flex items-center gap-2.5" title={t('volume.usagePercent', { percent: pct })}>
            <span className="gs-num">{formatGiB(v.used_gb ?? 0)} / {formatGiB(v.quota_gb ?? 0)}</span>
            <BlockGauge value={pct} label={t('volume.usagePercent', { percent: pct })} />
          </span>
        );
      },
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (v) => (
        <div className="flex gap-1.5 justify-end flex-nowrap">
          {isSharedToMe(v) ? (
            <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={leave.isPending} onClick={() => onLeave(v)}>{t('volume.leaveShare')}</button>
          ) : (
            <>
              <button type="button" className="gs-btn gs-btn-sm" onClick={() => setShareVol(v)}>{t('volume.share')}</button>
              <button type="button" className="gs-btn gs-btn-sm" onClick={() => setQuotaVol(v)}>{t('volume.expand')}</button>
              <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={del.isPending} onClick={() => onDelete(v)}>{t('common.delete')}</button>
            </>
          )}
        </div>
      ),
    },
  ];

  const [newOpen, setNewOpen] = useState(false);
  // Row click: who is mounting this volume, in a panel below (the org→dept pattern).
  const [selVol, setSelVol] = useState<Vol | null>(null);
  const [shareVol, setShareVol] = useState<Vol | null>(null);
  const [quotaVol, setQuotaVol] = useState<Vol | null>(null);

  return (
    <div>
      <PageHeader
        title={t('volume.title')}
        description={t('volume.subtitle')}
        actions={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setNewOpen(true)}>{t('volume.new')}</button>}
      />
      <Dialog open={newOpen} wide title={t('volume.new')} onClose={() => setNewOpen(false)}>
        <NewVolumeForm onDone={() => setNewOpen(false)} />
      </Dialog>
      <Dialog open={!!shareVol} wide title={t('volume.shareTitle') + (shareVol ? ' - ' + (shareVol.name || '') : '')} onClose={() => setShareVol(null)}>
        {shareVol && <VolumeShareForm volumeId={shareVol.id} onDone={() => setShareVol(null)} />}
      </Dialog>
      <Dialog open={!!quotaVol} title={`${t('volume.quotaTitle')}${quotaVol ? ` - ${quotaVol.name || volumeTypeLabel(quotaVol.type)}` : ''}`} onClose={() => setQuotaVol(null)}>
        {quotaVol && <VolumeQuotaForm volumeId={quotaVol.id} onDone={() => setQuotaVol(null)} />}
      </Dialog>
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('volume.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
      />
      <div className="gs-panel overflow-hidden">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <div className="p-4"><TableSkeleton rows={4} columns={5} /></div>
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : (
              <EmptyState
                icon={<Database size={26} />}
                title={t('volume.emptyTitle')}
                description={t('volume.emptyDescription')}
                action={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setNewOpen(true)}>{t('volume.new')}</button>}
              />
            )
        ) : (
          <div>
            <Table
              caption={t('volume.title')}
              columns={columns}
              rows={pageRows}
              rowKey={(v) => v.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
              onRowClick={(v) => setSelVol((cur) => (cur?.id === v.id ? null : v))}
              expandedKey={selVol?.id ?? null}
              renderExpansion={(v) => <VolumeMountsPanel vol={v} />}
            />
          </div>
        )}
      </div>
      <Pagination page={table.page} pageSize={25} total={rows.length} onPage={table.setPage} />

    </div>
  );
}

// The selected volume's ACTIVE mounts: which sessions hold it right now — also the reason a
// delete may refuse with volume_mounted.
export function VolumeMountsPanel({ vol }: { vol: { id: string; name?: string | null } }) {
  const { t } = useTranslation();
  const detail = useVolume(vol.id).data as (Vol & {
    active_mounts?: { session_id: string; name?: string | null; status: string; mount_path: string; mode: string; owner_user_id?: string | null; owner_name?: string | null }[];
  }) | undefined;
  const myId = useAuthStore((st) => (st.claims as { sub?: string }).sub);
  const mounts = detail?.active_mounts ?? [];
  return (
    <div className="mt-1 rounded-card bg-surface-2/60 px-4 py-3">
      <h3 className="text-xs font-semibold text-muted mb-2">{t('volume.mountsPanelTitle', { name: vol.name || vol.id })} <span className="font-normal">{mounts.length}</span></h3>
      {mounts.length === 0 ? (
        <p className="text-muted text-sm">{t('volume.mountsPanelEmpty')}</p>
      ) : (
        <ul className="divide-y divide-border/60">
          {mounts.map((m) => (
            <li key={m.session_id} className="py-2 flex items-center gap-3 text-sm min-w-0">
              {m.owner_user_id === myId ? (
                <Link to={`/sessions/${m.session_id}`} className="font-semibold text-primary hover:underline truncate">{m.name || m.session_id}</Link>
              ) : (
                <b className="truncate">{m.name || m.session_id}</b>
              )}
              {m.owner_name && <span className="text-muted text-xs shrink-0">{m.owner_name}</span>}
              <StatusPill kind={m.status} label={sessionStatusLabel(m.status)} />
              <span className="ml-auto text-muted text-xs gs-num shrink-0">{m.mode === 'ro' ? 'RO' : 'RW'} · {m.mount_path}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// New volume, on its own page at /data/new.
export function NewVolumeForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const create = useCreateVolume();
  const defaultScopeId = useAuthStore((s) => s.claims.sub) ?? '';
  const memberships = useAuthStore((s) => s.memberships);

  const [scope, setScope] = useState<'user' | 'group'>('user');
  // Group volumes are created by group administrators only (decision 4-10a): the scope choice is
  // hidden for plain members, and the group list offers only the groups the caller administers.
  const globalRole = useAuthStore((st) => st.claims.global_role);
  const orgAdminOrgs = useAuthStore((st) => st.orgAdminOrgs);
  const adminMemberships = memberships.filter((m) =>
    ['group_admin', 'org_admin'].includes(m.role)
    || globalRole === 'super_admin'
    || (m.org_id != null && orgAdminOrgs.includes(m.org_id)));
  const canCreateGroup = adminMemberships.length > 0;
  // A personal volume always belongs to the caller. A group volume is picked from their memberships,
  // with group_id as the value.
  const [projectId, setProjectId] = useState(adminMemberships[0]?.group_id ?? '');
  const [name, setName] = useState('');
  const [type, setType] = useState<CreateVolumeBody['type']>('dataset');
  const [accessMode, setAccessMode] = useState<CreateVolumeBody['access_mode']>('RWX');
  const [quota, setQuota] = useState('10');
  const scopeId = scope === 'user' ? defaultScopeId : projectId;
  // The per-scope storage policy limit. A quota beyond the remaining headroom warns and blocks, using
  // the same rule as the backend's _assert_storage_quota.
  const usage = useStorageQuotaUsage(scope, scopeId || undefined).data as
    | { has_limit?: boolean; remaining_gb?: number | null; allocated_gb?: number; limit_gb?: number; physical_remaining_gb?: number | null }
    | undefined;
  const reqGb = Number(quota) || 0;
  const overLimit = !!usage?.has_limit && usage.remaining_gb != null && reqGb > usage.remaining_gb;
  // Physics beats policy: even with no policy limit, the pool can only back so much.
  const physRemaining = usage?.physical_remaining_gb ?? null;
  const overPhysical = physRemaining != null && reqGb > physRemaining;
  // Which types each scope offers: personal hides group, group hides home, and scratch cannot be
  // created here.
  const typeAllowed = (v: string) => v !== 'scratch' && (scope === 'user' ? v !== 'group' : v !== 'home');
  const availableTypes = VOLUME_TYPES.filter((v) => typeAllowed(v.value));
  const typeDescKey = VOLUME_TYPES.find((v) => v.value === type)?.descKey;
  const valid = scopeId.trim().length > 0 && name.trim().length > 0 && Number(quota) >= 0 && !overLimit && !overPhysical;
  const blockers = [
    !name.trim() && t('volume.nameLabel'),
    !scopeId.trim() && t('volume.target'),
    overLimit && t('volume.overLimitShort'),
    overPhysical && t('volume.overPhysical', { requested: reqGb }),
  ].filter(Boolean) as string[];
  const dirty = name.trim().length > 0 || quota !== '10';
  useUnsavedGuard(dirty && !create.isPending);

  const submit = () => {
    if (!valid) return;
    create.mutate(
      { scope, scope_id: scopeId.trim(), name: name.trim(), type, access_mode: accessMode, quota_gb: Number(quota) },
      {
        onSuccess: () => { pushToast('success', t('volume.created')); onDone(); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
      <form
        className="gs-card" noValidate
        {...unsavedGuardProps}
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
          <Field label={t('volume.nameLabel')} required className="sm:col-span-2" hint={t('volume.nameHint')}>
            {(ids) => <input {...ids} className="gs-input w-full mt-1" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('volume.namePlaceholder')} maxLength={80} autoFocus autoComplete="off" />}
          </Field>
          <label className="block"><span className="text-xs font-semibold text-muted">{t('volume.ownerScope')}</span>
            <Select className="gs-input w-full mt-1" value={scope} onChange={(e) => {
              const next = e.target.value as 'user' | 'group';
              setScope(next);
              // If the selected type is hidden in the new scope, fall back to a valid one.
              const ok = (v: string) => (next === 'user' ? v !== 'group' : v !== 'home');
              if (!ok(type ?? '')) setType('dataset');
            }}>
              <option value="user">{t('volume.scopeMine')}</option>
              {canCreateGroup && <option value="group">{t('volume.type.group')}</option>}
            </Select>
            {scope === 'group' && <span className="text-muted text-2xs mt-1 block">{t('volume.groupAutoShare')}</span>}
            </label>
          {scope === 'user' ? (
            <label className="block"><span className="text-xs font-semibold text-muted">{t('volume.target')}</span>
              <input className="gs-input w-full mt-1" value={t('volume.myAccount')} disabled autoComplete="off" /></label>
          ) : (
            <label className="block"><span className="text-xs font-semibold text-muted">{t('common.group')}</span>
              <Select className="gs-input w-full mt-1" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {adminMemberships.length === 0 && <option value="">{t('volume.noGroup')}</option>}
                {adminMemberships.map((m) => <option key={m.group_id} value={m.group_id}>{m.project_name}</option>)}
              </Select></label>
          )}
          <label className="block col-span-2"><span className="text-xs font-semibold text-muted">{t('common.type')}</span>
            <Select className="gs-input w-full mt-1" value={type} onChange={(e) => setType(e.target.value as CreateVolumeBody['type'])}>
              {availableTypes.map((v) => <option key={v.value} value={v.value}>{t(v.labelKey)}</option>)}</Select>
            {typeDescKey && <span className="text-muted text-2xs mt-1 block">{t(typeDescKey)}</span>}</label>
          <Field label={t('volume.colAccessMode')}>
            {(ids) => (
              <Select {...ids} className="gs-input w-full" value={accessMode} onChange={(e) => setAccessMode(e.target.value as CreateVolumeBody['access_mode'])}>
                <option value="RWX">{t('volume.accessRw')}</option><option value="ROX">{t('volume.accessRo')}</option></Select>
            )}
          </Field>
          <Field
            label={t('volume.quotaGib')}
            required
            error={overPhysical ? t('volume.overPhysical', { requested: reqGb }) : overLimit ? t('volume.overLimit', { requested: reqGb }) : null}
          >
            {(ids) => (
              <input
                {...ids}
                className={`gs-input w-full ${overLimit ? 'border-danger' : ''}`}
                type="number"
                inputMode="numeric"
                min={1}
                max={usage?.has_limit && usage.remaining_gb != null ? usage.remaining_gb : 65536}
                step={10}
                value={quota}
                onChange={(e) => setQuota(e.target.value)} autoComplete="off" />
            )}
          </Field>
          {usage && (() => {
            // How much this scope can still provision right now: policy headroom capped by physics.
            const creatable = usage.has_limit && usage.remaining_gb != null
              ? (physRemaining != null ? Math.min(usage.remaining_gb, physRemaining) : usage.remaining_gb)
              : physRemaining;
            return (
              <div className={`col-span-2 text-xs ${overLimit || overPhysical ? 'text-danger font-semibold' : 'text-muted'}`}>
                <div>{usage.has_limit
                  ? t('volume.policyUsage', { used: usage.allocated_gb, limit: usage.limit_gb, remaining: usage.remaining_gb })
                  : t('volume.noPolicyLimit', { used: usage.allocated_gb })}</div>
                <div className="mt-0.5">{creatable != null ? t('volume.creatableUpTo', { gb: creatable }) : t('volume.creatableUnlimited')}</div>
                {overLimit && <div className="mt-0.5">{t('volume.overLimit', { requested: reqGb })}</div>}
              </div>
            );
          })()}
        </div>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={valid ? [] : blockers} />
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
            {create.isPending ? t('volume.creating') : t('common.create')}
          </button>
          <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
        </div>
      </form>
  );
}

// Volume sharing, on its own page at /data/:volumeId/share.
export function VolumeShareForm({ volumeId, onDone }: { volumeId: string; onDone: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const myId = useAuthStore((st) => (st.claims as { sub?: string }).sub);
  const volume = useVolume(volumeId).data as Vol | undefined;
  const { data: rawPerms = [] } = useVolumePermissions(volumeId);
  const perms = rawPerms as Array<{ user_id: string; user_name?: string | null; role: string }>;
  const grant = useGrantPermission(volumeId);
  const revoke = useRevokePermission(volumeId);

  const [uid, setUid] = useState('');
  const [role, setRole] = useState<'rw' | 'ro' | 'owner'>('rw');
  // A read-only (ROX) volume can only ever be shared read-only: rw/owner would promise a write
  // the mount gate rejects anyway.
  const readOnlyVolume = volume?.access_mode === 'ROX';
  useEffect(() => { if (readOnlyVolume && role !== 'ro') setRole('ro'); }, [readOnlyVolume, role]);
  const [touched, setTouched] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [resolved, setResolved] = useState<{ id: string; name: string; email: string } | null>(null);
  // Staged changes: nothing hits the API until 저장. adds are keyed by user_id; removes hold ids.
  const [adds, setAdds] = useState<Array<{ id: string; name: string; email: string; role: string }>>([]);
  const [removes, setRemoves] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const dirty = adds.length > 0 || removes.size > 0;
  const skipGuard = useRef(false);
  useUnsavedGuard(dirty && !saving, () => dirty && !saving && !skipGuard.current);

  const emailValid = /.+@.+\..+/.test(uid.trim());
  const emailError = touched && uid.trim() !== '' && !emailValid ? t('volume.emailInvalid') : undefined;

  const lookup = async () => {
    setResolving(true);
    try {
      const res = await fetch(`/api/v1/users/resolve?email=${encodeURIComponent(uid.trim())}`, {
        headers: { Authorization: `Bearer ${useAuthStore.getState().accessToken}` },
      });
      if (!res.ok) throw new Error();
      const u = (await res.json()) as { id: string; name: string; email?: string };
      if (u.id === myId) { pushToast('error', t('volume.cannotShareSelf')); return; }
      if (perms.some((pm) => pm.user_id === u.id && !removes.has(pm.user_id)) || adds.some((a) => a.id === u.id)) {
        pushToast('error', t('volume.alreadyShared')); return;
      }
      setResolved({ id: u.id, name: u.name, email: u.email ?? uid.trim() });
    } catch {
      pushToast('error', t('volume.lookupNotFound', { email: uid.trim() }));
    } finally {
      setResolving(false);
    }
  };

  const stageAdd = () => {
    if (!resolved) return;
    setAdds((a) => [...a, { ...resolved, role }]);
    setResolved(null); setUid(''); setTouched(false); setRole('rw');
  };

  const save = async () => {
    setSaving(true);
    try {
      for (const userId of removes) await revoke.mutateAsync(userId);
      for (const a of adds) await grant.mutateAsync({ user_id: a.id, role: a.role as 'rw' | 'ro' | 'owner' });
      pushToast('success', t('volume.shareSaved'));
      setAdds([]); setRemoves(new Set());
      onDone();
    } catch (e) {
      pushToast('error', humanizeError(asApiError(e)));
    } finally {
      setSaving(false);
    }
  };
  // Cancel always works: drop the staged changes and go back to the list. The unsaved guard
  // must not second-guess an explicit cancel, so it is bypassed through skipGuard.
  const cancel = () => {
    skipGuard.current = true;
    setAdds([]); setRemoves(new Set()); setResolved(null); setUid('');
    onDone();
  };

  const visible = perms.filter((pm) => pm.role !== 'owner');
  return (
      <div className="gs-card space-y-5">
        {volume && <p className="text-muted text-xs -mt-1">{scopeLabel(volume.scope)} · {accessModeLabel(volume.access_mode)}</p>}
        {/* current recipients + staged changes */}
        <section>
          <h2 className="text-xs font-semibold text-muted mb-2">{t('volume.sharedWith')}</h2>
          {visible.length === 0 && adds.length === 0 ? (
            <p className="text-muted text-sm">{t('volume.noShares')}</p>
          ) : (
            <ul className="divide-y divide-border">
              {visible.map((pm) => (
                <li key={pm.user_id} className={`py-2.5 flex items-center gap-3 ${removes.has(pm.user_id) ? 'opacity-50' : ''}`}>
                  <span className="text-sm">
                    <b>{pm.user_name ?? pm.user_id}</b>
                    <span className="text-muted text-xs ml-1.5">· {roleLabel(pm.role)}</span>
                    {removes.has(pm.user_id) && <span className="gs-tag text-danger ml-2">{t('volume.pendingRevoke')}</span>}
                  </span>
                  {removes.has(pm.user_id) ? (
                    <button type="button" className="gs-btn gs-btn-sm ml-auto"
                      onClick={() => setRemoves((r) => { const n = new Set(r); n.delete(pm.user_id); return n; })}>
                      {t('common.undo')}
                    </button>
                  ) : (
                    <button type="button" className="gs-btn gs-btn-sm gs-btn-danger ml-auto"
                      onClick={() => setRemoves((r) => new Set(r).add(pm.user_id))}>
                      {t('volume.revoke')}
                    </button>
                  )}
                </li>
              ))}
              {adds.map((a) => (
                <li key={a.id} className="py-2.5 flex items-center gap-3">
                  <span className="text-sm">
                    <b>{a.name}</b>
                    <span className="text-muted text-xs ml-1.5">· {roleLabel(a.role)}</span>
                    <span className="gs-tag text-free ml-2">{t('volume.pendingAdd')}</span>
                  </span>
                  <button type="button" className="gs-btn gs-btn-sm ml-auto"
                    onClick={() => setAdds((x) => x.filter((y) => y.id !== a.id))}>
                    {t('common.undo')}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* add: lookup → confirm → stage */}
        <section className="border-t border-border pt-4">
          <h2 className="text-xs font-semibold text-muted mb-2">{t('volume.addRecipient')}</h2>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); setTouched(true); if (resolved) stageAdd(); else if (emailValid) void lookup(); }}>
            <div className="flex gap-2 items-end">
              <div className="flex-1">
                <Field label={t('volume.userEmail')} required error={emailError}>
                  {(ids) => (
                    <input {...ids} className="gs-input w-full mt-1" type="email" inputMode="email" autoComplete="email"
                      value={uid} onChange={(e) => { setUid(e.target.value); setResolved(null); }}
                      onBlur={() => setTouched(true)} placeholder="user@example.com" />
                  )}
                </Field>
              </div>
              <Select className="gs-input w-auto" value={role} onChange={(e) => setRole(e.target.value as 'rw'|'ro'|'owner')} aria-label={t('common.role')} disabled={readOnlyVolume}>
                {!readOnlyVolume && (<option value="rw">{roleLabel('rw')}</option>)}<option value="ro">{roleLabel('ro')}</option>
              </Select>
              <button type="submit" className="gs-btn disabled:opacity-50" disabled={!uid.trim() || !emailValid || resolving}>
                {resolving ? t('common.loading') : t('volume.lookupUser')}
              </button>
            </div>
            {resolved && (
              <div className="rounded-ctl border border-border bg-surface-2/50 px-3 py-2.5 flex items-center gap-3 flex-wrap">
                <span className="text-sm"><b>{resolved.name}</b> <span className="text-muted font-mono text-xs">· {resolved.email}</span></span>
                <span className="gs-tag ml-auto">{roleLabel(readOnlyVolume ? 'ro' : role)}</span>
                <button type="button" className="gs-btn gs-btn-primary" onClick={stageAdd}>{t('common.add')}</button>
                <button type="button" className="gs-btn" onClick={() => setResolved(null)}>{t('common.cancel')}</button>
              </div>
            )}
          </form>
        </section>

        {/* apply staged changes */}
        <section className="border-t border-border pt-4 flex items-center justify-end gap-2">
          {dirty && <span className="text-muted text-xs mr-auto">{t('volume.pendingCount', { count: adds.length + removes.size })}</span>}
          <button type="button" className="gs-btn disabled:opacity-50" disabled={!dirty || saving} onClick={cancel}>{t('common.cancel')}</button>
          <button type="button" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!dirty || saving} onClick={save}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </section>
      </div>
  );
}

// Quota change, on its own page at /data/:volumeId/quota. Self-service in both directions: the
// floor is what is in use, the ceiling the scope's storage policy, and the price of the number
// being typed is shown before it is committed.
export function VolumeQuotaForm({ volumeId, onDone }: { volumeId: string; onDone: () => void }) {
  const { t } = useTranslation();
  const volume = useVolume(volumeId).data as Vol | undefined;
  const update = useUpdateVolumeQuota(volumeId);
  const pushToast = useUiStore((s) => s.pushToast);
  const cur = volume?.quota_gb ?? 0;
  const used = volume?.used_gb ?? 0;
  // Headroom under the scope's storage policy — the same rule the backend applies, with this
  // volume's own quota already counted in `allocated`, so the ceiling is cur + remaining.
  const usage = useStorageQuotaUsage((volume?.scope as 'user' | 'group') ?? 'user', volume?.scope_id).data as
    | { has_limit?: boolean; remaining_gb?: number | null; physical_remaining_gb?: number | null }
    | undefined;
  const [gb, setGb] = useState(String(cur));
  // useVolume is async: seed the field once the real quota lands, unless the user already typed.
  const [gbTouched, setGbTouched] = useState(false);
  useEffect(() => {
    if (!gbTouched && volume) setGb(String(volume.quota_gb ?? 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [volume?.quota_gb]);
  const target = Number(gb);
  const floor = Math.max(1, used);
  // Two ceilings: the scope's policy headroom (unlimited policy = none) and the storage pool's
  // physical headroom. The binding one wins; with neither known, fall back to a wide cap.
  const policyCeiling = usage?.has_limit && usage.remaining_gb != null ? cur + usage.remaining_gb : Infinity;
  const physicalCeiling = usage?.physical_remaining_gb != null ? cur + usage.physical_remaining_gb : Infinity;
  const ceiling = Math.min(policyCeiling, physicalCeiling, 1048576);
  const boundIsPhysical = physicalCeiling <= policyCeiling;
  const isNum = gb !== '' && Number.isFinite(target) && Number.isInteger(target);
  const belowUsed = isNum && target < floor;
  const overLimit = isNum && target > ceiling;
  const unchanged = isNum && target === cur;
  const valid = isNum && !belowUsed && !overLimit && !unchanged;
  const error = !isNum ? null
    : belowUsed ? t('volume.quotaBelowUsed', { used: formatGiB(floor) })
    : overLimit ? (boundIsPhysical ? t('volume.overPhysical', { requested: target }) : t('volume.overLimit', { requested: target }))
    : null;
  const reasons = [
    ...(unchanged ? [t('volume.quotaUnchangedShort')] : []),
    ...(belowUsed ? [t('volume.quotaBelowUsedShort')] : []),
  ];
  // Billing before and after. Storage charges max(quota, used), so a shrink below usage would not
  // save anything — which is exactly why the floor sits at usage.
  useUnsavedGuard(valid && !update.isPending);
  const submit = () => {
    if (!valid) return;
    update.mutate(target, {
      onSuccess: () => { pushToast('success', t('volume.quotaChanged', { quota: formatGiB(target) })); onDone(); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  return (
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <p className="text-sm text-muted mb-2">{t('volume.currentQuota', { quota: formatGiB(cur), used: formatGiB(used) })}</p>
        <Field label={t('volume.targetQuota')} required error={error} hint={t('volume.quotaHint', { used: formatGiB(floor) })}>
          {(ids) => (
            <input
              {...ids}
              className="gs-input w-full mt-1"
              type="number"
              inputMode="numeric"
              min={floor}
              max={Number.isFinite(ceiling) ? ceiling : undefined}
              step={1}
              value={gb}
              onChange={(e) => { setGbTouched(true); setGb(e.target.value); }}
              autoFocus autoComplete="off" />
          )}
        </Field>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={reasons} />
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || update.isPending}>
            {update.isPending ? t('common.saving') : t('common.save')}</button>
          <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
        </div>
      </form>
  );
}
