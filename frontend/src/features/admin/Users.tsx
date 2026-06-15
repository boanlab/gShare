import { useEffect, useMemo, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  useUsers,
  useUser,
  useCreateUser,
  useUpdateUser,
  useSetUserDepartment,
  useDeleteUser,
  type AdminUser,
  type UserStatus,
} from '@/api/hooks/useUsers';
import { useProjects, useOrganizations } from '@/api/hooks/useGroups';
import { useAuthStore } from '@/auth/authStore';
import { Table, TableToolbar, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { Field, DisabledReason } from '@/components/Field';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { roleLabel, userStatusLabel } from '@/lib/format';

// User management: list and add, plus edit (name, email, group, status, password reset) and delete.
// What each role may change: super_admin edits email, name, organization, group, and password;
// org_admin edits group and password; group_admin resets the password only.

// Matches the backend's email validation: at least one dot after the @, and no trailing dot.
const emailOk = (e: string) => /\S+@\S+\.\S+/.test(e.trim()) && !e.trim().endsWith('.');

const STATUS_PILL: Record<UserStatus, string> = {
  active: 'bg-free-soft text-free',
  invited: 'bg-warn-soft text-warn',
  suspended: 'bg-danger-soft text-danger',
};

// Flatten every role a user holds — global, organization, and group — into display items.
function allRoles(u: AdminUser): { key: string; label: string; tone: string }[] {
  const out: { key: string; label: string; tone: string }[] = [];
  const globals = u.global_roles?.length ? u.global_roles : u.global_role ? [u.global_role] : [];
  for (const r of globals) out.push({ key: `g:${r}`, label: roleLabel(r), tone: 'bg-primary-soft text-primary' });
  for (const o of u.org_admins ?? []) out.push({ key: `o:${o.org_id}`, label: i18n.t('admin.users.orgAdminOf', { org: o.org_name }), tone: 'bg-warn-soft text-warn' });
  for (const m of u.memberships ?? []) out.push({ key: `m:${m.group_id}`, label: `${roleLabel(m.role)} · ${m.group_name}`, tone: 'bg-surface-2 text-text' });
  return out;
}

export function AdminUsers() {
  const { t } = useTranslation();
  // Search, status and sort in the URL, so a filtered view survives Back and travels in a link.
  const table = useTableState('', { sort: 'name', dir: 'asc' });
  const search = table.query;
  const statusFilter = (table.tab ?? '') as UserStatus | '';

  const { data, isLoading, isFetching, refetch, dataUpdatedAt } = useUsers({ status: statusFilter });

  // Work out what the current user may do, from their own role.
  const claims = useAuthStore((s) => s.claims);
  const memberships = useAuthStore((s) => s.memberships);
  const orgAdminOrgs = useAuthStore((s) => s.orgAdminOrgs);
  const isSuper = claims.global_role === 'super_admin';
  const isOrgAdmin = isSuper || orgAdminOrgs.length > 0 || memberships.some((m) => m.role === 'org_admin');
  // Reaching this screen requires group_admin or above, which permits a password reset.
  const canCreate = isOrgAdmin;
  const canDelete = isOrgAdmin;

  // Adding a user requires a group, so the entry point is disabled while none exist.
  const { data: groups, isLoading: groupsLoading } = useProjects();
  const hasGroups = (groups?.length ?? 0) > 0;

  // Search filters client-side on name and email; the status filter is a server query.
  const matched = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = data ?? [];
    if (!q) return list;
    return list.filter((u) => u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q));
  }, [data, search]);

  const columns: Column<AdminUser>[] = useMemo(() => [
    {
      key: 'name',
      header: t('admin.users.colUser'),
      sortBy: (u) => u.name,
      render: (u) => (
        <div className="min-w-0">
          <b>{u.name}</b>
          <div className="flex items-center gap-1 text-muted text-[12px]">
            <span className="font-mono truncate max-w-[190px]" title={u.email}>{u.email}</span>
            <CopyButton value={u.email} label={t('admin.users.copyEmail')} />
          </div>
        </div>
      ),
    },
    {
      key: 'org',
      header: t('common.organization'),
      hideOnMobile: true,
      sortBy: (u) => Array.from(new Set([
        ...(u.memberships ?? []).map((m) => m.org_name),
        ...(u.org_admins ?? []).map((o) => o.org_name),
      ])).join(', '),
      render: (u) => {
        const orgs = Array.from(new Set([
          ...(u.memberships ?? []).map((m) => m.org_name),
          ...(u.org_admins ?? []).map((o) => o.org_name),
        ]));
        return orgs.length ? (
          <span className="text-[12px]">{orgs.join(', ')}</span>
        ) : (
          <span className="text-muted text-[12px]">—</span>
        );
      },
    },
    {
      key: 'dept',
      header: t('common.group'),
      hideOnMobile: true,
      sortBy: (u) => (u.memberships ?? []).map((m) => m.group_name).join(', '),
      render: (u) => {
        const ms = u.memberships ?? [];
        return ms.length ? (
          <div className="flex flex-wrap gap-1">
            {ms.map((m) => (
              <span key={m.group_id} className="inline-block rounded-full px-2 py-0.5 text-[11.5px] font-semibold bg-surface-2" title={roleLabel(m.role)}>
                {m.group_name}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-muted text-[12px]">—</span>
        );
      },
    },
    {
      key: 'roles',
      header: t('common.role'),
      hideOnMobile: true,
      sortBy: (u) => allRoles(u).map((r) => r.label).join(', '),
      render: (u) => {
        const rs = allRoles(u);
        return rs.length ? (
          <div className="flex flex-wrap gap-1">
            {rs.map((r) => (
              <span key={r.key} className={`inline-block rounded-full px-2 py-0.5 text-[11.5px] font-bold ${r.tone}`}>
                {r.label}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-muted text-[12px]">—</span>
        );
      },
    },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (u) => u.status,
      render: (u) => (
        <span className={`gs-pill ${STATUS_PILL[u.status]}`}>
          {userStatusLabel(u.status)}
        </span>
      ),
    },
    {
      key: 'created_at',
      header: t('common.created'),
      hideOnMobile: true,
      sortBy: (u) => (u.created_at ? new Date(u.created_at).getTime() : 0),
      render: (u) => <Timestamp value={u.created_at} className="text-muted" />,
    },
    {
      key: 'actions',
      header: t('admin.users.colActions'),
      sortable: false,
      align: 'right',
      render: (u) => (
        <div className="flex flex-nowrap gap-2 justify-end">
          <Link to={`/admin/users/${u.id}/edit`} className="gs-btn gs-btn-sm">{t('common.edit')}</Link>
          {canDelete && (
            <Link to={`/admin/users/${u.id}/delete`} className="gs-btn gs-btn-sm gs-btn-danger">{t('common.delete')}</Link>
          )}
        </div>
      ),
    },
  ], [t, canDelete]);

  const rows = useMemo(
    () => sortRows(matched, sortAccessor(columns, table.sort), table.dir),
    [matched, columns, table.sort, table.dir],
  );

  return (
    <div>
      <PageHeader
        title={t('admin.users.title')}
        description={t('admin.users.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={canCreate && (hasGroups ? (
          <Link to="/admin/users/new" className="gs-btn gs-btn-primary">{t('admin.users.add')}</Link>
        ) : (
          <button type="button" className="gs-btn gs-btn-primary" disabled title={t('admin.users.addDisabledHint')}>{t('admin.users.add')}</button>
        ))}
      />

      {canCreate && !groupsLoading && !hasGroups && (
        <div className="gs-card mb-4 bg-surface-2 text-[13px] text-muted">
          <Trans i18nKey="admin.users.noGroupWarning" components={{ 1: <b /> }} />
        </div>
      )}

      <TableToolbar
        query={search}
        onQueryChange={table.setQuery}
        placeholder={t('admin.users.searchPlaceholder')}
        total={(data ?? []).length}
        shown={matched.length}
        onClear={table.clear}
      >
        <label className="gs-sr-only" htmlFor="gs-user-status">{t('admin.users.allStatuses')}</label>
        <select
          id="gs-user-status"
          className="gs-input w-auto"
          value={statusFilter}
          onChange={(e) => table.setTab(e.target.value || null)}
        >
          <option value="">{t('admin.users.allStatuses')}</option>
          <option value="active">{t('enum.userStatus.active')}</option>
          <option value="suspended">{t('enum.userStatus.suspended')}</option>
        </select>
      </TableToolbar>

      <div className="gs-card">
        {isLoading ? (
          <TableSkeleton rows={5} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={search} onClear={table.clear} />
            : <EmptyState icon="☺" title={t('admin.users.empty')} description={t('admin.users.emptyDescription')} />
        ) : (
          <Table
            caption={t('admin.users.title')}
            columns={columns}
            rows={rows}
            rowKey={(u) => u.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
        <p className="text-muted text-[11.5px] mt-3">
          {t('admin.users.rolesNote')}
        </p>
      </div>
    </div>
  );
}

// Adding a user, at /admin/users/new.
export function NewUserPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const claims = useAuthStore((s) => s.claims);
  const orgAdminOrgs = useAuthStore((s) => s.orgAdminOrgs);
  const memberships = useAuthStore((s) => s.memberships);
  // org.read admits super_admin and org_admin only.
  const canListOrgs = claims.global_role === 'super_admin' || orgAdminOrgs.length > 0
    || memberships.some((m) => m.role === 'org_admin');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [orgId, setOrgId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [password, setPassword] = useState('');
  const orgs = useOrganizations({ enabled: canListOrgs }).data ?? [];
  // Only the selected organization's groups; with no organization selected the list is empty.
  const groups = (useProjects(orgId || undefined).data ?? []).filter((g) => !orgId || g.org_id === orgId);
  const create = useCreateUser();
  const pushToast = useUiStore((s) => s.pushToast);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const touch = (k: string) => setTouched((v) => ({ ...v, [k]: true }));

  const submit = () => {
    create.mutate(
      { email: email.trim(), name: name.trim(), group_id: groupId, initial_role: 'member', password },
      {
        onSuccess: () => { pushToast('success', t('admin.users.created', { name })); navigate('/admin/users'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  const valid = emailOk(email) && name.trim().length > 0 && groupId.length > 0 && password.length >= 8;
  // Validated on blur, at the field.
  const emailError = touched.email && email.trim() && !emailOk(email) ? t('admin.users.emailInvalid') : null;
  const passwordError = touched.password && password && password.length < 8 ? t('admin.users.passwordTooShort') : null;
  const blockers = [
    !emailOk(email) && t('common.email'),
    !name.trim() && t('common.name'),
    !groupId && t('common.group'),
    password.length < 8 && t('admin.users.initialPassword'),
  ].filter(Boolean) as string[];
  useUnsavedGuard((!!email || !!name || !!password) && !create.isPending);

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.users.newTitle')}
        crumbs={[{ label: t('admin.users.title'), to: '/admin/users' }, { label: t('admin.users.newTitle') }]}
        actions={<BackLink to={'/admin/users'} />}
      />
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); setTouched({ email: true, password: true }); submit(); }}>
      <div className="grid gap-3">
        <Field label={t('common.email')} required error={emailError}>
          {(ids) => (
            <input
              {...ids}
              className="gs-input w-full"
              type="email"
              inputMode="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => touch('email')}
              placeholder="user@example.com"
              autoFocus
            />
          )}
        </Field>
        <Field label={t('common.name')} required>
          {(ids) => (
            <input
              {...ids}
              className="gs-input w-full"
              autoComplete="name"
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('admin.users.namePlaceholder')}
            />
          )}
        </Field>
        {orgs.length === 0 && (
          <p className="text-[13px] text-danger">{t('admin.users.noOrgWarning')}</p>
        )}
        <Field
          label={t('common.organization')}
          required
          hint={!canListOrgs ? t('admin.users.orgListNotPermitted') : undefined}
        >
          {(ids) => (
            <select
              {...ids}
              className="gs-input w-full"
              value={orgId}
              onChange={(e) => {
                setOrgId(e.target.value);
                setGroupId('');
              }}
              disabled={orgs.length === 0}
            >
              <option value="">{t('admin.users.selectOrg')}</option>
              {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          )}
        </Field>
        <Field label={t('common.group')} required hint={!orgId ? t('admin.users.selectGroupFirst') : undefined}>
          {(ids) => (
            <select
              {...ids}
              className="gs-input w-full"
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
              disabled={!orgId || groups.length === 0}
            >
              <option value="">{!orgId ? t('admin.users.selectGroupFirst') : groups.length === 0 ? t('admin.users.noGroups') : t('admin.users.selectGroup')}</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          )}
        </Field>
        <Field label={t('admin.users.initialPassword')} required error={passwordError} hint={t('auth.passwordRule')}>
          {(ids) => (
            <input
              {...ids}
              className="gs-input w-full"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onBlur={() => touch('password')}
              placeholder={t('admin.users.initialPasswordPlaceholder')}
            />
          )}
        </Field>
        <p className="text-muted text-[12px]"><Trans i18nKey="admin.users.initialPasswordNote" components={{ 1: <b /> }} /></p>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : blockers} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/users')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('admin.users.creating') : t('admin.users.create')}</button>
      </div>
      </form>
    </div>
  );
}

// Editing a user, at /admin/users/:userId/edit. What is editable depends on the caller's role.
export function EditUserPage() {
  const { t } = useTranslation();
  const { userId = '' } = useParams();
  const user = useUser(userId).data ?? null;
  // Determine the caller's authority (super_admin or org_admin), by the same rule as AdminUsers.
  const claims = useAuthStore((s) => s.claims);
  const memberships = useAuthStore((s) => s.memberships);
  const orgAdminOrgs = useAuthStore((s) => s.orgAdminOrgs);
  const isSuper = claims.global_role === 'super_admin';
  const isOrgAdmin = isSuper || orgAdminOrgs.length > 0 || memberships.some((m) => m.role === 'org_admin');
  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.users.editTitle') + (user ? ' — ' + (user.name) : '')}
        crumbs={[{ label: t('admin.users.title'), to: '/admin/users' }, { label: t('admin.users.editTitle') }]}
        actions={<BackLink to={'/admin/users'} />}
      />
      {user ? <EditUserForm user={user} isSuper={isSuper} canEditDept={isOrgAdmin} /> : <p className="text-muted">{t('admin.users.notFound')}</p>}
    </div>
  );
}

function EditUserForm({
  user,
  isSuper,
  canEditDept,
}: {
  user: AdminUser;
  isSuper: boolean;
  canEditDept: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const update = useUpdateUser();
  const setDept = useSetUserDepartment();
  const pushToast = useUiStore((s) => s.pushToast);

  const orgs = useOrganizations({ enabled: isSuper }).data ?? [];
  const allProjects = useProjects().data ?? [];

  // Initialise from the current membership, taking the first group.
  const cur = user?.memberships?.[0];
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [orgId, setOrgId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [status, setStatus] = useState<'active' | 'suspended'>('active');

  const targetId = user?.id;
  useEffect(() => {
    setName(user?.name ?? '');
    setEmail(user?.email ?? '');
    setPassword('');
    setOrgId(cur?.org_id ?? '');
    setGroupId(cur?.group_id ?? '');
    setStatus(user?.status === 'suspended' ? 'suspended' : 'active');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  const curStatus: 'active' | 'suspended' = user.status === 'suspended' ? 'suspended' : 'active';

  // super_admin filters groups by organization; an org_admin sees every group they can see, which is
  // their own organization's.
  const deptOptions = isSuper ? allProjects.filter((p) => !orgId || p.org_id === orgId) : allProjects;
  const curGroupId = cur?.group_id ?? '';
  const multipleMemberships = (user.memberships?.length ?? 0) > 1;
  const emailInvalid = isSuper && email.trim() !== '' && !emailOk(email);

  const submit = async () => {
    try {
      // 1. Base fields (name, email, password): only send what this caller may change.
      const patch: { id: string; name?: string; email?: string; password?: string; status?: 'active' | 'suspended' } = { id: user.id };
      if (isSuper && name.trim() && name.trim() !== user.name) patch.name = name.trim();
      if (isSuper && email.trim() && email.trim().toLowerCase() !== user.email) patch.email = email.trim();
      if (password) patch.password = password;
      if (canEditDept && status !== curStatus) patch.status = status;  // status: super_admin and org_admin
      const didPatch = !!(patch.name || patch.email || patch.password || patch.status);
      if (didPatch) await update.mutateAsync(patch);
      // 2. Group change: super_admin or org_admin, and only for a single membership.
      const didDept = canEditDept && !multipleMemberships && groupId !== curGroupId;
      if (didDept) await setDept.mutateAsync({ id: user.id, group_id: groupId || null });
      pushToast(didPatch || didDept ? 'success' : 'info', didPatch || didDept ? t('admin.users.updated', { name: user.name }) : t('admin.users.noChanges'));
      navigate('/admin/users');
    } catch (e) {
      pushToast('error', humanizeError(asApiError(e)));
    }
  };

  const pending = update.isPending || setDept.isPending;
  const dirty = (isSuper && (name.trim() !== user.name || email.trim().toLowerCase() !== user.email))
    || !!password
    || (canEditDept && status !== curStatus)
    || (canEditDept && !multipleMemberships && groupId !== curGroupId);
  useUnsavedGuard(dirty && !pending);

  return (
    <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        {isSuper ? (
          <Field label={t('common.email')} error={emailInvalid ? t('admin.users.invalidEmail') : null}>
            {(ids) => (
              <input {...ids} className="gs-input w-full" type="email" inputMode="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            )}
          </Field>
        ) : (
          <div className="text-[13px] font-semibold">
            {t('common.email')}
            <div className="mt-1 text-[13px] font-mono text-muted">{user.email}</div>
          </div>
        )}
        {isSuper ? (
          <Field label={t('common.name')}>
            {(ids) => <input {...ids} className="gs-input w-full" autoComplete="name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
        ) : (
          <div className="text-[13px] font-semibold">
            {t('common.name')}
            <div className="mt-1 text-[13px] text-muted">{user.name}</div>
          </div>
        )}

        {canEditDept && (
          multipleMemberships ? (
            <div className="text-[12px] text-muted">
              <Trans i18nKey="admin.users.multiGroupWarning" components={{ 1: <b /> }} />
            </div>
          ) : (
            <>
              {isSuper && (
                <label className="text-[13px] font-semibold">
                  {t('common.organization')}
                  <select className="gs-input mt-1 w-full" value={orgId} onChange={(e) => { setOrgId(e.target.value); setGroupId(''); }}>
                    <option value="">{t('admin.users.selectOrg')}</option>
                    {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                  </select>
                </label>
              )}
              <label className="text-[13px] font-semibold">
                {t('common.group')}
                <select className="gs-input mt-1 w-full" value={groupId} onChange={(e) => setGroupId(e.target.value)} disabled={isSuper && !orgId}>
                  <option value="">{t('admin.users.noMembership')}</option>
                  {deptOptions.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                </select>
              </label>
            </>
          )
        )}

        {canEditDept && (
          <label className="text-[13px] font-semibold">
            {t('common.status')}
            <select className="gs-input mt-1 w-full" value={status} onChange={(e) => setStatus(e.target.value as 'active' | 'suspended')}>
              <option value="active">{t('enum.userStatus.active')}</option>
              <option value="suspended">{t('enum.userStatus.suspended')}</option>
            </select>
          </label>
        )}

        <Field
          label={`${t('admin.users.resetPassword')} (${t('admin.users.resetPasswordHint')})`}
          hint={t('auth.passwordRule')}
          error={password.length > 0 && password.length < 8 ? t('admin.users.passwordTooShort') : null}
        >
          {(ids) => (
            <input {...ids} className="gs-input w-full" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t('admin.users.resetPasswordPlaceholder')} />
          )}
        </Field>
        <p className="text-muted text-[12px]"><Trans i18nKey="admin.users.resetNote" components={{ 1: <b /> }} /></p>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={emailInvalid ? [t('admin.users.invalidEmail')] : dirty ? [] : [t('account.noChanges')]} />
        <button type="button" className="gs-btn" onClick={() => navigate('/admin/users')}>{t('common.cancel')}</button>
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={pending || emailInvalid || !dirty || (password.length > 0 && password.length < 8)}>
          {pending ? t('admin.users.saving') : t('common.save')}
        </button>
      </div>
    </form>
  );
}

// Delete confirmation, at /admin/users/:userId/delete.
export function DeleteUserPage() {
  const { t } = useTranslation();
  const { userId = '' } = useParams();
  const navigate = useNavigate();
  const del = useDeleteUser();
  const pushToast = useUiStore((s) => s.pushToast);
  const user = useUser(userId).data ?? null;
  const [typed, setTyped] = useState('');
  const typedOk = !!user && typed.trim() === user.email;

  const submit = () => {
    if (!user) return;
    del.mutate(
      { id: user.id },
      {
        onSuccess: () => { pushToast('success', t('admin.users.deleted', { name: user.name })); navigate('/admin/users'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={t('admin.users.deleteTitle')}
        crumbs={[{ label: t('admin.users.title'), to: '/admin/users' }, { label: t('admin.users.deleteTitle') }]}
        actions={<BackLink to={'/admin/users'} />}
      />
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); if (typedOk) submit(); }}>
        {user ? (
          <>
            <p className="text-[13px]">{t('admin.users.confirmDelete', { name: user.name, email: user.email })}</p>
            <ul className="mt-3 space-y-1 text-[12.5px] text-muted list-disc pl-5">
              <li>{t('admin.users.consequenceSignIn')}</li>
              <li>{t('admin.users.consequenceSessions')}</li>
              <li>{t('admin.users.consequenceLedger')}</li>
            </ul>
            {/* Typed confirmation: this is not recoverable from the console, so it takes more than
                one click to reach. */}
            <Field label={t('confirm.typeToConfirm', { text: user.email })} required className="mt-3">
              {(ids) => (
                <input {...ids} className="gs-input w-full" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
              )}
            </Field>
          </>
        ) : <p className="text-muted">{t('admin.users.notFound')}</p>}
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={!user || typedOk ? [] : [t('admin.users.typeEmailToConfirm')]} />
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/users')}>{t('common.cancel')}</button>
          <button type="submit" className="gs-btn gs-btn-danger disabled:opacity-50" disabled={!user || !typedOk || del.isPending}>
            {del.isPending ? t('admin.users.deleting') : t('common.delete')}
          </button>
        </div>
      </form>
    </div>
  );
}
