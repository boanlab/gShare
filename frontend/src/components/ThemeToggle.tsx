import { useTranslation } from 'react-i18next';
import { useUiStore } from '@/store/uiStore';
import { Moon, Sun } from './icons';

export function ThemeToggle() {
  const { t } = useTranslation();
  const theme = useUiStore((s) => s.theme);
  const toggleTheme = useUiStore((s) => s.toggleTheme);
  return (
    <button
      type="button"
      className="w-[34px] h-[34px] max-md:w-11 max-md:h-11 rounded-ctl border border-border bg-surface
                 text-muted grid place-items-center hover:text-text hover:bg-surface-2 transition-colors duration-150"
      onClick={toggleTheme}
      title={t('common.theme')}
      aria-label={t('common.theme')}
    >
      {theme === 'dark' ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}
    </button>
  );
}
