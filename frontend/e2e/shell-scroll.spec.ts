import { expect, test } from '@playwright/test';
import { loginAsAdmin } from './support';
test('menu stays, content scrolls', async ({ page }) => {
  await loginAsAdmin(page);
  await page.goto('/guide/10-integrations-and-sync');
  await expect(page.getByTestId('guide-doc')).toContainText('MCP Studio');
  const r = await page.evaluate(() => {
    const c = document.querySelector('main.content') as HTMLElement;
    c.scrollTop = 800;
    return {
      doc: document.documentElement.scrollHeight - innerHeight,
      scrolled: c.scrollTop,
      side: document.querySelector('aside')!.getBoundingClientRect().top,
    };
  });
  expect(r.doc).toBeLessThanOrEqual(0);
  expect(r.scrolled).toBeGreaterThan(0);
  expect(r.side).toBe(0);
});
