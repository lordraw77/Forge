import { createHmac } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';

import { APIRequestContext, expect, Locator, Page, request } from '@playwright/test';

type Cookie = Parameters<ReturnType<Page['context']>['addCookies']>[0][number];

export const ADMIN = {
  username: process.env['FORGE_E2E_ADMIN_USER'] ?? 'e2e-admin',
  password: process.env['FORGE_E2E_ADMIN_PASSWORD'] ?? '',
};

/** Session of the e2e admin saved by global-setup.ts. */
export const ADMIN_STATE = 'e2e/.auth/admin.json';

export function uniqueName(prefix: string): string {
  return `${prefix}${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
}

export async function login(page: Page, username: string, password: string): Promise<void> {
  await page.goto('/login');
  await page.locator('#login').fill(username);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

/**
 * Sign the page in as the e2e Super Admin. By default the session saved by global-setup.ts is
 * reused (no login request); `fresh` signs in through the login page, for tests that log out.
 */
export async function loginAsAdmin(page: Page, { fresh = false } = {}): Promise<void> {
  expect(ADMIN.password, 'FORGE_E2E_ADMIN_PASSWORD must be set (make test/e2e)').not.toBe('');
  if (fresh || !existsSync(ADMIN_STATE)) {
    await login(page, ADMIN.username, ADMIN.password);
  } else {
    const state = JSON.parse(readFileSync(ADMIN_STATE, 'utf8')) as { cookies: Cookie[] };
    await page.context().addCookies(state.cookies);
    await page.goto('/');
  }
  await expect(page.getByTestId('user-name')).toBeVisible();
}

/** API client authenticated as the e2e Super Admin (session cookies + CSRF header). */
export async function adminApi(baseURL: string): Promise<APIRequestContext> {
  const ctx = await request.newContext({ baseURL });
  const res = await ctx.post('/api/v1/auth/login', {
    data: { login: ADMIN.username, password: ADMIN.password },
  });
  expect(res.status()).toBe(200);
  const state = await ctx.storageState();
  await ctx.dispose();
  const csrf = state.cookies.find((c) => c.name === 'forge_csrf')?.value ?? '';
  return request.newContext({
    baseURL,
    storageState: state,
    extraHTTPHeaders: { 'X-CSRF-Token': csrf },
  });
}

/** RFC 6238 TOTP (SHA-1, 30 s, 6 digits) for enrolling and signing in from tests. */
export function totp(secretBase32: string, offsetSteps = 0): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of secretBase32.replace(/=+$/, '').toUpperCase()) {
    bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
  }
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Math.floor(Date.now() / 30_000) + offsetSteps;
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const hmac = createHmac('sha1', key).update(msg).digest();
  const offset = hmac[hmac.length - 1] & 0xf;
  const code = (hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, '0');
}

/**
 * Open an NG-ZORRO select (by its nzId), type `search` to filter it (its list is virtual-scrolled,
 * so far options are not in the DOM) and pick the option whose label matches `label`.
 */
export async function pickOption(
  page: Page,
  selectId: string,
  label: string | RegExp,
  search = typeof label === 'string' ? label : '',
): Promise<void> {
  await page.locator('nz-select', { has: page.locator(`#${selectId}`) }).click();
  if (search) await page.locator(`#${selectId}`).pressSequentially(search);
  // Option items carry their label in `title`; a regex is matched against the visible text.
  const option: Locator =
    typeof label === 'string'
      ? page.locator(`nz-option-item[title="${label.replace(/"/g, '\\"')}"]`)
      : page.locator('nz-option-item').filter({ hasText: label });
  await option.first().click();
}

/** Types into a Monaco template editor (it is not a form field, so `fill` does not apply). */
export async function fillTemplate(
  page: import('@playwright/test').Page,
  testId: string,
  text: string,
): Promise<void> {
  const editor = page.getByTestId(testId).locator('.monaco-editor').first();
  await editor.click();
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.press('Delete');
  await page.keyboard.type(text);
}
