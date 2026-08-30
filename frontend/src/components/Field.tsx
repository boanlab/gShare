import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * One labelled form control: `<label for>`, required marker, hint and error message, wired to the
 * control with `aria-describedby` / `aria-invalid` / `aria-required`.
 */
export function Field({
  label, children, required, hint, error, htmlFor, className = '',
}: {
  label: string;
  /** Receives the ids to attach: `(ids) => <input {...ids} />`. */
  children: (ids: { id: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean; 'aria-required'?: boolean; required?: boolean }) => ReactNode;
  required?: boolean;
  hint?: ReactNode;
  error?: string | null;
  htmlFor?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const auto = useId();
  const id = htmlFor ?? auto;
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;

  return (
    <div className={`block ${className}`}>
      <label htmlFor={id} className="text-xs font-semibold text-text">
        {label}
        {required && (
          <>
            <span aria-hidden="true" className="text-danger ml-0.5">*</span>
            <span className="gs-sr-only">{t('common.requiredField')}</span>
          </>
        )}
      </label>
      <div className="mt-1.5">
        {children({ id, 'aria-describedby': describedBy, 'aria-invalid': !!error, 'aria-required': required, required })}
      </div>
      {hint && <p id={hintId} className="text-muted text-2xs mt-1.5">{hint}</p>}
      {error && <p id={errorId} role="alert" className="text-danger text-2xs mt-1.5 font-medium">{error}</p>}
    </div>
  );
}

/** What is still missing before the primary action becomes available. */
export function DisabledReason({ reasons }: { reasons: string[] }) {
  const { t } = useTranslation();
  // Always rendered: the live region has to exist before its content changes.
  return (
    <p role="status" aria-live="polite" className={reasons.length ? 'text-2xs text-muted' : 'gs-sr-only'}>
      {reasons.length ? `${t('common.blockedBy')} ${reasons.join(t('common.listSeparator'))}` : ''}
    </p>
  );
}
