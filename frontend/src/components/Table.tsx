import { useId, useMemo, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { SortDir } from '@/hooks/useTableState';

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  /** Value to order by. Omitted, the raw field at `key` is used; `sortable: false` opts out. */
  sortBy?: (row: T) => unknown;
  sortable?: boolean;
  align?: 'left' | 'right';
  /** Hidden below `md`. */
  hideOnMobile?: boolean;
}

interface TableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  empty?: ReactNode;
  /** Accessible name for the table. */
  caption?: string;
  sort?: string | null;
  dir?: SortDir;
  onSort?: (key: string) => void;
  /** Row selection for bulk actions. Omit for a single-action table. */
  selected?: Set<string>;
  onSelectedChange?: (next: Set<string>) => void;
  selectable?: (row: T) => boolean;
  isLoading?: boolean;
}

const SORT_LABEL: Record<SortDir, string> = { asc: 'ascending', desc: 'descending' };

/** The accessor for a column: its explicit `sortBy`, or the raw field at `key`. */
export function sortAccessor<T>(columns: Column<T>[], key: string | null | undefined): ((row: T) => unknown) | null {
  if (!key) return null;
  const c = columns.find((col) => col.key === key);
  if (!c || c.sortable === false) return null;
  return c.sortBy ?? ((row: T) => (row as Record<string, unknown>)[key]);
}

/**
 * The shared table: sorting, row selection and a sticky header. Every capability is opt-in — a
 * table given no `onSort` renders plain headers.
 */
export function Table<T>({
  columns, rows, rowKey, empty, caption, sort, dir = 'asc', onSort,
  selected, onSelectedChange, selectable, isLoading,
}: TableProps<T>) {
  const { t } = useTranslation();
  const captionId = useId();
  const bulk = !!selected && !!onSelectedChange;

  const selectableRows = useMemo(
    () => rows.filter((r) => (selectable ? selectable(r) : true)),
    [rows, selectable],
  );
  const allSelected = bulk && selectableRows.length > 0 && selectableRows.every((r) => selected.has(rowKey(r)));
  const someSelected = bulk && selectableRows.some((r) => selected.has(rowKey(r)));

  const toggleAll = () => {
    if (!onSelectedChange) return;
    const next = new Set(selected);
    if (allSelected) selectableRows.forEach((r) => next.delete(rowKey(r)));
    else selectableRows.forEach((r) => next.add(rowKey(r)));
    onSelectedChange(next);
  };
  const toggleOne = (id: string) => {
    if (!onSelectedChange || !selected) return;
    const next = new Set(selected);
    if (next.has(id)) next.delete(id); else next.add(id);
    onSelectedChange(next);
  };

  return (
    <div className="overflow-x-auto -mx-1 px-1">
      <table className="w-full border-collapse" aria-labelledby={caption ? captionId : undefined} aria-busy={isLoading || undefined}>
        {caption && <caption id={captionId} className="gs-sr-only">{caption}</caption>}
        <thead>
          <tr>
            {bulk && (
              <th scope="col" className="w-9 px-3 py-2.5 border-b border-border sticky top-0 bg-surface z-10">
                <input
                  type="checkbox"
                  className="w-4 h-4 align-middle"
                  checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = !allSelected && someSelected; }}
                  onChange={toggleAll}
                  aria-label={t('table.selectAll')}
                  disabled={selectableRows.length === 0}
                />
              </th>
            )}
            {columns.map((c) => {
              const active = sort === c.key;
              const sortable = !!onSort && c.sortable !== false && (!!c.sortBy || c.key !== 'actions');
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={sortable ? (active ? (SORT_LABEL[dir] as 'ascending' | 'descending') : 'none') : undefined}
                  className={[
                    'text-[11.5px] text-muted font-bold uppercase tracking-wide px-3 py-2.5 border-b border-border',
                    'sticky top-0 bg-surface z-10',
                    c.align === 'right' ? 'text-right' : 'text-left',
                    c.hideOnMobile ? 'hidden md:table-cell' : '',
                  ].join(' ')}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => onSort(c.key)}
                      className="inline-flex items-center gap-1 uppercase tracking-wide font-bold hover:text-text"
                      title={t('table.sortBy', { column: c.header })}
                    >
                      {c.header}
                      <span aria-hidden="true" className={active ? 'text-primary' : 'opacity-40'}>
                        {active ? (dir === 'asc' ? '▲' : '▼') : '↕'}
                      </span>
                    </button>
                  ) : c.header}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length + (bulk ? 1 : 0)} className="px-3 py-8 text-center text-muted">
                {empty ?? t('table.empty')}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const id = rowKey(row);
              const canSelect = selectable ? selectable(row) : true;
              return (
                <tr key={id} className={`hover:bg-surface-2 ${bulk && selected?.has(id) ? 'bg-primary-soft' : ''}`}>
                  {bulk && (
                    <td className="px-3 py-3 border-b border-border">
                      <input
                        type="checkbox"
                        className="w-4 h-4 align-middle"
                        checked={selected?.has(id) ?? false}
                        disabled={!canSelect}
                        onChange={() => toggleOne(id)}
                        aria-label={t('table.selectRow', { name: id })}
                      />
                    </td>
                  )}
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={[
                        'px-3 py-3 border-b border-border text-[13px]',
                        c.align === 'right' ? 'text-right tabular-nums' : '',
                        c.hideOnMobile ? 'hidden md:table-cell' : '',
                      ].join(' ')}
                    >
                      {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '')}
                    </td>
                  ))}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

/** Toolbar above a list: labelled search, clear control, and a live match count. */
export function TableToolbar({
  query, onQueryChange, placeholder, total, shown, children, onClear,
}: {
  query: string;
  onQueryChange: (v: string) => void;
  placeholder?: string;
  total: number;
  shown: number;
  children?: ReactNode;
  onClear?: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const filtered = shown !== total;
  return (
    <div data-url-state className="flex items-center gap-2 flex-wrap mb-3">
      <div className="relative">
        <label htmlFor={id} className="gs-sr-only">{placeholder ?? t('table.search')}</label>
        <input
          id={id}
          type="search"
          className="gs-input w-[240px] max-w-full pr-8"
          value={query}
          placeholder={placeholder ?? t('table.search')}
          onChange={(e) => onQueryChange(e.target.value)}
        />
        {query && (
          <button
            type="button"
            data-clear-filter
            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted hover:text-text px-1.5 py-1"
            onClick={() => (onClear ? onClear() : onQueryChange(''))}
            aria-label={t('table.clearSearch')}
            title={t('table.clearSearch')}
          >
            ✕
          </button>
        )}
      </div>
      {children}
      <span data-result-count className="text-muted text-[12px] ml-auto" role="status" aria-live="polite">
        {filtered ? t('table.countFiltered', { shown, total }) : t('table.count', { count: total })}
      </span>
    </div>
  );
}

/** Page controls, backed by the page number in the URL. */
export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (n: number) => void }) {
  const { t } = useTranslation();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav data-pagination aria-label={t('table.pagination')} className="flex items-center justify-between gap-3 mt-3 flex-wrap">
      <span className="text-muted text-[12px]">{t('table.range', { from, to, total })}</span>
      <div className="flex items-center gap-1.5">
        <button type="button" className="gs-btn gs-btn-sm disabled:opacity-40" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          ← {t('table.previous')}
        </button>
        <span className="text-[12px] text-muted px-1">{t('table.pageOf', { page, pages })}</span>
        <button type="button" className="gs-btn gs-btn-sm disabled:opacity-40" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          {t('table.next')} →
        </button>
      </div>
    </nav>
  );
}
