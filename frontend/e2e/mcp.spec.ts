import { expect, test } from '@playwright/test';

import { loginAsAdmin, pickOption, uniqueName } from './support';

/** E3.5 in a real browser: MCP Studio (expose, connect, try, pause, remove) and the user guide
 * shown inside the app. */
test.describe('MCP Studio and user guide', () => {
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

  test('expose a collection, connect, try it, pause and remove', async ({ page }) => {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const me = await (await page.request.get('/api/v1/me')).json();
    const project = await page.request.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('Mcp '), admin_user_id: me.user.id },
    });
    expect(project.status()).toBe(201);
    const projectId = (await project.json()).project.id;
    await page.evaluate((id) => localStorage.setItem('forge.project', id), projectId);
    const collection = uniqueName('Docs ');
    expect(
      (
        await page.request.post(`/api/v1/projects/${projectId}/collections`, {
          headers,
          data: { name: collection, description: '' },
        })
      ).status(),
    ).toBe(201);

    await page.goto('/mcp');
    await expect(page).toHaveURL(/\/mcp\/exposed$/);
    await expect(page.getByText('Nothing is exposed yet.')).toBeVisible();

    // ---- Expose
    const tool = uniqueName('search_').replace(/[^A-Za-z0-9_-]/g, '_');
    await page.getByTestId('mcp-add').click();
    await pickOption(page, 'mcp-kind', 'Data collection');
    await pickOption(page, 'mcp-asset', collection);
    await page.locator('#mcp-name').fill(tool);
    await page.locator('#mcp-desc').fill('Search the e2e documents');
    await page.getByTestId('mcp-save').click();
    const row = page.getByRole('row').filter({ hasText: tool });
    await expect(row).toContainText('Active');

    // ---- Connect
    await page.getByTestId('mcp-tab-connect').click();
    await expect(page.getByTestId('mcp-url')).toContainText(`/api/v1/projects/${projectId}/mcp`);
    await expect(page.getByTestId('mcp-snippet-code')).toHaveValue(/claude mcp add/);

    // ---- Try: the tool is listed and answers
    await page.getByTestId('mcp-tab-exposed').click();
    await page.getByRole('row').filter({ hasText: tool }).getByTestId('mcp-try').click();
    await expect(page).toHaveURL(/\/mcp\/try\?name=/);
    await expect(page.locator('#try-args')).toHaveValue(/query/);
    await page.locator('#try-args').fill('{"query": "refund", "top_k": 3}');
    await page.getByTestId('try-send').click();
    await expect(page.getByTestId('try-answer')).toBeVisible();

    // ---- Pause hides it from clients, Remove deletes it
    await page.getByTestId('mcp-tab-exposed').click();
    const again = page.getByRole('row').filter({ hasText: tool });
    await again.getByRole('button', { name: 'Pause' }).click();
    await expect(again).toContainText('Inactive');
    await page.getByTestId('mcp-tab-try').click();
    await expect(page.getByText('Nothing is exposed (or everything is paused)')).toBeVisible();
    await page.getByTestId('mcp-tab-exposed').click();
    await page
      .getByRole('row')
      .filter({ hasText: tool })
      .getByRole('button', { name: 'Remove' })
      .click();
    await expect(page.getByText('Nothing is exposed yet.')).toBeVisible();

    expect(errors).toEqual([]);
  });

  test('the user guide opens inside the app, in both languages', async ({ page }) => {
    await loginAsAdmin(page);
    await page.getByRole('link', { name: 'User guide' }).click();
    await expect(page).toHaveURL(/\/guide$/);
    await expect(page.getByTestId('guide-doc').getByRole('heading', { level: 1 })).toBeVisible();
    await page.getByTestId('guide-10-integrations-and-sync').click();
    await expect(page).toHaveURL(/\/guide\/10-integrations-and-sync$/);
    await expect(page.getByTestId('guide-doc')).toContainText('MCP Studio');
    // Figures are served with the application.
    const image = page.getByTestId('guide-doc').locator('img').first();
    await expect(image).toBeVisible();
    expect(await image.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(100);
    // The same chapter in Italian.
    await page.getByTestId('guide-lang').click();
    await expect(page).toHaveURL(/\/guide\/10-integrazioni-e-sincronizzazione$/);
    expect(errors).toEqual([]);
  });
});
