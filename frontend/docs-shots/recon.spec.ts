import { test } from '@playwright/test';

import { open } from './session';

test('recon', async ({ page, baseURL }) => {
  await open(page, baseURL!, 'en');
  for (const path of (process.env['RECON'] ?? '/').split(',')) {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(500);
    const text = await page.locator('main.content').innerText();
    const controls = await page.locator('main.content button, main.content a, main.content [data-testid]').evaluateAll((els) =>
      els.map((e) => `${e.tagName.toLowerCase()}${e.getAttribute('data-testid') ? '#' + e.getAttribute('data-testid') : ''}: ${(e as HTMLElement).innerText.trim().slice(0, 40)}`).slice(0, 90));
    console.log(`=== ${path}\n${text.slice(0, 3000)}\n--- controls\n${controls.join('\n')}`);
  }
});
