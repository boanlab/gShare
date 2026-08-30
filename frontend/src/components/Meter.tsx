/**
 * The console's one meter: a 3px rule that fills (`.gs-bar`). Decorative by default; pass
 * `label` when the meter is the only carrier of the reading and it becomes a real `meter`
 * for assistive technology.
 */
export type MeterVariant = 'primary' | 'free' | 'warn' | 'danger';

export function Meter({ value, variant = 'primary', className = '', label }: {
  /** Percentage, clamped to 0..100. */
  value: number;
  variant?: MeterVariant;
  className?: string;
  /** Already-translated accessible name; omit when an adjacent reading states the numbers. */
  label?: string;
}) {
  const v = Math.max(0, Math.min(100, value));
  const a11y = label
    ? ({ role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(v), 'aria-label': label } as const)
    : ({ 'aria-hidden': true } as const);
  return (
    <div className={`gs-bar ${variant} ${className}`} {...a11y}>
      <i style={{ width: `${v}%` }} />
    </div>
  );
}
