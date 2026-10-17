/** UI language (US-1.4.3): English (source) and Italian, chosen in the profile, remembered per browser. */
export type Locale = 'en' | 'it';
const KEY = 'forge.locale';

export function currentLocale(): Locale {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === 'en' || stored === 'it') return stored;
  } catch {
    // storage unavailable: fall back to the browser language
  }
  return navigator.language?.toLowerCase().startsWith('it') ? 'it' : 'en';
}

export function setLocale(locale: Locale): void {
  try {
    localStorage.setItem(KEY, locale);
  } catch {
    // ignore: the reload below still applies the browser default
  }
  location.reload();
}
