// i18n bootstrap. English is the source language and the fallback; Korean is a full translation.
//
// Every user-visible string goes through `t()`. Bundles live in `locales/` and are imported
// statically, so a language switch never waits on a network request.
//
// Adding a language: drop `locales/<code>.json` in beside the others, add it to `resources` and to
// `SUPPORTED_LANGUAGES`. A key missing from a bundle falls back to English rather than rendering the
// key itself.
import i18n from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';

import en from './locales/en.json';
import ko from './locales/ko.json';

export const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'ko', label: '한국어' },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code'];

/** Locale passed to Intl for number and date formatting. */
export function currentLocale(): string {
  return i18n.resolvedLanguage === 'ko' ? 'ko-KR' : 'en-US';
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { en: { translation: en }, ko: { translation: ko } },
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LANGUAGES.map((l) => l.code),
    // Treat `en-GB` and friends as `en` rather than falling through to the fallback.
    nonExplicitSupportedLngs: true,
    detection: {
      // An explicit choice wins over the browser's preference and is remembered per browser.
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: 'gshare.lang',
      caches: ['localStorage'],
    },
    interpolation: {
      // React escapes for us.
      escapeValue: false,
    },
  });

// Keep <html lang> in step with the active language, for screen readers and browser hyphenation.
const syncHtmlLang = (lng: string) => {
  document.documentElement.lang = lng.startsWith('ko') ? 'ko' : 'en';
};
syncHtmlLang(i18n.resolvedLanguage ?? 'en');
i18n.on('languageChanged', syncHtmlLang);

export default i18n;
