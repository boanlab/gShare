/**
 * The console's ONLY status rendering: a small coloured dot and the label in the same status
 * colour, no filled pill background. Transitional states breathe through `.gs-dot-pulse`
 * (disabled under prefers-reduced-motion). Status colours live here and in Meter, nowhere else.
 */

export type StatusKind =
  | 'running'
  | 'paused'
  | 'pending'
  | 'preparing'
  | 'terminating'
  | 'terminated'
  | 'error'
  | 'queued'
  | 'ready'
  | 'busy'
  | 'cordoned'
  | 'offline'
  | 'draining'
  | 'applying'
  | 'approved'
  | 'rejected'
  | 'escalated'
  | 'creating'
  | 'failed'
  | (string & Record<never, never>);

type Tone = 'free' | 'warn' | 'danger' | 'muted';

const TONE: Record<string, Tone> = {
  running: 'free',
  ready: 'free',
  pending: 'warn',
  preparing: 'warn',
  queued: 'warn',
  busy: 'warn',
  cordoned: 'warn',
  draining: 'warn',
  applying: 'warn',
  error: 'danger',
  offline: 'danger',
  approved: 'free',
  rejected: 'danger',
  escalated: 'warn',
  creating: 'warn',
  failed: 'danger',
  paused: 'muted',
  terminating: 'muted',
  terminated: 'muted',
  // Extra vocabulary the admin screens speak: accounts, cluster links, image imports and builds.
  active: 'free',
  invited: 'warn',
  suspended: 'danger',
  connected: 'free',
  live: 'free',
  polling: 'warn',
  succeeded: 'free',
  building: 'warn',
  pushing: 'warn',
  scanning: 'warn',
  importing: 'warn',
  // GPU congestion levels on the user dashboard: a traffic light per model, no numbers.
  free: 'free',
  moderate: 'warn',
  congested: 'danger',
  unavailable: 'muted',
};

/** States that are in transit: the dot pulses until they settle. */
const TRANSITIONAL = new Set(['pending', 'preparing', 'terminating', 'draining', 'applying', 'creating']);

const TEXT: Record<Tone, string> = {
  free: 'text-free',
  warn: 'text-warn',
  danger: 'text-danger',
  muted: 'text-muted',
};

const DOT: Record<Tone, string> = {
  free: 'bg-free',
  warn: 'bg-warn',
  danger: 'bg-danger',
  muted: 'bg-border-strong',
};

export function StatusPill({ kind, label, className = '' }: {
  kind: StatusKind;
  /** Already-translated status text; the caller owns the i18n lookup. */
  label: string;
  className?: string;
}) {
  const tone = TONE[kind] ?? 'muted';
  return (
    <span
      data-status={kind}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold ${TEXT[tone]} ${className}`}
    >
      <span
        aria-hidden="true"
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${DOT[tone]} ${TRANSITIONAL.has(kind) ? 'gs-dot-pulse' : ''}`}
      />
      {label}
    </span>
  );
}
