import { useEffect, useState } from 'react';
import { Select } from '@/components/Select';
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
import { Dialog } from '@/components/Dialog';
import { useAuthStore } from '@/auth/authStore';
import { Table, TableToolbar, sortAccessor, type Column } from '@/components/Table';
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { PageHeader } from '@/components/PageHeader';
import { Field, DisabledReason } from '@/components/Field';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { useConfirm } from '@/components/ConfirmDialog';
import { humanizeError, asApiError } from '@/lib/errors';
import { StatusPill } from '@/components/StatusPill';
import { formatDateTime, roleLabel, statusLabel } from '@/lib/format';
import { Plus, Question, UsersThree } from '@/components/icons';

// Group management: organization → group → members. Projects are called groups in the UI.

// Roles offered when adding a member. org_admin belongs to organization management, and
// group_admin is granted with the Admins button, so neither appears here.
const ROLE_OPTIONS: MembershipRole[] = ['member', 'guest'];

export function AdminGroups() {
  const { t } = useTranslation();
  const [orgFilter, setOrgFilter] = useState('');
  const table = useTableState('', { sort: 'name', dir: 'asc' });
  const [memberProject, setMemberProject] = useState<Project | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [editGroupId, setEditGroupId] = useState<string | null>(null);
  // 관리자 button opens a popup — appointing an admin never deserved a page navigation.
  const updateProject = useUpdateProject();
  const confirmDlg = useConfirm();
  const [adminProject, setAdminProject] = useState<Project | null>(null);

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
  const { data: allProjects, isLoading, isError, error, refetch } = useProjects();
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
          <div className="flex items-center gap-1 text-muted text-xs">
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
            <code className="font-mono text-xs truncate max-w-[150px]" title={p.wallet_id}>{p.wallet_id}</code>
            <CopyButton value={p.wallet_id} label={t('admin.groups.copyWalletId')} />
          </span>
        )
        : <span className="text-muted">-</span>),
    },
    {
      key: 'member_count',
      header: t('admin.groups.colMemberCount'),
      align: 'right',
      sortBy: (p) => p.member_count ?? 0,
      render: (p) => p.member_count ?? 0,
    },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (p) => p.status,
      render: (p) => (
        <StatusPill kind={p.status} label={statusLabel(p.status)} />
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
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => setAdminProject(p)}>{t('admin.groups.admins')}</button>
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => setEditGroupId(p.id)}>{t('common.edit')}</button>
          {isOrgAdmin && (
            <button
              type="button"
              className="gs-btn gs-btn-sm"
              disabled={updateProject.isPending}
              onClick={async () => {
                const next = p.status === 'inactive' ? 'active' : 'inactive';
                if (next === 'inactive') {
                  const ok = await confirmDlg({
                    title: t('common.deactivate'),
                    body: t('admin.groups.deactivateConfirm', { name: p.name }),
                    confirmLabel: t('common.deactivate'),
                  });
                  if (!ok) return;
                }
                updateProject.mutate({ id: p.id, status: next }, {
                  onSuccess: () => useUiStore.getState().pushToast('success', `${p.name} → ${statusLabel(next)}`),
                  onError: (e) => useUiStore.getState().pushToast('error', humanizeError(asApiError(e))),
                });
              }}
            >
              {p.status === 'inactive' ? t('common.activate') : t('common.deactivate')}
            </button>
          )}
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
        actions={isOrgAdmin && (hasOrgs ? (
          <button type="button" className="gs-btn gs-btn-primary" onClick={() => setNewOpen(true)}><Plus size={15} weight="bold" aria-hidden="true" />{t('admin.groups.add')}</button>
        ) : (
          <button type="button" className="gs-btn gs-btn-primary" disabled title={t('admin.groups.addDisabledHint')}>
            {t('admin.groups.add')}
          </button>
        ))}
      />

      {isOrgAdmin && !orgsLoading && !hasOrgs && (
        <div className="gs-card mb-4 bg-surface-2 text-sm text-muted">
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
        <Select id="gs-group-org" className="gs-input w-auto" value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}>
          <option value="">{t('admin.groups.allOrgs')}</option>
          {orgOptions.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </Select>
      </TableToolbar>

      <div className="gs-card">
        {isError ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <TableSkeleton rows={4} columns={5} />
        ) : rows.length === 0 ? (
          table.isFiltered
            ? <NoResults query={table.query} onClear={table.clear} />
            : <EmptyState icon={<UsersThree size={26} />} title={t('admin.groups.empty')} description={t('admin.groups.emptyDescription')} />
        ) : (
          <Table
            caption={t('admin.groups.title')}
            columns={columns}
            rows={rows}
            rowKey={(p) => p.id}
            sort={table.sort}
            dir={table.dir}
            onSort={table.toggleSort}
            onRowClick={setMemberProject}
          />
        )}
      </div>

      {memberProject && (
        <div className="gs-card mt-4">
          <MembersPanel project={memberProject} />
        </div>
      )}

      <Dialog
        open={!!adminProject}
        title={t('admin.groups.adminsTitle') + (adminProject ? ' - ' + adminProject.name : '')}
        onClose={() => setAdminProject(null)}
      >
        {adminProject && <GroupAdminsPanel groupId={adminProject.id} />}
      </Dialog>

      <Dialog open={newOpen} title={t('admin.groups.newTitle')} onClose={() => setNewOpen(false)}>
        <NewGroupForm onDone={() => setNewOpen(false)} />
      </Dialog>
      <Dialog open={!!editGroupId} title={t('admin.groups.editTitle')} onClose={() => setEditGroupId(null)}>
        {editGroupId && <EditGroupForm groupId={editGroupId} onDone={() => setEditGroupId(null)} />}
      </Dialog>
    </div>
  );
}

