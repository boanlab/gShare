import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

/** Clipboard copy with visible confirmation. */
export function CopyButton({ value, label, className = '' }: { value: string; label?: string; className?: string }) {
  const { t } = useTranslation();
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const copied = state === 'copied';
  const timer = useRef<number>();

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    let ok = true;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Fallback for insecure origins, where the Clipboard API is unavailable.
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand('copy'); } catch { ok = false; } finally { ta.remove(); }
    }
    setState(ok ? 'copied' : 'failed');
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState('idle'), ok ? 1600 : 4000);
  };

  const name = label ?? t('common.copyValue', { value });
  return (
    <button
      type="button"
      data-copy
      onClick={copy}
      title={state === 'copied' ? t('common.copied') : state === 'failed' ? t('common.copyFailed') : name}
      aria-label={name}
      className={`gs-btn gs-btn-sm ${copied ? 'text-free border-free' : ''} ${state === 'failed' ? 'text-danger border-danger' : ''} ${className}`}
    >
      <span aria-hidden="true">{copied ? '✓' : state === 'failed' ? '!' : '⧉'}</span>
      <span role="status" className={state === 'idle' ? 'gs-sr-only' : 'text-[11px] font-bold'}>
        {state === 'copied' ? t('common.copied') : state === 'failed' ? t('common.copyFailed') : ''}
      </span>
    </button>
  );
}

/** Monospaced identifier, truncated, with its copy control. */
export function CopyableId({ value, className = '' }: { value: string; className?: string }) {
  if (!value) return <span className="text-muted">-</span>;
  return (
    <span className={`inline-flex items-center gap-1 max-w-full ${className}`}>
      <code className="font-mono text-[12px] truncate" title={value}>{value}</code>
      <CopyButton value={value} />
    </span>
  );
}
