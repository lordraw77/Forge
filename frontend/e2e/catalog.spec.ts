import { expect, test } from '@playwright/test';

import { loginAsAdmin, uniqueName } from './support';

/** M2.3d in a real browser: the end-user catalog (cards, search, favorites), the generated input
 * form, a run with its progress page and history, and re-run. No model is reachable from the
 * stack, so the run is checked on its failure path; outputs, files and the delivery modes are
 * covered by the backend tests. */
test.describe('M2.3d catalog and runs', () => {
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

  test('find a use case, favorite it, run it and run it again', async ({ page }) => {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const api = page.request;
    const me = await (await api.get('/api/v1/me')).json();
    const project = await api.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('Catalog '), admin_user_id: me.user.id },
    });
    expect(project.status()).toBe(201);
    const projectId = (await project.json()).project.id;
    const base = `/api/v1/projects/${projectId}`;
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
    const model = await api.post('/api/v1/admin/models', {
      headers,
      data: {
        name: modelName,
        display_name: 'Catalog model',
        provider_id: (await prov.json()).id,
        model_type: 'chat',
        model_id: 'unreachable',
        max_retries: 0,
        timeout_s: 5,
        enabled_project_ids: [projectId],
      },
    });
    expect(model.status()).toBe(201);

    // A published prompt, then a published use case that uses it.
    const promptName = uniqueName('Say ');
    const prompt = await api.post(`${base}/prompts`, {
      headers,
      data: {
        name: promptName,
        description: 'says it',
        version: {
          model: modelName,
          system_template: 'Answer briefly.',
          user_template: '{topic}',
          variables: [{ name: 'topic', value: '', required: false }],
        },
      },
    });
    expect(prompt.status()).toBe(201);
    const published = await api.post(`${base}/prompts/${(await prompt.json()).id}/publish`, {
      headers,
      data: { version: 1 },
    });
    expect(published.ok()).toBeTruthy();
    const catalogs = await (await api.get(`${base}/catalogs`)).json();
    const catalog = catalogs.find((c: { name: string }) => c.name === 'Forge.BizOps');
    const category = await api.post(`${base}/catalogs/${catalog.id}/categories`, {
      headers,
      data: { name: uniqueName('Reports ') },
    });
    expect(category.status()).toBe(201);
    const categoryRow = (await category.json()).categories.at(-1);
    const useCaseName = uniqueName('Weekly digest ');
    const created = await api.post(`${base}/use-cases`, {
      headers,
      data: {
        name: useCaseName,
        description: 'Summarises the week in a few lines',
        tags: ['reports'],
        pattern: 'graph',
        catalog_id: catalog.id,
        category_id: categoryRow.id,
        input_fields: [
          { name: 'topic', label: 'Topic', type: 'text', required: true },
          { name: 'notes', label: 'Notes', type: 'long_text' },
        ],
        definition: {
          nodes: [
            { id: 'start', type: 'start' },
            {
              id: 'say',
              type: 'llm',
              after: ['start'],
              prompt_ref: `${promptName}@1`,
              variables: { topic: '{topic}' },
              output: 'said',
            },
            { id: 'end', type: 'end', after: ['say'], output: '{said}' },
          ],
        },
      },
    });
    expect(created.status()).toBe(201);
    const useCaseId = (await created.json()).id;
    const publish = await api.post(`${base}/use-cases/${useCaseId}/publish`, {
      headers,
      data: {},
    });
    expect(publish.ok(), await publish.text()).toBeTruthy();
    await page.evaluate((id) => localStorage.setItem('forge.project', id), projectId);

    // ---- Catalog: the card, search, filters and favorites
    await page.goto('/catalog');
    const card = page.getByTestId(`uc-card-${useCaseName}`);
    await expect(card).toBeVisible();
    await expect(card).toContainText('Summarises the week');
    await page.getByTestId('catalog-search').fill('nothing like this');
    await expect(card).toHaveCount(0);
    await page.getByTestId('catalog-search').fill(useCaseName.slice(0, 12));
    await expect(card).toBeVisible();
    await page.getByTestId(`fav-${useCaseName}`).click();
    await page.getByTestId('catalog-views').getByText('Favorites').click();
    await expect(card).toBeVisible();
    await page.getByTestId('catalog-views').getByText('Recent').click();
    await expect(card).toHaveCount(0);
    await page.getByTestId('catalog-views').getByText('All').click();

    // ---- Detail page: the form comes from the published version; nothing of the workflow shows
    await card.click();
    await expect(page).toHaveURL(new RegExp(`/catalog/${useCaseId}$`));
    await expect(page.getByTestId('uc-title')).toContainText(useCaseName);
    await expect(page.getByText(promptName)).toHaveCount(0);
    await expect(page.getByTestId('uc-history')).toContainText(
      'You have not run this use case yet',
    );
    await page.getByLabel('Topic').fill('quarterly sales');
    await page.getByTestId('uc-run').click();

    // ---- Run page: progress, then the failure the unreachable model causes
    await expect(page).toHaveURL(/\/catalog\/runs\/[0-9a-f-]+$/);
    await expect(page.getByTestId('run-status')).toHaveText(/Failed/, { timeout: 60_000 });
    await expect(page.getByTestId('run-failure')).toBeVisible();
    await expect(page.getByTestId('run-timeline')).toContainText('say');

    // ---- Run again: same inputs, same session, a new run
    const first = page.url();
    await page.getByTestId('run-again').click();
    await expect(page).not.toHaveURL(first);
    await expect(page.getByTestId('run-session')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('run-status')).toHaveText(/Failed/, { timeout: 60_000 });

    // ---- History
    await page.goto('/catalog/runs');
    await expect(page.getByTestId('runs-table').getByText(useCaseName)).toHaveCount(2);
    await page.goto(`/catalog/${useCaseId}`);
    await expect(page.getByTestId('uc-history')).toContainText('v1');

    expect(errors).toEqual([]);
  });
});