// Appointing and removing group administrators. A non-member is added, an existing member is
// promoted, and removal demotes them back to member.
export function GroupAdminsPage() {
  const { t } = useTranslation();
  const { groupId = '' } = useParams();
  const project = useProject(groupId).data;
  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={t('admin.groups.adminsTitle') + (project ? ' - ' + project.name : '')}
        crumbs={[{ label: t('admin.groups.title'), to: '/admin/groups' }, { label: t('admin.groups.adminsTitle') }]}
      />
      <div className="gs-card"><GroupAdminsPanel groupId={groupId} /></div>
    </div>
  );
}

function GroupAdminsPanel({ groupId }: { groupId: string }) {
  const { t } = useTranslation();
  const { data: members = [], isLoading, isError, error, refetch } = useMemberships(groupId);
  const updateRole = useUpdateMembership();
  const pushToast = useUiStore((s) => s.pushToast);
  const [userId, setUserId] = useState('');
  const [q, setQ] = useState('');

  const admins = members.filter((m) => m.role === 'group_admin');
  // A department's admin comes FROM the department: candidates are its existing members, not an
  // org-wide search (which quietly created a membership for whoever was picked).
  const candidates = members.filter((m) =>
    m.role !== 'group_admin'
    && (!q.trim() || (m.user_name ?? '').toLowerCase().includes(q.trim().toLowerCase())));

  const designate = () => {
    if (!userId) return;
    const existing = members.find((m) => m.user_id === userId);
    if (!existing) return;
    updateRole.mutate(
      { projectId: groupId, membershipId: existing.id, role: 'group_admin' },
      {
        onSuccess: () => { pushToast('success', t('admin.groups.adminAdded')); setUserId(''); },
        onError: (e: unknown) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
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

  const pending = updateRole.isPending;

  return (
    <div className="space-y-4">
        <p className="text-muted text-xs">
          <Trans i18nKey="admin.groups.adminsNote" components={{ 1: <b /> }} />
        </p>

        <div className="space-y-2">
          <span className="text-xs font-semibold text-muted">{t('admin.groups.pickFromMembers')}</span>
          <input className="gs-input w-full" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={t('admin.groups.memberSearchPlaceholder')} autoComplete="off" />
          <div className="max-h-44 overflow-y-auto rounded-ctl border border-border divide-y divide-border/60">
            {candidates.length === 0 ? (
              <p className="px-3 py-2.5 text-muted text-sm">{t('admin.groups.noMemberCandidates')}</p>
            ) : candidates.map((m) => (
              <button key={m.id} type="button"
                className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-surface-2 ${userId === m.user_id ? 'bg-primary-soft text-primary font-semibold' : ''}`}
                onClick={() => setUserId(m.user_id === userId ? '' : m.user_id)}
                aria-pressed={userId === m.user_id}
              >
                <b>{m.user_name}</b>
                <span className="text-muted text-xs">{roleLabel(m.role)}</span>
              </button>
            ))}
          </div>
          <button type="button" className="gs-btn gs-btn-primary w-full disabled:opacity-50" disabled={!userId || pending} onClick={designate}>
            {pending ? t('admin.groups.appointing') : t('admin.groups.appoint')}
          </button>
        </div>

        <div>
          <div className="text-xs font-semibold text-muted mb-1.5">{t('admin.groups.currentAdmins')} {isLoading ? '' : `(${admins.length})`}</div>
          {isError ? (
            <ErrorState error={error} onRetry={() => refetch()} />
          ) : isLoading ? (
            <p className="text-muted text-sm">{t('common.loading')}</p>
          ) : admins.length === 0 ? (
            <p className="text-muted text-sm">{t('admin.groups.noAdmins')}</p>
          ) : (
            <ul className="space-y-1.5">
              {admins.map((m) => (
                <li key={m.id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-ctl bg-surface-2">
                  <span className="text-sm"><b>{m.user_name}</b></span>
                  <button type="button" className="gs-btn gs-btn-sm gs-btn-danger disabled:opacity-50" disabled={updateRole.isPending || admins.length <= 1}
                    title={admins.length <= 1 ? t('admin.groups.lastAdminHint') : undefined} onClick={() => demote(m)}>{t('admin.groups.demote')}</button>
                </li>
              ))}
            </ul>
          )}
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
      />
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); if (typedOk) submit(); }}>
        {project ? (
          <>
            <p className="text-sm">{t('admin.groups.confirmDelete', { name: project.name })}</p>
            <ul className="mt-3 space-y-1 text-xs text-muted list-disc pl-5">
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
          <button type="submit" className="gs-btn gs-btn-danger disabled:opacity-50" disabled={!project || !typedOk || del.isPending}>
            {del.isPending ? t('admin.groups.deleting') : t('common.delete')}
          </button>
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/groups')}>{t('common.cancel')}</button>
        </div>
      </form>
    </div>
  );
}


// New group (모달).
function NewGroupForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
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
  const [withPool, setWithPool] = useState(false);
  const [welcomeCredit, setWelcomeCredit] = useState('0');
  const valid = name.trim().length > 0 && orgId.length > 0;
  const blockers = [!orgId && t('admin.groups.ownerOrg'), !name.trim() && t('admin.groups.nameLabel')].filter(Boolean) as string[];
  useUnsavedGuard(!!name.trim() && !create.isPending);

  const submit = () => {
    if (!valid) return;
    create.mutate(
      { org_id: orgId, name: name.trim(), create_project_wallet: withWallet, create_node_pool: withPool, default_member_credit: welcomeCredit.trim() || '0' },
      {
        onSuccess: () => { pushToast('success', t('admin.groups.created', { name })); onDone(); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
      <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="grid gap-3">
          {orgs.length === 0 && (
            <p role="alert" className="text-sm text-danger">{t('admin.users.noOrgWarning')}</p>
          )}
          <Field
            label={t('admin.groups.ownerOrg')}
            required
            hint={!canListOrgs ? t('admin.users.orgListNotPermitted') : undefined}
          >
            {(ids) => (
              <Select {...ids} className="gs-input w-full" value={orgId} onChange={(e) => setOrgId(e.target.value)} disabled={orgs.length === 0}>
                <option value="">{t('admin.groups.selectOrg')}</option>
                {orgs.map((o) => (<option key={o.id} value={o.id}>{o.name}</option>))}
              </Select>
            )}
          </Field>
          <Field label={t('admin.groups.nameLabel')} required hint={t('admin.groups.nameHint')}>
            {(ids) => <input {...ids} className="gs-input w-full" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder="ml-lab" autoFocus autoComplete="off" />}
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={withWallet} onChange={(e) => setWithWallet(e.target.checked)} />
            {t('admin.groups.createWallet')}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={withPool} onChange={(e) => setWithPool(e.target.checked)} />
            <span>{t('admin.groups.createPool')} <span className="text-muted text-2xs block">{t('admin.groups.createPoolHint')}</span></span>
          </label>
          <Field label={t('admin.groups.welcomeCredit')} hint={t('admin.groups.welcomeCreditHint')}>
            {(ids) => <input {...ids} className="gs-input w-full" type="number" min={0} max={1000000} step="any" inputMode="numeric" value={welcomeCredit} onChange={(e) => setWelcomeCredit(e.target.value)} autoComplete="off" />}
          </Field>
        </div>
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={valid ? [] : blockers} />
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
            {create.isPending ? t('admin.groups.creating') : t('common.create')}
          </button>
          <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
        </div>
      </form>
  );
}

// Group editing (모달).
function EditGroupForm({ groupId, onDone }: { groupId: string; onDone: () => void }) {
  const { t } = useTranslation();
  const update = useUpdateProject();
  const pushToast = useUiStore((s) => s.pushToast);
  const { data: project, isLoading } = useProject(groupId);
  const [name, setName] = useState('');
  const [status, setStatus] = useState<ProjectStatus>('active');
  const [welcomeCredit, setWelcomeCredit] = useState('0');
  const pid = project?.id;
  useEffect(() => {
    if (project) { setName(project.name); setStatus(project.status ?? 'active'); setWelcomeCredit(String((project as { default_member_credit?: string }).default_member_credit ?? '0')); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  const dirty = !!project && (name.trim() !== project.name || status !== (project.status ?? 'active')
    || Number(welcomeCredit || 0) !== Number((project as { default_member_credit?: string }).default_member_credit ?? 0));
  useUnsavedGuard(dirty && !update.isPending);

  const submit = () => {
    if (!project) return;
    update.mutate(
      { id: project.id, name: (name.trim() || project.name), status, default_member_credit: welcomeCredit.trim() || '0' },
      {
        onSuccess: () => { pushToast('success', t('admin.groups.updated')); onDone(); },
        onError: (e) => pushToast('error', humanizeError(asApiError(e))),
      },
    );
  };

  return (
    <>
      {isLoading && !project ? <TableSkeleton rows={3} columns={2} /> : !project ? (
        <EmptyState icon={<Question size={26} />} title={t('admin.groups.notFound')} action={<Link to="/admin/groups" className="gs-btn gs-btn-primary">{t('admin.groups.backToList')}</Link>} />
      ) : (
        <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="grid gap-3">
            <Field label={t('common.name')} required>
              {(ids) => <input {...ids} className="gs-input w-full" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} autoFocus autoComplete="off" />}
            </Field>
            <Field label={t('admin.groups.welcomeCredit')} hint={t('admin.groups.welcomeCreditHint')}>
              {(ids) => <input {...ids} className="gs-input w-full" type="number" min={0} max={1000000} step="any" inputMode="numeric" value={welcomeCredit} onChange={(e) => setWelcomeCredit(e.target.value)} autoComplete="off" />}
            </Field>
            <Field label={t('common.status')} hint={t('admin.groups.statusHint')}>
              {(ids) => (
                <Select {...ids} className="gs-input w-full" value={status} onChange={(e) => setStatus(e.target.value as ProjectStatus)}>
                  <option value="active">{t('enum.status.active')}</option>
                  <option value="inactive">{t('enum.status.inactive')}</option>
                  <option value="archived">{t('enum.status.archived')}</option>
                </Select>
              )}
            </Field>
          </div>
          <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
            <DisabledReason reasons={name.trim() ? (dirty ? [] : [t('account.noChanges')]) : [t('common.name')]} />
            <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={update.isPending || !name.trim() || !dirty}>
              {update.isPending ? t('admin.groups.saving') : t('common.save')}</button>
            <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
          </div>
        </form>
      )}
    </>
  );
}

function MembersPanel({ project }: { project: Project }) {
  const { t } = useTranslation();
  const { data: members, isLoading } = useMemberships(project.id);
  const add = useAddMembership();
  const remove = useRemoveMembership();
  const pushToast = useUiStore((s) => s.pushToast);

  const [addOpen, setAddOpen] = useState(false);
  // A course roster can hold thousands of members: filter client-side and render incrementally.
  const [memberQuery, setMemberQuery] = useState('');
  const [shownMembers, setShownMembers] = useState(50);

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
      // Read-only tags across the board: group_admin changes via the Admins button, member/guest
      // via user management — an inline select made only this row taller and inconsistent.
      render: (m) => (
        <span className="gs-tag" title={m.role === 'group_admin' ? t('admin.groups.adminRoleHint') : undefined}>
          {roleLabel(m.role)}
        </span>
      ),
    },
    {
      key: 'expires_at',
      header: t('admin.groups.colExpires'),
      render: (m) => (m.expires_at ? formatDateTime(m.expires_at) : <span className="text-muted">-</span>),
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
          {t('admin.groups.membersTitle', { group: project.name })} <span className="text-muted text-xs font-normal">{t('admin.groups.memberCount', { count: members?.length ?? 0 })}</span>
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
        <>
          {(members?.length ?? 0) > 10 && (
            <input
              className="gs-input mb-2 max-w-[280px]"
              value={memberQuery}
              onChange={(e) => { setMemberQuery(e.target.value); setShownMembers(50); }}
              placeholder={t('admin.groups.memberSearch')}
              aria-label={t('admin.groups.memberSearch')}
            />
          )}
          {(() => {
            const q = memberQuery.trim().toLowerCase();
            const filtered = (members ?? []).filter(
              (m) => !q || (m.user_name ?? '').toLowerCase().includes(q) || m.user_id.toLowerCase().includes(q),
            );
            return (
              <>
                <Table columns={columns} rows={filtered.slice(0, shownMembers)} rowKey={(m) => m.id} empty={t('admin.groups.emptyMembers')} />
                {filtered.length > shownMembers && (
                  <button type="button" className="gs-btn gs-btn-sm mt-2" onClick={() => setShownMembers((n) => n + 200)}>
                    {t('admin.groups.showMoreMembers', { shown: shownMembers, total: filtered.length })}
                  </button>
                )}
              </>
            );
          })()}
        </>
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
    <div className="border border-border rounded-card p-3 mb-3">
      <div className="grid gap-3">
        <div className="text-sm font-semibold">
          {t('admin.groups.searchInOrg')}
          <div className="mt-1 font-normal">
            <UserSearchPicker orgId={orgId} excludeIds={excludeIds} selectedId={userId} onSelect={setUserId} emptyHint={t('admin.groups.noMatchInOrg')} />
          </div>
        </div>
        <label className="text-sm font-semibold">
          {t('admin.groups.role')}
          <Select className="gs-input mt-1 w-full" value={role} onChange={(e) => setRole(e.target.value as MembershipRole)}>
            {ROLE_OPTIONS.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </Select>
        </label>
        {isGuest && (
          <>
            <label className="text-sm font-semibold">
              {t('admin.groups.expiresAt')}
              <input className="gs-input mt-1 w-full" type="datetime-local" value={expiresAt} onChange={(e) => setExpiresAt(e.target.value)} autoComplete="off" />
            </label>
            <label className="text-sm font-semibold">
              {t('admin.groups.creditLimit')}
              <input className="gs-input mt-1 w-full" value={grantCredit} onChange={(e) => setGrantCredit(e.target.value)} placeholder="50.00" autoComplete="off" />
            </label>
          </>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" onClick={submit} disabled={!valid || pending}>
            {pending ? t('admin.groups.adding') : t('common.add')}
          </button>
          <button type="button" className="gs-btn gs-btn-sm" onClick={onCancel}>{t('common.cancel')}</button>
        </div>
      </div>
    </div>
  );
}
