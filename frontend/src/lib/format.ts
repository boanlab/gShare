// Pure formatting helpers for currency, time, and capacity.
//
// These run outside React, so they read the active language from the i18n singleton rather than
// from a hook. Enum codes stay as the API returns them; only the rendered label is localised.
import i18n, { currentLocale } from '@/i18n';

/** Internal role code (`super_admin` and friends) to a display label. */
export function roleLabel(role?: string | null): string {
  if (!role) return '';
  const key = `enum.role.${role}`;
  if (i18n.exists(key)) return i18n.t(key);
  // Unknown role: title-case the code so it is at least readable.
  return role
    .split('_')
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ');
}

/** Enum code to a display label, falling back to the raw code when there is no translation. */
const _label = (kind: string) => (code?: string | null): string => {
  if (!code) return '-';
  const key = `enum.${kind}.${code}`;
  return i18n.exists(key) ? i18n.t(key) : code;
};
export const userStatusLabel = _label('userStatus');
export const statusLabel = _label('status');
export const scopeLabel = _label('scope');
export const accessModeLabel = _label('accessMode');
export const sessionStatusLabel = _label('sessionStatus');
export const reqStatusLabel = _label('reqStatus');

/** Credits, which are whole units. */
export function formatCredit(amount?: number | null): string {
  if (amount == null) return '-';
  return new Intl.NumberFormat(currentLocale()).format(amount);
}

/** ISO timestamp to the viewer's local time, in their language. */
export function formatDateTime(iso?: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString(currentLocale(), { dateStyle: 'medium', timeStyle: 'short' });
}

/** VRAM in MB, rendered in whichever unit reads better. */
export function formatVram(mb?: number | null): string {
  if (mb == null) return '-';
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GiB` : `${mb} MiB`;
}

/** Capacity in GiB. */
export function formatGiB(gb?: number | null): string {
  if (gb == null) return '-';
  return `${new Intl.NumberFormat(currentLocale()).format(gb)} GiB`;
}

/**
 * Elapsed time between an ISO start and an end (or now) as "2h 13m" or "5m 02s". Used for uptime.
 * The unit letters are deliberately not translated: they read the same in every supported language.
 */
export function formatDuration(fromIso?: string | null, toMs?: number): string {
  if (!fromIso) return '—';
  const start = new Date(fromIso).getTime();
  if (Number.isNaN(start)) return '—';
  let sec = Math.max(0, Math.floor(((toMs ?? Date.now()) - start) / 1000));
  const h = Math.floor(sec / 3600); sec -= h * 3600;
  const m = Math.floor(sec / 60); const s = sec - m * 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

/** Elapsed time in fractional hours. Estimated cost is rate x occupancy x hours. */
export function hoursElapsed(fromIso?: string | null, toMs?: number): number {
  if (!fromIso) return 0;
  const start = new Date(fromIso).getTime();
  if (Number.isNaN(start)) return 0;
  return Math.max(0, ((toMs ?? Date.now()) - start) / 3_600_000);
}

/** Seconds remaining until expiry, as an mm:ss countdown. */
export function countdown(expiresAt?: string | null): string {
  if (!expiresAt) return '-';
  const left = Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000));
  const m = Math.floor(left / 60);
  const s = left % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
