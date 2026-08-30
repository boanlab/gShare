import { useMemo, useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import {
  useOrganizations, useOrganization, useCreateOrganization, useUpdateOrganization, useDeleteOrganization,
  useOrgAdmins, useAddOrgAdmin, useRemoveOrgAdmin, useProjects,
} from '@/api/hooks/useGroups';
import { Table, TableToolbar, type Column } from '@/components/Table';
import { UserSearchPicker } from '@/components/UserSearchPicker';
import { Dialog } from '@/components/Dialog';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, NoResults, TableSkeleton, ErrorState } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useConfirm } from '@/components/ConfirmDialog';
import { Field, DisabledReason } from '@/components/Field';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { StatusPill } from '@/components/StatusPill';
import { statusLabel } from '@/lib/format';
import { Buildings, Plus } from '@/components/icons';

// Organization management: CRUD over the top-level organizations, which contain groups, budgets,
// policy, and credit pools. super_admin only.
type Org = { id: string; name: string; status?: string; created_at?: string; group_count?: number; user_count?: number };

export function AdminOrgs() {
  const { t } = useTranslation();
  // 관리자 button opens a popup — appointing an admin never deserved a page navigation.
  const [adminOrg, setAdminOrg] = useState<Org | null>(null);
  // Row click shows the org's departments below — the count column alone said nothing.
  const [selOrg, setSelOrg] = useState<Org | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [editOrg, setEditOrg] = useState<Org | null>(null);
  const { data: orgs = [], isLoading, isError, error, refetch } = useOrganizations();
  const deleteOrg = useDeleteOrganization();
  const updateOrg = useUpdateOrganization();
  const pushToast = useUiStore((s) => s.pushToast);
  const confirm = useConfirm();
  const table = useTableState('', { sort: 'name', dir: 'asc' });

  const onDelete = async (o: Org) => {
    // Deleting an organization takes its groups, wallets and history with it: worth typing the name.
    const ok = await confirm({
      title: t('admin.orgs.confirmDeleteTitle', { name: o.name }),
      body: t('admin.orgs.confirmDelete', { name: o.name }),
      consequences: [
        t('admin.orgs.consequenceGroups', { count: o.group_count ?? 0 }),
        t('admin.orgs.consequenceUsers', { count: o.user_count ?? 0 }),
        t('admin.orgs.consequenceCredits'),
      ],
      confirmLabel: t('common.delete'),
      destructive: true,
      confirmText: o.name,
    });
    if (!ok) return;
    deleteOrg.mutate(o.id, {
      onSuccess: () => pushToast('success', t('admin.orgs.deleted')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const cols: Column<Org>[] = [
    {
      key: 'name',
      header: t('admin.orgs.colOrg'),
      sortBy: (o) => o.name,
      render: (o) => (
        <div className="min-w-0">
          <b>{o.name}</b>
          <div className="flex items-center gap-1 text-muted text-xs">
            <code className="font-mono truncate max-w-[150px]" title={o.id}>{o.id}</code>
            <CopyButton value={o.id} label={t('admin.orgs.copyId')} />
          </div>
        </div>
      ),
    },
    { key: 'group_count', header: t('admin.orgs.colGroups'), sortBy: (o) => o.group_count ?? 0, align: 'right', hideOnMobile: true, render: (o) => o.group_count ?? 0 },
    { key: 'user_count', header: t('admin.orgs.colUsers'), sortBy: (o) => o.user_count ?? 0, align: 'right', hideOnMobile: true, render: (o) => o.user_count ?? 0 },
    {
      key: 'status',
      header: t('common.status'),
      sortBy: (o) => o.status ?? 'active',
      render: (o) => {
        const s = o.status ?? 'active';
        return <StatusPill kind={s} label={statusLabel(s)} />;
      },
    },
    { key: 'created_at', header: t('common.created'), sortBy: (o) => (o.created_at ? new Date(o.created_at).getTime() : 0), hideOnMobile: true, render: (o) => <Timestamp value={o.created_at} /> },
    {
      key: 'actions', header: '', align: 'right',
      render: (o) => (
        <div className="flex flex-nowrap gap-1.5 justify-end">
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => setAdminOrg(o)}>{t('admin.orgs.admins')}</button>
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => setEditOrg(o)}>{t('common.edit')}</button>
          <button
            type="button"
            className="gs-btn gs-btn-sm"
            disabled={updateOrg.isPending}
            onClick={async () => {
              const next = (o.status ?? 'active') === 'inactive' ? 'active' : 'inactive';
              if (next === 'inactive') {
                const ok = await confirm({
                  title: t('common.deactivate'),
                  body: t('admin.orgs.deactivateConfirm', { name: o.name }),
                  confirmLabel: t('common.deactivate'),
                });
                if (!ok) return;
              }
              updateOrg.mutate({ id: o.id, status: next }, {
                onSuccess: () => pushToast('success', `${o.name} → ${statusLabel(next)}`),
                onError: (e) => pushToast('error', humanizeError(asApiError(e))),
              });
            }}
          >
            {(o.status ?? 'active') === 'inactive' ? t('common.activate') : t('common.deactivate')}
          </button>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={deleteOrg.isPending} onClick={() => onDelete(o)}>{t('common.delete')}</button>
        </div>
      ),
    },
  ];

  const all = orgs as Org[];
  const matched = all.filter((o) => {
    const q = table.query.trim().toLowerCase();
    return !q || o.name.toLowerCase().includes(q) || o.id.toLowerCase().includes(q);
  });
  const rows = sortRows(matched, cols.find((c) => c.key === table.sort)?.sortBy ?? null, table.dir);

  return (
    <div>
      <PageHeader
        title={t('admin.orgs.title')}
        description={t('admin.orgs.subtitle')}
        actions={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setNewOpen(true)}><Plus size={15} weight="bold" aria-hidden="true" />{t('admin.orgs.add')}</button>}
      />
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.orgs.searchPlaceholder')}
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
                icon={<Buildings size={26} />}
                title={t('admin.orgs.empty')}
                description={t('admin.orgs.emptyDescription')}
                action={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setNewOpen(true)}><Plus size={15} weight="bold" aria-hidden="true" />{t('admin.orgs.add')}</button>}
              />
            )
        ) : (
          <div>
            <Table
              caption={t('admin.orgs.title')}
              columns={cols}
              rows={rows}
              rowKey={(o) => o.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
              onRowClick={setSelOrg}
            />
          </div>
        )}
      </div>

      {selOrg && <OrgGroupsPanel org={selOrg} />}
      <Dialog open={newOpen} title={t('admin.orgs.newTitle')} onClose={() => setNewOpen(false)}>
        <NewOrgForm onDone={() => setNewOpen(false)} />
      </Dialog>
      <Dialog open={!!editOrg} title={`${t('admin.orgs.editTitle')}${editOrg ? ` - ${editOrg.name}` : ''}`} onClose={() => setEditOrg(null)}>
        {editOrg && <EditOrgForm org={editOrg} onDone={() => setEditOrg(null)} />}
      </Dialog>

      <Dialog
        open={!!adminOrg}
        title={`${t('admin.orgs.adminsTitle')}${adminOrg ? ` - ${adminOrg.name}` : ''}`}
        onClose={() => setAdminOrg(null)}
      >
        {adminOrg && <OrgAdminsPanel orgId={adminOrg.id} />}
      </Dialog>
    </div>
  );
}

