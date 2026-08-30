import { useEffect, useState } from 'react';
import { currentLocale } from '@/i18n';
import { formatDateTime } from '@/lib/format';

const MINUTE = 60_000;
const DAY = 86_400_000;

/** Delta to a whole unit, largest that fits. */
function relativeParts(deltaMs: number): [number, Intl.RelativeTimeFormatUnit] {
  const s = Math.round(deltaMs / 1000);
  const abs = Math.abs(s);
  if (abs < 60) return [s, 'second'];
  if (abs < 3600) return [Math.round(s / 60), 'minute'];
  return [Math.round(s / 3600), 'hour'];
}

/** The requested plain form: 2026-06-12 08:49 (local time, locale-independent). */
export function formatPlainDateTime(value: string | Date): string {
  const d = value instanceof Date ? value : new Date(value);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * Absolute time first — "2026-06-12 08:49" — with a relative hint appended while the moment is
 * recent ("(5분 전)", last 24 h only; beyond that the date already says everything). Seconds live
 * in `title`. Re-renders on a minute timer so the hint stays accurate in a long-open tab.
 */
export function Timestamp({ value, relative = true, compact = false, className = '' }: { value?: string | null; relative?: boolean; compact?: boolean; className?: string }) {
  const [, tick] = useState(0);

  useEffect(() => {
    if (!relative || !value) return;
    const id = window.setInterval(() => tick((n) => n + 1), MINUTE);
    return () => window.clearInterval(id);
  }, [relative, value]);

  if (!value) return <span className={className}>-</span>;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return <span className={className}>-</span>;

  const exact = formatPlainDateTime(d);
  const delta = d.getTime() - Date.now();
  const recent = relative && Math.abs(delta) < DAY;
  const rel = recent
    ? new Intl.RelativeTimeFormat(currentLocale(), { numeric: 'auto', style: 'short' })
        .format(...relativeParts(delta))
    : null;
  if (compact) {
    // Space-tight tables: just "5분 전" while recent, the plain date once it is old news.
    return (
      <time dateTime={d.toISOString()} title={formatDateTime(value)} className={className}>
        {rel ?? <span className="gs-num">{exact}</span>}
      </time>
    );
  }
  return (
    <time dateTime={d.toISOString()} title={formatDateTime(value)} className={className}>
      <span className="gs-num">{exact}</span>
      {rel && <span className="text-muted"> ({rel})</span>}
    </time>
  );
}
