import { useTranslation } from 'react-i18next';

/**
 * Language toggle for the top bar: one button that flips ko <-> en, styled like the
 * mode-switch button next to it. The choice is persisted per browser by the i18n
 * detector, so it survives a reload and is independent of the signed-in account.
 */
export function LanguageToggle() {
  const { i18n, t } = useTranslation();
  const current = i18n.resolvedLanguage ?? 'en';
  const next = current.startsWith('ko') ? 'en' : 'ko';
  return (
    <button
      type="button"
      className="h-[34px] rounded-ctl border border-border bg-surface-2 px-3 text-sm font-semibold hover:bg-surface"
      onClick={() => void i18n.changeLanguage(next)}
      title={t('common.language')}
      aria-label={t('common.language')}
    >
      {next === 'en' ? 'English' : '한국어'}
    </button>
  );
}
