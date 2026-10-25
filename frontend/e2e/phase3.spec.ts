import { expect, test } from '@playwright/test';

import { loginAsAdmin, pickOption, uniqueName } from './support';

/** Phase 3 screens in a real browser: Super Admin → Secrets vault and Email, Governance →
 * Evaluations, use case Triggers (E3.9), Projects Studio → Integrations (widgets, E3.5) with the
 * public widget script, and the memory panel of the agent chat (E3.6). No model is reachable from
 * the stack in CI, so what needs a model answer is covered by the backend tests. */
test.describe('Phase 3 screens', () => {
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

  async function newProject(page: import('@playwright/test').Page, prefix: string) {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const me = await (await page.request.get('/api/v1/me')).json();
    const project = await page.request.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName(prefix), admin_user_id: me.user.id },
    });
    expect(project.status()).toBe(201);
    const projectId = (await project.json()).project.id as string;
    await page.evaluate((id) => localStorage.setItem('forge.project', id), projectId);
    await page.reload();
    return { headers, projectId };
  }

  test('admin pages: secrets vault and email render and validate', async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/admin/vault');
    await expect(page.getByTestId('vault-current')).toBeVisible();
    await expect(page.getByTestId('vault-save')).toBeVisible();
    await page.goto('/admin/email');
    await expect(page.getByTestId('smtp-host')).toBeVisible();
    await expect(page.getByTestId('smtp-save')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('evaluations: dataset create, edit, delete and the run form', async ({ page }) => {
    await newProject(page, 'Evals ');
    await page.goto('/governance/evaluations');
    await expect(page.getByText('No datasets yet.')).toBeVisible();

    const name = uniqueName('Refunds ');
    await page.getByTestId('ds-new').click();
    await page.locator('#ds-name').fill(name);
    await page
      .getByTestId('ds-cases')
      .fill(
        '[{"input": {"question": "How long do I have to return an item?"}, "expected": "30 days"},' +
          ' {"input": {"question": "Who pays return shipping?"}, "expected": "the customer"}]',
      );
    await page.getByTestId('ds-save').click();
    const row = page.getByTestId(`ds-${name}`);
    await expect(row).toContainText('prompt');
    await expect(row).toContainText('2');

    // The run form lists the dataset and shows the evaluator template.
    await expect(page.getByTestId('ev-evaluators')).not.toHaveValue('');
    await expect(page.getByTestId('ev-start')).toBeVisible();
    await expect(page.getByTestId('ev-runs')).toBeVisible();

    // Edit keeps the cases.
    await row.getByRole('button', { name: 'Edit' }).click();
    await expect(page.getByTestId('ds-cases')).toHaveValue(/return shipping/);
    await page.getByRole('button', { name: 'Cancel' }).click();

    await row.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('button', { name: 'Delete', exact: true }).last().click();
    await expect(page.getByText('No datasets yet.')).toBeVisible();
    expect(errors).toEqual([]);
  });

  test('triggers: schedule, inbound URL, pause, rotate and delete', async ({ page }) => {
    const { headers, projectId } = await newProject(page, 'Triggers ');
    const created = await page.request.post(`/api/v1/projects/${projectId}/use-cases`, {
      headers,
      data: {
        name: uniqueName('Weekly report '),
        description: 'Weekly sales summary',
        pattern: 'graph',
      },
    });
    expect(created.status()).toBe(201);
    const useCaseId = (await created.json()).id as string;

    await page.goto(`/build/${useCaseId}/triggers`);
    await expect(page.getByText('No triggers yet.')).toBeVisible();

    // ---- Cron
    await page.getByTestId('trigger-new').click();
    await page.locator('#trg-name').fill('Monday report');
    await pickOption(page, 'trg-kind', 'Cron schedule');
    await page.locator('#trg-cron').fill('0 8 * * 1');
    await page.locator('#trg-tz').fill('Europe/Rome');
    await page.getByTestId('trigger-save').click();
    const cron = page.getByRole('row').filter({ hasText: 'Monday report' });
    await expect(cron).toContainText('0 8 * * 1');
    await expect(cron).toContainText('Europe/Rome');
    await expect(cron).toContainText(/\d+\/\d+\/\d+/); // a next run is computed

    // ---- Inbound URL: shown once
    await page.getByTestId('trigger-new').click();
    await page.locator('#trg-name').fill('CRM hook');
    await pickOption(page, 'trg-kind', /Inbound URL/);
    await page.getByTestId('trigger-save').click();
    await expect(page.getByTestId('trigger-url')).toHaveValue(
      /\/api\/v1\/triggers\/[A-Za-z0-9_-]{20,}/,
    );
    await page.getByRole('button', { name: 'Done' }).click();

    // ---- Pause and resume, rotate, delete
    await cron.getByRole('button', { name: 'Pause' }).click();
    await expect(cron).toContainText('Paused');
    await cron.getByRole('button', { name: 'Resume' }).click();
    await expect(cron).not.toContainText('Paused');
    const hook = page.getByRole('row').filter({ hasText: 'CRM hook' });
    await hook.getByRole('button', { name: 'New token' }).click();
    await expect(page.getByTestId('trigger-url')).toBeVisible();
    await page.getByRole('button', { name: 'Done' }).click();
    await hook.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('button', { name: 'Delete', exact: true }).last().click();
    await expect(page.getByRole('row').filter({ hasText: 'CRM hook' })).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('widget: create in Integrations, embed code, public script and origin check', async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const { headers, projectId } = await newProject(page, 'Widget ');
    const api = page.request;
    const prov = await api.post('/api/v1/admin/providers', {
      headers,
      data: {
        name: uniqueName('p'),
        provider_type: 'openai_compatible',
        base_url: 'http://127.0.0.1:9/v1',
        config: { allow_http: true },
      },
    });
    const chat = uniqueName('chat');
    expect(
      (
        await api.post('/api/v1/admin/models', {
          headers,
          data: {
            name: chat,
            display_name: chat,
            model_type: 'chat',
            model_id: 'unreachable',
            provider_id: (await prov.json()).id,
            capabilities: ['streaming', 'tools'],
            max_retries: 0,
            timeout_s: 5,
            enabled_project_ids: [projectId],
          },
        })
      ).status(),
    ).toBe(201);
    const promptName = uniqueName('Instructions ');
    expect(
      (
        await api.post(`/api/v1/projects/${projectId}/prompts`, {
          headers,
          data: {
            name: promptName,
            description: 'agent instructions',
            version: { model: chat, system_template: 'You answer questions about returns.' },
          },
        })
      ).status(),
    ).toBe(201);

    await page.goto('/agents/new');
    await page.getByTestId('agent-name').click();
    const agentName = uniqueName('Returns bot ');
    await page.getByTestId('agent-name-input').fill(agentName);
    await page.getByTestId('agent-name-input').press('Enter');
    await pickOption(page, 'ae-prompt', promptName);
    await pickOption(page, 'ae-model', new RegExp(chat));
    await page.getByTestId('agent-save-test').click();
    await page.getByTestId('agent-description').fill('Answers questions about returns');
    await page.getByTestId('agent-description-save').click();
    await expect(page).toHaveURL(/\/agents\/chat\?agent=[0-9a-f-]+$/);
    const agentId = new URL(page.url()).searchParams.get('agent')!;
    expect(
      (
        await api.post(`/api/v1/projects/${projectId}/agents/${agentId}/publish`, {
          headers,
          data: {},
        })
      ).status(),
    ).toBe(200);

    // ---- Chat memory panel (E3.6): present, empty for a new agent
    await page.goto(`/agents/chat?agent=${agentId}`);
    await page.getByTestId('chat-memory').locator('summary').click();
    await expect(page.getByTestId('chat-memory')).toContainText('Nothing yet');

    // ---- New widget
    const origin = new URL(page.url()).origin;
    await page.goto('/projects/integrations');
    await expect(page.getByText('No widgets yet.')).toBeVisible();
    await page.getByTestId('widget-new').click();
    await page.locator('#wd-name').fill('Help bubble');
    await pickOption(page, 'wd-agent', agentName);
    await page.locator('#wd-title').fill('Returns help');
    await page.locator('#wd-welcome').fill('Ask me about returns.');
    await page.locator('#wd-origins').fill(origin);
    await page.getByTestId('widget-save').click();
    const row = page.getByRole('row').filter({ hasText: 'Help bubble' });
    await expect(row).toContainText(agentName);
    await expect(row).toContainText(origin);

    // Creating a widget opens its embed code at once.
    const snippet = await page.getByTestId('widget-snippet').inputValue();
    expect(snippet).toContain('/api/v1/widget/widget.js');
    const key = /data-key="([^"]+)"/.exec(snippet)![1];
    await page.getByRole('button', { name: 'Done' }).click();

    // ---- Public side: no session needed. An allowed origin gets the config, another one not.
    const anon = await (await import('@playwright/test')).request.newContext({ baseURL: origin });
    const js = await anon.get('/api/v1/widget/widget.js');
    expect(js.status()).toBe(200);
    expect(js.headers()['content-type']).toContain('javascript');
    const ok = await anon.get(`/api/v1/widget/${key}/config`, { headers: { Origin: origin } });
    expect(ok.status()).toBe(200);
    expect(await ok.json()).toMatchObject({
      title: 'Returns help',
      welcome: 'Ask me about returns.',
    });
    const denied = await anon.get(`/api/v1/widget/${key}/config`, {
      headers: { Origin: 'https://evil.example.com' },
    });
    expect(denied.status()).toBeGreaterThanOrEqual(400);
    await anon.dispose();

    // ---- The script runs in a page served from the allowed origin and adds its bubble
    await page.route(`${origin}/widget-demo`, (route) =>
      route.fulfill({
        contentType: 'text/html',
        body: `<!doctype html><title>demo</title><h1>Intranet</h1>${snippet}`,
      }),
    );
    await page.goto('/widget-demo');
    await expect(page.locator('body > div').last()).toBeAttached();
    const config = page.waitForResponse((r) => r.url().includes(`/widget/${key}/config`));
    await page.reload();
    expect((await config).status()).toBe(200);

    // ---- Delete
    await page.goto('/projects/integrations');
    page.once('dialog', (d) => void d.accept());
    await page
      .getByRole('row')
      .filter({ hasText: 'Help bubble' })
      .getByRole('button', { name: 'Delete' })
      .click();
    await expect(page.getByText('No widgets yet.')).toBeVisible();
    expect(errors).toEqual([]);
  });
});
