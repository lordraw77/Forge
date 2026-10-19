import { mkdirSync } from 'node:fs';

import { request } from '@playwright/test';

import { ADMIN, ADMIN_STATE } from './support';

/** One API login for the e2e Super Admin, shared by every test that only needs to be signed
 * in: the stack allows 20 logins per minute per IP, and the whole suite runs from one IP. */
export default async function globalSetup(): Promise<void> {
  if (!ADMIN.password) return;
  const ctx = await request.newContext({
    baseURL: process.env['FORGE_E2E_BASE_URL'] ?? 'http://localhost:8080',
  });
  const res = await ctx.post('/api/v1/auth/login', {
    data: { login: ADMIN.username, password: ADMIN.password },
  });
  if (res.status() !== 200) throw new Error(`e2e admin login failed: ${res.status()}`);
  mkdirSync('e2e/.auth', { recursive: true });
  await ctx.storageState({ path: ADMIN_STATE });
  await ctx.dispose();
}
