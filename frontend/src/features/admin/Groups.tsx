import { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  useOrganizations,
  useProjects,
  useProject,
  useCreateProject,
  useUpdateProject,
  useDeleteProject,
  useMemberships,
  useAddMembership,
  useUpdateMembership,
  useRemoveMembership,
  type Project,
  type ProjectStatus,
  type Membership,
  type MembershipRole,
} from '@/api/hooks/useGroups';
import { UserSearchPicker } from '@/components/UserSearchPicker';
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
import { formatDateTime, roleLabel, statusLabel } from '@/lib/format';

// Group management: organization → group → members. Projects are called groups in the UI.

// Roles offered when adding a member. org_admin belongs to organization management, and
// group_admin is granted with the Admins button, so neither appears here.
const ROLE_OPTIONS: MembershipRole[] = ['member', 'guest'];

export function AdminGroups() {
  const { t } = useTranslation();
  const [orgFilter, setOrgFilter] = useState('');
  const table = useTableState('', { sort: 'name', dir: 'asc' });
  const [memberProject, setMemberProject] = useState<Project | null>(null);

  // Creating and deleting a group needs org_admin or above; members, admins, and editing need
  // group_admin or above.
  const claims = useAuthStore((s) => s.claims);
  const memberships = useAuthStore((s) => s.memberships);
  const orgAdminOrgs = useAuthStore((s) => s.orgAdminOrgs);
  const isSuper = claims.global_role === 'super_admin';
  const isOrgAdmin = isSuper || orgAdminOrgs.length > 0 || memberships.some((m) => m.role === 'org_admin');

  // org.read admits only super_admin and org_admin, so a group_admin would get a 403; the call is
  // skipped entirely.
  const { data: orgs, isLoading: orgsLoading } = useOrganizations({ enabled: isOrgAdmin });
  // Fetch every visible group and filter by organization client-side; the filter options are derived
  // from the same list.
  const { data: allProjects, isLoading, isFetching, refetch, dataUpdatedAt } = useProjects();
  const projects = (allProjects ?? []).filter((p) => !orgFilter || p.org_id === orgFilter);
  // A group must belong to an organization, so creation is blocked while none exist.
  const hasOrgs = (orgs?.length ?? 0) > 0;

  const orgName = (id: string) => orgs?.find((o) => o.id === id)?.name ?? id;
  // Organization filter options, taken as the distinct organizations of the visible groups (which
  // works for a group_admin too).
  const orgOptions = (() => {
    const m = new Map<string, string>();
    for (const p of allProjects ?? []) m.set(p.org_id, p.org_name ?? p.org_id);
    return [...m.entries()].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  })();

  const columns: Column<Project>[] = [
    { key: 'org_id', header: t('common.organization'), hideOnMobile: true, sortBy: (p) => p.org_name ?? orgName(p.org_id), render: (p) => p.org_name ?? orgName(p.org_id) },
    {
      key: 'name',
      header: t('admin.groups.colGroup'),
      sortBy: (p) => p.name,
      render: (p) => (
        <div className="min-w-0">
          <b>{p.name}</b>
          <div className="flex items-center gap-1 text-muted text-[12px]">
            <code className="font-mono truncate max-w-[150px]" title={p.id}>{p.id}</code>
            <CopyButton value={p.id} label={t('admin.groups.copyId')} />
          </div>
        </div>
      ),
    },
    {
      key: 'wallet_id',
      header: t('admin.groups.colWallet'),
      hideOnMobile: true,
      sortBy: (p) => p.wallet_id ?? '',
      render: (p) => (p.wallet_id
        ? (
          <span className="inline-flex items-center gap-1 min-w-0">
            <code className="font-mono text-[12px] truncate max-w-[150px]" title={p.wallet_id}>{p.wallet_id}</code>
            <CopyButton value={p.wallet_id} label={t('admin.groups.copyWalletId')} />
          </span>
        )
        : <span className="text-muted">—</span>),
    },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (p) => p.status,
      render: (p) => (
        <span className={`gs-pill ${p.status === 'active' ? 'bg-free-soft text-free' : 'bg-surface-2 text-muted'}`}>{statusLabel(p.status)}</span>
      ),
    },
    {
      key: 'created_at',
      header: t('common.created'),
      hideOnMobile: true,
      sortBy: (p) => (p.created_at ? new Date(p.created_at).getTime() : 0),
      render: (p) => <Timestamp value={p.created_at} className="text-muted" />,
    },
    {
      key: 'actions',
      header: t('admin.groups.colActions'),
      sortable: false,
      align: 'right',
      render: (p) => (
        <div className="flex flex-nowrap gap-2 justify-end">
          <Link to={`/admin/groups/${p.id}/admins`} className="gs-btn gs-btn-sm">{t('admin.groups.admins')}</Link>
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => setMemberProject(p)}>
            {t('admin.groups.members')}
          </button>
          <Link to={`/admin/groups/${p.id}/edit`} className="gs-btn gs-btn-sm">{t('common.edit')}</Link>
          {isOrgAdmin && (
            <Link to={`/admin/groups/${p.id}/delete`} className="gs-btn gs-btn-sm gs-btn-danger">{t('common.delete')}</Link>
          )}
        </div>
      ),
    },
  ];

  const all = projects ?? [];
  const matched = all.filter((p) => {
    const q = table.query.trim().toLowerCase();
    return !q || p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q) || (p.org_name ?? '').toLowerCase().includes(q);
  });
  const rows = sortRows(matched, sortAccessor(columns, table.sort), table.dir);

  return (
    <div>
      <PageHeader
        title={t('admin.groups.title')}
        description={t('admin.groups.subtitle')}
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={isOrgAdmin && (hasOrgs ? (
          <Link to="/admin/groups/new" className="gs-btn gs-btn-primary">{t('admin.groups.add')}</Link>
        ) : (
          <button type="button" className="gs-btn gs-btn-primary" disabled title={t('admin.groups.addDisabledHint')}>
            {t('admin.groups.add')}
          </button>
        ))}
      />

      {isOrgAdmin && !orgsLoading && !hasOrgs && (
        <div className="gs-card mb-4 bg-surface-2 text-[13px] text-muted">
          <Trans i18nKey="admin.groups.noOrgWarning" components={{ 1: <b /> }} />
        </div>
      )}

      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.groups.searchPlaceholder')}
        total={all.length}
        shown={matched.length}
        onClear={table.clear}
      >
        <label className="gs-sr-only" htmlFor="gs-group-org">{t('admin.groups.orgFilter')}</label>
        <select id="gs-group-org" className="gs-input w-auto" value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}>
          <option value="">{t('admin.groups.allOrgs')}</option>
          {orgOptions.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </TableToolbar>

      <div className="gs-card">
        {isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon="◧" title={t('admin.groups.empty')} description={t('admin.groups.emptyDescription')} />
        ) : (
          <Table
            caption={t('admin.groups.title')}
            columns={columns}
            rows={rows}
            rowKey={(p) => p.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
          />
        )}
      </div>

      {memberProject && (
        <div className="gs-card mt-4">
          <MembersPanel project={memberProject} />
        </div>
      )}

    </div>
  );
}

