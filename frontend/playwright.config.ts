import { defineConfig, devices } from '@playwright/test';

// E2E against a running stack (`make docker/run`); run with `make test/e2e`.
export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.results',
  globalSetup: './e2e/global-setup.ts',
  timeout: 60_000,
  retries: process.env['CI'] ? 1 : 0,
  // One worker: the specs share one stack, one admin session and a 5 s default expectation, so
  // parallel files made them flaky on small hosts (CI included).
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env['FORGE_E2E_BASE_URL'] ?? 'http://localhost:8080',
    viewport: { width: 1600, height: 1000 },
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 } },
    },
  ],
});
