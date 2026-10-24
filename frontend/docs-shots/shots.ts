import type { Page } from '@playwright/test';

/**
 * One picture of the user guide, saved as docs/guide/images/<locale>/<name>.png.
 * The pictures are taken in both UI languages from the same steps, so a step must never depend on
 * visible text: use ids, test ids and CSS selectors.
 */
export interface Shot {
  name: string;
  /** Page to open; omitted to take the picture of the page left by the previous shot. */
  path?: string;
  /** Taken before signing in (login page). */
  anonymous?: boolean;
  /** Element to wait for before the picture is taken. */
  ready?: string;
  /** Steps run after the page loaded (open a menu, fill a field...). */
  before?: (page: Page) => Promise<void>;
  /** Crop the picture to this element. */
  clip?: string;
  /** Numbered markers drawn on these elements, in order: the guide text refers to them as 1, 2, 3... */
  marks?: string[];
  fullPage?: boolean;
}

export const SHOTS: Shot[] = [
  // ---- 01 Getting started
  {
    name: '01-login',
    path: '/login',
    anonymous: true,
    ready: '#login',
    clip: 'nz-card',
    marks: ['#login', '#password', 'button[type=submit]'],
  },
  {
    name: '01-login-error',
    path: '/login',
    anonymous: true,
    ready: '#login',
    clip: 'nz-card',
    before: async (p) => {
      await p.locator('#login').fill('nobody@example.com');
      await p.locator('#password').fill('wrong-password-123');
      await p.locator('button[type=submit]').click();
      await p.locator('[data-testid=login-error]').waitFor();
    },
  },
  {
    name: '01-home',
    path: '/',
    marks: ['aside.sidebar nav', 'nz-select.project-select', '[data-testid=profile-menu]', 'main.content h1, main.content h2'],
  },
  { name: '01-sidebar', path: '/', clip: 'aside.sidebar' },
  {
    name: '01-project-select',
    path: '/',
    before: async (p) => {
      await p.getByTestId('project-select').click();
    },
  },
  { name: '01-change-password', path: '/account/password', clip: 'nz-card' },
  { name: '01-eula', path: '/account/eula', clip: 'nz-card' },
  { name: '01-totp', path: '/account/totp', clip: 'nz-card' },
  {
    name: '01-profile-menu',
    path: '/',
    before: async (p) => {
      await p.getByTestId('profile-menu').click();
    },
  },
];

const ctrl = (id: string) => `[data-testid=${id}]`;
const tab = (name: string, path: string, extra: Partial<Shot> = {}): Shot => ({ name, path, ...extra });
const click = (sel: string) => async (p: Page) => {
  await p.locator(sel).first().click();
};

SHOTS.push(
  // ---- 02 Projects Studio
  tab('02-info', '/projects/info', {
    marks: ['a[href$="/projects/models"]', 'form.head input', 'nz-select.scopes', 'form.head button', 'nz-table'],
  }),
  tab('02-test-connection', '/projects/models', {
    before: async (p) => {
      await p.locator('tr:has-text("groq-gpt-oss-20b") button').first().click();
      const notice = p.locator('forge-model-registry nz-alert').first();
      await notice.waitFor({ timeout: 40000 });
      await notice.scrollIntoViewIfNeeded();
    },
  }),
  tab('02-providers', '/projects/models', { clip: '[data-testid=providers]' }),
  tab('02-new-provider', '/projects/models', { clip: '.ant-modal-content', before: click('[data-testid=new-provider]') }),
  tab('02-new-model', '/projects/models', { clip: '.ant-modal-content', before: click('[data-testid=new-model]') }),
  tab('02-new-alias', '/projects/models', { clip: '.ant-modal-content', before: click('[data-testid=new-alias]') }),
  tab('02-limits', '/projects/models', { clip: '[data-testid=limits]' }),
  tab('02-users', '/projects/users', { marks: [ctrl('invite'), '[data-testid="groups-anna.builder"]'] }),
  tab('02-invite', '/projects/users', { clip: '.ant-modal-content', before: click('[data-testid=invite]') }),
  tab('02-groups', '/projects/groups'),
  tab('02-new-group', '/projects/groups', { clip: '.ant-modal-content', before: click('[data-testid=new-group]') }),
  tab('02-roles', '/projects/roles', { marks: ['form.head input', 'form.head nz-select', 'form.head button', 'nz-table'] }),
  tab('02-catalogs', '/projects/catalogs', { fullPage: true }),
);

