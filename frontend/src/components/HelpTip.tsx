import { Question } from './icons';

/**
 * The console's one help affordance: a ? icon that reveals a CSS bubble on hover/focus.
 * The browser's native `title` tooltip is delayed and unreliable (and never keyboard-reachable),
 * so this renders the same bubble the spend chart uses. Anchors to the icon's left edge by
 * default so it never clips at a panel's left border; pass `align` for right-edge placements.
 */
export function HelpTip({ text, align = 'start', className = '' }: {
  /** Already-translated explanation. */
  text: string;
  align?: 'start' | 'end';
  className?: string;
}) {
  return (
    <span tabIndex={0} aria-label={text} className={`group relative inline-flex cursor-help text-muted/70 hover:text-text focus-visible:text-text outline-none ${className}`}>
      <Question size={13} aria-hidden="true" />
      <span
        role="tooltip"
        className={`pointer-events-none absolute bottom-full mb-1.5 hidden group-hover:block group-focus-within:block z-20
                    w-max max-w-[280px] whitespace-normal rounded-ctl border border-border bg-surface px-2.5 py-1.5
                    text-2xs leading-relaxed text-text text-left font-normal shadow-raised ${align === 'end' ? 'right-0' : 'left-0'}`}
      >
        {text}
      </span>
    </span>
  );
}
