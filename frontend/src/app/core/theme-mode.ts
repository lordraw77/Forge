import { signal } from '@angular/core';

/** Light, dark, or following the operating system (the default). */
export type ThemeMode = 'light' | 'dark' | 'system';

const KEY = 'forge.theme';
const LINK_ID = 'forge-dark-theme';
const query =
  typeof window !== 'undefined' ? window.matchMedia?.('(prefers-color-scheme: dark)') : null;

function stored(): ThemeMode {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

export const themeMode = signal<ThemeMode>(stored());

function resolved(mode: ThemeMode): 'light' | 'dark' {
  return mode === 'system' ? (query?.matches ? 'dark' : 'light') : mode;
}

/** Apply the theme: `data-theme` on <html> for Forge's tokens, and NG-ZORRO's dark stylesheet
 * (bundle `theme-dark.css`, built from src/themes/dark.less) loaded only when needed. */
export function applyTheme(mode: ThemeMode = themeMode()): void {
  const dark = resolved(mode) === 'dark';
  document.documentElement.dataset['theme'] = dark ? 'dark' : 'light';
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  const existing = document.getElementById(LINK_ID);
  if (dark && !existing) {
    const link = document.createElement('link');
    link.id = LINK_ID;
    link.rel = 'stylesheet';
    link.href = 'theme-dark.css';
    document.head.appendChild(link);
  } else if (!dark && existing) {
    existing.remove();
  }
}

export function setThemeMode(mode: ThemeMode): void {
  themeMode.set(mode);
  try {
    if (mode === 'system') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, mode);
  } catch {
    // storage unavailable: the choice lasts for this tab
  }
  applyTheme(mode);
}

/** Follow the operating system while in "system" mode. */
query?.addEventListener?.('change', () => {
  if (themeMode() === 'system') applyTheme('system');
});
