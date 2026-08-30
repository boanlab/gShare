import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { CaretDown, Check } from './icons';

export interface SelectMenuOption {
  value: string;
  /** Main line of the option. */
  label: ReactNode;
  /** Muted second part, e.g. "gpu3 · 8e20143e". */
  hint?: string;
  /** Optional group header; consecutive options sharing a group render under one label. */
  group?: string;
  disabled?: boolean;
}

/**
 * A styled replacement for `<select>` where the native option list looks out of place
 * (long technical labels, hints, theming). The panel opens flush under the trigger,
 * left-aligned, at least as wide as the trigger.
 *
 * Same contract as a native select: `value` + `onChange(value)`, with '' as the
 * "all/none" choice. Keyboard: Enter/Space/ArrowDown open; ArrowUp/Down move; Enter
 * selects; Escape closes. Not a form control — a filter control.
 */
export function SelectMenu({ value, onChange, options, disabled, ariaLabel, className = '', id, buttonClassName = '' }: {
  value: string;
  onChange: (v: string) => void;
  options: SelectMenuOption[];
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
  /** Field integration: the label's htmlFor targets the trigger button. */
  id?: string;
  buttonClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);           // keyboard cursor, index into options
  // Portal coordinates: the list renders into document.body at fixed position, so containers
  // with overflow (table wrappers, cards, dialogs) can neither clip nor stretch around it.
  const [pos, setPos] = useState<{ minWidth: number; left?: number; right?: number; top?: number; bottom?: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listboxId = useId();
  const selected = options.find((o) => o.value === value) ?? options[0];

  // Close on Escape and on any press outside the control.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    const onDown = (e: MouseEvent) => {
      const n = e.target as Node;
      if (!rootRef.current?.contains(n) && !listRef.current?.contains(n)) setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  // Anchor the list to the trigger. Opens upward when the space below is short, and
  // right-aligns when the trigger sits in the right half of the viewport.
  useEffect(() => {
    if (!open) { setPos(null); return; }
    const update = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (!r) return;
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < 240 && r.top > spaceBelow;
      const alignRight = (r.left + r.right) / 2 > window.innerWidth / 2;
      setPos({
        minWidth: r.width,
        ...(alignRight ? { right: Math.max(8, window.innerWidth - r.right) } : { left: Math.max(8, r.left) }),
        ...(openUp ? { bottom: window.innerHeight - r.top + 6 } : { top: r.bottom + 6 }),
      });
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => { window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true); };
  }, [open]);

  // The keyboard cursor starts on the current value and stays in view.
  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
  }, [open, options, value]);
  useEffect(() => {
    if (!open) return;
    listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [open, active]);

  const pick = (v: string) => { onChange(v); setOpen(false); };
  const onTriggerKey = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault(); setOpen(true); return;
    }
    if (!open) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(options.length - 1, i + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0); }
    else if (e.key === 'End') { e.preventDefault(); setActive(options.length - 1); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(options[active]?.value ?? ''); }
    else if (e.key === 'Tab') setOpen(false);
  };

  return (
    <div className={`relative ${className}`} ref={rootRef}>
      <button
        id={id}
        ref={btnRef}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        aria-controls={open ? listboxId : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKey}
        className={`gs-input w-auto min-w-[10rem] max-w-full inline-flex items-center justify-between gap-2
                   text-left disabled:opacity-50 disabled:cursor-not-allowed ${buttonClassName}`}
      >
        <span className="truncate">
          {selected?.label ?? '-'}
          {selected?.hint && <span className="text-muted ml-1.5 text-xs">{selected.hint}</span>}
        </span>
        <CaretDown size={12} className={`shrink-0 text-muted transition-transform duration-150 ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>

      {open && pos && createPortal(
        <ul
          id={listboxId}
          ref={listRef}
          role="listbox"
          aria-label={ariaLabel}
          style={{ position: 'fixed', ...pos }}
          className="z-50 w-max max-w-[min(26rem,calc(100vw-2rem))]
                     max-h-72 overflow-y-auto bg-surface border border-border rounded-card shadow-raised py-1"
        >
          {options.map((o, i) => {
            const isSel = o.value === value;
            const groupHead = o.group && (i === 0 || options[i - 1].group !== o.group) ? o.group : null;
            return (
              <li
                key={o.value || '∅'}
                role="option"
                aria-selected={isSel}
                aria-disabled={o.disabled || undefined}
                onMouseEnter={() => !o.disabled && setActive(i)}
                onMouseDown={(e) => e.preventDefault() /* keep trigger focus for keyboard flow */}
                onClick={() => !o.disabled && pick(o.value)}
                className={`text-sm transition-colors duration-100 ${o.disabled ? 'opacity-45' : 'cursor-pointer'}
                           ${i === active && !o.disabled ? 'bg-surface-2' : ''} ${isSel ? 'text-primary font-semibold' : ''}`}
              >
                {groupHead && (
                  <div className="px-3 pt-2 pb-1 text-2xs font-semibold text-muted uppercase tracking-wide cursor-default">{groupHead}</div>
                )}
                <div className="flex items-center gap-2.5 px-3 py-2">
                <span className={`w-4 shrink-0 ${isSel ? '' : 'invisible'}`} aria-hidden="true">
                  <Check size={14} weight="bold" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate">{o.label}</span>
                  {o.hint && <span className={`block truncate text-xs font-normal ${isSel ? 'text-primary/70' : 'text-muted'}`}>{o.hint}</span>}
                </span>
                </div>
              </li>
            );
          })}
        </ul>,
        document.body,
      )}
    </div>
  );
}