// Appointing and removing group administrators. A non-member is added, an existing member is
// promoted, and removal demotes them back to member.
export function GroupAdminsPage() {
  const { t } = useTranslation();
  const { groupId = '' } = useParams();
  const project = useProject(groupId).data;
  const { data: members = [], isLoading } = useMemberships(groupId);
  const add = useAddMembership();
  const updateRole = useUpdateMembership();
  const pushToast = useUiStore((s) => s.pushToast);
  const [userId, setUserId] = useState('');

  const admins = members.filter((m) => m.role === 'group_admin');
  const adminIds = new Set(admins.map((m) => m.user_id));

  const designate = () => {
    if (!userId) return;
    const existing = members.find((m) => m.user_id === userId);
    const onSuccess = () => { pushToast('success', t('admin.groups.adminAdded')); setUserId(''); };
    const onError = (e: unknown) => pushToast('error', humanizeError(asApiError(e)));
    if (existing) {
      updateRole.mutate({ projectId: groupId, membershipId: existing.id, role: 'group_admin' }, { onSuccess, onError });
    } else {
      add.mutate({ projectId: groupId, user_id: userId, role: 'group_admin' }, { onSuccess, onError });
    }
  };

  const demote = (m: Membership) => {
    updateRole.mutate(
      { projectId: groupId, membershipId: m.id, role: 'member' },
      {
        onSuccess: () => pushToast('success', t('admin.groups.adminRemovedNamed', { name: m.user_name }), {
          label: t('common.undo'),
          run: () => updateRole.mutate({ projectId: groupId, membershipId: m.id, role: 'group_admin' }, {
            onSuccess: () => pushToast('success', t('admin.groups.adminRestored', { name: m.user_name })),
            onError: (e) => pushToast('error', humanizeError(asApiError(e))),
          }),
        }),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  const pending = add.isPending || updateRole.isPending;

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.groups.adminsTitle') + (project ? ' — ' + project.name : '')}
        crumbs={[{ label: t('admin.groups.title'), to: '/admin/groups' }, { label: t('admin.groups.adminsTitle') }]}
        actions={<BackLink to="/admin/groups" label={t('admin.groups.backToList')} />}
      />
      <div className="gs-card space-y-4">
        <p className="text-muted text-[12px]">
          <Trans i18nKey="admin.groups.adminsNote" components={{ 1: <b /> }} />
        </p>

        <div className="space-y-2">
          <span className="text-[12px] font-semibold text-muted">{t('admin.groups.searchInOrg')}</span>
          <UserSearchPicker orgId={project?.org_id} excludeIds={adminIds} selectedId={userId} onSelect={setUserId} emptyHint={t('admin.groups.noMatchInOrg')} />
          <button type="button" className="gs-btn gs-btn-primary w-full disabled:opacity-50" disabled={!userId || pending} onClick={designate}>
            {pending ? t('admin.groups.appointing') : t('admin.groups.appoint')}
          </button>
        </div>

        <div>
          <div className="text-[12px] font-semibold text-muted mb-1.5">{t('admin.groups.currentAdmins')} {isLoading ? '' : `(${admins.length})`}</div>
          {isLoading ? (
            <p className="text-muted text-[13px]">{t('common.loading')}</p>
          ) : admins.length === 0 ? (
            <p className="text-muted text-[13px]">{t('admin.groups.noAdmins')}</p>
          ) : (
            <ul className="space-y-1.5">
              {admins.map((m) => (
                <li key={m.id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-surface-2">
                  <span className="text-[13px]"><b>{m.user_name}</b></span>
                  <button type="button" className="gs-btn gs-btn-sm text-danger" disabled={updateRole.isPending} onClick={() => demote(m)}>{t('admin.groups.demote')}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// Delete confirmation, at /admin/groups/:groupId/delete.
export function DeleteGroupPage() {
  const { t } = useTranslation();
  const { groupId = '' } = useParams();
  const navigate = useNavigate();
  const del = useDeleteProject();
  const pushToast = useUiStore((s) => s.pushToast);
  const project = useProject(groupId).data;
  const [typed, setTyped] = useState('');
  const typedOk = !!project && typed.trim() === project.name;

  const submit = () => {
    if (!project) return;
    del.mutate(project.id, {
      onSuccess: () => { pushToast('success', t('admin.groups.deleted', { name: project.name })); navigate('/admin/groups'); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={t('admin.groups.deleteTitle')}
        crumbs={[{ label: t('admin.groups.title'), to: '/admin/groups' }, { label: t('admin.groups.deleteTitle') }]}
        actions={<BackLink to={'/admin/groups'} />}
      />
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); if (typedOk) submit(); }}>
        {project ? (
          <>
            <p className="text-[13px]">{t('admin.groups.confirmDelete', { name: project.name })}</p>
            <ul className="mt-3 space-y-1 text-[12.5px] text-muted list-disc pl-5">
              <li>{t('admin.groups.consequenceMembers')}</li>
              <li>{t('admin.groups.consequenceWallet')}</li>
              <li>{t('admin.groups.consequenceVolumes')}</li>
            </ul>
            <Field label={t('confirm.typeToConfirm', { text: project.name })} required className="mt-3">
              {(ids) => <input {...ids} className="gs-input w-full" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />}
            </Field>
          </>
        ) : <p className="text-muted">{t('admin.groups.notFound')}</p>}
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={!project || typedOk ? [] : [t('admin.groups.typeNameToConfirm')]} />
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/groups')}>{t('common.cancel')}</button>
          <button type="submit" className="gs-btn gs-btn-danger disabled:opacity-50" disabled={!project || !typedOk || del.isPending}>
            {del.isPending ? t('admin.groups.deleting') : t('common.delete')}
          </button>
        </div>
      </form>
    </div>
  );
}


// New group, at /admin/groups/new.
export function NewGroupPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const create = useCreateProject();
  const claims = useAuthStore((s) => s.claims);
  const orgAdminOrgs = useAuthStore((s) => s.orgAdminOrgs);
  const memberships = useAuthStore((s) => s.memberships);
  // org.read admits super_admin and org_admin only.
  const canListOrgs = claims.global_role === 'super_admin' || orgAdminOrgs.length > 0
    || memberships.some((m) => m.role === 'org_admin');
  const { data: orgs = [] } = useOrganizations({ enabled: canListOrgs });
  const [name, setName] = useState('');
  const [orgId, setOrgId] = useState('');
  const [withWallet, setWithWallet] = useState(true);
  const valid = name.trim().length > 0 && orgId.length > 0;
  const blockers = [!orgId && t('admin.groups.ownerOrg'), !name.trim() && t('admin.groups.nameLabel')].filter(Boolean) as string[];
  useUnsavedGuard(!!name.trim() && !create.isPending);

  const submit = () => {
    if (!valid) return;
    create.mutate(
      { org_id: orgId, name: name.trim(), create_project_wallet: withWallet },
      {
        onSuccess: () => { pushToast('success', t('admin.groups.created', { name })); navigate('/admin/groups'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.groups.newTitle')}
        crumbs={[{ label: t('admin.groups.title'), to: '/admin/groups' }, { label: t('admin.groups.newTitle') }]}
        actions={<BackLink to={'/admin/groups'} />}
      />
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="grid gap-3">
          {orgs.length === 0 && (
            <p role="alert" className="text-[13px] text-danger">{t('admin.users.noOrgWarning')}</p>
          )}
          <Field
            label={t('admin.groups.ownerOrg')}
            required
            hint={!canListOrgs ? t('admin.users.orgListNotPermitted') : undefined}
          >
            {(ids) => (
              <select {...ids} className="gs-input w-full" value={orgId} onChange={(e) => setOrgId(e.target.value)} disabled={orgs.length === 0}>
                <option value="">{t('admin.groups.selectOrg')}</option>
                {orgs.map((o) => (<option key={o.id} value={o.id}>{o.name}</option>))}
              </select>
            )}
          </Field>
          <Field label={t('admin.groups.nameLabel')} required hint={t('admin.groups.nameHint')}>
            {(ids) => <input {...ids} className="gs-input w-full" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder="ml-lab" autoFocus autoComplete="off" />}
          </Field>
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={withWallet} onChange={(e) => setWithWallet(e.target.checked)} />
            {t('admin.groups.createWallet')}
          </label>
        </div>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={valid ? [] : blockers} />
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/groups')}>{t('common.cancel')}</button>
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
            {create.isPending ? t('admin.groups.creating') : t('common.create')}
          </button>
        </div>
      </form>
    </div>
  );
}

// Group editing, at /admin/groups/:groupId/edit.
export function EditGroupPage() {
  const { t } = useTranslation();
  const { groupId = '' } = useParams();
  const navigate = useNavigate();
  const update = useUpdateProject();
  const pushToast = useUiStore((s) => s.pushToast);
  const { data: project, isLoading } = useProject(groupId);
  const [name, setName] = useState('');
  const [status, setStatus] = useState<ProjectStatus>('active');
  const pid = project?.id;
  useEffect(() => {
    if (project) { setName(project.name); setStatus(project.status ?? 'active'); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  const dirty = !!project && (name.trim() !== project.name || status !== (project.status ?? 'active'));
  useUnsavedGuard(dirty && !update.isPending);

  const submit = () => {
    if (!project) return;
    update.mutate(
      { id: project.id, name: (name.trim() || project.name), status },
      {
        onSuccess: () => { pushToast('success', t('admin.groups.updated')); navigate('/admin/groups'); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <div className="w-full">
      <PageHeader
        title={t('admin.groups.editTitle') + (project ? ' — ' + (project.name) : '')}
        crumbs={[{ label: t('admin.groups.title'), to: '/admin/groups' }, { label: t('admin.groups.editTitle') }]}
        actions={<BackLink to={'/admin/groups'} />}
      />
      {isLoading && !project ? <TableSkeleton rows={3} columns={2} /> : !project ? (
        <EmptyState icon="?" title={t('admin.groups.notFound')} action={<Link to="/admin/groups" className="gs-btn gs-btn-primary">{t('admin.groups.backToList')}</Link>} />
      ) : (
        <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="grid gap-3">
            <Field label={t('common.name')} required>
              {(ids) => <input {...ids} className="gs-input w-full" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} autoFocus autoComplete="off" />}
            </Field>
            <Field label={t('common.status')} hint={t('admin.groups.statusHint')}>
              {(ids) => (
                <select {...ids} className="gs-input w-full" value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)}>
                  <option value="active">{t('enum.status.active')}</option>
                  <option value="inactive">{t('enum.status.inactive')}</option>
                  <option value="archived">{t('enum.status.archived')}</option>
                </select>
              )}
            </Field>
          </div>
          <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
            <DisabledReason reasons={name.trim() ? (dirty ? [] : [t('account.noChanges')]) : [t('common.name')]} />
            <button type="button" className="gs-btn" onClick={() => navigate('/admin/groups')}>{t('common.cancel')}</button>
            <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={update.isPending || !name.trim() || !dirty}>
              {update.isPending ? t('admin.groups.saving') : t('common.save')}</button>
          </div>
        </form>
      )}
    </div>
  );
}

function MembersPanel({ project }: { project: Project }) {
  const { t } = useTranslation();
  const { data: members, isLoading } = useMemberships(project.id);
  const add = useAddMembership();
  const updateRole = useUpdateMembership();
  const remove = useRemoveMembership();
  const pushToast = useUiStore((s) => s.pushToast);

  const [addOpen, setAddOpen] = useState(false);

  const changeRole = (m: Membership, role: MembershipRole) =>
    updateRole.mutate(
      { projectId: project.id, membershipId: m.id, role },
      {
        onSuccess: () => pushToast('success', `${m.user_name} → ${roleLabel(role)}`),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );

  const removeMember = (m: Membership) =>
    remove.mutate(
      { projectId: project.id, membershipId: m.id },
      {
        onSuccess: () => pushToast('success', t('admin.groups.removed', { name: m.user_name })),
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );

  const columns: Column<Membership>[] = [
    { key: 'user_name', header: t('admin.groups.colMember'), render: (m) => <b>{m.user_name}</b> },
    {
      key: 'role',
      header: t('common.role'),
      // group_admin is granted and revoked with the Admins button, so it is read-only here.
      render: (m) =>
        m.role === 'group_admin' ? (
          <span className="gs-pill bg-warn-soft text-warn" title={t('admin.groups.adminRoleHint')}>
            {roleLabel('group_admin')}
          </span>
        ) : (
          <select
            className="gs-input w-auto py-1"
            value={m.role}
            onChange={(e) => changeRole(m, e.target.value as MembershipRole)}
            disabled={updateRole.isPending}
          >
            {ROLE_OPTIONS.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
        ),
    },
    {
      key: 'expires_at',
      header: t('admin.groups.colExpires'),
      render: (m) => (m.expires_at ? formatDateTime(m.expires_at) : <span className="text-muted">—</span>),
    },
    {
      key: 'actions',
      header: t('admin.groups.colActions'),
      render: (m) => {
        // The last group administrator cannot be removed; the server rejects it with 409 too.
        const adminCount = (members ?? []).filter((x) => x.role === 'group_admin').length;
        const isLastAdmin = m.role === 'group_admin' && adminCount <= 1;
        return (
          <button
            type="button"
            className="gs-btn gs-btn-sm gs-btn-danger"
            onClick={() => removeMember(m)}
            disabled={remove.isPending || isLastAdmin}
            title={isLastAdmin ? t('admin.groups.lastAdminHint') : undefined}
          >
            {t('admin.groups.remove')}
          </button>
        );
      },
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-bold">
          {t('admin.groups.membersTitle', { group: project.name })} <span className="text-muted text-[12px] font-normal">{t('admin.groups.memberCount', { count: members?.length ?? 0 })}</span>
        </h2>
        <button type="button" className="gs-btn gs-btn-sm" onClick={() => setAddOpen(true)}>
          {t('admin.groups.addMember')}
        </button>
      </div>
      {addOpen && (
        <AddMemberForm
          orgId={project.org_id}
          excludeIds={new Set((members ?? []).map((m) => m.user_id))}
          onCancel={() => setAddOpen(false)}
          onAdd={(payload) =>
            add.mutate(
              { projectId: project.id, ...payload },
              {
                onSuccess: () => { pushToast('success', t('admin.groups.memberAdded')); setAddOpen(false); },
                onError: (e) => pushToast('error', humanizeError(asApiError(e))),
              },
            )
          }
          pending={add.isPending}
        />
      )}
      {isLoading ? (
        <p className="text-muted">{t('common.loading')}</p>
      ) : (
        <Table columns={columns} rows={members ?? []} rowKey={(m) => m.id} empty={t('admin.groups.emptyMembers')} />
      )}
    </div>
  );
}

// Add member: an inline form inside the members panel.
function AddMemberForm({
  onCancel,
  onAdd,
  pending,
  orgId,
  excludeIds,
}: {
  onCancel: () => void;
  onAdd: (p: { user_id: string; role: MembershipRole; expires_at?: string; grant_credit?: string }) => void;
  pending: boolean;
  orgId: string;
  excludeIds: Set<string>;
}) {
  const { t } = useTranslation();
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState<MembershipRole>('member');
  const [expiresAt, setExpiresAt] = useState('');
  const [grantCredit, setGrantCredit] = useState('');

  const isGuest = role === 'guest';
  const valid = userId.length > 0 && (!isGuest || expiresAt.trim().length > 0);

  const submit = () => {
    const payload: { user_id: string; role: MembershipRole; expires_at?: string; grant_credit?: string } = {
      user_id: userId,
      role,
    };
    if (isGuest) {
      payload.expires_at = new Date(expiresAt).toISOString();
      if (grantCredit.trim()) payload.grant_credit = grantCredit.trim();
    }
    onAdd(payload);
  };

  return (
    <div className="border border-border rounded-xl p-3 mb-3">
      <div className="grid gap-3">
        <div className="text-[13px] font-semibold">
          {t('admin.groups.searchInOrg')}
          <div className="mt-1 font-normal">
            <UserSearchPicker orgId={orgId} excludeIds={excludeIds} selectedId={userId} onSelect={setUserId} emptyHint={t('admin.groups.noMatchInOrg')} />
          </div>
        </div>
        <label className="text-[13px] font-semibold">
          {t('admin.groups.role')}
          <select className="gs-input mt-1 w-full" value={role} onChange={(e) => setRole(e.target.value as MembershipRole)}>
            {ROLE_OPTIONS.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
        </label>
        {isGuest && (
          <>
            <label className="text-[13px] font-semibold">
              {t('admin.groups.expiresAt')}
              <input className="gs-input mt-1 w-full" type="datetime-local" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} autoComplete="off" />
            </label>
            <label className="text-[13px] font-semibold">
              {t('admin.groups.creditLimit')}
              <input className="gs-input mt-1 w-full" value={grantCredit} onChange={(e) => setGrantCredit(e.target.value)} placeholder="50.00" autoComplete="off" />
            </label>
          </>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="gs-btn gs-btn-sm" onClick={onCancel}>{t('common.cancel')}</button>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" onClick={submit} disabled={!valid || pending}>
            {pending ? t('admin.groups.adding') : t('common.add')}
          </button>
        </div>
      </div>
    </div>
  );
}
