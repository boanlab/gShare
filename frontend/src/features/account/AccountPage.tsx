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

// The account screen: profile editing through PATCH /users/{id}. Changing the password lives on its
// own page at /account/password.
export function AccountPage() {
  const { t } = useTranslation();
  const userId = useAuthStore((s) => s.claims.sub) ?? '';
  const pushToast = useUiStore((s) => s.pushToast);

  const { data: profile, isLoading } = useMyProfile();
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
    <div className="w-full">
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
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[13px]">
              <div>
                <span className="text-[12px] font-semibold text-muted">{t('common.email')}</span>
                <div className="mt-1">{profile?.email ?? '-'}</div>
              </div>
              <div>
                <span className="text-[12px] font-semibold text-muted">{t('common.role')}</span>
                <div className="mt-1">{roleText}</div>
              </div>
              <div>
                <span className="text-[12px] font-semibold text-muted">{t('account.userId')}</span>
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
    </div>
  );
}
