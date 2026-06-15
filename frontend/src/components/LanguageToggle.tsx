import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES } from '@/i18n';

/**
 * Language selector for the top bar. The choice is persisted per browser by the i18n detector,
 * so it survives a reload and is independent of the signed-in account.
 */
export function LanguageToggle() {
  const { i18n, t } = useTranslation();
  const current = i18n.resolvedLanguage ?? 'en';

  return (
    <select
      className="h-[34px] rounded-lg border border-border bg-surface-2 px-2 text-[13px] font-semibold"
      value={current}
      onChange={(e) => void i18n.changeLanguage(e.target.value)}
      title={t('common.language')}
      aria-label={t('common.language')}
    >
      {SUPPORTED_LANGUAGES.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  );
}
