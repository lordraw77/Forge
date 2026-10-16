import { defineConfig, devices } from '@playwright/test';

// E2E against a running stack (`make docker/run`); run with `make test/e2e`.
export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.results',
  timeout: 60_000,
  retries: process.env['CI'] ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: process.env['FORGE_E2E_BASE_URL'] ?? 'http://localhost:8080',
    viewport: { width: 1600, height: 1000 },
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 } } }],
});
