import { useState } from 'react';
import { Select } from '@/components/Select';
import { useTranslation } from 'react-i18next';
import { PageHeader } from '@/components/PageHeader';
import { Dialog } from '@/components/Dialog';
import { Pagination, Table, type Column } from '@/components/Table';
import { Field, DisabledReason } from '@/components/Field';
import { StatusPill } from '@/components/StatusPill';
import { Timestamp } from '@/components/Timestamp';
import { EmptyState, TableSkeleton, NoResults } from '@/components/EmptyState';
import { useConfirm } from '@/components/ConfirmDialog';
import { useUiStore } from '@/store/uiStore';
import { useAuthStore } from '@/auth/authStore';
import { useProjects } from '@/api/hooks/useGroups';
import { humanizeError, asApiError } from '@/lib/errors';
import { Megaphone, ChatCircleText, CaretDown } from '@/components/icons';
import {
  useNotices, useCreateNotice, useUpdateNotice, useDeleteNotice,
  useInquiries, useInquiry, useCreateInquiry, useReplyInquiry, useCloseInquiry,
  type Notice, type Inquiry,
} from '@/api/hooks/useBoards';

// ── 공지 (user view): pinned first, expand-in-place. Group notices carry their department tag. ──

// 게시판형 공지 테이블: 번호/제목/작성자/작성일 (+관리) 헤더, 행 클릭으로 본문이 아래에 펼쳐진다.
// 고정 공지는 번호 대신 '중요' 배지를 달고 맨 위로 온다. 사용자/관리자 목록이 함께 쓴다.
function NoticeTable({ rows, ordered, manage }: {
  rows: Notice[];
  /** The FULL filtered list (all pages), for stable board numbering. */
  ordered: Notice[];
  manage?: { can: (n: Notice) => boolean; onEdit: (n: Notice) => void; onDelete: (n: Notice) => void; deleting: boolean };
}) {
  const { t } = useTranslation();
  const [openId, setOpenId] = useState<string | null>(null);
  const plain = ordered.filter((n) => !n.pinned);
  const numOf = new Map(plain.map((n, i) => [n.id, plain.length - i]));
  const isNew = (n: Notice) => !!n.created_at && Date.now() - new Date(n.created_at).getTime() < 3 * 86_400_000;
  const columns: Column<Notice>[] = [
    {
      key: 'no', header: t('boards.colNo'), sortable: false, align: 'center', headerClassName: 'font-bold w-16 whitespace-nowrap',
      render: (n) => n.pinned
        ? <span className="gs-tag text-primary font-semibold">{t('boards.pinnedTag')}</span>
        : <span className="gs-num text-muted text-xs">{numOf.get(n.id)}</span>,
    },
    {
      key: 'title', header: t('boards.colTitle'), sortable: false, headerAlign: 'center', headerClassName: 'font-bold w-full',
      render: (n) => (
        <span className="flex items-center gap-2 min-w-0">
          <span className="gs-tag shrink-0">{n.scope === 'global' ? t('boards.scopeGlobal') : (n.group_name ?? t('boards.scopeGroup'))}</span>
          <span className="font-semibold truncate">{n.title}</span>
          {isNew(n) && <span className="shrink-0 text-primary text-2xs font-bold" aria-label={t('boards.badgeNew')}>N</span>}
          <CaretDown size={12} className={`shrink-0 text-muted transition-transform duration-150 ${openId === n.id ? 'rotate-180' : ''}`} aria-hidden="true" />
        </span>
      ),
    },
    {
      key: 'author', header: t('boards.colAuthor'), sortable: false, hideOnMobile: true, align: 'center', headerClassName: 'font-bold whitespace-nowrap px-6', cellClassName: 'px-6',
      render: (n) => <span className="text-muted text-xs whitespace-nowrap">{n.author_name ?? '-'}</span>,
    },
    {
      key: 'created', header: t('boards.colDate'), sortable: false, hideOnMobile: true, align: 'center', headerClassName: 'font-bold whitespace-nowrap px-6', cellClassName: 'px-6',
      render: (n) => <span className="whitespace-nowrap"><Timestamp value={n.created_at} className="text-muted text-xs" /></span>,
    },
    ...(manage ? [{
      key: 'actions', header: t('boards.colManage'), sortable: false, align: 'center' as const, headerClassName: 'whitespace-nowrap px-6', cellClassName: 'px-6',
      render: (n: Notice) => (manage.can(n) ? (
        <span className="flex gap-2 justify-center">
          <button type="button" className="gs-btn gs-btn-sm" onClick={() => manage.onEdit(n)}>{t('common.edit')}</button>
          <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={manage.deleting} onClick={() => manage.onDelete(n)}>{t('common.delete')}</button>
        </span>
      ) : null),
    }] : []),
  ];
  return (
    <Table
      columns={columns}
      rows={rows}
      rowKey={(n) => n.id}
      caption={t('boards.noticesTitle')}
      onRowClick={(n) => setOpenId((v) => (v === n.id ? null : n.id))}
      expandedKey={openId}
      renderExpansion={(n) => <NoticeBody n={n} />}
    />
  );
}

