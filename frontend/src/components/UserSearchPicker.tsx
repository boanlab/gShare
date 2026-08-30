import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useUsers } from '@/api/hooks/useUsers';

// User picker backed by a server-side search over names and emails (q), optionally scoped to an
// organization with org_id.
export function UserSearchPicker({
  orgId,
  excludeIds,
  selectedId,
  onSelect,
  emptyHint,
}: {
  orgId?: string;
  excludeIds?: Set<string>;
  selectedId: string;
  onSelect: (id: string) => void;
  emptyHint?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [q, setQ] = useState('');
  const { data: users = [], isFetching } = useUsers({ q: q.trim() || undefined, org_id: orgId, size: 20 });
  const list = excludeIds ? users.filter((u) => !excludeIds.has(u.id)) : users;

  return (
    <div>
      {/* A real label, not just the placeholder: the placeholder disappears as soon as the user
          types, and this control is the only thing on some screens. */}
      <label htmlFor={id} className="gs-sr-only">{t('userPicker.label')}</label>
      <input
        id={id}
        type="search"
        className="w-full px-3 py-2 border border-border rounded-ctl bg-surface-2 text-sm"
        placeholder={t('userPicker.placeholder')}
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <ul
        className="mt-1.5 max-h-44 overflow-auto rounded-card border border-border divide-y divide-border"
        aria-live="polite"
        aria-label={t('userPicker.resultsLabel')}
      >
        {list.length === 0 ? (
          <li className="px-3 py-2 text-xs text-muted">
            {isFetching ? t('userPicker.searching') : (emptyHint ?? t('userPicker.empty'))}
          </li>
        ) : (
          list.map((u) => (
            <li key={u.id}>
              <button
                type="button"
                onClick={() => onSelect(u.id)}
                className={`w-full text-left px-3 py-2 text-sm hover:bg-surface-2 ${selectedId === u.id ? 'bg-primary-soft text-primary font-semibold' : ''}`}
              >
                {u.name} <span className="text-muted font-mono text-xs">{u.email}</span>
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
