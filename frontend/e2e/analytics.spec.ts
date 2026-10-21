import { expect, test } from '@playwright/test';

import { loginAsAdmin, pickOption, uniqueName } from './support';

/** M2.6 in a real browser: the Analytics page and its activity log, scheduled sources, Model
 * Insights, the Email settings and the home page's recent activity. */
test.describe('M2.6 analytics and sources', () => {
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

  test('dashboards, activity log, sources, model insights and email', async ({ page }) => {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const me = await (await page.request.get('/api/v1/me')).json();
    const project = await page.request.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('Insight '), admin_user_id: me.user.id },
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
    await page.reload();

    // ---- Analytics: tiles, charts, and the activity log with the project's own actions
    await page.goto('/analytics');
    await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
    await expect(page.getByText('Model calls')).toBeVisible();
    await expect(page.getByText('Guardrail interventions').first()).toBeVisible();
    await expect(page.getByText('Activity log (kept 180 days)')).toBeVisible();
    await page.getByPlaceholder('Search action, name, user').fill('collection');
    await page.getByPlaceholder('Search action, name, user').press('Enter');
    await expect(page.getByRole('cell', { name: /collection\.create/ }).first()).toBeVisible();

    // ---- Sources: create one (manual), see it listed, delete it keeping nothing behind
    await page.goto('/projects/sources');
    await page.getByTestId('source-new').click();
    const name = uniqueName('src');
    await page.locator('#src-name').fill(name);
    await pickOption(page, 'src-collection', collection);
    await page.getByTestId('source-save').click();
    const row = page.getByRole('row').filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(row).toContainText('Manual');
    await row.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('button', { name: 'Keep documents' }).click();
    await expect(row).toHaveCount(0);

    // ---- Model Insights (Super Admin console)
    await page.goto('/admin/insights');
    await expect(page.getByRole('heading', { name: 'Model Insights' })).toBeVisible();
    await expect(page.getByText('Calls over time (errors in red)')).toBeVisible();

    // ---- Email: save a server, the test card appears; clear it again
    await page.goto('/admin/email');
    await page.getByTestId('smtp-host').fill('smtp.example.test');
    await page.getByTestId('smtp-save').click();
    await expect(page.getByText('Send a test message')).toBeVisible();
    await page.getByTestId('smtp-host').fill('');
    await page.getByTestId('smtp-save').click();

    // ---- Home keeps working with the activity list
    await page.goto('/');
    await expect(page.getByText('Recent activity', { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });
});
