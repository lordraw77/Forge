import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { en_US, it_IT, provideNzI18n } from 'ng-zorro-antd/i18n';
import { provideNzNativeDateAdapter } from 'ng-zorro-antd/core/time';
import { provideNzIcons } from 'ng-zorro-antd/icon';

import { provideApi } from './api-client';
import { routes } from './app.routes';
import { csrfInterceptor, refreshInterceptor } from './core/http';
import { currentLocale } from './core/locale';
import { FORGE_ICONS } from './core/theme';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withFetch(), withInterceptors([csrfInterceptor, refreshInterceptor])),
    provideApi(''),
    provideNzIcons(FORGE_ICONS),
    // Texts inside NG-ZORRO components (empty tables, modal buttons) follow the UI language.
    provideNzI18n(currentLocale() === 'it' ? it_IT : en_US),
    // NG-ZORRO 22 date pickers need an explicit date adapter: native Date + Intl, UI language.
    provideNzNativeDateAdapter(
      currentLocale() === 'it'
        ? { locale: 'it-IT', firstDayOfWeek: 1 }
        : { locale: 'en-US', firstDayOfWeek: 0 },
    ),
  ],
};
