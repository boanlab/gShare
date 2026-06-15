import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

export type SortDir = 'asc' | 'desc';

export interface TableState {
  query: string;
  setQuery: (v: string) => void;
  sort: string | null;
  dir: SortDir;
  toggleSort: (key: string) => void;
  page: number;
  setPage: (n: number) => void;
  tab: string | null;
  setTab: (v: string | null) => void;
  /** Whether anything is narrowing the list — an empty result versus an empty list. */
  isFiltered: boolean;
  clear: () => void;
}

/**
 * Search, sort, tab and page in the query string, so a view survives a reload and travels in a
 * link. `prefix` namespaces the keys where a screen carries more than one table.
 */
export function useTableState(prefix = '', defaults: { sort?: string; dir?: SortDir; tab?: string } = {}): TableState {
  const [params, setParams] = useSearchParams();
  const k = (name: string) => (prefix ? `${prefix}_${name}` : name);

  const set = useCallback((changes: Record<string, string | null>) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      for (const [name, value] of Object.entries(changes)) {
        if (value === null || value === '') next.delete(name);
        else next.set(name, value);
      }
      // Any change to the result set restarts paging.
      if (!('page' in changes) && Object.keys(changes).some((n) => n !== k('page'))) next.delete(k('page'));
      return next;
    }, { replace: true });
  }, [setParams, prefix]); // eslint-disable-line react-hooks/exhaustive-deps

  const query = params.get(k('q')) ?? '';
  const sort = params.get(k('sort')) ?? defaults.sort ?? null;
  const dir = (params.get(k('dir')) as SortDir | null) ?? defaults.dir ?? 'asc';
  const page = Number(params.get(k('page')) ?? '1') || 1;
  const tab = params.get(k('tab')) ?? defaults.tab ?? null;

  const toggleSort = useCallback((key: string) => {
    if (sort === key) set({ [k('dir')]: dir === 'asc' ? 'desc' : 'asc' });
    else set({ [k('sort')]: key, [k('dir')]: 'asc' });
  }, [sort, dir, set]); // eslint-disable-line react-hooks/exhaustive-deps

  const clear = useCallback(() => {
    set({ [k('q')]: null, [k('sort')]: null, [k('dir')]: null, [k('page')]: null, [k('tab')]: null });
  }, [set]); // eslint-disable-line react-hooks/exhaustive-deps

  return useMemo(() => ({
    query,
    setQuery: (v: string) => set({ [k('q')]: v || null }),
    sort,
    dir,
    toggleSort,
    page,
    setPage: (n: number) => set({ [k('page')]: n > 1 ? String(n) : null }),
    tab,
    setTab: (v: string | null) => set({ [k('tab')]: v }),
    isFiltered: !!query || (!!tab && tab !== defaults.tab),
    clear,
  }), [query, sort, dir, page, tab, toggleSort, clear, set, defaults.tab]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Sort by an accessor; nullish values last in both directions. */
export function sortRows<T>(rows: T[], accessor: ((row: T) => unknown) | null, dir: SortDir): T[] {
  if (!accessor) return rows;
  const out = [...rows];
  out.sort((a, b) => {
    const x = accessor(a);
    const y = accessor(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    const cmp = typeof x === 'number' && typeof y === 'number'
      ? x - y
      : String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: 'base' });
    return dir === 'asc' ? cmp : -cmp;
  });
  return out;
}
