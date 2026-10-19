import { expect, test } from '@playwright/test';

import { loginAsAdmin, pickOption, uniqueName } from './support';

/** M1.5 Agentic AI Studio and chat in a real browser (US-1.18.1, US-1.19.1, US-1.19.2,
 * US-1.20.1). No model provider is reachable from the stack in CI, so the run fails on its first
 * model call: the browser checks the studio (create, publish, library), the chat (message, stream
 * end, failure reason, feedback) and the run view (steps, status). Answers, tool calls,
 * citations, limits, stop and crash recovery are covered by test_agents.py. */

test.describe('US-1.18.1 agent studio, chat and run view (M1.5)', () => {
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

  test('builder defines, publishes and chats with an agent; the run view shows why it failed', async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const api = page.request;
    const me = await (await api.get('/api/v1/me')).json();
    const project = await api.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('Agents '), admin_user_id: me.user.id },
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
    const model = await api.post('/api/v1/admin/models', {
      headers,
      data: {
        name: chat,
        display_name: chat,
        model_type: 'chat',
        model_id: 'unreachable',
        provider_id: providerId,
        capabilities: ['streaming', 'tools'],
        max_retries: 0,
        timeout_s: 5,
        enabled_project_ids: [projectId],
      },
    });
    expect(model.status()).toBe(201);
    const promptName = uniqueName('Instructions ');
    const prompt = await api.post(`/api/v1/projects/${projectId}/prompts`, {
      headers,
      data: {
        name: promptName,
        description: 'agent instructions',
        version: { model: chat, system_template: 'You are a helpful operations assistant.' },
      },
    });
    expect(prompt.status()).toBe(201);
    await page.evaluate((id) => localStorage.setItem('forge.project', id), projectId);
    await page.reload();

    // ---- New Agent: Save Configuration & Test Agent (US-1.18.1)
    await page.goto('/agents/new');
    await page.getByTestId('agent-name').click();
    const agentName = uniqueName('Helper ');
    await page.getByTestId('agent-name-input').fill(agentName);
    await page.getByTestId('agent-name-input').press('Enter');
    await pickOption(page, 'ae-prompt', promptName);
    await pickOption(page, 'ae-model', new RegExp(chat));
    await page.getByTestId('agent-tool-time').click();
    await page.getByTestId('agent-max-steps').locator('input').fill('4');
    await page.getByTestId('agent-save-test').click();
    await page.getByTestId('agent-description').fill('Answers questions about operations');
    await page.getByTestId('agent-description-save').click();
    await expect(page).toHaveURL(/\/agents\/chat\?agent=[0-9a-f-]+$/);

    // ---- Chat (US-1.19.1): the run fails on the first model call, and says why
    await page.getByTestId('chat-input').fill('What time is it in Rome?');
    await page.getByTestId('chat-send').click();
    await expect(page.getByTestId('chat-user')).toContainText('What time is it in Rome?');
    await expect(page.getByTestId('chat-failure')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('chat-send')).toBeVisible(); // no longer busy

    // ---- Feedback (US-1.19.2)
    await page.getByTestId('chat-thumbs-down').click();
    await page.getByTestId('chat-comment').fill('The model was unreachable');
    await page.getByTestId('chat-comment').press('Enter');
    await page.getByTestId('chat-thumbs-up').click();
    await expect(page.getByTestId('chat-comment')).toBeVisible();

    // ---- Run view (US-1.20.1)
    await page.getByTestId('chat-run-link').click();
    await expect(page.getByTestId('run-view')).toBeVisible();
    await expect(page.getByTestId('run-status')).toContainText('Failed');
    await expect(page.getByTestId('run-failure')).toContainText('upstream_unavailable');
    await expect(page.getByTestId('run-steps').locator('tbody tr')).toHaveCount(1);
    await expect(page.getByTestId('run-feedback')).toBeVisible();
    await expect(page.getByTestId('run-totals')).toContainText('Steps');

    // ---- Runs list
    await page.goto('/agents/runs');
    await expect(page.getByTestId('run-row').first()).toContainText('What time is it in Rome?');
    await expect(page.getByTestId('run-row').first()).toContainText('Failed');

    // ---- Publish and Library (US-1.18.1, library pattern)
    await page.goto('/agents/library');
    await page.getByTestId('agent-search').fill(agentName);
    await expect(page.getByTestId(`agent-row-${agentName}`)).toContainText('Draft');
    await page.getByTestId(`agent-row-${agentName}`).getByRole('link', { name: agentName }).click();
    await expect(page.getByTestId('agent-status-tag')).toContainText('Draft');
    await page.getByTestId('agent-publish').click();
    await expect(page.getByTestId('agent-message')).toContainText('Published version 1');
    await expect(page.getByTestId('agent-status-tag')).toContainText('Published v1');
    await page.goto('/agents/library');
    await page.getByTestId('agent-search').fill(agentName);
    await expect(page.getByTestId(`agent-row-${agentName}`)).toContainText('Published');

    // ---- Duplicate and delete
    await page.getByTestId(`agent-actions-${agentName}`).click();
    await page.getByTestId('action-duplicate').click();
    await page.getByTestId('duplicate-name').fill(`${agentName} copy`);
    await page.getByTestId('duplicate-confirm').click();
    await expect(page.getByTestId('agent-library-message')).toContainText('Created');
    await page.getByTestId('agent-search').fill(`${agentName} copy`);
    await page.getByTestId(`agent-actions-${agentName} copy`).click();
    await page.getByTestId('action-delete').click();
    await page.getByTestId('delete-yes').click();
    await expect(page.getByTestId('agent-library-message')).toContainText('Deleted');
    expect(errors).toEqual([]);
  });
});
