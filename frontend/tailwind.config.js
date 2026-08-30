/** @type {import('tailwindcss').Config} */
// The theme is expressed as CSS variables, mapped through var(--…); dark mode switches on the
// data-theme="dark" selector.
export default {
  darkMode: ['selector', '[data-theme="dark"]'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        border: 'var(--border)',
        'border-strong': 'var(--border-strong)',
        text: 'var(--text)',
        muted: 'var(--muted)',
        primary: 'var(--primary)',
        'primary-soft': 'var(--primary-soft)',
        'on-primary': 'var(--on-primary)',
        free: 'var(--free)',
        'free-soft': 'var(--free-soft)',
        warn: 'var(--warn)',
        'warn-soft': 'var(--warn-soft)',
        danger: 'var(--danger)',
        'danger-soft': 'var(--danger-soft)',
      },
      borderRadius: {
        card: 'var(--radius)',
        ctl: 'var(--radius-control)',
        tag: 'var(--radius-tag)',
      },
      // The console's type scale. New and edited code uses these instead of arbitrary values.
      fontSize: {
        '2xs': ['11px', { lineHeight: '1.3' }],
        xs: ['12px', { lineHeight: '1.4' }],
        sm: ['13px', { lineHeight: '1.5' }],
        base: ['14px', { lineHeight: '1.55' }],
        md: ['15px', { lineHeight: '1.5' }],
        lg: ['17px', { lineHeight: '1.4' }],
        kpi: ['26px', { lineHeight: '1.1', fontWeight: '700' }],
      },
      boxShadow: {
        card: 'var(--shadow)',
        raised: 'var(--shadow-raised)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
    },
  },
  plugins: [],
};
