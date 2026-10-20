import { expect, test } from '@playwright/test';

import { fillTemplate, loginAsAdmin, uniqueName } from './support';

/** M2.3 Build Your Own in a real browser: catalogs and access (US-2.4.x), the requirements page,
 * the workflow canvas with its node drawer, and the Monaco definition editor (US-2.8.x). No model
 * is reachable from the stack, so the Planner itself is covered by the backend tests. */
test.describe('M2.3 use cases', () => {
  const errors: string[] = [];

  test.beforeEach(async ({ page }) => {
    errors.length = 0;
    page.on(
      'console',
      (m) =>
        m.type() === 'error' &&
        !m.text().startsWith('Failed to load resource') &&
        errors.push(m.text()),
    );
    page.on('pageerror', (e) => errors.push(e.message));
  });

  test('catalogs, requirements, canvas and definition editor', async ({ page }) => {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const me = await (await page.request.get('/api/v1/me')).json();
    const project = await page.request.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('Cases '), admin_user_id: me.user.id },
    });
    expect(project.status()).toBe(201);
    await page.evaluate(
      (id) => localStorage.setItem('forge.project', id),
      (await project.json()).project.id,
    );
    await page.reload();

    // ---- Catalogs: built-ins, a new catalog with a category, access for a role
    await page.goto('/projects/catalogs');
    await expect(page.getByTestId('catalog-Forge.ITOps')).toBeVisible();
    const catalog = uniqueName('Team ');
    await page.getByTestId('new-catalog-name').fill(catalog);
    await page.getByTestId('add-catalog').click();
    const box = page.getByTestId(`catalog-${catalog}`);
    await expect(box).toBeVisible();
    await page.getByTestId(`new-category-${catalog}`).fill('Reports');
    await box.getByRole('button', { name: 'Add category' }).click();
    await expect(box.getByText('Reports')).toBeVisible();
    await page.getByTestId(`access-catalog-${catalog}`).click();
    await page.getByTestId('save-access').click();
    await expect(page.getByText('Access saved')).toBeVisible();

    // ---- Requirements → workflow with the canvas
    const name = uniqueName('Flow ');
    await page.goto('/build/new');
    await page.getByTestId('rq-name').fill(name);
    await page.getByTestId('rq-text').fill('Summarise the weekly sales report.');
    await page.getByTestId('rq-create').click();
    await expect(page).toHaveURL(/\/build\/[0-9a-f-]+\/workflow$/);
    const canvas = page.getByTestId('graph-canvas');
    await expect(canvas.locator('[data-node-id="start"]')).toBeVisible();
    await page.getByTestId('add-router').click();
    const router = canvas.locator('[data-node-id^="router_"]');
    await expect(router).toBeVisible();
    await router.locator('.title').dblclick();
    await expect(page.getByTestId('node-drawer')).toBeVisible();
    await page.getByTestId('add-section').click();
    await expect(canvas.locator('[data-node-id^="router_"]').getByText('path_2')).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByTestId('wf-save').click();
    await expect(page.getByText('Draft saved')).toBeVisible();
    // The server reports what is still wrong (the router depends on nothing).
    await expect(page.getByTestId('wf-issues')).toContainText('runs after nothing');

    // ---- Settings: Monaco edits the JSON definition
    await page.getByRole('link', { name: 'Settings' }).click();
    await expect(page.getByTestId('uc-definition').locator('.monaco-editor')).toBeVisible();
    await fillTemplate(page, 'uc-definition', '{"nodes": [{"id": "start", "type": "start"}]}');
    await page.getByTestId('uc-save').click();
    await expect(page.getByText('Draft saved')).toBeVisible();
    expect(errors).toEqual([]);
  });
});
