import { expect, test } from '@playwright/test';

import { fillTemplate, loginAsAdmin, pickOption, uniqueName } from './support';

/** M1.3 Prompt Studio in a real browser (US-1.10.1, US-1.10.2, US-1.11.1, US-1.11.2).
 * No provider is reachable from the stack in CI, so the test run is checked on its failure
 * path: the run is still recorded and its trace shows the rendered input. */

test.describe('US-1.11.2 prompt lifecycle (M1.3)', () => {
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

  test('builder writes, tests, publishes, duplicates and deletes a prompt', async ({ page }) => {
    // One login only: the suite shares the per-IP login rate limit. The e2e Super Admin is
    // made Project Admin of a fresh project and drives the API through the browser session.
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const api = page.request;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const me = await (await api.get('/api/v1/me')).json();
    const project = await api.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('Prompts '), admin_user_id: me.user.id },
    });
    expect(project.status()).toBe(201);
    const projectId = (await project.json()).project.id;
    const prov = await api.post('/api/v1/admin/providers', {
      headers,
      data: {
        name: uniqueName('p'),
        provider_type: 'openai_compatible',
        base_url: 'http://127.0.0.1:9/v1',
        config: { allow_http: true },
      },
    });
    const modelName = uniqueName('chat');
    const created = await api.post('/api/v1/admin/models', {
      headers,
      data: {
        name: modelName,
        display_name: 'Prompt model',
        provider_id: (await prov.json()).id,
        model_type: 'chat',
        model_id: 'unreachable',
        max_retries: 0,
        timeout_s: 5,
        enabled_project_ids: [projectId],
      },
    });
    expect(created.status()).toBe(201);
    // Work in the new project (the selection is stored per browser).
    await page.evaluate((id) => localStorage.setItem('forge.project', id), projectId);
    await page.reload();

    // ---- New Prompt: inline name, model, `{name}` creates a variable (US-1.10.1)
    const name = uniqueName('Support ');
    await page.goto('/prompts/new');
    await page.getByTestId('prompt-name').click();
    await page.getByTestId('prompt-name-input').fill(name);
    await page.getByTestId('prompt-name-input').press('Enter');
    await pickOption(page, 'pe-model', new RegExp(modelName)); // the list is virtual-scrolled: search for it
    await fillTemplate(page, 'prompt-system', 'You are a polite support agent.');
    await fillTemplate(page, 'prompt-user', 'Answer {customer} about {topic}');
    await expect(page.getByTestId('variable-customer')).toBeVisible();
    await expect(page.getByTestId('variable-topic')).toBeVisible();
    await page.getByTestId('variable-topic').locator('input').nth(1).fill('billing');

    // Save & Test: the description is asked on first save.
    await page.getByTestId('save-test').click();
    await page.getByTestId('prompt-description').fill('Answers customer questions');
    await page.getByTestId('description-save').click();
    await expect(page).toHaveURL(/\/prompts\/[0-9a-f-]+\/test$/);

    // ---- Test page: required variables gate Proceed (US-1.11.1)
    await expect(page.getByTestId('test-var-topic')).toHaveValue('billing');
    await expect(page.getByTestId('proceed')).toBeDisabled();
    await page.getByTestId('test-var-customer').fill('Ada');
    await expect(page.getByTestId('proceed')).toBeEnabled();
    await page.getByTestId('proceed').click();
    await expect(page.getByTestId('result')).toContainText('upstream_unavailable');
    await expect(page.getByTestId('result-stats')).toContainText('ms');
    await page.getByTestId('trace-link').click();
    await expect(page.getByTestId('trace-input')).toContainText('Answer Ada about billing');
    await expect(page.getByTestId('trace-metadata')).toContainText(modelName);
    await page.keyboard.press('Escape');

    await page.getByTestId('publish').click();
    await expect(page.getByTestId('test-message')).toContainText('published');

    // ---- Edit: a second save is a new version; v1 stays as it was (US-1.10.2)
    await page.getByRole('link', { name: 'Edit' }).click();
    await fillTemplate(page, 'prompt-user', 'Reply to {customer}: {topic}');
    await page.getByTestId('save-draft').click();
    await expect(page.getByTestId('editor-message')).toContainText('version 2');
    await page.getByTestId('open-versions').click();
    await page.getByRole('button', { name: 'v1', exact: true }).click();
    await expect(page.getByTestId('version-view')).toContainText('Answer {customer} about {topic}');
    await expect(page.getByTestId('version-view')).toContainText('prod');
    await page.keyboard.press('Escape');

    // ---- Library: filters and row actions (US-1.11.2)
    await page.getByTestId('tab-library').click();
    await page.getByTestId('prompt-search').fill(name);
    const row = page.getByTestId(`prompt-row-${name}`);
    await expect(row).toContainText('Published');
    await expect(row).toContainText('published v1');
    await page.locator('nz-select', { has: page.locator('#prompt-status') }).click();
    await page.locator('nz-option-item[title="Draft"]').click();
    await expect(row).toHaveCount(0);
    await page.locator('nz-select', { has: page.locator('#prompt-status') }).click();
    await page.locator('nz-option-item[title="All statuses"]').click();

    await page.getByTestId(`prompt-actions-${name}`).click();
    await page.getByTestId('action-duplicate').click();
    const copy = `${name} copy`;
    await page.getByTestId('duplicate-name').fill(copy);
    await page.getByTestId('duplicate-confirm').click();
    const copyRow = page.getByTestId(`prompt-row-${copy}`);
    await expect(copyRow).toContainText('Draft');

    await page.getByTestId(`prompt-actions-${copy}`).click();
    await page.getByTestId('action-delete').click();
    await page.getByTestId('delete-yes').click();
    await expect(copyRow).toHaveCount(0);
    await expect(row).toBeVisible();
    expect(errors).toEqual([]);
  });
});
