import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { currentLocale } from '@/i18n';
import { formatDateTime } from '@/lib/format';

const MINUTE = 60_000;

/** Delta to a whole unit, largest that fits. */
function relativeParts(deltaMs: number): [number, Intl.RelativeTimeFormatUnit] {
  const s = Math.round(deltaMs / 1000);
  const abs = Math.abs(s);
  if (abs < 60) return [s, 'second'];
  if (abs < 3600) return [Math.round(s / 60), 'minute'];
  if (abs < 86400) return [Math.round(s / 3600), 'hour'];
  if (abs < 2592000) return [Math.round(s / 86400), 'day'];
  if (abs < 31536000) return [Math.round(s / 2592000), 'month'];
  return [Math.round(s / 31536000), 'year'];
}

/**
 * Relative time, with the exact value in `title` and `datetime`.
 *
 * Re-renders on a minute timer so a long-open tab stays accurate.
 */
export function Timestamp({ value, relative = true, className = '' }: { value?: string | null; relative?: boolean; className?: string }) {
  const { t } = useTranslation();
  const [, tick] = useState(0);

  useEffect(() => {
    if (!relative || !value) return;
    const id = window.setInterval(() => tick((n) => n + 1), MINUTE);
    return () => window.clearInterval(id);
  }, [relative, value]);

  if (!value) return <span className={className}>-</span>;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return <span className={className}>-</span>;

  const exact = formatDateTime(value);
  if (!relative) return <time dateTime={d.toISOString()} title={exact} className={className}>{exact}</time>;

  const [amount, unit] = relativeParts(d.getTime() - Date.now());
  const rtf = new Intl.RelativeTimeFormat(currentLocale(), { numeric: 'auto', style: 'short' });
  return (
    <time dateTime={d.toISOString()} title={t('common.exactTime', { time: exact })} className={className}>
      {rtf.format(amount, unit)}
    </time>
  );
}
