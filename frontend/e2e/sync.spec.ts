import { mkdirSync, writeFileSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import { loginAsAdmin, uniqueName } from './support';

/** E3.3 in a real browser: everything `forgectl` does is on the Sync page — the project as a zip
 * of files, a comparison with a bundle, a preview and an apply that sends only the missing
 * versions, and a key for the command line. */
test.describe('E3.3 sync with files', () => {
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

  test('download, compare, preview, apply and a key for the command line', async ({
    page,
  }, testInfo) => {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const api = page.request;
    const me = await (await api.get('/api/v1/me')).json();
    const project = await api.post('/api/v1/admin/projects', {
      headers,
      data: { name: uniqueName('Sync '), admin_user_id: me.user.id },
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
    expect(
      (
        await api.post('/api/v1/admin/models', {
          headers,
          data: {
            name: modelName,
            display_name: 'Sync model',
            provider_id: (await prov.json()).id,
            model_type: 'chat',
            model_id: 'unreachable',
            max_retries: 0,
            timeout_s: 5,
            enabled_project_ids: [projectId],
          },
        })
      ).status(),
    ).toBe(201);
    const promptName = uniqueName('Style ');
    const base = `/api/v1/projects/${projectId}`;
    const created = await api.post(`${base}/prompts`, {
      headers,
      data: {
        name: promptName,
        description: 'answer style',
        version: { model: modelName, system_template: 'Answer briefly.' },
      },
    });
    expect(created.status()).toBe(201);
    await page.evaluate((id) => localStorage.setItem('forge.project', id), projectId);
    await page.reload();

    await page.goto('/projects/sync');

    // ---- the project as a zip
    const download = page.waitForEvent('download');
    await page.getByTestId('sync-download').click();
    expect((await download).suggestedFilename()).toMatch(/^forge-project-.*\.zip$/);
    await expect(page.getByTestId('sync-notice')).toContainText('zip was downloaded');

    // ---- a bundle with one more version than the project has
    const exported = await api.post(`${base}/export`, {
      headers,
      data: { prompts: [{ id: (await created.json()).id, versions: 'all' }] },
    });
    const bundle = await exported.json();
    const item = bundle.items.find((i: { name: string }) => i.name === promptName);
    item.versions.push({ ...item.versions.at(-1), system_template: 'Answer in one line.' });
    const file = testInfo.outputPath('bundle.json');
    writeFileSync(file, JSON.stringify({ ...bundle, items: [item] }));
    await page.getByTestId('sync-zip').setInputFiles(file);

    const row = page.locator(`[data-plan="${promptName}"]`);
    await expect(row).toContainText('changed');
    await expect(row).toContainText('1');

    // ---- preview writes nothing, apply sends one version, then nothing is left to send
    await page.getByTestId('sync-preview').click();
    await expect(page.getByTestId('sync-report')).toContainText('Preview');
    expect(
      (await (await api.get(`${base}/prompts`)).json()).items.find(
        (p: { name: string }) => p.name === promptName,
      ).latest_version,
    ).toBe(1);
    await page.getByTestId('sync-apply').click();
    await expect(page.getByTestId('sync-notice')).toContainText('Applied');
    await expect(page.locator(`[data-plan="${promptName}"]`)).toContainText('unchanged');
    expect(
      (await (await api.get(`${base}/prompts`)).json()).items.find(
        (p: { name: string }) => p.name === promptName,
      ).latest_version,
    ).toBe(2);
    await expect(page.getByTestId('sync-apply')).toBeDisabled();

    // ---- a picked folder in the Git layout (the server reads the paths, whatever the folder is called)
    const dir = testInfo.outputPath('my-repo');
    mkdirSync(`${dir}/prompts`, { recursive: true });
    writeFileSync(
      `${dir}/forge.json`,
      JSON.stringify({ format: 'forge.bundle', schema_version: bundle.schema_version, source: {} }),
    );
    const edited = {
      ...item,
      versions: [...item.versions, { ...item.versions.at(-1), user_template: 'Three.' }],
    };
    writeFileSync(`${dir}/prompts/${promptName.replace(/ /g, '-')}.json`, JSON.stringify(edited));
    await page.getByTestId('sync-folder').setInputFiles(dir);
    await expect(page.locator(`[data-plan="${promptName}"]`)).toContainText('changed');

    // ---- a key for the command line
    await page.getByTestId('sync-key').click();
    await expect(page.getByTestId('sync-cli')).toContainText('FORGE_API_KEY=fk_');
    expect(errors).toEqual([]);
  });
});
