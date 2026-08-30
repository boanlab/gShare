import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

export interface PromptOptions {
  title: string;
  /** Optional explanatory line under the title. */
  body?: ReactNode;
  label?: string;
  defaultValue?: string;
  placeholder?: string;
  /** 'text' (default) or 'number'. */
  inputType?: 'text' | 'number';
  /** Refuse an empty submission (the OK button stays disabled). */
  required?: boolean;
  confirmLabel?: string;
  cancelLabel?: string;
}

type Resolver = (value: string | null) => void;
const PromptContext = createContext<((o: PromptOptions) => Promise<string | null>) | null>(null);

/**
 * Single-input dialog in place of `window.prompt`, which breaks translation, focus handling,
 * and mobile layouts (see docs/console-ux.md). Resolves to the entered string, or null on
 * cancel/escape - the same contract as window.prompt.
 */
export function PromptProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [state, setState] = useState<{ options: PromptOptions; resolve: Resolver } | null>(null);
  const [value, setValue] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  const prompt = useCallback((options: PromptOptions) => {
    openerRef.current = document.activeElement as HTMLElement | null;
    setValue(options.defaultValue ?? '');
    return new Promise<string | null>((resolve) => setState({ options, resolve }));
  }, []);

  const close = useCallback((v: string | null) => {
    setState((s) => { s?.resolve(v); return null; });
    window.setTimeout(() => openerRef.current?.focus?.(), 0);
  }, []);

  useEffect(() => {
    if (!state) return;
    dialogRef.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); close(null); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [state, close]);

  const o = state?.options;
  const canSubmit = !o?.required || value.trim().length > 0;

  return (
    <PromptContext.Provider value={prompt}>
      {children}
      {o && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4 backdrop-blur-[2px]"
          onMouseDown={(e) => { if (e.target === e.currentTarget) close(null); }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="gs-prompt-title"
            className="gs-card w-full max-w-[440px] shadow-raised"
          >
            <h2 id="gs-prompt-title" className="gs-h2">{o.title}</h2>
            {o.body && <div className="text-sm text-muted mt-2">{o.body}</div>}
            <label className="block mt-3">
              {o.label && <span className="text-xs font-semibold text-muted">{o.label}</span>}
              <input
                data-autofocus
                type={o.inputType ?? 'text'}
                className="gs-input w-full mt-1"
                value={value}
                placeholder={o.placeholder}
                autoComplete="off"
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && canSubmit) close(value); }}
              />
            </label>
            <div className="flex justify-end gap-2 mt-4">
              <button type="button" className="gs-btn" onClick={() => close(null)}>
                {o.cancelLabel ?? t('common.cancel')}
              </button>
              <button
                type="button"
                className="gs-btn gs-btn-primary disabled:opacity-50"
                disabled={!canSubmit}
                onClick={() => close(value)}
              >
                {o.confirmLabel ?? t('common.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </PromptContext.Provider>
  );
}

/** `const v = await prompt({...}); if (v !== null) { ... }` */
export function usePrompt() {
  const ctx = useContext(PromptContext);
  if (!ctx) throw new Error('usePrompt must be used inside <PromptProvider>');
  return ctx;
}
