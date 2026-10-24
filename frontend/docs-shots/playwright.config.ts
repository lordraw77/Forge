import { defineConfig, devices } from '@playwright/test';

// Screenshots for the user guide (docs/guide), one run per UI language; see scripts/docs-screenshots.sh.
export default defineConfig({
  testDir: '.',
  outputDir: './.results',
  timeout: 1_500_000,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env['FORGE_E2E_BASE_URL'] ?? 'http://localhost:8080',
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
    actionTimeout: 30_000,
  },
  projects: [
    { name: 'en', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, locale: 'en-US' } },
    { name: 'it', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, locale: 'it-IT' } },
  ],
});
