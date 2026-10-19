import { loadTranslations } from '@angular/localize';

import { currentLocale } from './app/core/locale';
import { applyTheme } from './app/core/theme-mode';

// Translations must be loaded before any module that calls $localize at import time
// (menu, labels), so the application is imported dynamically afterwards.
async function start(): Promise<void> {
  applyTheme(); // before the first render: no flash of the light theme
  const locale = currentLocale();
  document.documentElement.lang = locale;
  if (locale !== 'en') {
    try {
      const res = await fetch(`/i18n/${locale}.json`);
      const data = (await res.json()) as { translations: Record<string, string> };
      loadTranslations(data.translations);
    } catch (error) {
      console.error('translations unavailable, falling back to English', error);
    }
  }
  const { bootstrap } = await import('./bootstrap');
  await bootstrap();
}

start().catch((err: unknown) => console.error(err));
