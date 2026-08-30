import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/auth/authStore';
import { useMyProfile, useUpdateProfile } from '@/api/hooks/useAccount';
import { PageHeader } from '@/components/PageHeader';
import { Field, DisabledReason } from '@/components/Field';
import { CopyableId } from '@/components/CopyButton';
import { TableSkeleton } from '@/components/EmptyState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { roleLabel } from '@/lib/format';
import { humanizeError, asApiError } from '@/lib/errors';
import { useEffectivePolicy } from '@/api/hooks/useResources';
import { useResourceRequests } from '@/api/hooks/useResourceRequests';
import { StatusPill } from '@/components/StatusPill';
import { ReasonPopover } from '@/components/ReasonPopover';
import { Timestamp } from '@/components/Timestamp';
import { Pagination } from '@/components/Table';
import { useNotificationLog } from '@/api/hooks/useNotifications';
import { formatVram } from '@/lib/format';

// The account screen: profile editing through PATCH /users/{id}. Changing the password lives on its
// own page at /account/password.
export function AccountPage() {
  const { t } = useTranslation();
  const userId = useAuthStore((s) => s.claims.sub) ?? '';
  const pushToast = useUiStore((s) => s.pushToast);

  const { data: profile, isLoading } = useMyProfile();
  const { data: pol } = useEffectivePolicy();
  const { data: myQuotaReqs = [] } = useResourceRequests('mine');
  const updateProfile = useUpdateProfile(userId);
  const [name, setName] = useState('');
  useEffect(() => {
    if (profile?.name) setName(profile.name);
  }, [profile?.name]);

  // Organization, group, and role are read-only and derived from the memberships. Several are
  // joined with commas.
  const memberships = profile?.memberships ?? [];
  const orgs = [...new Set(memberships.map((m) => m.org_name).filter(Boolean) as string[])];
  const depts = [...new Set(memberships.map((m) => m.project_name).filter(Boolean) as string[])];
  const roleText =
    [...new Set([
      ...(profile?.global_roles?.length ? profile.global_roles : profile?.global_role ? [profile.global_role] : []),
      ...memberships.map((m) => m.role),
    ])]
      .map((r) => roleLabel(r))
      .join(', ') || t('account.defaultRole');

  const dirty = !!profile && name.trim() !== (profile.name ?? '') && name.trim().length > 0;
  useUnsavedGuard(dirty && !updateProfile.isPending);

  function saveProfile() {
    updateProfile.mutate(
      { name: name.trim() },
      {
        onSuccess: () => pushToast('success', t('account.saved')),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  }

  return (
    <div>
      <PageHeader
        title={t('account.title')}
        actions={<Link to="/account/password" className="gs-btn gs-btn-sm">{t('account.changePassword')}</Link>}
      />

      <div className="gs-card mb-4">
        <h2 className="font-bold mb-3">{t('account.profile')}</h2>
        {isLoading ? (
          <TableSkeleton rows={4} columns={2} />
        ) : (
          <form className="space-y-3" {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); if (dirty) saveProfile(); }}>
            {/* Organization and group are read-only boxes, shown above the editable name. */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label={`${t('common.organization')} (${t('account.readOnly')})`} hint={t('account.membershipHint')}>
                {(ids) => (
                  <input
                    {...ids}
                    className="gs-input w-full text-muted disabled:opacity-100"
                    value={orgs.join(', ') || '-'}
                    disabled
                    readOnly autoComplete="off" />
                )}
              </Field>
              <Field label={`${t('common.group')} (${t('account.readOnly')})`} hint={t('account.membershipHint')}>
                {(ids) => (
                  <input
                    {...ids}
                    className="gs-input w-full text-muted disabled:opacity-100"
                    value={depts.join(', ') || '-'}
                    disabled
                    readOnly autoComplete="off" />
                )}
              </Field>
            </div>
            <Field label={t('account.displayName')} required hint={t('account.displayNameHint')}>
              {(ids) => (
                <input
                  {...ids}
                  className="gs-input w-full"
                  autoComplete="name"
                  maxLength={80}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              )}
            </Field>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-xs font-semibold text-muted">{t('common.email')}</span>
                <div className="mt-1">{profile?.email ?? '-'}</div>
              </div>
              <div>
                <span className="text-xs font-semibold text-muted">{t('common.role')}</span>
                <div className="mt-1">{roleText}</div>
              </div>
              <div>
                <span className="text-xs font-semibold text-muted">{t('account.userId')}</span>
                <div className="mt-1"><CopyableId value={userId} /></div>
              </div>
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <button
                type="submit"
                className="gs-btn gs-btn-primary disabled:opacity-50"
                disabled={updateProfile.isPending || !dirty}
              >
                {updateProfile.isPending ? t('account.saving') : t('common.save')}
              </button>
              <DisabledReason reasons={dirty ? [] : [t('account.noChanges')]} />
            </div>
          </form>
        )}
      </div>

      {/* WHAT LIMITS APPLY TO ME: the same user→group→org→global merge the admission gate uses.
          Compact by design — the numbers, not the mechanics. */}
      <div className="gs-card mb-4">
        <h2 className="font-bold mb-1">{t('account.policyTitle')}</h2>
        <p className="text-muted text-xs mb-3">{t('account.policyHint')}</p>
        {!pol?.has_policy ? (
          <p className="text-muted text-sm">{t('account.policyNone')}</p>
        ) : (
          <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-3 text-sm">
            {([
              ['limMaxConcurrent', pol.max_concurrent || null, ''],
              ['limMaxQueued', pol.max_queued || null, ''],
              ['limCpu', pol.limits?.cpu || null, ' vCPU'],
              ['limMemGb', pol.limits?.mem_gb || null, ' GiB'],
              ['limGpuMemMb', pol.limits?.gpu_mem_mb ? formatVram(pol.limits.gpu_mem_mb) : null, ''],
              ['limGpuCores', pol.limits?.gpu_cores || null, '%'],
              ['limStorageGb', pol.limits?.storage_gb || null, ' GiB'],
              ['limVolumeGb', (pol.limits as { volume_gb?: number } | undefined)?.volume_gb || null, ' GiB'],
              // Reaper windows — 0 is explicit unlimited, so `|| null` maps it onto the same
              // "무제한" rendering the other limits use.
              ['limMaxRuntime', (pol as { max_runtime_min?: number | null }).max_runtime_min || null, t('account.unitMin')],
              ['limIdle', (() => { const v = (pol as { idle_timeout_sec?: number | null }).idle_timeout_sec; return v ? Math.round(v / 60) : null; })(), t('account.unitMin')],
              ['limCpuSessConcurrent', (pol.limits as { cpu_session_max_concurrent?: number })?.cpu_session_max_concurrent || null, ''],
              ['limCpuSessRuntime', (pol.limits as { cpu_session_max_runtime_min?: number })?.cpu_session_max_runtime_min || null, t('account.unitMin')],
              ['limCpuSessIdle', (() => { const v = (pol.limits as { cpu_session_idle_timeout_sec?: number })?.cpu_session_idle_timeout_sec; return v ? Math.round(v / 60) : null; })(), t('account.unitMin')],
            ] as const).map(([key, value, unit]) => (
              <div key={key}>
                <dt className="text-xs font-semibold text-muted">{t(`account.${key}`)}</dt>
                <dd className="mt-0.5 gs-num">
                  {value == null ? <span className="text-muted">{t('account.unlimited')}</span> : `${value}${unit}`}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {/* The quota-request page is gone (the form opens as a modal from the dashboard); its
          history lives here, next to the policy those requests try to move. */}
      <div className="gs-card mb-4">
        <h2 className="font-bold mb-3">{t('account.quotaHistoryTitle')}</h2>
        {myQuotaReqs.length === 0 ? (
          <p className="text-muted text-sm">{t('account.quotaHistoryEmpty')}</p>
        ) : (
          <ul className="divide-y divide-border text-sm">
            {myQuotaReqs.map((r) => (
              <li key={r.id} className="py-2.5 flex items-center gap-3 flex-wrap">
                <Timestamp value={r.created_at} className="gs-num text-xs text-muted shrink-0" />
                <span className="gs-num">
                  {[r.cpu != null ? `CPU ${r.cpu}` : null, r.mem_gb != null ? `MEM ${r.mem_gb}GiB` : null,
                    r.storage_gb != null ? `DISK ${r.storage_gb}GB` : null].filter(Boolean).join(' · ')}
                </span>
                <span className="text-muted text-xs truncate max-w-[220px]" title={r.note}>{r.note}</span>
                <span className="ml-auto inline-flex items-center gap-1.5">
                  <StatusPill kind={r.status} label={t(`enum.reqStatus.${r.status}`, { defaultValue: r.status })} />
                  {r.status === 'rejected' && r.decided_reason && <ReasonPopover reason={r.decided_reason} />}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <NotificationLogCard />
    </div>
  );
}

// 알림 로그: 알림함(벨)에서 지운 알림도 여기에는 회색으로 남는다 (soft delete).
function NotificationLogCard() {
  const { t } = useTranslation();
  const { data: items = [] } = useNotificationLog();
  const [page, setPage] = useState(1);
  const rows = items.slice((page - 1) * 10, page * 10);
  return (
    <div className="gs-card mb-4">
      <h2 className="font-bold mb-1">{t('account.notifLogTitle')}</h2>
      <p className="text-muted text-xs mb-3">{t('account.notifLogHint')}</p>
      {items.length === 0 ? <p className="text-muted text-sm">{t('account.notifLogEmpty')}</p> : (
        <>
          <ul className="divide-y divide-border">
            {rows.map((n) => (
              <li key={n.id} className={`py-2.5 text-sm ${n.deleted_at ? 'opacity-55' : ''}`}>
                <div className="flex items-center gap-2 min-w-0">
                  {!n.read_at && !n.deleted_at && <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0" aria-hidden="true" />}
                  <span className="font-semibold truncate">{t(`notif.${n.type}.title`, { ...(n.params ?? {}), defaultValue: n.title })}</span>
                  {n.deleted_at && <span className="gs-tag shrink-0">{t('account.notifDeletedTag')}</span>}
                  <span className="ml-auto shrink-0"><Timestamp value={n.created_at} className="text-muted text-xs" /></span>
                </div>
                {n.body && <div className="text-muted text-xs mt-0.5 break-words">{t(`notif.${n.type}.body`, { ...(n.params ?? {}), defaultValue: n.body })}</div>}
              </li>
            ))}
          </ul>
          <Pagination page={page} pageSize={10} total={items.length} onPage={setPage} />
        </>
      )}
    </div>
  );
}
