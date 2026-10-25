import { mkdirSync } from 'node:fs';

import { expect, Page, test } from '@playwright/test';

import { login } from './support';

/** Walks through the demo project (scripts/demo/seed_meridiana.py, `make demo/seed`) as its
 * Project Admin and checks that each studio shows what the seed created. It only runs when
 * FORGE_E2E_DEMO_PASSWORD is set, because the demo is not part of a fresh install; pictures of
 * every page land in e2e/.results/demo. */
const PASSWORD = process.env['FORGE_E2E_DEMO_PASSWORD'] ?? '';
const PROJECT = 'Meridiana Assicurazioni - Sinistri e Assistenza Clienti';

test.describe('demo project Meridiana', () => {
  test.skip(!PASSWORD, 'set FORGE_E2E_DEMO_PASSWORD to check the demo project');
  test.describe.configure({ mode: 'serial' });

  const errors: string[] = [];
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    mkdirSync('e2e/.results/demo', { recursive: true });
    page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on(
      'console',
      (m) =>
        m.type() === 'error' &&
        !m.text().startsWith('Failed to load resource') &&
        errors.push(m.text()),
    );
    page.on('pageerror', (e) => errors.push(e.message));
    await login(page, 'demo-admin', PASSWORD);
    await expect(page.getByTestId('user-name')).toBeVisible();
    const projects = await (await page.request.get('/api/v1/projects')).json();
    const list = Array.isArray(projects) ? projects : (projects.items ?? projects.projects);
    const id = list.find((p: { name: string }) => p.name === PROJECT)?.id;
    expect(id, 'run scripts/demo/seed_meridiana.py first').toBeTruthy();
    await page.evaluate((v) => localStorage.setItem('forge.project', v), id);
  });

  test.afterAll(async () => {
    await page.close();
  });

  async function visit(path: string, shot: string, ...texts: (string | RegExp)[]): Promise<void> {
    await page.goto(path);
    for (const t of texts) await expect(page.getByText(t).first()).toBeVisible();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `e2e/.results/demo/${shot}.png` });
  }

  test('home and knowledge', async () => {
    await visit('/', '01-home', /Meridiana/);
    await visit('/projects/info', '02-project', 'Meridiana');
    await visit('/projects/users', '03-users', 'chiara.moretti', 'giulia.ferraris');
    await visit('/projects/groups', '04-groups', 'Liquidatori Auto', 'Contact Center');
    await visit('/projects/data', '05-data', 'Condizioni di polizza e procedure sinistri');
    await visit('/rag/library', '06-rag', 'Ricerca condizioni e procedure');
  });

  test('prompts, tools, agents', async () => {
    await visit('/prompts/library', '07-prompts', 'Classificazione denuncia di sinistro', 'Bozza di risposta al cliente');
    await visit('/tools/library', '08-tools', 'meteo_storico', 'geocodifica_localita');
    await visit('/agents/library', '09-agents', 'Assistente liquidatori', 'Assistente portale clienti');
    await visit('/agents/runs', '10-agent-runs', /grandine|furto|antifrode/i);
  });

  test('use cases, approvals, triggers', async () => {
    await visit('/catalog', '11-catalog', 'Istruttoria sinistro con approvazione', 'Controllo meteo eventi diffusi');
    await visit('/catalog/runs', '12-catalog-runs', 'Istruttoria sinistro con approvazione');
    await visit('/approvals', '13-approvals', 'Istruttoria sinistro con approvazione');
    await visit('/build/library', '14-build', 'Istruttoria sinistro con approvazione');
    await page.getByRole('link', { name: 'Triggers' }).first().click();
    await expect(page.getByText('Controllo meteo ore 7:30').or(page.getByText('Portale denunce online')).first()).toBeVisible();
    await page.screenshot({ path: 'e2e/.results/demo/15-triggers.png' });
  });

  test('integrations, governance, analytics', async () => {
    await visit('/mcp/exposed', '16-mcp', 'cerca_condizioni_polizza', 'assistente_liquidatori');
    await visit('/projects/integrations', '17-widget', 'Portale clienti');
    await visit('/governance/guardrails', '18-guardrails', /pii/i);
    await visit('/governance/evaluations', '19-evals', 'Domande frequenti su polizza e procedure');
    await visit('/analytics', '20-analytics', /Run|Chiamate|Calls|Costo|Cost/i);
    expect(errors).toEqual([]);
  });
});
