import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  useOrganizations, useOrganization, useCreateOrganization, useUpdateOrganization, useDeleteOrganization,
  useOrgAdmins, useAddOrgAdmin, useRemoveOrgAdmin,
} from '@/api/hooks/useGroups';
import { Table, TableToolbar, type Column } from '@/components/Table';
import { UserSearchPicker } from '@/components/UserSearchPicker';
import { PageHeader, BackLink } from '@/components/PageHeader';
import { EmptyState, NoResults, TableSkeleton } from '@/components/EmptyState';
import { CopyButton } from '@/components/CopyButton';
import { Timestamp } from '@/components/Timestamp';
import { useConfirm } from '@/components/ConfirmDialog';
import { Field, DisabledReason } from '@/components/Field';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUnsavedGuard, unsavedGuardProps } from '@/hooks/useUnsavedGuard';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import { statusLabel } from '@/lib/format';

// Organization management: CRUD over the top-level organizations, which contain groups, budgets,
// policy, and credit pools. super_admin only.
type Org = { id: string; name: string; status?: string; created_at?: string; group_count?: number; user_count?: number };

export function AdminOrgs() {
  const { t } = useTranslation();
  const { data: orgs = [], isLoading, isFetching, refetch, dataUpdatedAt } = useOrganizations();
  const deleteOrg = useDeleteOrganization();
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
          <div className="flex items-center gap-1 text-muted text-[12px]">
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
        return <span className={`gs-pill ${s === 'active' ? 'bg-free-soft text-free' : 'bg-surface-2 text-muted'}`}>{statusLabel(s)}</span>;
      },
    },
    { key: 'created_at', header: t('common.created'), sortBy: (o) => (o.created_at ? new Date(o.created_at).getTime() : 0), hideOnMobile: true, render: (o) => <Timestamp value={o.created_at} /> },
    {
      key: 'actions', header: '', align: 'right',
      render: (o) => (
        <div className="flex flex-nowrap gap-1.5 justify-end">
          <Link to={`/admin/orgs/${o.id}/admins`} className="gs-btn gs-btn-sm">{t('admin.orgs.admins')}</Link>
          <Link to={`/admin/orgs/${o.id}/edit`} className="gs-btn gs-btn-sm">{t('common.edit')}</Link>
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
        updatedAt={dataUpdatedAt || null}
        onRefresh={() => refetch()}
        isFetching={isFetching}
        actions={<Link to="/admin/orgs/new" className="gs-btn gs-btn-primary">{t('admin.orgs.add')}</Link>}
      />
      <TableToolbar
        query={table.query}
        onQueryChange={table.setQuery}
        placeholder={t('admin.orgs.searchPlaceholder')}
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
                icon="◫"
                title={t('admin.orgs.empty')}
                description={t('admin.orgs.emptyDescription')}
                action={<Link to="/admin/orgs/new" className="gs-btn gs-btn-primary">{t('admin.orgs.add')}</Link>}
              />
            )
        ) : (
          <div className="p-1">
            <Table
              caption={t('admin.orgs.title')}
              columns={cols}
              rows={rows}
              rowKey={(o) => o.id}
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

// Appointing and removing organization administrators, at /admin/orgs/:orgId/admins.
export function OrgAdminsPage() {
  const { t } = useTranslation();
  const { orgId = '' } = useParams();
  const org = useOrganization(orgId).data as Org | undefined;
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
    <div className="w-full">
      <PageHeader
        title={`${t('admin.orgs.adminsTitle')}${org ? ` — ${org.name}` : ''}`}
        crumbs={[{ label: t('admin.orgs.title'), to: '/admin/orgs' }, { label: t('admin.orgs.adminsTitle') }]}
        actions={<BackLink to="/admin/orgs" label={t('admin.orgs.backToList')} />}
      />
      <div className="gs-card space-y-4">
        <p className="text-muted text-[12px]">
          {t('admin.orgs.adminsNote')}
        </p>

        <div className="space-y-2">
          <span className="text-[12px] font-semibold text-muted">{t('admin.orgs.searchUsers')}</span>
          <UserSearchPicker excludeIds={adminIds} selectedId={userId} onSelect={setUserId} />
          <button type="button" className="gs-btn gs-btn-primary w-full disabled:opacity-50" disabled={!userId || addAdmin.isPending} onClick={add}>
            {addAdmin.isPending ? t('admin.orgs.appointing') : t('admin.orgs.appoint')}
          </button>
        </div>

        <div>
          <div className="text-[12px] font-semibold text-muted mb-1.5">{t('admin.orgs.currentAdmins')} {isLoading ? '' : `(${admins.length})`}</div>
          {isLoading ? (
            <p className="text-muted text-[13px]">{t('common.loading')}</p>
          ) : admins.length === 0 ? (
            <p className="text-muted text-[13px]">{t('admin.orgs.noAdmins')}</p>
          ) : (
            <ul className="space-y-1.5">
              {admins.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-surface-2">
                  <span className="text-[13px]"><b>{a.user_name}</b>{a.email && <span className="text-muted font-mono text-[12px]"> · {a.email}</span>}</span>
                  <button type="button" className="gs-btn gs-btn-sm text-danger" disabled={removeAdmin.isPending} onClick={() => remove(a.user_id, a.user_name)}>{t('admin.orgs.removeAdmin')}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// Organization editing, at /admin/orgs/:orgId/edit.
export function EditOrgPage() {
  const { t } = useTranslation();
  const { orgId = '' } = useParams();
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const { data: org, isLoading } = useOrganization(orgId) as { data?: Org; isLoading: boolean };
  const updateOrg = useUpdateOrganization();
  const [name, setName] = useState('');
  const [status, setStatus] = useState<'active' | 'inactive'>('active');
  const [loaded, setLoaded] = useState(false);
  if (org && !loaded) { setName(org.name); setStatus((org.status as 'active' | 'inactive') ?? 'active'); setLoaded(true); }
  const sel = 'w-full mt-1 px-3 py-2 border border-border rounded-lg bg-surface-2';

  const submit = () => {
    if (!name.trim()) return;
    updateOrg.mutate({ id: orgId, name: name.trim(), status }, {
      onSuccess: () => { pushToast('success', t('admin.orgs.updated')); navigate('/admin/orgs'); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const dirty = !!org && (name.trim() !== org.name || status !== (org.status ?? 'active'));
  useUnsavedGuard(dirty && !updateOrg.isPending);

  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={`${t('admin.orgs.editTitle')}${org ? ` — ${org.name}` : ''}`}
        crumbs={[{ label: t('admin.orgs.title'), to: '/admin/orgs' }, { label: t('admin.orgs.editTitle') }]}
        actions={<BackLink to="/admin/orgs" label={t('admin.orgs.backToList')} />}
      />
      {isLoading && !org ? <TableSkeleton rows={3} columns={2} /> : !org ? (
        <EmptyState
          icon="?"
          title={t('admin.orgs.notFound')}
          action={<Link to="/admin/orgs" className="gs-btn gs-btn-primary">{t('admin.orgs.backToList')}</Link>}
        />
      ) : (
        <form className="gs-card" noValidate {...unsavedGuardProps} onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="space-y-3">
            <Field label={t('common.name')} required>
              {(ids) => <input {...ids} className={sel} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoFocus autoComplete="off" />}
            </Field>
            <Field label={t('common.status')} hint={t('admin.orgs.statusHint')}>
              {(ids) => (
                <select {...ids} className={sel} value={status} onChange={(e) => setStatus(e.target.value as 'active' | 'inactive')}>
                  <option value="active">{t('enum.status.active')}</option><option value="inactive">{t('enum.status.inactive')}</option>
                </select>
              )}
            </Field>
          </div>
          <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
            <DisabledReason reasons={name.trim() ? (dirty ? [] : [t('account.noChanges')]) : [t('common.name')]} />
            <button type="button" className="gs-btn" onClick={() => navigate('/admin/orgs')}>{t('common.cancel')}</button>
            <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || !dirty || updateOrg.isPending}>
              {updateOrg.isPending ? t('admin.orgs.saving') : t('common.save')}</button>
          </div>
        </form>
      )}
    </div>
  );
}

// New organization, at /admin/orgs/new.
export function NewOrgPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const createOrg = useCreateOrganization();
  const [name, setName] = useState('');

  const submit = () => {
    if (!name.trim()) return;
    createOrg.mutate({ name: name.trim() }, {
      onSuccess: () => { pushToast('success', t('admin.orgs.created')); navigate('/admin/orgs'); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  useUnsavedGuard(!!name.trim() && !createOrg.isPending);

  return (
    <div className="w-full max-w-2xl">
      <PageHeader
        title={t('admin.orgs.newTitle')}
        crumbs={[{ label: t('admin.orgs.title'), to: '/admin/orgs' }, { label: t('admin.orgs.newTitle') }]}
        actions={<BackLink to="/admin/orgs" label={t('admin.orgs.backToList')} />}
      />
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
        <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
          <DisabledReason reasons={name.trim() ? [] : [t('admin.orgs.nameLabel')]} />
          <button type="button" className="gs-btn" onClick={() => navigate('/admin/orgs')}>{t('common.cancel')}</button>
          <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!name.trim() || createOrg.isPending}>
            {createOrg.isPending ? t('admin.orgs.creating') : t('common.create')}
          </button>
        </div>
      </form>
    </div>
  );
}
