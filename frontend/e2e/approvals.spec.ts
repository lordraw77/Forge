import { expect, test } from '@playwright/test';

import { loginAsAdmin, uniqueName } from './support';

/** M2.4 in a real browser: the Approvals inbox and the public page of an email link. No model is
 * reachable from the stack, so pausing and resuming a run is covered by the backend tests. */
test.describe('M2.4 approvals', () => {
  test('the inbox is empty and a dead link says so, without signing in', async ({
    page,
    browser,
  }) => {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const me = await (await page.request.get('/api/v1/me')).json();
    const project = await page.request.post('/api/v1/admin/projects', {
      headers: { 'X-CSRF-Token': csrf ?? '' },
      data: { name: uniqueName('Approvals '), admin_user_id: me.user.id },
    });
    expect(project.status()).toBe(201);
    await page.evaluate(
      (id) => localStorage.setItem('forge.project', id),
      (await project.json()).project.id,
    );

    await page.goto('/approvals');
    await expect(page.getByRole('heading', { name: 'Approvals' })).toBeVisible();
    await expect(page.getByTestId('approvals-empty')).toBeVisible();

    // The public page needs no session: a fresh browser context, an unknown token.
    const anonymous = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    const visitor = await anonymous.newPage();
    await visitor.goto(`/approve/fap_${'0'.repeat(32)}_nothing`);
    await expect(visitor.getByTestId('link-gone')).toBeVisible();
    await expect(visitor).toHaveURL(/\/approve\//);
    await anonymous.close();
  });
});
