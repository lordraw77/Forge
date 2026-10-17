import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { providePrimeNG } from 'primeng/config';

import { provideApi } from './api-client';
import { routes } from './app.routes';
import { csrfInterceptor, refreshInterceptor } from './core/http';
import { ForgePreset } from './core/theme';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withFetch(), withInterceptors([csrfInterceptor, refreshInterceptor])),
    provideApi(''),
    providePrimeNG({
      theme: { preset: ForgePreset, options: { darkModeSelector: '.forge-dark', cssLayer: false } },
    }),
  ],
};