const PROMPT = '{prompt:customer-reply}';
SHOTS.push(
  // ---- 03 Prompt Studio
  tab('03-library', '/prompts/library', {
    marks: [ctrl('prompt-search'), ctrl('prompt-status'), ctrl('prompt-range'), ctrl('new-prompt')],
  }),
  tab('03-library-actions', '/prompts/library', { before: click(ctrl('prompt-actions-customer-reply')) }),
  tab('03-new-prompt', '/prompts/new'),
  tab('03-editor', `/prompts/${PROMPT}/edit`, {
    marks: [ctrl('prompt-model'), ctrl('prompt-system'), ctrl('prompt-user'), ctrl('help-me-write'), ctrl('open-versions'), ctrl('open-test')],
  }),
  tab('03-editor-variables', `/prompts/${PROMPT}/edit`, {
    clip: ctrl('variables'),
    marks: [ctrl('add-variable'), ctrl('variable-company')],
  }),
  tab('03-editor-save', `/prompts/${PROMPT}/edit`, {
    before: async (p) => {
      await p.locator(ctrl('save-draft')).scrollIntoViewIfNeeded();
    },
    marks: [ctrl('save-draft'), ctrl('save-test')],
  }),
  tab('03-versions', `/prompts/${PROMPT}/edit`, { before: click(ctrl('open-versions')) }),
  tab('03-help-me-write', `/prompts/${PROMPT}/edit`, { before: click(ctrl('help-me-write')) }),
  tab('03-test', `/prompts/${PROMPT}/test`, {
    marks: [ctrl('test-version'), ctrl('test-var-message'), ctrl('proceed'), ctrl('publish')],
  }),
  tab('03-test-result', `/prompts/${PROMPT}/test`, {
    before: async (p) => {
      await p.locator(ctrl('test-var-message')).fill('Hello, my parcel arrived damaged. What can I do?');
      await p.locator(ctrl('proceed')).click();
      await p.waitForTimeout(12000);
    },
  }),
  tab('03-compare', `/prompts/${PROMPT}/test`, { clip: ctrl('compare'), marks: [ctrl('compare-models'), ctrl('compare-run')] }),
  tab('03-compare-result', `/prompts/${PROMPT}/test`, {
    before: async (p) => {
      await p.locator(ctrl('test-var-message')).fill('Hello, my parcel arrived damaged. What can I do?');
      for (const model of ['groq-gpt-oss-20b', 'ministral-8b']) {
        const select = p.locator(ctrl('compare-models'));
        await select.click();
        await select.locator('input').pressSequentially(model);
        await p.locator('nz-option-item').filter({ hasText: model }).first().click();
      }
      await p.keyboard.press('Escape');
      await p.locator(ctrl('compare-run')).click();
      await p.waitForTimeout(15000);
      await p.locator(ctrl('compare')).scrollIntoViewIfNeeded();
    },
    clip: ctrl('compare'),
  }),
);

const COLLECTION = '{collection:Company policies}';
const RAGP = '{rag:policies-qa}';

SHOTS.push(
  // ---- 04 Data and RAG Studio
  tab('04-collections', '/projects/data', { marks: [ctrl('new-collection'), ctrl('collections')] }),
  tab('04-new-collection', '/projects/data', { clip: '.ant-modal-content', before: click(ctrl('new-collection')) }),
  tab('04-collection', `/projects/data/${COLLECTION}`, {
    marks: ['.upload button', ctrl('upload-groups'), ctrl('documents'), ctrl('knowledge-bases')],
  }),
  tab('04-documents', `/projects/data/${COLLECTION}`, { clip: ctrl('documents') }),
  tab('04-access', `/projects/data/${COLLECTION}`, {
    clip: '.ant-modal-content',
    before: click('tr[data-document="expenses-policy.md"] td.actions button'),
  }),
  tab('04-indexes', `/projects/data/${COLLECTION}`, { clip: ctrl('knowledge-bases') }),
  tab('04-sources', '/projects/sources', { marks: [ctrl('source-new'), 'nz-table'] }),
  tab('04-new-source', '/projects/sources', { clip: '.ant-modal-content', before: click(ctrl('source-new')) }),
  tab('04-rag-library', '/rag/library', {
    marks: [ctrl('rag-search'), ctrl('rag-status'), ctrl('rag-range'), ctrl('new-rag'), ctrl('rag-table')],
  }),
  tab('04-rag-editor', `/rag/${RAGP}/edit`, {
    marks: [ctrl('rag-source'), ctrl('rag-llm'), ctrl('rag-embedding'), ctrl('rag-method'), ctrl('rag-threshold'), ctrl('rag-chunk-size')],
  }),
  tab('04-rag-editor-answer', `/rag/${RAGP}/edit`, {
    before: async (p) => {
      await p.locator(ctrl('rag-save')).scrollIntoViewIfNeeded();
    },
    marks: [ctrl('rag-save'), ctrl('rag-save-test')],
  }),
  tab('04-rag-test', `/rag/${RAGP}/test`, { marks: [ctrl('rag-query'), ctrl('rag-proceed'), ctrl('rag-publish')] }),
  tab('04-rag-answer', `/rag/${RAGP}/test`, {
    before: async (p) => {
      await p.locator(ctrl('rag-query')).fill('How many days of annual leave do I have, and how do I ask for them?');
      await p.locator(ctrl('rag-proceed')).click();
      await p.locator(ctrl('rag-citations')).waitFor({ timeout: 60000 });
    },
    marks: [ctrl('rag-answer'), ctrl('rag-citations'), ctrl('rag-stats'), ctrl('rag-trace-link')],
  }),
  tab('04-rag-trace', `/rag/${RAGP}/test`, {
    before: async (p) => {
      await p.locator(ctrl('rag-query')).fill('What is the meal allowance on business trips?');
      await p.locator(ctrl('rag-proceed')).click();
      await p.locator(ctrl('rag-citations')).waitFor({ timeout: 60000 });
      await p.locator(ctrl('rag-trace-link')).click();
      await p.locator(ctrl('rag-trace')).waitFor();
    },
  }),
);

