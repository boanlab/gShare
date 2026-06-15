import { type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/** Placeholder for a list with nothing in it, plus the action that creates the first item. */
export function EmptyState({ title, description, action, icon = '◌' }: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: string;
}) {
  return (
    <div className="text-center py-10 px-4">
      <div aria-hidden="true" className="text-[28px] text-muted opacity-60">{icon}</div>
      <p className="font-bold mt-2">{title}</p>
      {description && <p className="text-muted text-[12.5px] mt-1 max-w-[420px] mx-auto">{description}</p>}
      {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
    </div>
  );
}

/** Placeholder for a filter that matched nothing, with a way to clear it. */
export function NoResults({ query, onClear }: { query?: string; onClear: () => void }) {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon="⌕"
      title={query ? t('table.noResultsFor', { query }) : t('table.noResults')}
      description={t('table.noResultsHint')}
      action={<button type="button" className="gs-btn" onClick={onClear}>{t('table.clearFilters')}</button>}
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
