import { expect, test } from '@playwright/test';

import { loginAsAdmin, uniqueName } from './support';

/** M1.4 Data Collections and RAG Studio in a real browser (US-1.14.1, US-1.14.2, US-1.17.1,
 * US-1.17.2). No model provider is reachable from the stack in CI: built-in parsing succeeds,
 * then embedding fails, so the failure paths (file status, error, Retry, failed run with a
 * trace) are what the browser checks. Answers with citations are covered by test_rag.py. */

test.describe('US-1.17.1 data collections and RAG studio (M1.4)', () => {
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

  test('builder uploads files, builds a RAG pipeline, tests and publishes it', async ({ page }) => {
    test.setTimeout(180_000);
    // One login (per-IP login rate limit): the e2e admin is Project Admin of a fresh project.
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const api = page.request;
    const me = await (await api.get('/api/v1/me')).json();
    const project = await api.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('RAG '), admin_user_id: me.user.id },
    });
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
    const providerId = (await prov.json()).id;
    const chat = uniqueName('chat');
    const embed = uniqueName('emb');
    for (const body of [
      { name: chat, model_type: 'chat', model_id: 'unreachable' },
      { name: embed, model_type: 'embedding', model_id: 'unreachable', embedding_dims: 8 },
    ]) {
      const r = await api.post('/api/v1/admin/models', {
        headers,
        data: {
          ...body,
          display_name: body.name,
          provider_id: providerId,
          max_retries: 0,
          timeout_s: 5,
          enabled_project_ids: [projectId],
        },
      });
      expect(r.status()).toBe(201);
    }
    await page.evaluate((id) => localStorage.setItem('forge.project', id), projectId);
    await page.reload();

    // ---- Data Collections (US-1.14.1, US-1.14.2)
    const collection = uniqueName('Manuals ');
    await page.goto('/projects/data');
    await page.getByTestId('new-collection').click();
    await page.getByTestId('collection-name-input').fill(collection);
    await page.getByTestId('collection-create').click();
    await expect(page.getByTestId('collection-name')).toHaveText(collection);

    const manual = {
      name: 'manual.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from('# Manual\n\n## Backups\n\nNightly backups run at 02:00.\n'),
    };
    const binary = {
      name: 'tool.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from([0x4d, 0x5a, 0x90, 0x00, 0x03, 0x00, 0x00, 0x00, 0xff, 0x00]),
    };
    await page.getByTestId('upload-files').setInputFiles([manual, binary]);
    await expect(page.getByTestId('upload-summary')).toContainText(
      '1 accepted, 0 unchanged, 1 rejected',
    );
    await expect(page.getByTestId('upload-summary')).toContainText('tool.pdf');
    await expect(page.getByTestId('status-manual.md')).toHaveText('Success', { timeout: 30_000 });
    // The same content again is recognised and not re-processed.
    await page.getByTestId('upload-files').setInputFiles([manual]);
    await expect(page.getByTestId('upload-summary')).toContainText('0 accepted, 1 unchanged');

    // ---- RAG Studio: configure, Save Configuration & Test (US-1.17.1)
    await page.goto('/rag/new');
    await page.getByTestId('rag-name').click();
    const ragName = uniqueName('Ops answers ');
    await page.getByTestId('rag-name-input').fill(ragName);
    await page.getByTestId('rag-name-input').press('Enter');
    await page.locator('nz-select', { has: page.locator('#re-source') }).click();
    await page.locator('nz-option-item').filter({ hasText: collection }).first().click();
    await page.locator('nz-select', { has: page.locator('#re-llm') }).click();
    await page.locator('nz-option-item').filter({ hasText: chat }).first().click();
    await page.locator('nz-select', { has: page.locator('#re-emb') }).click();
    await page.locator('nz-option-item').filter({ hasText: embed }).first().click();
    await page.getByTestId('rag-save-test').click();
    await page.getByTestId('rag-description').fill('Answers about operations');
    await page.getByTestId('rag-description-save').click();
    await expect(page).toHaveURL(/\/rag\/[0-9a-f-]+\/test$/);

    await page.getByTestId('rag-query').fill('When do backups run?');
    await page.getByTestId('rag-proceed').click();
    await expect(page.getByTestId('rag-result')).toContainText('Failed');
    await expect(page.getByTestId('rag-result')).toContainText('upstream_unavailable');
    await page.getByTestId('rag-trace-link').click();
    await expect(page.getByTestId('rag-trace-metadata')).toContainText('"status": "error"');
    await page.keyboard.press('Escape');

    await page.getByTestId('rag-publish').click();
    await expect(page.getByTestId('rag-test-message')).toContainText('published');

    // ---- The new index cannot embed: the file shows Failed, why, and Retry (US-1.14.1 #4)
    await page.goto('/projects/data');
    await page.locator(`[data-collection="${collection}"] a`).click();
    await expect(page.getByTestId('status-manual.md')).toHaveText('Failed', { timeout: 120_000 });
    await expect(page.getByTestId('error-manual.md')).toContainText('Indexing failed');
    await expect(page.getByTestId('retry-manual.md')).toBeVisible();

    // ---- RAG Library (US-1.17.2)
    await page.goto('/rag/library');
    await page.getByTestId('rag-search').fill(ragName);
    await expect(page.getByTestId(`rag-row-${ragName}`)).toContainText('Published');
    await expect(page.getByTestId(`rag-row-${ragName}`)).toContainText(collection);
    expect(errors).toEqual([]);
  });
});