const EXCHANGE = '{tool:exchange_rate}';

SHOTS.push(
  // ---- 05 Tools Studio
  tab('05-library', '/tools/library', {
    marks: [ctrl('tool-search'), ctrl('import-openapi'), ctrl('new-tool'), ctrl('tool-table')],
  }),
  tab('05-import-openapi', '/tools/library', { clip: '.ant-modal-content', before: click(ctrl('import-openapi')) }),
  tab('05-new-tool', '/tools/new'),
  tab('05-tool-http', `/tools/${EXCHANGE}/edit`, { fullPage: true, marks: [ctrl('add-param'), ctrl('save-tool')] }),
  tab('05-tool-test', `/tools/${EXCHANGE}/edit`, {
    before: async (p) => {
      await p.locator('#tc-base').fill('EUR');
      await p.locator('#tc-symbols').fill('USD,GBP');
      await p.locator(ctrl('run-tool')).click();
      await p.locator(ctrl('tool-result')).waitFor({ timeout: 30000 });
      await p.locator(ctrl('tool-result')).scrollIntoViewIfNeeded();
    },
    marks: [ctrl('run-tool'), ctrl('tool-result')],
  }),
  tab('05-tool-python', '/tools/{tool:word_count}/edit', { fullPage: true, marks: [ctrl('tool-code')] }),
  tab('05-connections', '/tools/connections', { fullPage: true }),
  tab('05-calls', '/tools/calls', { marks: [ctrl('call-table')] }),
);

const AGENT = '{agent:internal-assistant}';
const scrollTo = (sel: string) => async (p: Page) => {
  await p.locator(sel).first().scrollIntoViewIfNeeded();
};
const pickAgent = async (p: Page) => {
  const select = p.locator(ctrl('chat-agent'));
  await select.click();
  await select.locator('input').pressSequentially('internal-assistant');
  await p.locator('nz-option-item').filter({ hasText: 'internal-assistant' }).first().click();
};

