import { type ReactNode, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from './icons';

/**
 * The console's one detail overlay: a right-hand drawer over a scrim, opened by clicking a table
 * row. Wide list screens keep their density; everything about ONE row (and its actions) lives
 * here. Escape and the scrim close it; focus lands on the panel when it opens.
 */
export function DetailDrawer({ open, title, tag, onClose, children, footer }: {
  open: boolean;
  /** Already-translated heading. */
  title: ReactNode;
  /** Optional element rendered beside the title (a status pill, a tag). */
  tag?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** Action row pinned to the bottom (edit / delete / terminate buttons). */
  footer?: ReactNode;
}) {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50" role="presentation">
      <div className="absolute inset-0 bg-black/45" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="absolute inset-y-0 right-0 w-full max-w-[440px] bg-surface border-l border-border
                   shadow-raised flex flex-col outline-none"
      >
        <div className="flex items-center gap-2.5 px-5 py-4 border-b border-border">
          <h2 className="font-bold text-md min-w-0 truncate">{title}</h2>
          {tag}
          <button
            type="button"
            className="ml-auto shrink-0 w-8 h-8 grid place-items-center rounded-ctl text-muted hover:text-text hover:bg-surface-2"
            onClick={onClose}
            aria-label={t('common.close')}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="px-5 py-4 border-t border-border flex gap-2 justify-end flex-wrap">{footer}</div>}
      </div>
    </div>
  );
}

/** One label/value line inside a drawer. */
export function DrawerRow({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-border/50 last:border-0 text-sm">
      <dt className="text-muted shrink-0">{label}</dt>
      <dd className={`min-w-0 text-right ${mono ? 'gs-num text-xs break-all' : ''}`}>{children}</dd>
    </div>
  );
}