// The unfolded notice: labelled meta line + body — one component, user list and admin list alike.
function NoticeBody({ n }: { n: Notice }) {
  const { t } = useTranslation();
  return (
    <div className="mt-2.5 rounded-card bg-surface-2/60 px-4 py-3 text-sm">
      <div className="text-2xs text-muted mb-2 flex items-center gap-3 flex-wrap">
        {n.author_name && <span>{t('boards.metaAuthor')}: {n.author_name}</span>}
        <span>{t('boards.metaCreated')}: <Timestamp value={n.created_at} /></span>
        {n.updated_at && n.updated_at !== n.created_at && (
          <span>{t('boards.metaUpdated')}: <Timestamp value={n.updated_at} /></span>
        )}
      </div>
      <div className="text-2xs text-muted mb-2">{t('boards.metaTitle')}: <span className="text-text font-semibold">{n.title}</span></div>
      <div className="whitespace-pre-wrap break-words">{n.body || <span className="text-muted">-</span>}</div>
    </div>
  );
}

export function NoticesPage() {
  const { t } = useTranslation();
  const { data: notices, isLoading } = useNotices();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState('');
  const [qInput, setQInput] = useState('');
  // Search applies on submit (the button or Enter), not per keystroke.
  const searchBar = (
    <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); setPage(1); }}>
      <input type="search" className="gs-input gs-input-sm w-56 max-w-full" value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder={t('boards.searchPlaceholder')} aria-label={t('boards.searchPlaceholder')} />
      <button type="submit" className="gs-btn gs-btn-sm">{t('table.search')}</button>
    </form>
  );
  const needle = q.trim().toLowerCase();
  const all = (notices ?? []).filter((n) =>
    !needle
    || n.title.toLowerCase().includes(needle)
    || (n.body ?? '').toLowerCase().includes(needle)
    || (n.author_name ?? '').toLowerCase().includes(needle))
    .sort((a, b) => Number(b.pinned) - Number(a.pinned) || (b.created_at ?? '').localeCompare(a.created_at ?? ''));
  const rows = all.slice((page - 1) * 25, page * 25);
  return (
    <div>
      <PageHeader title={t('boards.noticesTitle')} description={t('boards.noticesSubtitle')} />
      <div className="gs-card">
        {isLoading ? <TableSkeleton rows={4} columns={4} /> : (notices ?? []).length === 0 ? (
          <EmptyState icon={<Megaphone size={26} />} title={t('boards.noNotices')} />
        ) : (
          <>
            {all.length === 0 ? <NoResults query={q} /> : <NoticeTable rows={rows} ordered={all} />}
            <Pagination page={page} pageSize={25} total={all.length} onPage={setPage} center={searchBar} />
          </>
        )}
      </div>
    </div>
  );
}

// ── 공지 관리 (admin): super_admin posts globally; a group_admin to their own department. ──

