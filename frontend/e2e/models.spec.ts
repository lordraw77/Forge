import { expect, test } from '@playwright/test';

import { adminApi, login, loginAsAdmin, uniqueName } from './support';

/** M1.2 model screens in a real browser (US-1.9.1, US-1.5.2, US-1.5.3). No provider is
 * reachable from the stack in CI, so Test Connection is checked on its failure path. */

test.describe('model registry (M1.2)', () => {
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

  test('super admin adds a provider and a model, tests it and builds an alias', async ({
    page,
  }) => {
    const provider = uniqueName('prov');
    const model = uniqueName('mdl');
    const alias = uniqueName('al');
    await loginAsAdmin(page);
    await page.goto('/admin/models');

    await page.getByTestId('new-provider').click();
    await expect(page.locator('#pv-name')).toBeFocused();
    await page.locator('#pv-name').fill(provider);
    await page.locator('#pv-url').fill('http://127.0.0.1:9/v1');
    await page.locator('#pv-key').fill('sk-e2e-secret');
    await page.getByTestId('save-provider').click();
    // Plain HTTP is refused until it is allowed explicitly (threat model TB3).
    await expect(page.getByRole('dialog')).toContainText('allow_http');
    await page.locator('#pv-http').click();
    await page.getByTestId('save-provider').click();
    const row = page.locator(`[data-provider="${provider}"]`);
    await expect(row).toContainText('Stored');
    await expect(page.getByText('sk-e2e-secret')).toHaveCount(0);

    await page.getByTestId('new-model').click();
    await page.locator('#md-provider').click();
    await page.getByRole('option', { name: provider, exact: true }).click();
    await page.locator('#md-name').fill(model);
    await page.locator('#md-mid').fill('tiny-model');
    await page.getByTestId('test-connection').click();
    // Nothing listens on port 9: the provider error is shown, the draft stays open.
    await expect(page.getByTestId('test-result')).toBeVisible();
    await expect(page.getByTestId('test-result')).toHaveClass(/error/);
    await page.getByTestId('save-model').click();
    await expect(page.locator(`[data-model="${model}"]`)).toContainText('tiny-model');

    await page.getByTestId('new-alias').click();
    await page.locator('#al-name').fill(alias);
    await page.locator('#al-pick').click();
    await page.getByRole('option', { name: new RegExp(`^${model} `) }).click();
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByTestId('save-alias').click();
    await expect(page.locator(`[data-alias="${alias}"]`)).toContainText(model);

    // A model used by an alias cannot be deleted; the blocking alias is named.
    await page.locator(`[data-model="${model}"]`).getByRole('button', { name: 'Delete' }).click();
    await page.getByTestId('confirm-delete').click();
    await expect(page.getByTestId('registry-notice')).toContainText(alias);
    expect(errors).toEqual([]);
  });

  test('project admin sees enabled platform models and sets project limits', async ({
    page,
    baseURL,
  }) => {
    const api = await adminApi(baseURL!);
    const owner = uniqueName('owner');
    const password = 'Owner-Strong-Pass-2026';
    const user = await api.post('/api/v1/admin/users', {
      data: { username: owner, email: `${owner}@example.com`, full_name: 'Paola Rossi', password },
    });
    expect(user.status()).toBe(201);
    const ownerId = (await user.json()).user.id;
    const project = await api.post('/api/v1/admin/projects', {
      data: { name: uniqueName('Models '), admin_user_id: ownerId },
    });
    const projectId = (await project.json()).project.id;
    const prov = await api.post('/api/v1/admin/providers', {
      data: {
        name: uniqueName('p'),
        provider_type: 'openai_compatible',
        base_url: 'http://127.0.0.1:9/v1',
        config: { allow_http: true },
      },
    });
    const modelName = uniqueName('shared');
    const created = await api.post('/api/v1/admin/models', {
      data: {
        name: modelName,
        display_name: 'Shared model',
        provider_id: (await prov.json()).id,
        model_type: 'chat',
        model_id: 'shared',
        enabled_project_ids: [projectId],
      },
    });
    expect(created.status()).toBe(201);
    // First login of the new owner: password change and EULA come first.
    await login(page, owner, password);
    await expect(page).toHaveURL(/\/account\/password/);
    await page.locator('#current').fill(password);
    await page.locator('#next').fill('Owner-Newer-Pass-2026');
    await page.getByRole('button', { name: 'Save' }).click();
    await page.getByRole('button', { name: 'Accept' }).click();
    await expect(page.locator('h1')).toContainText('Welcome');

    await page.goto('/projects/models');
    await expect(page.locator(`[data-model="${modelName}"]`)).toContainText('Platform');
    await expect(page.getByTestId('usage')).toBeVisible();
    await page.locator('#lim-tokens_per_day').fill('50000');
    await page.getByTestId('save-limits').click();
    await expect(page.getByTestId('limits')).toContainText('50000 tokens/day');
    expect(errors).toEqual([]);
  });
});
