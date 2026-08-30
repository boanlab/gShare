import { type ReactNode, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from './icons';

/**
 * The console's one modal popup: a centered panel over a scrim, for small focused tasks
 * (assigning admins, quick pickers) that never deserved a full page navigation.
 * Escape and the scrim close it; focus lands on the panel when it opens.
 */
export function Dialog({ open, title, onClose, children, wide }: {
  open: boolean;
  /** Already-translated heading. */
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
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
    <div className="fixed inset-0 z-50 grid place-items-center p-4" role="presentation">
      <div className="absolute inset-0 bg-black/45" onClick={onClose} aria-hidden="true" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className={`relative w-full ${wide ? 'max-w-2xl' : 'max-w-lg'} max-h-[85vh] flex flex-col
                    bg-surface border border-border rounded-card shadow-raised outline-none`}
      >
        <div className="flex items-center gap-2.5 px-5 py-4 border-b border-border">
          <h2 className="font-bold text-md min-w-0 truncate">{title}</h2>
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
      </div>
    </div>
  );
}