SHOTS.push(
  // ---- 06 Agentic AI Studio
  tab('06-library', '/agents/library', {
    marks: [ctrl('agent-search'), ctrl('agent-status'), ctrl('agent-range'), ctrl('new-agent'), ctrl('agent-table')],
  }),
  tab('06-editor', `/agents/${AGENT}/edit`, {
    marks: [ctrl('agent-versions'), ctrl('agent-publish'), ctrl('agent-open-chat'), ctrl('agent-prompt'), ctrl('agent-model')],
  }),
  tab('06-editor-memory', `/agents/${AGENT}/edit`, {
    before: scrollTo(ctrl('agent-memory')),
    marks: [ctrl('agent-guardrails'), ctrl('agent-memory')],
  }),
  tab('06-editor-tools', `/agents/${AGENT}/edit`, {
    before: scrollTo(ctrl('agent-pipeline-add')),
    marks: [ctrl('agent-pipeline-add'), ctrl('agent-tool-add'), ctrl('agent-tool-time')],
  }),
  tab('06-editor-limits', `/agents/${AGENT}/edit`, {
    before: scrollTo(ctrl('agent-save')),
    marks: [ctrl('agent-max-steps'), ctrl('agent-save'), ctrl('agent-save-test')],
  }),
  tab('06-versions', `/agents/${AGENT}/edit`, { before: click(ctrl('agent-versions')) }),
  tab('06-chat-empty', '/agents/chat', { marks: [ctrl('chat-agent')] }),
  tab('06-chat', '/agents/chat', {
    before: async (p) => {
      await pickAgent(p);
      await p.locator(ctrl('chat-input')).fill('How many days of annual leave do I have, and how many US dollars are 200 euro today?');
      await p.locator(ctrl('chat-send')).click();
      await p.locator(ctrl('chat-thumbs-up')).waitFor({ timeout: 120000 });
      await p.waitForTimeout(1000);
    },
    marks: [ctrl('chat-agent'), ctrl('chat-user'), ctrl('chat-steps'), ctrl('chat-answer'), ctrl('chat-thumbs-up'), ctrl('chat-input')],
  }),
  tab('06-chat-details', '/agents/chat', {
    before: async (p) => {
      await pickAgent(p);
      await p.locator(ctrl('chat-conversation')).filter({ hasText: 'Leave and currency' }).first().click();
      await p.locator(ctrl('chat-answer')).first().waitFor({ timeout: 30000 });
    },
    marks: [ctrl('chat-conversation'), ctrl('chat-answer'), ctrl('chat-run-link')],
  }),
  tab('06-runs', '/agents/runs', { marks: [ctrl('runs-usage'), ctrl('runs-agent'), ctrl('runs-status'), ctrl('runs-table')] }),
  tab('06-run', '/agents/runs', {
    before: async (p) => {
      await p.locator(`${ctrl('runs-table')} a`).first().click();
      await p.locator(ctrl('run-view')).waitFor();
    },
    marks: [ctrl('run-status'), ctrl('run-output'), ctrl('run-totals'), ctrl('run-steps')],
  }),
);

const HELPER = '{usecase:Policy helper}';
const REPLY = '{usecase:Customer reply with review}';

SHOTS.push(
  // ---- 07 Use cases
  tab('07-catalog', '/catalog', {
    marks: [ctrl('catalog-views'), ctrl('catalog-search'), ctrl('catalog-category'), ctrl('catalog-grid'), ctrl('catalog-approvals-link'), ctrl('catalog-runs-link')],
  }),
  tab('07-use-case', `/catalog/${HELPER}`, { marks: [ctrl('uc-form'), ctrl('uc-run'), ctrl('uc-history')] }),
  tab('07-run', `/catalog/${HELPER}`, {
    before: async (p) => {
      await p.locator(`${ctrl('uc-form')} textarea`).first().fill('How many days of annual leave do I have?');
      await p.locator(ctrl('uc-run')).click();
      await p.locator(ctrl('run-result')).waitFor({ timeout: 120000 });
      await p.waitForTimeout(2000);
    },
    marks: [ctrl('run-status'), ctrl('run-timeline'), ctrl('run-result')],
  }),
  tab('07-runs', '/catalog/runs', { marks: [ctrl('runs-status'), ctrl('runs-table')] }),
  tab('07-approvals', '/approvals', { marks: [ctrl('approvals'), ctrl('approval-open')] }),
  tab('07-approval-review', '/approvals', {
    before: click(ctrl('approval-open')),
    clip: '.ant-modal-content',
  }),
  tab('07-build-library', '/build/library', { marks: [ctrl('uc-search'), ctrl('new-usecase'), ctrl('uc-table')] }),
  tab('07-build-requirements', '/build/new', {
    marks: [ctrl('rq-name'), ctrl('rq-text'), ctrl('rq-pattern'), ctrl('rq-file'), ctrl('rq-create')],
  }),
  tab('07-build-workflow', `/build/${REPLY}/workflow`, { fullPage: true }),
  tab('07-build-settings', `/build/${REPLY}/edit`, { fullPage: true, marks: [ctrl('uc-add-field'), ctrl('uc-definition'), ctrl('uc-save')] }),
  tab('07-build-lifecycle', `/build/${REPLY}/edit`, {
    before: scrollTo(ctrl('uc-versions')),
    marks: [ctrl('uc-deprecate'), ctrl('uc-publish'), ctrl('uc-versions')],
  }),
  tab('07-build-test', `/build/${REPLY}/test`, { fullPage: true }),
  tab('07-triggers', `/build/${HELPER}/triggers`, { marks: [ctrl('trigger-new')] }),
  tab('07-new-trigger', `/build/${HELPER}/triggers`, { clip: '.ant-modal-content', before: click(ctrl('trigger-new')) }),
);

