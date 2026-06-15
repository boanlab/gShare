import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface ConfirmOptions {
  title: string;
  /** What happens, and to which record. */
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  /** Exact text the user must type. For the irreversible. */
  confirmText?: string;
  consequences?: string[];
}

type Resolver = (ok: boolean) => void;
const ConfirmContext = createContext<((o: ConfirmOptions) => Promise<boolean>) | null>(null);

/**
 * Confirmation dialog for destructive actions, in place of `window.confirm`: names the record,
 * lists consequences, traps and restores focus, and can require the name to be typed.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [state, setState] = useState<{ options: ConfirmOptions; resolve: Resolver } | null>(null);
  const [typed, setTyped] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  const confirm = useCallback((options: ConfirmOptions) => {
    openerRef.current = document.activeElement as HTMLElement | null;
    setTyped('');
    return new Promise<boolean>((resolve) => setState({ options, resolve }));
  }, []);

  const close = useCallback((ok: boolean) => {
    setState((s) => { s?.resolve(ok); return null; });
    // Focus back to the control that opened the dialog.
    window.setTimeout(() => openerRef.current?.focus?.(), 0);
  }, []);

  useEffect(() => {
    if (!state) return;
    const node = dialogRef.current;
    node?.querySelector<HTMLElement>('[data-autofocus]')?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close(false); return; }
      if (e.key !== 'Tab' || !node) return;
      // Focus trap.
      const focusables = [...node.querySelectorAll<HTMLElement>('button, input, a[href], [tabindex]:not([tabindex="-1"])')]
        .filter((el) => !el.hasAttribute('disabled'));
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [state, close]);

  const o = state?.options;
  const needsTyping = !!o?.confirmText;
  const canConfirm = !needsTyping || typed.trim() === o?.confirmText;

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {o && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
          onMouseDown={(e) => { if (e.target === e.currentTarget) close(false); }}
        >
          <div
            ref={dialogRef}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="gs-confirm-title"
            aria-describedby={o.body ? 'gs-confirm-body' : undefined}
            className="gs-card w-full max-w-[440px] shadow-card"
          >
            <h2 id="gs-confirm-title" className="text-[15px] font-extrabold">{o.title}</h2>
            {o.body && <div id="gs-confirm-body" className="text-[13px] text-muted mt-2">{o.body}</div>}
            {o.consequences && o.consequences.length > 0 && (
              <ul className="mt-3 space-y-1 text-[12.5px] text-muted list-disc pl-5">
                {o.consequences.map((c) => <li key={c}>{c}</li>)}
              </ul>
            )}
            {needsTyping && (
              <label className="block mt-3">
                <span className="text-[12px] font-semibold text-muted">
                  {t('confirm.typeToConfirm', { text: o.confirmText })}
                </span>
                <input
                  data-autofocus
                  className="gs-input w-full mt-1"
                  value={typed}
                  autoComplete="off"
                  onChange={(e) => setTyped(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && canConfirm) close(true); }}
                  aria-invalid={typed.length > 0 && !canConfirm}
                />
              </label>
            )}
            <div className="flex justify-end gap-2 mt-4">
              <button type="button" className="gs-btn" onClick={() => close(false)} {...(needsTyping ? {} : { 'data-autofocus': true })}>
                {o.cancelLabel ?? t('common.cancel')}
              </button>
              <button
                type="button"
                className={`gs-btn ${o.destructive ? 'gs-btn-danger' : 'gs-btn-primary'} disabled:opacity-50`}
                disabled={!canConfirm}
                onClick={() => close(true)}
              >
                {o.confirmLabel ?? t('common.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

/** `if (await confirm({...})) { ... }` */
export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used inside <ConfirmProvider>');
  return ctx;
}
