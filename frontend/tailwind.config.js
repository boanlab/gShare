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
        text: 'var(--text)',
        muted: 'var(--muted)',
        primary: 'var(--primary)',
        'primary-soft': 'var(--primary-soft)',
        gpu: 'var(--gpu)',
        'gpu-soft': 'var(--gpu-soft)',
        free: 'var(--free)',
        'free-soft': 'var(--free-soft)',
        warn: 'var(--warn)',
        'warn-soft': 'var(--warn-soft)',
        danger: 'var(--danger)',
        'danger-soft': 'var(--danger-soft)',
        ok: 'var(--ok)',
      },
      borderRadius: {
        card: 'var(--radius)',
      },
      boxShadow: {
        card: 'var(--shadow)',
      },
    },
  },
  plugins: [],
};
