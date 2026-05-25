import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import de from "./locales/de.json";
import fr from "./locales/fr.json";

/** The languages the UI offers. `code` is what we persist + pass to i18next. */
export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
  { code: "fr", label: "Français" },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];

// Namespaced like the app's other localStorage prefs (sidebar-collapsed,
// lambda-erp:known-chat-sessions). Read/written with try/catch so quota or
// private-mode errors never break rendering.
const STORAGE_KEY = "lambda-erp:language";

function isSupported(code: string | null): code is LanguageCode {
  return !!code && SUPPORTED_LANGUAGES.some((l) => l.code === code);
}

export function getStoredLanguage(): LanguageCode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (isSupported(v)) return v;
  } catch {
    // ignore (private mode / unavailable storage)
  }
  return "en";
}

function storeLanguage(code: string) {
  try {
    localStorage.setItem(STORAGE_KEY, code);
  } catch {
    // ignore storage errors (quota / private mode)
  }
}

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    de: { translation: de },
    fr: { translation: fr },
  },
  lng: getStoredLanguage(),
  fallbackLng: "en", // any key missing in de/fr falls back to the English value
  interpolation: { escapeValue: false }, // React already escapes
  react: { useSuspense: false }, // resources are bundled synchronously — no Suspense needed
});

// Persist on every change, including programmatic changeLanguage() calls.
i18n.on("languageChanged", storeLanguage);

export default i18n;
