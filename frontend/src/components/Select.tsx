import { Children, isValidElement, type ReactElement, type ReactNode, type SelectHTMLAttributes } from 'react';
import { SelectMenu, type SelectMenuOption } from './SelectMenu';

/**
 * Drop-in replacement for a native `<select>`: same children (`<option>` / `<optgroup>`), same
 * `value`, and an `onChange` that still hands back `{ target: { value } }` — so converting a call
 * site is a tag rename, nothing else. Renders the console-styled SelectMenu listbox underneath.
 */
type Nativeish = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'onChange' | 'value' | 'children'>;

function collect(children: ReactNode, group?: string): SelectMenuOption[] {
  const out: SelectMenuOption[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    const el = child as ReactElement<{ value?: unknown; children?: ReactNode; disabled?: boolean; label?: string }>;
    if (el.type === 'option') {
      out.push({
        value: String(el.props.value ?? ''),
        label: el.props.children as ReactNode,
        disabled: el.props.disabled,
        group,
      });
    } else if (el.type === 'optgroup') {
      out.push(...collect(el.props.children, el.props.label));
    } else if (el.props?.children) {
      // Fragments / arrays produced by maps and conditionals.
      out.push(...collect(el.props.children as ReactNode, group));
    }
  });
  return out;
}

export function Select({ value, onChange, children, className = '', disabled, id, title, ...rest }: Nativeish & {
  value: string | number | undefined;
  onChange?: (e: { target: { value: string } }) => void;
  children: ReactNode;
  className?: string;
}) {
  const options = collect(children);
  const ariaLabel = (rest as Record<string, unknown>)['aria-label'] as string | undefined;
  return (
    <SelectMenu
      id={id}
      value={String(value ?? '')}
      onChange={(v) => onChange?.({ target: { value: v } })}
      options={options}
      disabled={disabled}
      ariaLabel={ariaLabel ?? title}
      className={className.includes('w-full') ? 'w-full' : className.includes('block') ? 'block' : ''}
      buttonClassName={className}
    />
  );
}
