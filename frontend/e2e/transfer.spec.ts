import { readFileSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import { loginAsAdmin, uniqueName } from './support';

/** Export/Import of assets between projects, and the dark theme, in a real browser. */

test.describe('bundles and theme (M1.4+)', () => {
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

  test('a prompt is exported from one project and imported into another', async ({ page }) => {
    await loginAsAdmin(page);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'forge_csrf')?.value;
    const headers = { 'X-CSRF-Token': csrf ?? '' };
    const api = page.request;
    const me = await (await api.get('/api/v1/me')).json();
    const mk = async (prefix: string) =>
      (
        await (
          await api.post('/api/v1/admin/projects', {
            headers,
            data: { name: uniqueName(prefix), admin_user_id: me.user.id },
          })
        ).json()
      ).project.id as string;
    const source = await mk('Src ');
    const target = await mk('Dst ');
    const provider = await (
      await api.post('/api/v1/admin/providers', {
        headers,
        data: {
          name: uniqueName('p'),
          provider_type: 'openai_compatible',
          base_url: 'http://127.0.0.1:9/v1',
          config: { allow_http: true },
        },
      })
    ).json();
    const model = uniqueName('chat');
    await api.post('/api/v1/admin/models', {
      headers,
      data: {
        name: model,
        display_name: model,
        provider_id: provider.id,
        model_type: 'chat',
        model_id: 'x',
        all_projects: true,
      },
    });
    const name = uniqueName('Greeter ');
    const created = await api.post(`/api/v1/projects/${source}/prompts`, {
      headers,
      data: {
        name,
        description: 'greets',
        version: { model, user_template: 'Hello {who}' },
      },
    });
    expect(created.status()).toBe(201);
    await api.post(`/api/v1/projects/${source}/prompts/${(await created.json()).id}/versions`, {
      headers,
      data: { model, user_template: 'Hello again {who}' },
    });

    // ---- export from the source project
    await page.evaluate((id) => localStorage.setItem('forge.project', id), source);
    await page.goto('/projects/transfer');
    await page.locator('nz-select', { has: page.locator('#ex-prompts') }).click();
    await page.locator('nz-option-item').filter({ hasText: name }).first().click();
    await page.keyboard.press('Escape');
    const download = page.waitForEvent('download');
    await page.getByTestId('export-run').click();
    const file = await (await download).path();
    const bundle = JSON.parse(readFileSync(file, 'utf8'));
    expect(bundle.format).toBe('forge.bundle');
    expect(bundle.items[0].versions).toHaveLength(2);

    // ---- import into the target project: preview first, then apply
    await page.evaluate((id) => localStorage.setItem('forge.project', id), target);
    await page.goto('/projects/transfer');
    await page.getByTestId('import-file').setInputFiles(file);
    await expect(page.getByTestId('import-report')).toContainText('Preview');
    await expect(page.locator(`[data-import="${name}"]`)).toContainText('created');
    await page.getByTestId('import-apply').click();
    await expect(page.getByTestId('bundle-notice')).toContainText('Imported');
    const list = await (await api.get(`/api/v1/projects/${target}/prompts`)).json();
    expect(list.items.map((p: { name: string }) => p.name)).toContain(name);
    expect(errors).toEqual([]);
  });

  test('the dark theme can be chosen, persists and loads the dark stylesheet', async ({ page }) => {
    await loginAsAdmin(page);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    // The menu cycles Light -> Dark -> System; click until the theme is dark.
    for (
      let i = 0;
      i < 3 && (await page.locator('html').getAttribute('data-theme')) !== 'dark';
      i++
    ) {
      await page.getByTestId('profile-menu').click();
      await page.getByRole('menuitem', { name: /Theme:/ }).click();
      // The dropdown closes after a choice; wait for it before opening it again.
      await expect(page.getByRole('menuitem', { name: /Theme:/ })).toHaveCount(0);
    }
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.locator('link#forge-dark-theme')).toHaveCount(1);
    const background = await page
      .locator('body')
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(background).toBe('rgb(20, 20, 20)');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    expect(errors).toEqual([]);
  });
});
