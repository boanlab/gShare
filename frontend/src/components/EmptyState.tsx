import { type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { asApiError, humanizeError } from '@/lib/errors';
import { MagnifyingGlass, Tray, Warning } from './icons';

/** A failed fetch, distinct from an empty list: shows the real error and offers a retry.
 * Rendering "no data yet" over a 500 tells the user a lie - use this in the isError branch. */
export function ErrorState({ error, onRetry }: { error?: unknown; onRetry?: () => void }) {
  const { t } = useTranslation();
  const message = error ? humanizeError(asApiError(error)) : t('common.loadFailedBody');
  return (
    <div role="alert" className="text-center py-12 px-4">
      <div aria-hidden="true" className="text-danger flex justify-center"><Warning size={26} /></div>
      <p className="font-semibold mt-3">{t('common.loadFailed')}</p>
      <p className="text-danger text-xs mt-1 max-w-[420px] mx-auto">{message}</p>
      {onRetry && (
        <div className="mt-4 flex justify-center">
          <button type="button" className="gs-btn gs-btn-sm" onClick={onRetry}>{t('common.retry')}</button>
        </div>
      )}
    </div>
  );
}

/** Placeholder for a list with nothing in it, plus the action that creates the first item. */
export function EmptyState({ title, description, action, icon }: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  /** A Phosphor glyph. Defaults to an empty tray. */
  icon?: ReactNode;
}) {
  return (
    <div className="text-center py-12 px-4">
      <div aria-hidden="true" className="text-muted opacity-70 flex justify-center">
        {icon ?? <Tray size={26} />}
      </div>
      <p className="font-semibold mt-3">{title}</p>
      {description && <p className="text-muted text-xs mt-1.5 max-w-[420px] mx-auto">{description}</p>}
      {action && <div className="mt-5 flex justify-center gap-2">{action}</div>}
    </div>
  );
}

/** Placeholder for a filter that matched nothing, with a way to clear it. */
export function NoResults({ query }: { query?: string; onClear?: () => void }) {
  const { t } = useTranslation();
  // The clear-filters affordance lives beside the search box (TableToolbar), not down here —
  // the empty state only explains WHY the table is empty.
  return (
    <EmptyState
      icon={<MagnifyingGlass size={26} />}
      title={query ? t('table.noResultsFor', { query }) : t('table.noResults')}
      description={t('table.noResultsHint')}
    />
  );
}

/** Placeholder rows holding the layout while data loads. */
export function TableSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  const { t } = useTranslation();
  return (
    <div data-skeleton aria-busy="true" aria-live="polite" className="animate-pulse">
      <span className="gs-sr-only">{t('common.loading')}</span>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-3 py-3 border-b border-border">
          {Array.from({ length: columns }).map((__, c) => (
            <div key={c} className="h-3 rounded bg-surface-2 flex-1" style={{ maxWidth: c === 0 ? '28%' : undefined }} />
          ))}
        </div>
      ))}
    </div>
  );
}
