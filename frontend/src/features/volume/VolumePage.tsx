import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  useVolumes, useVolume, useCreateVolume, useDeleteVolume, useStorageQuotaUsage, useVolumePricing,
  useVolumePermissions, useGrantPermission, useRevokePermission,
  useCreateQuotaRequest, useSnapshots, useCreateSnapshot, useRestoreSnapshot, useDeleteSnapshot,
  type CreateVolumeBody,
} from '@/api/hooks/useVolumes';
import { Table, TableToolbar, type Column } from '@/components/Table';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Field, DisabledReason } from '@/components/Field';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, useFormGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useAuthStore } from '@/auth/authStore';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { formatGiB, roleLabel, accessModeLabel, formatCredit, scopeLabel } from '@/lib/format';
import { Timestamp } from '@/components/Timestamp';
import i18n from '@/i18n';

// The backend's VolumeRead: { id, scope, scope_id, type, access_mode, quota_gb, used_gb }.
type Vol = Record<string, unknown> & {
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
  const { data, isLoading, isFetching, refetch, dataUpdatedAt } = useVolumes();
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
      return v.scope_id === userId ? t('volume.scopeMine') : t('volume.scopeUser', { id: v.scope_id });
    }
    return scopeLabel(v.scope);
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

  const columns: Column<Vol>[] = [
    {
      key: 'id',
      header: t('volume.colVolume'),
      sortBy: (v) => v.name || v.id,
      render: (v) => (
        <div className="min-w-0">
          <b>{v.name || volumeTypeLabel(v.type)}</b>
          <div className="flex items-center gap-1 text-muted text-[12px]">
            {volumeTypeLabel(v.type)} · <code className="font-mono truncate max-w-[150px]" title={v.id}>{v.id}</code>
            <CopyButton value={v.id} label={t('volume.copyId')} />
          </div>
        </div>
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
          <div className="inline-flex flex-col items-end gap-1" title={t('volume.usagePercent', { percent: pct })}>
            <span className="tabular-nums">{formatGiB(v.used_gb ?? 0)} / {formatGiB(v.quota_gb ?? 0)}</span>
            <span className="h-1.5 w-20 rounded bg-surface-2 overflow-hidden">
              <span className={`block h-full ${pct >= 90 ? 'bg-danger' : pct >= 70 ? 'bg-warn' : 'bg-primary'}`} style={{ width: `${pct}%` }} />
            </span>
          </div>
        );
      },
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (v) => (
        <div className="flex gap-1.5 justify-end flex-wrap">
          <Link to={`/data/${v.id}/share`} className="gs-btn gs-btn-sm">{t('volume.share')}</Link>
          <Link to={`/data/${v.id}/quota`} className="gs-btn gs-btn-sm">{t('volume.expand')}</Link>
          <Link to={`/data/${v.id}/snapshots`} className="gs-btn gs-btn-sm">{t('volume.snapshots')}</Link>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={del.isPending} onClick={() => onDelete(v)}>{t('common.delete')}</button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t('volume.title')}
        description={t('volume.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={<Link to="/data/new" className="gs-btn gs-btn-primary">{t('volume.new')}</Link>}
      />
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('volume.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
      />
      <div className="gs-card p-0 overflow-hidden">
        {isLoading ? (
          <div className="p-4"><TableSkeleton rows={4} columns={5} /></div>
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : (
              <EmptyState
                icon="▤"
                title={t('volume.emptyTitle')}
                description={t('volume.emptyDescription')}
                action={<Link to="/data/new" className="gs-btn gs-btn-primary">{t('volume.new')}</Link>}
              />
            )
        ) : (
          <div className="p-1">
            <Table
              caption={t('volume.title')}
              columns={columns}
              rows={rows}
              rowKey={(v) => v.id}
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

const sel = 'w-full mt-1 px-3 py-2 border border-border rounded-lg bg-surface-2';

// New volume, on its own page at /data/new.
export function NewVolumePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const create = useCreateVolume();
  const defaultScopeId = useAuthStore((s) => s.claims.sub) ?? '';
  const memberships = useAuthStore((s) => s.memberships);

  const [scope, setScope] = useState<'user' | 'group'>('user');
  // A personal volume always belongs to the caller. A group volume is picked from their memberships,
  // with group_id as the value.
  const [projectId, setProjectId] = useState(memberships[0]?.group_id ?? '');
  const [name, setName] = useState('');
  const [type, setType] = useState<CreateVolumeBody['type']>('dataset');
  const [accessMode, setAccessMode] = useState<CreateVolumeBody['access_mode']>('RWX');
  const [quota, setQuota] = useState('10');
  const scopeId = scope === 'user' ? defaultScopeId : projectId;
  // The per-scope storage policy limit. A quota beyond the remaining headroom warns and blocks, using
  // the same rule as the backend's _assert_storage_quota.
  const usage = useStorageQuotaUsage(scope, scopeId || undefined).data;
  // The storage rate, in credits per GB-hour. Unlike a session, a volume bills continuously for its
  // provisioned capacity.
  const storageRate = useVolumePricing().data?.credit_per_gb_hour ?? 0;
  const reqGb = Number(quota) || 0;
  const estPerHour = reqGb * storageRate;
  const estPerMonth = estPerHour * 720;  // about 30 days
  const overLimit = !!usage?.has_limit && usage.remaining_gb != null && reqGb > usage.remaining_gb;
  // Which types each scope offers: personal hides group, group hides home, and scratch cannot be
  // created here.
  const typeAllowed = (v: string) => v !== 'scratch' && (scope === 'user' ? v !== 'group' : v !== 'home');
  const availableTypes = VOLUME_TYPES.filter((v) => typeAllowed(v.value));
  const typeDescKey = VOLUME_TYPES.find((v) => v.value === type)?.descKey;
  const valid = scopeId.trim().length > 0 && name.trim().length > 0 && Number(quota) >= 0 && !overLimit;
  const blockers = [
    !name.trim() && t('volume.nameLabel'),
    !scopeId.trim() && t('volume.target'),
    overLimit && t('volume.overLimitShort'),
  ].filter(Boolean) as string[];
  const dirty = name.trim().length > 0 || quota !== '10';
  useUnsavedGuard(dirty && !create.isPending);

  const submit = () => {
    if (!valid) return;
    create.mutate(
      { scope, scope_id: scopeId.trim(), name: name.trim(), type, access_mode: accessMode, quota_gb: Number(quota) },
      {
        onSuccess: () => { pushToast('success', t('volume.created')); navigate('/data'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={t('volume.new')}
        crumbs={[{ label: t('volume.title'), to: '/data' }, { label: t('volume.new') }]}
        actions={<BackLink to="/data" label={t('volume.backToList')} />}
      />
      <form
        className="gs-card" noValidate
        {...unsavedGuardProps}
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[13px]">
          <Field label={t('volume.nameLabel')} required className="sm:col-span-2" hint={t('volume.nameHint')}>
            {(ids) => <input {...ids} className={sel} value={name} onChange={(e) => setName(e.target.value)} placeholder={t('volume.namePlaceholder')} maxLength={80} autoFocus autoComplete="off" />}
          </Field>
          <label className="block"><span className="text-[12px] font-semibold text-muted">{t('volume.ownerScope')}</span>
            <select className={sel} value={scope} onChange={(e) => {
              const next = e.target.value as 'user' | 'group';
              setScope(next);
              // If the selected type is hidden in the new scope, fall back to a valid one.
              const ok = (v: string) => (next === 'user' ? v !== 'group' : v !== 'home');
              if (!ok(type ?? '')) setType('dataset');
            }}>
              <option value="user">{t('volume.scopeMine')}</option>
              <option value="group">{t('volume.type.group')}</option>
            </select></label>
          {scope === 'user' ? (
            <label className="block"><span className="text-[12px] font-semibold text-muted">{t('volume.target')}</span>
              <input className={sel} value={t('volume.myAccount')} disabled autoComplete="off" /></label>
          ) : (
            <label className="block"><span className="text-[12px] font-semibold text-muted">{t('common.group')}</span>
              <select className={sel} value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {memberships.length === 0 && <option value="">{t('volume.noGroup')}</option>}
                {memberships.map((m) => <option key={m.group_id} value={m.group_id}>{m.project_name}</option>)}
              </select></label>
          )}
          <label className="block col-span-2"><span className="text-[12px] font-semibold text-muted">{t('common.type')}</span>
            <select className={sel} value={type} onChange={(e) => setType(e.target.value as CreateVolumeBody['type'])}>
              {availableTypes.map((v) => <option key={v.value} value={v.value}>{t(v.labelKey)}</option>)}</select>
            {typeDescKey && <span className="text-muted text-[11px] mt-1 block">{t(typeDescKey)}</span>}</label>
          <label className="block"><span className="text-[12px] font-semibold text-muted">{t('volume.colAccessMode')}</span>
            <select className={sel} value={accessMode} onChange={(e) => setAccessMode(e.target.value as CreateVolumeBody['access_mode'])}>
              <option value="RWX">{t('volume.accessRw')}</option><option value="ROX">{t('volume.accessRo')}</option></select></label>
          <Field
            label={t('volume.quotaGib')}
            required
            error={overLimit ? t('volume.overLimit', { requested: reqGb }) : null}
          >
            {(ids) => (
              <input
                {...ids}
                className={`${sel} ${overLimit ? 'border-danger' : ''}`}
                type="number"
                inputMode="numeric"
                min={1}
                max={usage?.has_limit && usage.remaining_gb != null ? usage.remaining_gb : 65536}
                step={10}
                value={quota}
                onChange={(e) => setQuota(e.target.value)} autoComplete="off" />
            )}
          </Field>
          {usage?.has_limit && (
            <div className={`col-span-2 text-[12px] ${overLimit ? 'text-danger font-semibold' : 'text-muted'}`}>
              {t('volume.policyUsage', { used: usage.allocated_gb, limit: usage.limit_gb, remaining: usage.remaining_gb })}
              {overLimit && <div className="mt-0.5">{t('volume.overLimit', { requested: reqGb })}</div>}
            </div>
          )}
          {storageRate > 0 && (
            <div className="col-span-2 flex items-center justify-between rounded-lg border border-border p-3">
              <span className="text-[12px] font-semibold text-muted">{t('volume.estimatedCost')}</span>
              <span className="text-[15px] font-extrabold">
                {t('volume.perMonth', { amount: formatCredit(Math.round(estPerMonth)) })}
                <span className="text-muted text-[11px] font-normal ml-2">{t('volume.perHourShort', { amount: formatCredit(Number(estPerHour.toFixed(2))) })}</span>
              </span>
            </div>
          )}
        </div>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={valid ? [] : blockers} />
          <button type="button" className="gs-btn" onClick={() => navigate('/data')}>{t('common.cancel')}</button>
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
            {create.isPending ? t('volume.creating') : t('common.create')}
          </button>
        </div>
      </form>
    </div>
  );
}

// Volume sharing, on its own page at /data/:volumeId/share.
export function VolumeSharePage() {
  const { t } = useTranslation();
  const { volumeId = '' } = useParams();
  const volume = useVolume(volumeId).data as Vol | undefined;
  const { data: perms = [], isLoading } = useVolumePermissions(volumeId);
  const grant = useGrantPermission(volumeId);
  const revoke = useRevokePermission(volumeId);
  const pushToast = useUiStore((s) => s.pushToast);
  const [uid, setUid] = useState('');
  const [touched, setTouched] = useState(false);
  const [role, setRole] = useState<'rw' | 'ro' | 'owner'>('rw');
  // Validated on blur.
  const emailValid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(uid.trim());
  const emailError = touched && uid.trim() && !emailValid ? t('volume.emailInvalid') : null;
  const guard = useFormGuard(grant.isPending);
  const add = () => grant.mutate({ email: uid.trim(), role }, {
    onSuccess: () => { pushToast('success', t('volume.granted')); setUid(''); },
    onError: (e) => pushToast('error', humanizeError(asApiError(e))),
  });
  return (
    <div className="w-full">
      <PageHeader
        title={`${t('volume.shareTitle')}${volume ? ` — ${volume.name || volumeTypeLabel(volume.type)}` : ''}`}
        description={volume ? `${volumeTypeLabel(volume.type)} · ${accessModeLabel(volume.access_mode)}` : undefined}
        crumbs={[{ label: t('volume.title'), to: '/data' }, { label: t('volume.shareTitle') }]}
        actions={<BackLink to="/data" label={t('volume.backToList')} />}
      />
      <div className="gs-card">
      {isLoading ? <p className="text-muted">{t('common.loading')}</p> : (
        <ul className="mb-3 divide-y divide-border text-[13px]">
          {(perms as Array<Record<string, unknown>>).length === 0 && <li className="py-2 text-muted">{t('volume.noShares')}</li>}
          {(perms as Array<{ user_id: string; user_name?: string; role: string }>).map((pm) => (
            <li key={pm.user_id} className="flex items-center justify-between py-2">
              <span><b>{pm.user_name ?? pm.user_id}</b> <span className="text-muted">· {roleLabel(pm.role)}</span></span>
              <button
                type="button"
                className="gs-btn gs-btn-sm gs-btn-danger"
                disabled={revoke.isPending}
                onClick={() => {
                  revoke.mutate(pm.user_id, {
                    onSuccess: () => pushToast('success', t('volume.revokedFrom', { name: pm.user_name ?? pm.user_id }), {
                      label: t('common.undo'),
                      run: () => grant.mutate({ email: pm.user_id, role: pm.role as 'rw' | 'ro' | 'owner' }, {
                        onSuccess: () => pushToast('success', t('volume.revokeUndone')),
                        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
                      }),
                    }),
                    onError: (e) => pushToast('error', humanizeError(asApiError(e))),
                  });
                }}
              >{t('volume.revoke')}</button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="grid grid-cols-1 sm:grid-cols-[1fr_auto_auto] gap-2 items-start"
        {...guard.props}
        onSubmit={(e) => { e.preventDefault(); setTouched(true); if (emailValid) add(); }}
      >
        <Field label={t('volume.userEmail')} required error={emailError}>
          {(ids) => (
            <input
              {...ids}
              className={sel}
              type="email"
              inputMode="email"
              autoComplete="email"
              value={uid}
              onChange={(e) => setUid(e.target.value)}
              onBlur={() => setTouched(true)}
              placeholder="user@example.com"
            />
          )}
        </Field>
        <Field label={t('common.role')}>
          {(ids) => (
            <select {...ids} className={sel} value={role} onChange={(e) => setRole(e.target.value as 'rw'|'ro'|'owner')}>
              <option value="rw">{roleLabel('rw')}</option><option value="ro">{roleLabel('ro')}</option><option value="owner">{roleLabel('owner')}</option>
            </select>
          )}
        </Field>
        <div className="sm:mt-[22px] flex items-center gap-3 flex-wrap">
          <DisabledReason reasons={uid.trim() && emailValid ? [] : [t('volume.userEmail')]} />
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!uid.trim() || !emailValid || grant.isPending}>{t('common.add')}</button>
        </div>
      </form>
      </div>
    </div>
  );
}

// Volume expansion request, on its own page at /data/:volumeId/quota.
export function VolumeQuotaPage() {
  const { t } = useTranslation();
  const { volumeId = '' } = useParams();
  const navigate = useNavigate();
  const volume = useVolume(volumeId).data as Vol | undefined;
  const quota = useCreateQuotaRequest(volumeId);
  const pushToast = useUiStore((s) => s.pushToast);
  const cur = volume?.quota_gb ?? 0;
  const [gb, setGb] = useState(String(cur + 10));
  const requested = Number(gb);
  const valid = requested > cur && Number.isFinite(requested);
  const error = gb !== '' && requested <= cur ? t('volume.quotaMustGrow', { current: formatGiB(cur) }) : null;
  useUnsavedGuard(valid && !quota.isPending);
  const submit = () => {
    if (!valid) return;
    quota.mutate(requested, {
      onSuccess: () => { pushToast('success', t('volume.quotaRequested')); navigate('/data'); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={`${t('volume.quotaTitle')}${volume ? ` — ${volume.name || volumeTypeLabel(volume.type)}` : ''}`}
        crumbs={[{ label: t('volume.title'), to: '/data' }, { label: t('volume.quotaTitle') }]}
        actions={<BackLink to="/data" label={t('volume.backToList')} />}
      />
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <p className="text-[13px] text-muted mb-2">{t('volume.currentQuota', { quota: formatGiB(cur) })}</p>
        <Field label={t('volume.targetQuota')} required error={error} hint={t('volume.quotaHint')}>
          {(ids) => (
            <input
              {...ids}
              className={sel}
              type="number"
              inputMode="numeric"
              min={cur + 1}
              max={65536}
              step="any"
              value={gb}
              onChange={(e) => setGb(e.target.value)}
              autoFocus autoComplete="off" />
          )}
        </Field>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={valid ? [] : [t('volume.quotaMustGrowShort')]} />
          <button type="button" className="gs-btn" onClick={() => navigate('/data')}>{t('common.cancel')}</button>
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || quota.isPending}>
            {quota.isPending ? t('volume.requesting') : t('common.request')}</button>
        </div>
      </form>
    </div>
  );
}

// Volume snapshots, on their own page at /data/:volumeId/snapshots.
export function VolumeSnapshotsPage() {
  const { t } = useTranslation();
  const { volumeId = '' } = useParams();
  const volume = useVolume(volumeId).data as Vol | undefined;
  const { data: snaps = [], isLoading, isFetching, refetch, dataUpdatedAt } = useSnapshots(volumeId);
  const create = useCreateSnapshot(volumeId);
  const restore = useRestoreSnapshot(volumeId);
  const del = useDeleteSnapshot(volumeId);
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();
  const toast = (m: { mutate: (a: string, o: { onSuccess: () => void; onError: (e: unknown) => void }) => void }, id: string, ok: string) =>
    m.mutate(id, { onSuccess: () => pushToast('success', ok), onError: (e) => pushToast('error', humanizeError(asApiError(e))) });
  return (
    <div className="w-full">
      <PageHeader
        title={`${t('volume.snapshotTitle')}${volume ? ` — ${volume.name || volumeTypeLabel(volume.type)}` : ''}`}
        crumbs={[{ label: t('volume.title'), to: '/data' }, { label: t('volume.snapshotTitle') }]}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={
          <>
            <button type="button" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={create.isPending}
              onClick={() => create.mutate(undefined as never, { onSuccess: () => pushToast('success', t('volume.snapshotStarted')), onError: (e) => pushToast('error', humanizeError(asApiError(e))) })}>
              {create.isPending ? t('volume.creating') : t('volume.newSnapshot')}</button>
            <BackLink to="/data" label={t('volume.backToList')} />
          </>
        }
      />
      <div className="gs-card">
      {isLoading ? <TableSkeleton rows={3} columns={3} /> : (
        <ul className="divide-y divide-border text-[13px]">
          {(snaps as Array<Record<string, unknown>>).length === 0 && (
            <li className="py-2">
              <EmptyState icon="⎘" title={t('volume.noSnapshots')} description={t('volume.snapshotHint')} />
            </li>
          )}
          {(snaps as Array<{ id: string; name?: string; status?: string; created_at?: string }>).map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-3 py-2 flex-wrap">
              <span className="flex items-center gap-1.5 min-w-0">
                <b>{s.name || s.id}</b>
                <CopyButton value={s.id} label={t('volume.copySnapshotId')} />
                <span className="text-muted">· {s.status} · <Timestamp value={s.created_at} /></span>
              </span>
              <span className="flex gap-1.5">
                <button
                  type="button"
                  className="gs-btn gs-btn-sm"
                  disabled={s.status !== 'ready'}
                  title={s.status !== 'ready' ? t('volume.restoreNotReady', { status: s.status }) : undefined}
                  onClick={async () => {
                    const ok = await confirm({
                      title: t('volume.confirmRestoreTitle', { name: s.name || s.id }),
                      body: t('volume.confirmRestoreBody'),
                      consequences: [t('volume.consequenceOverwrite')],
                      confirmLabel: t('volume.restore'),
                      destructive: true,
                    });
                    if (ok) toast(restore, s.id, t('volume.restoreStarted'));
                  }}
                >{t('volume.restore')}</button>
                <button
                  type="button"
                  className="gs-btn gs-btn-sm gs-btn-danger"
                  disabled={del.isPending}
                  onClick={async () => {
                    const ok = await confirm({
                      title: t('volume.confirmDeleteSnapshotTitle', { name: s.name || s.id }),
                      body: t('volume.confirmDeleteSnapshotBody'),
                      consequences: [t('volume.consequenceSnapshotGone')],
                      confirmLabel: t('common.delete'),
                      destructive: true,
                      confirmText: s.name || s.id,
                    });
                    if (ok) toast(del, s.id, t('volume.snapshotDeleted'));
                  }}
                >{t('common.delete')}</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      </div>
    </div>
  );
}
