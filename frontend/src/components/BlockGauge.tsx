/**
 * Segmented usage gauge: a fixed row of cells that fill left to right. Reads at a glance in
 * table rows where the 3px Meter rule was too subtle. Cells round up so any non-zero usage
 * lights at least one cell; the variant escalates with fill like the Meter's.
 */
export function BlockGauge({ value, cells = 5, className = '', label }: {
  /** Percentage, clamped to 0..100. */
  value: number;
  cells?: number;
  className?: string;
  /** Already-translated accessible name; omit when an adjacent reading states the numbers. */
  label?: string;
}) {
  const v = Math.max(0, Math.min(100, value));
  const filled = v === 0 ? 0 : Math.max(1, Math.round((v / 100) * cells));
  const tone = v >= 90 ? 'bg-danger' : v >= 75 ? 'bg-warn' : 'bg-primary';
  const a11y = label
    ? ({ role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(v), 'aria-label': label } as const)
    : ({ 'aria-hidden': true } as const);
  return (
    <span className={`inline-flex items-center gap-[3px] ${className}`} {...a11y}>
      {Array.from({ length: cells }, (_, i) => (
        <i key={i} className={`h-[10px] w-[7px] rounded-[1px] ${i < filled ? tone : 'bg-surface-2 border border-border/70'}`} />
      ))}
    </span>
  );
}