SHOTS.push(
  // ---- 08 Governance and Evaluation Studio
  tab('08-guardrails', '/governance/guardrails', { marks: [ctrl('gr-enabled'), ctrl('gr-add'), ctrl('gr-rules'), ctrl('gr-save')] }),
  tab('08-guardrails-try', '/governance/guardrails', {
    before: scrollTo(ctrl('gr-try')),
    marks: [ctrl('gr-sample'), ctrl('gr-try'), ctrl('gr-events')],
  }),
  tab('08-guardrails-result', '/governance/guardrails', {
    before: async (p) => {
      await p.locator(ctrl('gr-sample')).fill('Write to anna.rossi@example.com about IBAN IT60X0542811101000000123456');
      await p.locator(ctrl('gr-try')).click();
      await p.locator(ctrl('gr-result')).waitFor({ timeout: 30000 });
      await p.locator(ctrl('gr-result')).scrollIntoViewIfNeeded();
    },
    marks: [ctrl('gr-result')],
  }),
  tab('08-evaluations', '/governance/evaluations', { marks: [ctrl('ds-new'), '[data-testid="ds-Reply checks"]', ctrl('ev-evaluators'), ctrl('ev-start'), ctrl('ev-runs')] }),
  tab('08-dataset', '/governance/evaluations', {
    clip: '.ant-modal-content',
    before: click(ctrl('ds-new')),
  }),
  tab('08-eval-compare', '/governance/evaluations', {
    before: async (p) => {
      const boxes = p.locator(`${ctrl('ev-runs')} input[type=checkbox]`);
      await boxes.nth(0).check();
      await boxes.nth(1).check();
      await p.locator(ctrl('ev-compare')).click();
      await p.locator(ctrl('ev-comparison')).waitFor({ timeout: 30000 });
      await p.locator(ctrl('ev-comparison')).scrollIntoViewIfNeeded();
    },
    marks: [ctrl('ev-compare'), ctrl('ev-comparison')],
  }),
);

SHOTS.push(
  // ---- 09 Analytics
  tab('09-analytics', '/analytics'),
  tab('09-analytics-lower', '/analytics', {
    before: async (p) => {
      await p.evaluate(() => document.querySelector('main.content')?.scrollTo(0, 560) ?? window.scrollTo(0, 560));
      await p.mouse.wheel(0, 560);
    },
  }),
  tab('09-activity-log', '/analytics', {
    before: async (p) => {
      await p.mouse.wheel(0, 1300);
    },
  }),
  tab('09-home-activity', '/'),
);

SHOTS.push(
  // ---- 10 Integrations, sync and transfer
  tab('10-integrations', '/projects/integrations', { fullPage: true, marks: [ctrl('mcp-url'), ctrl('mcp-add'), ctrl('widget-new')] }),
  tab('10-expose', '/projects/integrations', { clip: '.ant-modal-content', before: click(ctrl('mcp-add')) }),
  tab('10-new-widget', '/projects/integrations', { clip: '.ant-modal-content', before: click(ctrl('widget-new')) }),
  tab('10-sync', '/projects/sync', { fullPage: true, marks: [ctrl('sync-download'), ctrl('sync-folder'), ctrl('sync-key')] }),
  tab('10-transfer', '/projects/transfer', { fullPage: true, marks: [ctrl('export-run'), ctrl('import-file')] }),
);

SHOTS.push(
  // ---- 11 Super Admin console
  tab('11-users', '/admin/users', { marks: [ctrl('new-user')] }),
  tab('11-new-user', '/admin/users', { clip: '.ant-modal-content', before: click(ctrl('new-user')) }),
  tab('11-projects', '/admin/projects', { marks: [ctrl('new-project')] }),
  tab('11-new-project', '/admin/projects', { clip: '.ant-modal-content', before: click(ctrl('new-project')) }),
  tab('11-models', '/admin/models'),
  tab('11-auth', '/admin/auth', { marks: [ctrl('new-oidc'), ctrl('new-ldap')] }),
  tab('11-new-ldap', '/admin/auth', { clip: '.ant-modal-content', before: click(ctrl('new-ldap')) }),
  tab('11-settings', '/admin/settings', { fullPage: true }),
  tab('11-audit', '/admin/audit'),
  tab('11-vault', '/admin/vault', { fullPage: true, marks: [ctrl('vault-current'), ctrl('vault-active'), ctrl('vault-save'), ctrl('vault-test'), ctrl('vault-rewrap')] }),
  tab('11-email', '/admin/email', { marks: [ctrl('smtp-host'), ctrl('smtp-save')] }),
  tab('11-insights', '/admin/insights', { fullPage: true }),
);
