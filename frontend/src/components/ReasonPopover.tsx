import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from '@/components/icons';

/**
 * A short justification (a rejection reason, say) that would break a row's rhythm if inlined.
 *
 * Click to open, click again / outside / Escape to close. Deliberately NOT hover-triggered: a
 * mouse crossing a table would flash bubbles the reader never asked for, and a hover-only reveal
 * is unreachable on touch.
 */
export function ReasonPopover({ reason, label }: { reason: string; label?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span ref={ref} className="relative inline-flex">
      <button
        type="button"
        aria-expanded={open}
        // Rows can carry their own click handler (drawer, navigation) — this must not trigger it.
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        className="text-danger text-2xs underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-1 focus-visible:ring-primary rounded-tag"
      >
        {label ?? t('wallet.viewReason')}
      </button>
      {open && (
        <span
          role="note"
          className="absolute bottom-full right-0 mb-1.5 z-30 w-max max-w-[300px] whitespace-normal rounded-ctl border border-border bg-surface pl-2.5 pr-7 py-1.5 text-2xs leading-relaxed text-text text-left font-normal shadow-raised"
        >
          {reason}
          {/* Pinned close affordance: the bubble stays until dismissed, so it needs a visible exit. */}
          <button
            type="button"
            aria-label={t('common.close')}
            onClick={(e) => { e.stopPropagation(); setOpen(false); }}
            className="absolute top-1 right-1 inline-flex items-center justify-center w-4 h-4 rounded-tag text-muted hover:text-text hover:bg-surface-2 outline-none focus-visible:ring-1 focus-visible:ring-primary"
          >
            <X size={10} weight="bold" aria-hidden="true" />
          </button>
        </span>
      )}
    </span>
  );
}
