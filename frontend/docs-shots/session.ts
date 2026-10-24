import { expect, Page } from '@playwright/test';

const USER = process.env['FORGE_E2E_ADMIN_USER'] ?? 'docs-admin';
const PASSWORD = process.env['FORGE_E2E_ADMIN_PASSWORD'] ?? '';
export const DEMO_PROJECT = 'Forge Demo';

/** Names the screenshots must never show: left over by the automated test suites on the dev stack. */
const JUNK = /^(chat|emb|shared|mdl|alm|prv|prov|pmv|pp|mc)[a-z0-9]{4,}$|^(AC\d|Agents |Demo [0-9a-f]{6}|Lab$)/i;
const KEEP_USERS = new Set(['docs-admin', 'anna.builder', 'marco.user']);

/** Filters the lists returned by the API so that the pictures only show clean demo data. */
export async function scrub(page: Page): Promise<void> {
  await page.route(/\/api\/v1\/(admin\/|projects\/[^/]+\/)?(models|aliases|providers|users|projects)(\?[^/]*)?$/, async (route) => {
    let res;
    try {
      res = await route.fetch({ timeout: 120_000 });
    } catch {
      return route.continue().catch(() => undefined);
    }
    if (res.status() !== 200 || route.request().method() !== 'GET') return route.fulfill({ response: res });
    const body = await res.json();
    if (!Array.isArray(body)) return route.fulfill({ response: res });
    const url = route.request().url();
    const kept = body.filter((row: Record<string, unknown>) => {
      if (/\/admin\/users/.test(url)) return KEEP_USERS.has(String(row['username']));
      if (/\/admin\/projects/.test(url)) return row['name'] === DEMO_PROJECT;
      if (/\/api\/v1\/projects(\?|$)/.test(url)) return row['name'] === DEMO_PROJECT;
      return !JUNK.test(String(row['name'] ?? ''));
    });
    await route.fulfill({ response: res, json: kept });
  });
}

/** Signs in with the documentation account, in the given UI language, and selects the demo project. */
export async function open(page: Page, baseURL: string, locale: string): Promise<void> {
  // The UI language is remembered per browser (core/locale.ts).
  await page.addInitScript((l) => localStorage.setItem('forge.locale', l), locale);
  await scrub(page);
  await page.goto(`${baseURL}/login`);
  await page.locator('#login').fill(USER);
  await page.locator('#password').fill(PASSWORD);
  await page.locator('button[type=submit], button.ant-btn-primary').first().click();
  await expect(page.getByTestId('user-name')).toBeVisible();
  const me = await (await page.request.get('/api/v1/me')).json();
  const demo = me.projects.find((p: { name: string }) => p.name === DEMO_PROJECT);
  expect(demo, `project ${DEMO_PROJECT} missing: run scripts/docs-seed.py`).toBeTruthy();
  await page.evaluate((id) => localStorage.setItem('forge.project', id), demo.id);
  await page.goto('/');
  await expect(page.getByTestId('project-select')).toContainText(DEMO_PROJECT);
}

/** Draws numbered orange markers (and an outline) on the given elements, for the guided pictures. */
export async function mark(page: Page, selectors: string[]): Promise<void> {
  const boxes: { x: number; y: number; w: number; h: number }[] = [];
  for (const sel of selectors) {
    const box = await page.locator(sel).first().boundingBox({ timeout: 5000 });
    expect(box, `marker target ${sel} not found`).toBeTruthy();
    boxes.push({ x: box!.x, y: box!.y, w: box!.width, h: box!.height });
  }
  await page.evaluate((all) => {
    all.forEach((b, i) => {
      const outline = document.createElement('div');
      outline.className = 'docs-mark';
      outline.style.cssText = `position:fixed;z-index:99999;pointer-events:none;left:${b.x - 3}px;top:${b.y - 3}px;width:${b.w + 6}px;height:${b.h + 6}px;border:2px solid #e8590c;border-radius:6px;`;
      const badge = document.createElement('div');
      badge.className = 'docs-mark';
      badge.textContent = String(i + 1);
      badge.style.cssText = `position:fixed;z-index:100000;pointer-events:none;left:${b.x - 14}px;top:${b.y - 14}px;width:24px;height:24px;border-radius:50%;background:#e8590c;color:#fff;font:700 14px/24px sans-serif;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.4);`;
      document.body.append(outline, badge);
    });
  }, boxes);
}

const KINDS: Record<string, string> = {
  prompt: 'prompts',
  rag: 'rag',
  agent: 'agents',
  tool: 'tools',
  usecase: 'use-cases',
  collection: 'collections',
};

/** Replaces `{prompt:name}`-style tokens in a path with the id of the named object of the demo project. */
export async function resolve(page: Page, path: string): Promise<string> {
  const tokens = [...path.matchAll(/\{(\w+):([^}]+)\}/g)];
  if (!tokens.length) return path;
  const me = await (await page.request.get('/api/v1/me')).json();
  const pid = me.projects.find((p: { name: string }) => p.name === DEMO_PROJECT).id;
  let out = path;
  for (const [token, kind, name] of tokens) {
    const res = await page.request.get(`/api/v1/projects/${pid}/${KINDS[kind!]}`);
    const body = await res.json();
    const row = (Array.isArray(body) ? body : body.items).find((r: { name: string }) => r.name === name);
    expect(row, `${kind} ${name} not found in the demo project`).toBeTruthy();
    out = out.replace(token, row.id);
  }
  return out;
}
