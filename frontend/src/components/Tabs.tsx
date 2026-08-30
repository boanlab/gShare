/**
 * The console's one tab pattern: underline tabs on a hairline, with an optional count chip.
 * Lifted from the session list; segmented controls and ad-hoc tab rows convert to this.
 * Tab state stays with the caller (usually a `?tab=` URL param via useTableState).
 */
export interface TabItem {
  key: string;
  /** Already-translated tab label; the caller owns the i18n lookup. */
  label: string;
  count?: number;
  /** Unselectable, with `disabledReason` as the hover/focus explanation. */
  disabled?: boolean;
  disabledReason?: string;
}

export function Tabs({ items, active, onChange, ariaLabel, className = '' }: {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
  /** Already-translated name for the tablist. */
  ariaLabel: string;
  className?: string;
}) {
  return (
    <div className={`flex gap-1 mb-4 flex-wrap border-b border-border ${className}`} role="tablist" aria-label={ariaLabel}>
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          role="tab"
          aria-selected={active === it.key}
          aria-disabled={it.disabled || undefined}
          disabled={it.disabled}
          title={it.disabled ? it.disabledReason : undefined}
          onClick={() => { if (!it.disabled) onChange(it.key); }}
          className={`inline-flex items-center gap-1.5 px-3 py-2 -mb-px border-b-2 text-sm min-h-[40px]
            transition-colors duration-150 ${
            it.disabled
              ? 'border-transparent text-muted/50 cursor-not-allowed'
              : active === it.key
              ? 'border-primary text-text font-semibold'
              : 'border-transparent text-muted font-medium hover:text-text'
          }`}
        >
          {it.label}
          {it.count != null && (
            <span className={`gs-num text-2xs px-1.5 rounded-tag ${
              active === it.key ? 'bg-primary-soft text-primary' : 'bg-surface-2 text-muted'
            }`}>{it.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}
