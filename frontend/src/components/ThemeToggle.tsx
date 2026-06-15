import { useTranslation } from 'react-i18next';
import { useUiStore } from '@/store/uiStore';

export function ThemeToggle() {
  const { t } = useTranslation();
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  return (
    <button
      type="button"
      className="w-[34px] h-[34px] max-md:w-11 max-md:h-11 rounded-lg border border-border bg-surface-2 grid place-items-center"
      onClick={toggleTheme}
      title={t('common.theme')}
      aria-label={t('common.theme')}
    >
      {theme === 'dark' ? '☀️' : '🌙'}
    </button>
  );
}