function NoticeForm({ initial, onDone }: { initial?: Notice | null; onDone: () => void }) {
  const { t } = useTranslation();
  const pushToast = useUiStore((s) => s.pushToast);
  const create = useCreateNotice();
  const update = useUpdateNotice();
  const isSuper = useAuthStore((s) => s.claims.global_role === 'super_admin');
  const memberships = useAuthStore((s) => s.memberships);
  const adminGroups = memberships.filter((m) => ['group_admin', 'org_admin'].includes(m.role));
  // super_admin holds no group membership, but may address ANY department: offer the full list.
  const { data: allGroups = [] } = useProjects();
  const groupOptions = isSuper
    ? allGroups.map((g) => ({ id: g.id, name: g.name }))
    : adminGroups.map((m) => ({ id: m.group_id, name: m.project_name }));
  const [scope, setScope] = useState<'global' | 'group'>(initial?.scope ?? (isSuper ? 'global' : 'group'));
  const [groupId, setGroupId] = useState(initial?.group_id ?? groupOptions[0]?.id ?? '');
  const [title, setTitle] = useState(initial?.title ?? '');
  const [body, setBody] = useState(initial?.body ?? '');
  const [pinned, setPinned] = useState(initial?.pinned ?? false);
  const [sendNotify, setSendNotify] = useState(true);
  const pending = create.isPending || update.isPending;
  const valid = title.trim().length > 0 && (scope === 'global' || !!groupId);

  const submit = () => {
    if (!valid) return;
    const done = () => { pushToast('success', t(initial ? 'boards.noticeUpdated' : 'boards.noticePosted')); onDone(); };
    const fail = (e: unknown) => pushToast('error', humanizeError(asApiError(e)));
    if (initial) update.mutate({ id: initial.id, title: title.trim(), body, pinned }, { onSuccess: done, onError: fail });
    else create.mutate({ scope, group_id: scope === 'group' ? groupId : null, title: title.trim(), body, pinned, notify: sendNotify }, { onSuccess: done, onError: fail });
  };

  return (
    <form className="gs-card" noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        {!initial && (
          <Field label={t('boards.scopeLabel')} hint={!isSuper ? t('boards.scopeGroupOnly') : undefined}>
            {(ids) => (
              <Select {...ids} className="gs-input w-full" value={scope} disabled={!isSuper}
                onChange={(e) => setScope(e.target.value as 'global' | 'group')}>
                {isSuper && <option value="global">{t('boards.scopeGlobal')}</option>}
                <option value="group">{t('boards.scopeGroup')}</option>
              </Select>
            )}
          </Field>
        )}
        {!initial && scope === 'group' && (
          <Field label={t('common.group')} required>
            {(ids) => (
              <Select {...ids} className="gs-input w-full" value={groupId || groupOptions[0]?.id || ''} onChange={(e) => setGroupId(e.target.value)}>
                {groupOptions.length === 0 && <option value="">-</option>}
                {groupOptions.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </Select>
            )}
          </Field>
        )}
        <Field label={t('boards.titleLabel')} required>
          {(ids) => <input {...ids} className="gs-input w-full" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} autoFocus autoComplete="off" />}
        </Field>
        <Field label={t('boards.bodyLabel')}>
          {(ids) => (
            <textarea {...ids} className="gs-input w-full h-44 font-normal" value={body} maxLength={20000}
              onChange={(e) => setBody(e.target.value)} />
          )}
        </Field>
        <div className="flex items-center gap-5 flex-wrap">
          <label className="inline-flex items-center gap-2 text-sm">
            <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
            {t('boards.pinLabel')}
          </label>
          {!initial && (
            <label className="inline-flex items-center gap-2 text-sm">
              <input type="checkbox" checked={sendNotify} onChange={(e) => setSendNotify(e.target.checked)} />
              {t('boards.notifyLabel')}
            </label>
          )}
        </div>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : [t('boards.titleLabel')]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || pending}>
          {pending ? t('common.saving', { defaultValue: '…' }) : initial ? t('common.save') : t('boards.post')}
        </button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

export function AdminNoticesPage() {
  const { t } = useTranslation();
  const { data: notices, isLoading } = useNotices('admin');
  const del = useDeleteNotice();
  const confirm = useConfirm();
  const pushToast = useUiStore((s) => s.pushToast);
  const isSuper = useAuthStore((s) => s.claims.global_role === 'super_admin');
  const myAdminGroups = useAuthStore((s) => s.memberships)
    .filter((m) => ['group_admin', 'org_admin'].includes(m.role)).map((m) => m.group_id);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Notice | null>(null);
  const [q, setQ] = useState('');
  const [qInput, setQInput] = useState('');
  // Search applies on submit (the button or Enter), not per keystroke.
  const searchBar = (
    <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); setPage(1); }}>
      <input type="search" className="gs-input gs-input-sm w-56 max-w-full" value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder={t('boards.searchPlaceholder')} aria-label={t('boards.searchPlaceholder')} />
      <button type="submit" className="gs-btn gs-btn-sm">{t('table.search')}</button>
    </form>
  );
  const [page, setPage] = useState(1);
  const canManage = (n: Notice) => isSuper || (n.scope === 'group' && !!n.group_id && myAdminGroups.includes(n.group_id));

  const onDelete = async (n: Notice) => {
    const ok = await confirm({
      title: t('boards.confirmDeleteNotice', { title: n.title }),
      confirmLabel: t('common.delete'),
      destructive: true,
    });
    if (!ok) return;
    del.mutate(n.id, {
      onSuccess: () => pushToast('success', t('boards.noticeDeleted')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  return (
    <div>
      <PageHeader
        title={t('boards.adminNoticesTitle')}
        description={t('boards.adminNoticesSubtitle')}
        actions={<button type="button" className="gs-btn gs-btn-primary" onClick={() => setCreateOpen(true)}>{t('boards.newNotice')}</button>}
      />
      <div className="gs-card">
        {(() => {
          const needle = q.trim().toLowerCase();
          const filtered = (notices ?? []).filter((n) =>
            !needle
            || n.title.toLowerCase().includes(needle)
            || (n.body ?? '').toLowerCase().includes(needle)
            || (n.author_name ?? '').toLowerCase().includes(needle))
            .sort((a, b) => Number(b.pinned) - Number(a.pinned) || (b.created_at ?? '').localeCompare(a.created_at ?? ''));
          const rows = filtered.slice((page - 1) * 25, page * 25);
          return isLoading ? <TableSkeleton rows={4} columns={5} /> : (notices ?? []).length === 0 ? (
          <EmptyState icon={<Megaphone size={26} />} title={t('boards.noNotices')} />
        ) : (
          <>
          {filtered.length === 0 ? <NoResults query={q} /> : (
            <NoticeTable rows={rows} ordered={filtered} manage={{ can: canManage, onEdit: setEditTarget, onDelete, deleting: del.isPending }} />
          )}
          <Pagination page={page} pageSize={25} total={filtered.length} onPage={setPage} center={searchBar} />
          </>
        );
        })()}
      </div>
      <Dialog open={createOpen} wide title={t('boards.newNotice')} onClose={() => setCreateOpen(false)}>
        <NoticeForm onDone={() => setCreateOpen(false)} />
      </Dialog>
      <Dialog open={!!editTarget} wide title={t('boards.editNotice')} onClose={() => setEditTarget(null)}>
        {editTarget && <NoticeForm initial={editTarget} onDone={() => setEditTarget(null)} />}
      </Dialog>
    </div>
  );
}

// ── 문의 (user): write one, read mine, follow the answer thread. ──

function statusPill(t: (k: string) => string, s: Inquiry['status']) {
  return <StatusPill kind={s === 'open' ? 'pending' : s === 'answered' ? 'active' : 'terminated'} label={t(`boards.inqStatus.${s}`)} />;
}

function InquiryThread({ id, canAnswer, onDone }: { id: string; canAnswer: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const { data: inq, isLoading } = useInquiry(id);
  const reply = useReplyInquiry(id);
  const close = useCloseInquiry();
  const pushToast = useUiStore((s) => s.pushToast);
  const [text, setText] = useState('');
  const [alsoClose, setAlsoClose] = useState(true);
  if (isLoading || !inq) return <TableSkeleton rows={3} columns={1} />;
  const send = () => {
    if (!text.trim()) return;
    reply.mutate({ body: text.trim(), close: canAnswer ? alsoClose : false }, {
      onSuccess: () => { setText(''); pushToast('success', t('boards.replySent')); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  return (
    <div className="mt-2.5 rounded-card bg-surface-2/60 px-4 py-3">
      <p className="text-sm whitespace-pre-wrap break-words">{inq.body}</p>
      {inq.replies.length > 0 && (
        <ul className="mt-3 space-y-2.5">
          {inq.replies.map((r) => (
            <li key={r.id} className="text-sm">
              <div className="text-xs text-muted mb-0.5">
                <b className="text-text">{r.author_name ?? '-'}</b>
                {(r as { author_role?: string | null }).author_role && (
                  <span> ({t(`boards.role.${(r as { author_role?: string }).author_role}`)})</span>
                )}
                {' '}· <Timestamp value={r.created_at} />
              </div>
              <div className="whitespace-pre-wrap break-words border-l-2 border-border pl-3">{r.body}</div>
            </li>
          ))}
        </ul>
      )}
      {inq.status !== 'closed' && (
        <div className="mt-4 border-t border-border pt-3">
          <textarea className="gs-input w-full h-24" value={text} maxLength={20000}
            placeholder={t(canAnswer ? 'boards.answerPlaceholder' : 'boards.followupPlaceholder')}
            onChange={(e) => setText(e.target.value)} />
          <div className="flex items-center justify-end gap-3 mt-2 flex-wrap">
            {canAnswer && (
              <label className="inline-flex items-center gap-1.5 text-xs text-muted">
                <input type="checkbox" checked={alsoClose} onChange={(e) => setAlsoClose(e.target.checked)} />
                {t('boards.closeWithAnswer')}
              </label>
            )}
            {canAnswer && (
              <button type="button" className="gs-btn gs-btn-sm" disabled={close.isPending}
                onClick={() => close.mutate(id, { onSuccess: onDone })}>
                {inq.status === 'answered' || inq.replies.length > 0 ? t('boards.close') : t('boards.closeOnly')}
              </button>
            )}
            <button type="button" className="gs-btn gs-btn-sm gs-btn-primary disabled:opacity-50" disabled={!text.trim() || reply.isPending} onClick={send}>
              {canAnswer ? t('boards.sendReply') : t('boards.submitReply')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function NewInquiryForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const create = useCreateInquiry();
  const pushToast = useUiStore((s) => s.pushToast);
  const memberships = useAuthStore((s) => s.memberships);
  const groupName = memberships[0]?.project_name;
  const [to, setTo] = useState<'group' | 'system'>(groupName ? 'group' : 'system');
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const valid = title.trim().length > 0 && body.trim().length > 0;
  const submit = () => {
    if (!valid) return;
    create.mutate({ title: title.trim(), body: body.trim(), to }, {
      onSuccess: () => { pushToast('success', t('boards.inquirySent')); onDone(); },
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };
  return (
    <form className="gs-card" noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3">
        <Field label={t('boards.toLabel')}>
          {(ids) => (
            <Select {...ids} className="gs-input w-full" value={to} onChange={(e) => setTo(e.target.value as 'group' | 'system')}>
              {groupName && <option value="group">{t('boards.toGroup', { name: groupName })}</option>}
              <option value="system">{t('boards.toSystem')}</option>
            </Select>
          )}
        </Field>
        <Field label={t('boards.titleLabel')} required>
          {(ids) => <input {...ids} className="gs-input w-full" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} autoFocus autoComplete="off" />}
        </Field>
        <Field label={t('boards.bodyLabel')} required hint={t('boards.inquiryHint')}>
          {(ids) => <textarea {...ids} className="gs-input w-full h-44" value={body} maxLength={20000} onChange={(e) => setBody(e.target.value)} />}
        </Field>
      </div>
      <div className="flex justify-end items-center gap-3 mt-4 flex-wrap">
        <DisabledReason reasons={valid ? [] : [!title.trim() && t('boards.titleLabel'), !body.trim() && t('boards.bodyLabel')].filter(Boolean) as string[]} />
        <button type="submit" className="gs-btn gs-btn-primary disabled:opacity-50" disabled={!valid || create.isPending}>
          {create.isPending ? t('wallet.sending') : t('boards.send')}
        </button>
        <button type="button" className="gs-btn" onClick={onDone}>{t('common.cancel')}</button>
      </div>
    </form>
  );
}

function InquiryList({ box, canAnswer }: { box: 'mine' | 'incoming'; canAnswer: boolean }) {
  const { t } = useTranslation();
  const { data: items, isLoading } = useInquiries(box);
  const [openId, setOpenId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState('');
  // 번호는 시간순(최신이 큰 번호)으로 고정하고, 표시는 상태 우선(대기 → 답변 완료 → 종료),
  // 같은 상태 안에서는 최신순. 상태 드롭다운으로 필터링한다.
  const byTime = [...(items ?? [])].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''));
  const numOf = new Map(byTime.map((x, i) => [x.id, byTime.length - i]));
  const ORDER: Record<Inquiry['status'], number> = { open: 0, answered: 1, closed: 2 };
  const all = byTime
    .filter((x) => !statusFilter || x.status === statusFilter)
    .sort((a, b) => ORDER[a.status] - ORDER[b.status]);
  const rows = all.slice((page - 1) * 25, page * 25);
  const filterBar = (
    <Select className="gs-input gs-input-sm w-auto" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }} aria-label={t('common.status')}>
      <option value="">{t('boards.statusFilterAll')}</option>
      <option value="open">{t('boards.inqStatus.open')}</option>
      <option value="answered">{t('boards.inqStatus.answered')}</option>
      <option value="closed">{t('boards.inqStatus.closed')}</option>
    </Select>
  );
  const columns: Column<Inquiry>[] = [
    {
      key: 'no', header: t('boards.colNo'), sortable: false, align: 'center', headerClassName: 'font-bold w-16 whitespace-nowrap',
      render: (i) => <span className="gs-num text-muted text-xs">{numOf.get(i.id)}</span>,
    },
    {
      key: 'title', header: t('boards.colTitle'), sortable: false, headerAlign: 'center', headerClassName: 'font-bold w-full',
      render: (i) => (
        <span className="flex items-center gap-2 min-w-0">
          <span className="gs-tag shrink-0">{i.group_id ? t('boards.inqTagGroup') : t('boards.inqTagSystem')}</span>
          <span className="font-semibold truncate">{i.title}</span>
          <span className="text-muted text-xs shrink-0">{t('boards.replyCount', { count: i.reply_count })}</span>
          <CaretDown size={12} className={`shrink-0 text-muted transition-transform duration-150 ${openId === i.id ? 'rotate-180' : ''}`} aria-hidden="true" />
        </span>
      ),
    },
    ...(box === 'incoming' ? [{
      key: 'author', header: t('boards.colInquirer'), sortable: false, hideOnMobile: true, align: 'center' as const,
      headerClassName: 'font-bold whitespace-nowrap px-6', cellClassName: 'px-6',
      render: (i: Inquiry) => <span className="text-muted text-xs whitespace-nowrap">{i.author_name ?? '-'}</span>,
    }] : []),
    {
      key: 'created', header: t('boards.colDate'), sortable: false, hideOnMobile: true, align: 'center',
      headerClassName: 'font-bold whitespace-nowrap px-6', cellClassName: 'px-6',
      render: (i) => <span className="whitespace-nowrap"><Timestamp value={i.created_at} className="text-muted text-xs" /></span>,
    },
    {
      key: 'status', header: t('common.status'), sortable: false, align: 'center',
      headerClassName: 'font-bold whitespace-nowrap px-6', cellClassName: 'px-6',
      render: (i) => statusPill(t, i.status),
    },
  ];
  return (
      <div className="gs-card">
        {isLoading ? <TableSkeleton rows={4} columns={5} /> : (items ?? []).length === 0 ? (
          <EmptyState icon={<ChatCircleText size={26} />} title={t(box === 'mine' ? 'boards.noInquiries' : 'boards.noIncoming')} />
        ) : (
          <>
          {all.length === 0 ? <NoResults query={t(`boards.inqStatus.${statusFilter}`)} /> : (
          <Table
            columns={columns}
            rows={rows}
            rowKey={(i) => i.id}
            caption={t(box === 'mine' ? 'boards.inquiriesTitle' : 'boards.adminInquiriesTitle')}
            onRowClick={(i) => setOpenId((v) => (v === i.id ? null : i.id))}
            expandedKey={openId}
            renderExpansion={(i) => <InquiryThread id={i.id} canAnswer={canAnswer} onDone={() => setOpenId(null)} />}
          />
          )}
          <Pagination page={page} pageSize={25} total={all.length} onPage={setPage} center={filterBar} />
          </>
        )}
      </div>
  );
}

export function InquiriesPage() {
  const { t } = useTranslation();
  const [writing, setWriting] = useState(false);
  return (
    <div>
      <PageHeader
        title={t('boards.inquiriesTitle')}
        description={t('boards.inquiriesSubtitle')}
        actions={
          <button type="button" className="gs-btn gs-btn-primary" onClick={() => setWriting((v) => !v)}>
            {writing ? t('common.cancel') : t('boards.newInquiry')}
          </button>
        }
      />
      {/* The form unfolds IN the page, right above the list — a question is not worth a modal. */}
      {writing && (
        <div className="mb-4">
          <NewInquiryForm onDone={() => setWriting(false)} />
        </div>
      )}
      <InquiryList box="mine" canAnswer={false} />
    </div>
  );
}

export function AdminInquiriesPage() {
  const { t } = useTranslation();
  return (
    <div>
      <PageHeader title={t('boards.adminInquiriesTitle')} description={t('boards.adminInquiriesSubtitle')} />
      <InquiryList box="incoming" canAnswer />
    </div>
  );
}