// Appointing and removing organization administrators, at /admin/orgs/:orgId/admins.
export function OrgAdminsPage() {
  const { t } = useTranslation();
  const { orgId = '' } = useParams();
  const org = useOrganization(orgId).data as Org | undefined;
  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={`${t('admin.orgs.adminsTitle')}${org ? ` - ${org.name}` : ''}`}
        crumbs={[{ label: t('admin.orgs.title'), to: '/admin/orgs' }, { label: t('admin.orgs.adminsTitle') }]}
      />
      <div className="gs-card"><OrgAdminsPanel orgId={orgId} /></div>
    </div>
  );
}

function OrgAdminsPanel({ orgId }: { orgId: string }) {
  const { t } = useTranslation();
  const { data: admins = [], isLoading } = useOrgAdmins(orgId);
  const addAdmin = useAddOrgAdmin();
  const removeAdmin = useRemoveOrgAdmin();
  const pushToast = useUiStore((s) => s.pushToast);
  const [userId, setUserId] = useState('');

  // Existing administrators are excluded from the candidate list.
  const adminIds = useMemo(() => new Set(admins.map((a) => a.user_id)), [admins]);

  const add = () => {
    if (!userId) return;
    addAdmin.mutate({ orgId, user_id: userId }, {
      onSuccess: () => { pushToast('success', t('admin.orgs.adminAdded')); setUserId(''); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  const remove = (uid: string, label: string) => {
    removeAdmin.mutate({ orgId, userId: uid }, {
      onSuccess: () => pushToast('success', t('admin.orgs.adminRemovedNamed', { name: label }), {
        label: t('common.undo'),
        run: () => addAdmin.mutate({ orgId, user_id: uid }, {
          onSuccess: () => pushToast('success', t('admin.orgs.adminRestored', { name: label })),
          onError: (e) => pushToast('error', humanizeError(asApiError(e))),
        }),
      }),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  return (
    <div className="space-y-4">
        <p className="text-muted text-xs">
          {t('admin.orgs.adminsNote')}
        </p>

        <div className="space-y-2">
          <span className="text-xs font-semibold text-muted">{t('admin.orgs.searchUsers')}</span>
          <UserSearchPicker orgId={orgId} excludeIds={adminIds} selectedId={userId} onSelect={setUserId} emptyHint={t('admin.orgs.noMatchInOrg')} />
          <button type="button" className="gs-btn gs-btn-primary w-full disabled:opacity-50" disabled={!userId || addAdmin.isPending} onClick={add}>
            {addAdmin.isPending ? t('admin.orgs.appointing') : t('admin.orgs.appoint')}
          </button>
        </div>

        <div>
          <div className="text-xs font-semibold text-muted mb-1.5">{t('admin.orgs.currentAdmins')} {isLoading ? '' : `(${admins.length})`}</div>
          {isLoading ? (
            <p className="text-muted text-sm">{t('common.loading')}</p>
          ) : admins.length === 0 ? (
            <p className="text-muted text-sm">{t('admin.orgs.noAdmins')}</p>
          ) : (
            <ul className="space-y-1.5">
              {admins.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-ctl bg-surface-2">
                  <span className="text-sm"><b>{a.user_name}</b>{a.email && <span className="text-muted font-mono text-xs"> · {a.email}</span>}</span>
                  <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={removeAdmin.isPending} onClick={() => remove(a.user_id, a.user_name)}>{t('admin.orgs.removeAdmin')}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
    </div>
  );
}

// Organization editing (모달).
function EditOrgForm({ org, onDone }: { org: Org; onDone: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const updateOrg = useUpdateOrganization();
  const [name, setName] = useState(org.name);
  const [status, setStatus] = useState<'active' | 'inactive'>((org.status as 'active' | 'inactive') ?? 'active');

  const submit = () => {
    if (!name.trim()) return;
    updateOrg.mutate({ id: org.id, name: name.trim(), status }, {
      onSuccess: () => { pushToast('success', t('admin.orgs.updated')); onDone(); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const dirty = name.trim() !== org.name || status !== (org.status ?? 'active');
  useUnsavedGuard(dirty && !updateOrg.isPending);

  return (
    <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="space-y-3">
        <Field label={t('common.name')} required>
          {(ids) => <input {...ids} className="gs-input w-full" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoFocus autoComplete="off" />}
        </Field>
        <Field label={t('common.status')} hint={t('admin.orgs.statusHint')}>
          {(ids) => (
            <Select {...ids} className="gs-input w-full" value={status} onChange={(e) => setStatus(e.target.value as 'active' | 'inactive')}>
              <option value="active">{t('enum.status.active')}</option><option value="inactive">{t('enum.status.inactive')}</option>
            </Select>
          )}
        </Field>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={name.trim() ? (dirty ? [] : [t('account.noChanges')]) : [t('common.name')]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || !dirty || updateOrg.isPending}>
          {updateOrg.isPending ? t('admin.orgs.saving') : t('common.save')}</button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

// New organization (모달).
function NewOrgForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const createOrg = useCreateOrganization();
  const [name, setName] = useState('');
  const [withPool, setWithPool] = useState(false);

  const submit = () => {
    if (!name.trim()) return;
    createOrg.mutate({ name: name.trim(), create_node_pool: withPool }, {
      onSuccess: () => { pushToast('success', t('admin.orgs.created')); onDone(); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  useUnsavedGuard(!!name.trim() && !createOrg.isPending);

  return (
    <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <Field label={t('admin.orgs.nameLabel')} required hint={t('admin.orgs.nameHint')}>
        {(ids) => (
          <input
            {...ids}
            className="gs-input w-full"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('admin.orgs.namePlaceholder')}
            autoFocus autoComplete="off" />
        )}
      </Field>
      <label className="flex items-center gap-2 text-sm mt-3">
        <input type="checkbox" checked={withPool} onChange={(e) => setWithPool(e.target.checked)} />
        <span>{t('admin.orgs.createPool')} <span className="text-muted text-2xs block">{t('admin.orgs.createPoolHint')}</span></span>
      </label>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={name.trim() ? [] : [t('admin.orgs.nameLabel')]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || createOrg.isPending}>
          {createOrg.isPending ? t('admin.orgs.creating') : t('common.create')}
        </button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}


// The selected organization's departments: name, status, and a jump to department management —
// the same bottom-panel pattern the department screen uses for members.
function OrgGroupsPanel({ org }: { org: { id: string; name: string } }) {
  const { t } = useTranslation();
  const groups = (useProjects().data ?? []).filter((g) => g.org_id === org.id);
  return (
    <div className="gs-card mt-4">
      <div className="flex items-baseline justify-between gap-2 mb-3">
        <h2 className="font-bold">{t('admin.orgs.groupsPanelTitle', { name: org.name })} <span className="text-muted text-sm font-normal">{groups.length}</span></h2>
        <Link to="/admin/groups" className="text-primary text-xs font-semibold hover:underline">{t('admin.orgs.groupsPanelLink')}</Link>
      </div>
      {groups.length === 0 ? (
        <p className="text-muted text-sm">{t('admin.orgs.groupsPanelEmpty')}</p>
      ) : (
        <ul className="divide-y divide-border/60">
          {groups.map((g) => (
            <li key={g.id} className="py-2 flex items-center gap-3 text-sm">
              <b className="min-w-0 truncate">{g.name}</b>
              <code className="font-mono text-2xs text-muted truncate">{g.id}</code>
              <span className="ml-auto shrink-0"><StatusPill kind={g.status} label={statusLabel(g.status)} /></span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
