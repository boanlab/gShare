import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQueue, useCancelQueueEntry } from '@/api/hooks/useQueue';
import { Table, Pagination, type Column } from '@/components/Table';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, TableSkeleton } from '@/components/EmptyState';
import { Timestamp } from '@/components/Timestamp';
import { useConfirm } from '@/components/ConfirmDialog';
import { useTableState, sortRows } from '@/hooks/useTableState';
import { useUiStore } from '@/store/uiStore';
import { humanizeError, asApiError } from '@/lib/errors';
import type { components } from '@/api/schema';
import { Hourglass, Plus } from '@/components/icons';

type QueueEntryView = components['schemas']['QueueEntryView'];

// Queue status, polled every 5 seconds: position, estimated wait, and the option to leave.
export function QueuePage() {
  const { t } = useTranslation();
  const { data, isLoading, isError, refetch } = useQueue();
  const cancel = useCancelQueueEntry();
  const confirm = useConfirm();
  const pushToast = useUiStore((s) => s.pushToast);
  const table = useTableState('', { sort: 'position', dir: 'asc' });

  // Identify the session by session_req.name when it has one, otherwise by session_id.
  const sessionLabel = (q: QueueEntryView) => {
    const name = (q.session_req as { name?: string } | undefined)?.name;
    return name || q.session_id || '-';
  };

  const onCancel = async (q: QueueEntryView) => {
    const ok = await confirm({
      title: t('queue.confirmCancelTitle', { name: sessionLabel(q) }),
      body: t('queue.confirmCancelBody'),
      consequences: [t('queue.consequenceLosePlace', { position: q.position })],
      confirmLabel: t('queue.cancel'),
      destructive: true,
    });
    if (!ok) return;
    cancel.mutate(q.id, {
      onSuccess: () => pushToast('success', t('queue.cancelled')),
      onError: (e) => pushToast('error', humanizeError(asApiError(e))),
    });
  };

  const rows = sortRows((data ?? []) as QueueEntryView[], {
    session_name: sessionLabel,
    position: (q: QueueEntryView) => q.position ?? 0,
    waiting: (q: QueueEntryView) => (q.enqueued_at ? new Date(q.enqueued_at).getTime() : 0),
  }[table.sort ?? 'position'] ?? null, table.dir);
  const pageRows = rows.slice((table.page - 1) * 25, table.page * 25);

  const columns: Column<QueueEntryView>[] = [
    { key: 'session_name', header: t('queue.colSession'), sortBy: sessionLabel, render: (q) => sessionLabel(q) },
    {
      key: 'position',
      header: t('queue.colPosition'),
      sortBy: (q) => q.position ?? 0,
      align: 'right',
      render: (q) => (
        <span className="gs-num font-semibold" title={t('queue.positionHint')}>
          #{q.position}
        </span>
      ),
    },
    {
      key: 'eta',
      header: t('queue.colEta'),
      sortable: false,
      align: 'right',
      render: (q) => (
        q.eta_minutes != null
          ? <span className="gs-num" title={t('queue.etaHint')}>{t('queue.etaValue', { minutes: q.eta_minutes })}</span>
          : <span className="text-muted">-</span>
      ),
    },
    {
      key: 'waiting',
      header: t('queue.colWaiting'),
      sortBy: (q) => (q.enqueued_at ? new Date(q.enqueued_at).getTime() : 0),
      align: 'right',
      hideOnMobile: true,
      render: (q) => <Timestamp value={q.enqueued_at} className="text-xs" />,
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (q) => (
        <button type="button" className="gs-btn gs-btn-sm gs-btn-danger" disabled={cancel.isPending} onClick={() => onCancel(q)}>
          {t('queue.cancel')}
        </button>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t('queue.title')}
        description={t('queue.subtitle')}
      />
      {isError && (
        <p role="alert" className="text-warn mb-3">
          {t('queue.pollFailed')}{' '}
          <button type="button" className="underline font-semibold" onClick={() => refetch()}>{t('common.retry')}</button>
        </p>
      )}
      <div data-url-state className="gs-panel overflow-hidden">
        {isLoading ? (
          <div className="p-4"><TableSkeleton rows={3} columns={4} /></div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Hourglass size={26} />}
            title={t('queue.emptyTitle')}
            description={t('queue.emptyDescription')}
            action={<Link to="/sessions/new" className="gs-btn gs-btn-primary"><Plus size={15} weight="bold" aria-hidden="true" />{t('session.new')}</Link>}
          />
        ) : (
          <div>
            <Table
              caption={t('queue.title')}
              columns={columns}
              rows={pageRows}
              rowKey={(q) => q.id}
              sort={table.sort}
              dir={table.dir}
              onSort={table.toggleSort}
            />
          </div>
        )}
      </div>
      <Pagination page={table.page} pageSize={25} total={rows.length} onPage={table.setPage} />
    </div>
  );
}
