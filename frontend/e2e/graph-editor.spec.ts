import { expect, Page, test } from '@playwright/test';

/** Spike E0.5c: Foblex Flow editor over the ADR-010 graph schema, in a real browser. */

interface Definition {
  nodes: { id: string; after?: string[] }[];
}

async function definition(page: Page): Promise<Definition> {
  return JSON.parse(await page.evaluate(() => window.__forgeGraph!.json())) as Definition;
}

async function dragBetween(page: Page, from: string, to: string): Promise<void> {
  const a = (await page.locator(from).boundingBox())!;
  const b = (await page.locator(to).boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 8 });
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
}

test.describe('graph editor spike (E0.5c)', () => {
  const errors: string[] = [];

  test.beforeEach(async ({ page }) => {
    errors.length = 0;
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('/spikes/graph-editor');
    await expect(page.locator('[data-node-id="start"]')).toBeVisible();
  });

  test('renders the PRD sample with typed ports and no validation issues', async ({ page }) => {
    await expect(page.locator('[data-node-id]')).toHaveCount(8);
    await expect(page.locator('[data-port="per_epic.loop"]')).toBeVisible();
    await expect(page.locator('[data-port="per_epic.done"]')).toBeVisible();
    await expect(page.locator('[data-port="size_check.large"]')).toBeVisible();
    await expect(page.locator('[data-port="size_check.default"]')).toBeVisible();
    await expect(page.locator('[data-port="start::in"]')).toHaveCount(0);
    await expect(page.locator('f-connection')).toHaveCount(8);
    await expect(page.getByTestId('valid')).toBeVisible();
    await page.screenshot({ path: 'e2e/.results/graph-editor-prd.png' });
    expect(errors).toEqual([]);
  });

  test('drag-to-connect updates `after` and clears validation errors', async ({ page }) => {
    await page.getByTestId('add-agent').click();
    await expect(page.locator('[data-node-id="agent_9"]')).toBeVisible();
    await expect(page.getByTestId('issue-unreachable')).toBeVisible();

    await dragBetween(page, '[data-port="epics"]', '[data-port="agent_9::in"]');

    await expect
      .poll(async () => (await definition(page)).nodes.find((n) => n.id === 'agent_9')?.after)
      .toEqual(['epics']);
    await expect(page.locator('f-connection')).toHaveCount(9);
    await expect(page.getByTestId('issue-unreachable')).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test('connecting a router section port records the section reference', async ({ page }) => {
    await page.getByTestId('add-end').click();
    await expect(page.locator('[data-node-id="end_9"]')).toBeVisible();
    await dragBetween(page, '[data-port="size_check.default"]', '[data-port="end_9::in"]');
    await expect
      .poll(async () => (await definition(page)).nodes.find((n) => n.id === 'end_9')?.after)
      .toEqual(['size_check.default']);
  });

  test('moving a node keeps the semantic definition unchanged', async ({ page }) => {
    const before = await definition(page);
    const title = page.locator('[data-node-id="end"] .title');
    const box = (await title.boundingBox())!;
    await page.mouse.move(box.x + 20, box.y + 5);
    await page.mouse.down();
    const t0 = Date.now();
    for (let i = 1; i <= 30; i++) await page.mouse.move(box.x + 20 + i * 4, box.y + 5 + i * 3);
    const dragMs = Date.now() - t0;
    await page.mouse.up();
    console.log(`E05C_DRAG_SMALL ${JSON.stringify({ moves: 30, total_ms: dragMs, per_move_ms: dragMs / 30 })}`);
    const moved = (await title.boundingBox())!;
    expect(Math.abs(moved.x - box.x)).toBeGreaterThan(50);
    expect(await definition(page)).toEqual(before);
  });

  test('renders a 200-node graph within budget', async ({ page }) => {
    await page.getByTestId('load-200').click();
    await expect(page.getByTestId('render-ms')).toContainText('200 nodes');
    await expect(page.locator('[data-node-id]')).toHaveCount(200);
    await expect(page.locator('f-connection')).toHaveCount(199);
    const metrics = await page.evaluate(() => window.__forgeGraph!.metrics());
    console.log(`E05C_METRICS ${JSON.stringify(metrics)}`);
    expect(metrics['last_render_ms']).toBeLessThan(3000);

    // Drag responsiveness on the large graph: time for one node drag gesture.
    const title = page.locator('[data-node-id="n100"] .title');
    await title.scrollIntoViewIfNeeded();
    const box = (await title.boundingBox())!;
    await page.mouse.move(box.x + 10, box.y + 5);
    await page.mouse.down();
    const t0 = Date.now();
    for (let i = 1; i <= 30; i++) await page.mouse.move(box.x + 10 + i * 4, box.y + 5 + i * 2);
    const dragMs = Date.now() - t0;
    await page.mouse.up();
    console.log(`E05C_DRAG ${JSON.stringify({ moves: 30, total_ms: dragMs, per_move_ms: dragMs / 30 })}`);
    await page.screenshot({ path: 'e2e/.results/graph-editor-200.png' });
    expect(errors).toEqual([]);
  });
});
