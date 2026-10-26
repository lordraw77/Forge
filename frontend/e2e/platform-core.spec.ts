import { expect, test } from '@playwright/test';

import { adminApi, login, loginAsAdmin, totp, uniqueName } from './support';

/** M1.1 platform core in a real browser (US-1.2.x, US-1.1.x, US-1.4.x). */

test.describe('platform core (M1.1)', () => {
  const errors: string[] = [];

  test.beforeEach(async ({ page }) => {
    errors.length = 0;
    // HTTP error statuses are asserted through the UI (e.g. the pre-login 401 probe, policy 422s).
    page.on(
      'console',
      (m) =>
        m.type() === 'error' &&
        !m.text().startsWith('Failed to load resource') &&
        errors.push(m.text()),
    );
    page.on('pageerror', (e) => errors.push(e.message));
  });

  test('wrong password shows a generic error; admin signs in and out', async ({ page }) => {
    await login(page, 'nobody-' + uniqueName('x'), 'wrong-password');
    await expect(page.getByTestId('login-error')).toContainText('Invalid credentials');
    await loginAsAdmin(page, { fresh: true });
    await expect(page.locator('h1')).toContainText('Welcome');
    await page.getByTestId('profile-menu').click();
    await page.getByRole('menuitem', { name: 'Log out' }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto('/');
    await expect(page).toHaveURL(/\/login/);
    expect(errors).toEqual([]);
  });

  test('new user created in the console completes the first-login steps', async ({ page }) => {
    const username = uniqueName('user');
    await loginAsAdmin(page, { fresh: true });
    await page.goto('/admin/users');
    await page.getByTestId('new-user').click();
    // The dialog focuses its first field when the open animation ends; filling earlier races it.
    await expect(page.locator('#u-username')).toBeFocused();
    await page.locator('#u-username').fill(username);
    await page.locator('#u-email').fill(`${username}@example.com`);
    await page.locator('#u-name').fill('Mario Bianchi');
    await page.getByTestId('create-user').click();
    const notice = await page.getByTestId('notice').innerText();
    const temp = notice.split(': ').pop()!.trim();
    expect(temp).toMatch(/^[A-Za-z0-9]{20}$/);
    // The list shows the first 200 users by name and the dev database holds more: search for it.
    await page.getByPlaceholder('Search').fill(username);
    await expect(page.locator(`[data-user="${username}"]`)).toBeVisible();

    await page.getByTestId('profile-menu').click();
    await page.getByRole('menuitem', { name: 'Log out' }).click();
    await login(page, username, temp);
    await expect(page).toHaveURL(/\/account\/password/);
    await page.locator('#current').fill(temp);
    await page.locator('#next').fill('unbelievable');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('step-error')).toContainText('too common');
    await page.locator('#next').fill('Mario-Strong-Pass-2026');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page).toHaveURL(/\/account\/eula/);
    await expect(page.getByTestId('eula-text')).toBeVisible();
    await page.getByRole('button', { name: 'Accept' }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('h1')).toContainText('Welcome, Mario Bianchi');
    expect(errors).toEqual([]);
  });

  test('project admin sees the project, invites a member and manages roles', async ({
    page,
    baseURL,
  }) => {
    const api = await adminApi(baseURL!);
    const owner = uniqueName('owner');
    const guest = uniqueName('guest');
    const password = 'Owner-Strong-Pass-2026';
    const created: Record<string, string> = {};
    for (const name of [owner, guest]) {
      const res = await api.post('/api/v1/admin/users', {
        data: { username: name, email: `${name}@example.com`, full_name: name, password },
      });
      expect(res.status()).toBe(201);
      created[name] = (await res.json()).user.id;
    }
    const projectName = 'Project ' + uniqueName('p');
    const proj = await api.post('/api/v1/admin/projects', {
      data: { name: projectName, description: 'E2E', admin_user_id: created[owner] },
    });
    expect(proj.status()).toBe(201);
    await api.dispose();

    await login(page, owner, password);
    // Users created with a password still change it and accept the EULA at first login.
    await page.locator('#current').fill(password);
    await page.locator('#next').fill('Owner-Second-Pass-2026');
    await page.getByRole('button', { name: 'Save' }).click();
    await page.getByRole('button', { name: 'Accept' }).click();
    await expect(page.getByTestId('project-select')).toContainText(projectName);

    await page.goto('/projects/users');
    await expect(page.locator(`[data-member="${owner}"]`)).toBeVisible();
    await page.getByTestId('invite').click();
    await expect(page.locator('#inv-ids')).toBeFocused();
    await page.locator('#inv-ids').fill(`${guest}\nghost-${guest}`);
    await page.getByTestId('send-invite').click();
    await expect(page.getByTestId('notice')).toContainText('1 added');
    await expect(page.getByTestId('notice')).toContainText(`ghost-${guest}`);
    await expect(page.locator(`[data-member="${guest}"]`)).toBeVisible();

    await page.goto('/projects/info');
    await page.getByRole('button', { name: 'Create token' }).click();
    await expect(page.getByTestId('new-token')).toContainText('fk_');
    expect(errors).toEqual([]);
  });

  test('a user enables two-factor authentication and signs in with a code', async ({
    page,
    baseURL,
  }) => {
    const api = await adminApi(baseURL!);
    const name = uniqueName('mfa');
    const password = 'Mfa-Strong-Pass-2026';
    expect(
      (
        await api.post('/api/v1/admin/users', {
          data: { username: name, email: `${name}@example.com`, full_name: name, password },
        })
      ).status(),
    ).toBe(201);
    await api.dispose();

    await login(page, name, password);
    await page.locator('#current').fill(password);
    await page.locator('#next').fill('Mfa-Second-Pass-2026');
    await page.getByRole('button', { name: 'Save' }).click();
    await page.getByRole('button', { name: 'Accept' }).click();
    await expect(page.locator('h1')).toContainText('Welcome');

    await page.goto('/account/totp');
    await page.getByRole('button', { name: 'Set up' }).click();
    const secret = (await page.getByTestId('totp-secret').innerText()).trim();
    await expect(page.locator('img.qr')).toBeVisible();
    await page.locator('#code').fill(totp(secret));
    await page.getByRole('button', { name: 'Enable' }).click();
    await expect(page.getByTestId('recovery-codes').locator('li')).toHaveCount(10);
    await page.getByRole('button', { name: 'Continue' }).click();

    await page.getByTestId('profile-menu').click();
    await page.getByRole('menuitem', { name: 'Log out' }).click();
    await login(page, name, 'Mfa-Second-Pass-2026');
    await expect(page.locator('#code')).toBeVisible();
    // The enrolment code is consumed; the next time step gives a fresh one.
    await page.locator('#code').fill(totp(secret, 1));
    await page.getByRole('button', { name: 'Verify' }).click();
    await expect(page.locator('h1')).toContainText('Welcome');
    expect(errors).toEqual([]);
  });

  test('the interface switches to Italian and back', async ({ page }) => {
    await loginAsAdmin(page);
    await page.getByTestId('profile-menu').click();
    await page.getByRole('menuitem', { name: 'Italiano' }).click();
    await expect(page.locator('nav')).toContainText('Catalogo casi d');
    await expect(page.locator('h1')).toContainText('Benvenuto');
    await expect(page.locator('html')).toHaveAttribute('lang', 'it');
    await page.getByTestId('profile-menu').click();
    await page.getByRole('menuitem', { name: 'English' }).click();
    await expect(page.locator('nav')).toContainText('Use Cases Catalog');
  });
});
