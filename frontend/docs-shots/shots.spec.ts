import { mkdirSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import { mark, open, resolve } from './session';
import { SHOTS } from './shots';

const OUT = process.env['DOCS_IMAGES_DIR'] ?? '../docs/guide/images';
const ONLY = process.env['SHOTS'] ? new RegExp(process.env['SHOTS']) : null;

test('user guide screenshots', async ({ page, baseURL }, info) => {
  const locale = info.project.name;
  const dir = `${OUT}/${locale}`;
  mkdirSync(dir, { recursive: true });
  const todo = SHOTS.filter((s) => !ONLY || ONLY.test(s.name));

  const take = async (shot: (typeof SHOTS)[number]) => {
    if (shot.path) await page.goto(await resolve(page, shot.path));
    await page.waitForLoadState('networkidle');
    if (shot.ready) await page.locator(shot.ready).first().waitFor();
    if (shot.before) await shot.before(page);
    // Monaco editors lay themselves out after the page is idle: let them finish.
    if (await page.locator('forge-monaco').count()) {
      await page.locator('.monaco-editor .view-lines').first().waitFor();
      await page.waitForTimeout(2500);
    }
    await page.waitForTimeout(500);
    const file = `${dir}/${shot.name}.png`;
    const target = shot.clip ? page.locator(shot.clip).first() : null;
    await target?.scrollIntoViewIfNeeded({ timeout: 8000 });
    // A dialog taller than the window (long forms) needs a taller window to be pictured whole.
    let resized = false;
    if (target && /modal|drawer/.test(shot.clip!)) {
      const tall = (await target.boundingBox())?.height ?? 0;
      if (tall > 820) {
        await page.setViewportSize({ width: 1440, height: Math.ceil(tall) + 160 });
        await page.waitForTimeout(500);
        resized = true;
      }
    }
    if (shot.marks) await mark(page, shot.marks);
    if (target && !shot.marks) {
      await target.screenshot({ path: file });
    } else if (target) {
      const box = (await target.boundingBox())!;
      const pad = 14;
      await page.screenshot({
        path: file,
        clip: {
          x: Math.max(0, box.x - pad),
          y: Math.max(0, box.y - pad),
          width: Math.min(box.width + 2 * pad, 1440),
          height: Math.min(box.height + 2 * pad, 900),
        },
      });
    } else await page.screenshot({ path: file, fullPage: shot.fullPage ?? false });
    await page.evaluate(() => document.querySelectorAll('.docs-mark').forEach((e) => e.remove()));
    if (resized) await page.setViewportSize({ width: 1440, height: 900 });
  };

  // Pictures of the login page come first, then the session of the documentation account starts.
  await page.addInitScript((l) => localStorage.setItem('forge.locale', l), locale);
  for (const shot of todo.filter((s) => s.anonymous)) await take(shot);
  await page.context().clearCookies();
  await open(page, baseURL!, locale);
  for (const shot of todo.filter((s) => !s.anonymous)) await take(shot);
  expect(todo.length).toBeGreaterThan(0);
});
