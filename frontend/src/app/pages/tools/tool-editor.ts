import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { DataSourceOut, ToolDetail, ToolTestOut, ToolsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { SchemaField, buildArguments, schemaFields, toolName } from './tool-util';

interface ParamRow {
  name: string;
  in: 'path' | 'query' | 'header' | 'body';
  type: string;
  description: string;
  required: boolean;
  enum: string;
}

interface SqlParamRow {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

interface HttpCfg {
  method: string;
  url: string;
  credential?: string | null;
  response_max_chars?: number;
  headers?: Record<string, string>;
  params?: {
    name: string;
    in: ParamRow['in'];
    type: string;
    description?: string;
    required?: boolean;
    enum?: string[] | null;
  }[];
}

interface SqlCfg {
  datasource: string;
  query: string;
  row_limit?: number;
  params?: { name: string; type: string; description?: string; required?: boolean }[];
}

interface Draft {
  name: string;
  description: string;
  kind: 'http' | 'sql' | 'mcp' | 'python';
  timeout_s: number;
  requires_approval: boolean;
  note: string;
  // http
  method: string;
  url: string;
  credential: string | null;
  response_max_chars: number;
  headers: string;
  params: ParamRow[];
  // sql
  datasource: string | null;
  query: string;
  row_limit: number;
  sqlParams: SqlParamRow[];
  // python (sandbox)
  code: string;
  memory_mb: number;
  /** Hosts the code may reach (one per line in the form), a subset of the project's allowed hosts */
  pyHosts: string;
  pyParams: SqlParamRow[];
  // mcp (kept as imported)
  mcp: Record<string, unknown> | null;
}

function blank(): Draft {
  return {
    name: '',
    description: '',
    kind: 'http',
    timeout_s: 15,
    requires_approval: false,
    note: '',
    method: 'GET',
    url: 'https://',
    credential: null,
    response_max_chars: 8000,
    headers: '',
    params: [],
    datasource: null,
    query: 'select 1 as one',
    row_limit: 100,
    sqlParams: [],
    code: 'def run(args):\n    return {}\n',
    memory_mb: 128,
    pyHosts: '',
    pyParams: [],
    mcp: null,
  };
}

/** New tool / edit tool (a save is a new version) with the test console (US-2.2.1, 2.2.4, 2.2.7,
 * 2.2.8). */
@Component({
  selector: 'forge-tool-editor',
  imports: [
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzIconModule,
    NzInputModule,
    NzInputNumberModule,
    NzSelectModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <h2 class="grow">
        @if (detail(); as d) {
          {{ d.name }} <nz-tag>v{{ d.latest_version }}</nz-tag>
          @if (d.published_version) {
            <nz-tag nzColor="success" i18n>Published v{{ d.published_version }}</nz-tag>
          }
        } @else {
          <span i18n>New tool</span>
        }
      </h2>
      <a nz-button routerLink="../../library" i18n>Back to the library</a>
    </div>

    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }

    <div class="editor-grid">
      <form class="form" (ngSubmit)="save()">
        @if (!detail()) {
          <label for="t-name" i18n>Name (lower-case, digits, underscores)</label>
          <input nz-input id="t-name" name="name" [(ngModel)]="draft.name" required />
          <label for="t-kind" i18n>Type</label>
          <nz-select nzId="t-kind" name="kind" [(ngModel)]="draft.kind">
            <nz-option nzValue="http" nzLabel="HTTP" />
            <nz-option nzValue="sql" nzLabel="SQL (read-only)" />
            <nz-option nzValue="python" nzLabel="Python (sandbox)" />
          </nz-select>
        }
        <label for="t-desc" i18n>Description the model reads</label>
        <textarea
          nz-input
          id="t-desc"
          name="description"
          rows="2"
          [(ngModel)]="draft.description"
          required
        ></textarea>
        <label for="t-timeout" i18n>Timeout (seconds)</label>
        <nz-input-number
          nzId="t-timeout"
          name="timeout"
          [nzMin]="1"
          [nzMax]="60"
          [(ngModel)]="draft.timeout_s"
        />
        <label nz-checkbox nzId="t-approval" name="approval" [(ngModel)]="draft.requires_approval">
          <span i18n>Requires approval (not available until human approval ships)</span>
        </label>

        @switch (draft.kind) {
          @case ('http') {
            <h3 i18n>HTTP request</h3>
            <div class="row">
              <nz-select
                nzId="t-method"
                name="method"
                [(ngModel)]="draft.method"
                aria-label="Method"
                i18n-aria-label
              >
                @for (m of methods; track m) {
                  <nz-option [nzValue]="m" [nzLabel]="m" />
                }
              </nz-select>
              <input nz-input name="url" aria-label="URL" [(ngModel)]="draft.url" required />
            </div>
            <small i18n>Use {{ '{name}' }} in the URL for path parameters.</small>
            <label for="t-cred" i18n>Credential</label>
            <nz-select nzId="t-cred" name="credential" nzAllowClear [(ngModel)]="draft.credential">
              @for (c of credentials(); track c) {
                <nz-option [nzValue]="c" [nzLabel]="c" />
              }
            </nz-select>
            <label for="t-headers" i18n>Static headers (one per line, Name: value)</label>
            <textarea
              nz-input
              id="t-headers"
              name="headers"
              rows="2"
              [(ngModel)]="draft.headers"
            ></textarea>
            <label for="t-resp" i18n>Response limit for the model (characters)</label>
            <nz-input-number
              nzId="t-resp"
              name="resp"
              [nzMin]="500"
              [nzMax]="64000"
              [(ngModel)]="draft.response_max_chars"
            />
            <h3 i18n>Parameters</h3>
            @for (p of draft.params; track $index) {
              <div class="row param">
                <input
                  nz-input
                  [name]="'pn' + $index"
                  aria-label="Name"
                  i18n-aria-label
                  placeholder="name"
                  [(ngModel)]="p.name"
                />
                <nz-select
                  [nzId]="'pi' + $index"
                  [name]="'pi' + $index"
                  [(ngModel)]="p.in"
                  aria-label="Location"
                  i18n-aria-label
                >
                  @for (l of locations; track l) {
                    <nz-option [nzValue]="l" [nzLabel]="l" />
                  }
                </nz-select>
                <nz-select
                  [nzId]="'pt' + $index"
                  [name]="'pt' + $index"
                  [(ngModel)]="p.type"
                  aria-label="Type"
                  i18n-aria-label
                >
                  @for (t of types; track t) {
                    <nz-option [nzValue]="t" [nzLabel]="t" />
                  }
                </nz-select>
                <input
                  nz-input
                  [name]="'pd' + $index"
                  aria-label="Description"
                  i18n-aria-label
                  placeholder="description"
                  [(ngModel)]="p.description"
                />
                <input
                  nz-input
                  [name]="'pe' + $index"
                  aria-label="Allowed values"
                  i18n-aria-label
                  placeholder="a, b, c"
                  [(ngModel)]="p.enum"
                />
                <label
                  nz-checkbox
                  [nzId]="'pr' + $index"
                  [name]="'pr' + $index"
                  [(ngModel)]="p.required"
                >
                  <span i18n>required</span>
                </label>
                <button
                  nz-button
                  type="button"
                  nzType="text"
                  nzDanger
                  aria-label="Remove"
                  i18n-aria-label
                  (click)="removeParam($index)"
                >
                  <nz-icon nzType="delete" />
                </button>
              </div>
            }
            <button nz-button type="button" (click)="addParam()" data-testid="add-param">
              <nz-icon nzType="plus" /><span i18n>Add parameter</span>
            </button>
          }
          @case ('sql') {
            <h3 i18n>Query</h3>
            <label for="t-ds" i18n>Data source</label>
            <nz-select nzId="t-ds" name="datasource" [(ngModel)]="draft.datasource">
              @for (d of datasources(); track d.id) {
                <nz-option [nzValue]="d.name" [nzLabel]="d.name" />
              }
            </nz-select>
            <label for="t-query" i18n>One SELECT or WITH statement; use :name for parameters</label>
            <textarea
              nz-input
              id="t-query"
              name="query"
              rows="6"
              class="mono"
              [(ngModel)]="draft.query"
            ></textarea>
            <label for="t-rows" i18n>Row limit</label>
            <nz-input-number
              nzId="t-rows"
              name="rows"
              [nzMin]="1"
              [nzMax]="1000"
              [(ngModel)]="draft.row_limit"
            />
            <h3 i18n>Parameters</h3>
            @for (p of draft.sqlParams; track $index) {
              <div class="row param">
                <input
                  nz-input
                  [name]="'sn' + $index"
                  aria-label="Name"
                  i18n-aria-label
                  placeholder="name"
                  [(ngModel)]="p.name"
                />
                <nz-select
                  [nzId]="'st' + $index"
                  [name]="'st' + $index"
                  [(ngModel)]="p.type"
                  aria-label="Type"
                  i18n-aria-label
                >
                  @for (t of sqlTypes; track t) {
                    <nz-option [nzValue]="t" [nzLabel]="t" />
                  }
                </nz-select>
                <input
                  nz-input
                  [name]="'sd' + $index"
                  aria-label="Description"
                  i18n-aria-label
                  placeholder="description"
                  [(ngModel)]="p.description"
                />
                <label
                  nz-checkbox
                  [nzId]="'sr' + $index"
                  [name]="'sr' + $index"
                  [(ngModel)]="p.required"
                >
                  <span i18n>required</span>
                </label>
                <button
                  nz-button
                  type="button"
                  nzType="text"
                  nzDanger
                  aria-label="Remove"
                  i18n-aria-label
                  (click)="removeSqlParam($index)"
                >
                  <nz-icon nzType="delete" />
                </button>
              </div>
            }
            <button nz-button type="button" (click)="addSqlParam()">
              <nz-icon nzType="plus" /><span i18n>Add parameter</span>
            </button>
          }
          @case ('python') {
            <h3 i18n>Python function</h3>
            <label for="t-code" i18n
              >Define run(args) and return a JSON value. No files, standard library only; no network
              unless you list hosts below.</label
            >
            <textarea
              nz-input
              id="t-code"
              name="code"
              rows="12"
              class="mono"
              spellcheck="false"
              [(ngModel)]="draft.code"
              data-testid="tool-code"
            ></textarea>
            <label for="t-mem" i18n>Memory (MB)</label>
            <nz-input-number
              nzId="t-mem"
              name="memory"
              [nzMin]="32"
              [nzMax]="512"
              [(ngModel)]="draft.memory_mb"
            />
            <label for="t-hosts" i18n>Network access (one host per line, optional)</label>
            <textarea
              nz-input
              id="t-hosts"
              name="hosts"
              rows="3"
              class="mono"
              placeholder="api.example.com"
              [(ngModel)]="draft.pyHosts"
              data-testid="tool-hosts"
            ></textarea>
            <p class="hint" i18n>
              Each host must also be in the project's allowed hosts. The code reaches them over HTTP
              and HTTPS only (urllib uses the proxy by itself); the sandbox refuses everything else,
              including private addresses.
            </p>
            <h3 i18n>Arguments</h3>
            @for (p of draft.pyParams; track $index) {
              <div class="row param">
                <input
                  nz-input
                  [name]="'pn' + $index"
                  aria-label="Name"
                  i18n-aria-label
                  placeholder="name"
                  [(ngModel)]="p.name"
                />
                <nz-select
                  [nzId]="'pt' + $index"
                  [name]="'pt' + $index"
                  [(ngModel)]="p.type"
                  aria-label="Type"
                  i18n-aria-label
                >
                  @for (t of pyTypes; track t) {
                    <nz-option [nzValue]="t" [nzLabel]="t" />
                  }
                </nz-select>
                <input
                  nz-input
                  [name]="'pd' + $index"
                  aria-label="Description"
                  i18n-aria-label
                  placeholder="description"
                  [(ngModel)]="p.description"
                />
                <label
                  nz-checkbox
                  [nzId]="'pr' + $index"
                  [name]="'pr' + $index"
                  [(ngModel)]="p.required"
                >
                  <span i18n>required</span>
                </label>
                <button
                  nz-button
                  type="button"
                  nzType="text"
                  nzDanger
                  aria-label="Remove"
                  i18n-aria-label
                  (click)="removePyParam($index)"
                >
                  <nz-icon nzType="delete" />
                </button>
              </div>
            }
            <button nz-button type="button" (click)="addPyParam()">
              <nz-icon nzType="plus" /><span i18n>Add argument</span>
            </button>
          }
          @case ('mcp') {
            <nz-alert
              nzType="info"
              nzMessage="MCP tools come from a server (Connections → MCP servers). Here you can change the description, timeout and approval flag."
              i18n-nzMessage
            />
          }
        }

        <label for="t-note" i18n>Version note</label>
        <input nz-input id="t-note" name="note" [(ngModel)]="draft.note" />
        <div class="buttons">
          <button
            nz-button
            nzType="primary"
            type="submit"
            [nzLoading]="busy()"
            data-testid="save-tool"
          >
            @if (detail()) {
              <span i18n>Save as new version</span>
            } @else {
              <span i18n>Create tool</span>
            }
          </button>
        </div>
      </form>

      <section class="console" aria-label="Test console" i18n-aria-label>
        <h3 i18n>Test console</h3>
        @if (!detail()) {
          <p class="muted" i18n>Save the tool to try it.</p>
        } @else {
          <label for="tc-version" i18n>Version</label>
          <nz-select
            nzId="tc-version"
            [ngModel]="testVersion()"
            (ngModelChange)="testVersion.set($event)"
          >
            @for (v of detail()!.versions; track v) {
              <nz-option [nzValue]="v" [nzLabel]="'v' + v" />
            }
          </nz-select>
          <form class="form" (ngSubmit)="run()">
            @for (f of fields(); track f.name) {
              @if (f.type === 'boolean') {
                <label
                  nz-checkbox
                  [nzId]="'tc-' + f.name"
                  [name]="'tc-' + f.name"
                  [(ngModel)]="values[f.name]"
                >
                  {{ f.name }} <span class="muted small">{{ f.description }}</span>
                </label>
              } @else {
                <label [for]="'tc-' + f.name">
                  {{ f.name }}
                  @if (f.required) {
                    <span aria-hidden="true"> *</span>
                  }
                  <span class="muted small">{{ f.description }}</span>
                </label>
                <input
                  nz-input
                  [id]="'tc-' + f.name"
                  [name]="'tc-' + f.name"
                  [(ngModel)]="values[f.name]"
                  [placeholder]="f.enum ? f.enum.join(' | ') : f.type"
                />
              }
            }
            <button
              nz-button
              nzType="primary"
              type="submit"
              [nzLoading]="running()"
              data-testid="run-tool"
              i18n
            >
              Run
            </button>
          </form>
          @if (testError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
          }
          @if (result(); as r) {
            <div data-testid="tool-result">
              <nz-tag [nzColor]="r.status === 'ok' ? 'success' : 'error'">{{ r.status }}</nz-tag>
              @if (r.error_code) {
                <nz-tag nzColor="error">{{ r.error_code }}</nz-tag>
              }
              <span class="muted">{{ r.latency_ms }} ms</span>
              <h4 i18n>Request</h4>
              <pre>{{ pretty(r.request) }}</pre>
              <h4 i18n>What the model reads</h4>
              <pre>{{ r.content }}</pre>
            </div>
          }
        }
      </section>
    </div>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ToolEditor {
  private readonly api = inject(ToolsService);
  private readonly session = inject(Session);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly detail = signal<ToolDetail | null>(null);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly busy = signal(false);
  protected readonly credentials = signal<string[]>([]);
  protected readonly datasources = signal<DataSourceOut[]>([]);
  protected readonly testVersion = signal<number | null>(null);
  protected readonly running = signal(false);
  protected readonly result = signal<ToolTestOut | null>(null);
  protected readonly testError = signal<string | null>(null);
  protected readonly fields = computed<SchemaField[]>(() =>
    schemaFields(this.detail()?.version_detail.schema_in),
  );

  protected draft: Draft = blank();
  protected values: Record<string, string | boolean | undefined> = {};
  protected readonly methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
  protected readonly locations = ['path', 'query', 'header', 'body'];
  protected readonly types = ['string', 'integer', 'number', 'boolean', 'array', 'object'];
  protected readonly sqlTypes = ['string', 'integer', 'number', 'boolean'];
  protected readonly pyTypes = ['string', 'integer', 'number', 'boolean', 'array', 'object'];

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      if (projectId) void this.init(projectId);
    });
  }

  private async init(projectId: string): Promise<void> {
    const [creds, sources] = await Promise.all([
      firstValueFrom(this.api.listCredentials({ projectId })),
      firstValueFrom(this.api.listDatasources({ projectId })),
    ]);
    this.credentials.set(creds.map((c) => c.name));
    this.datasources.set(sources);
    const id = this.route.snapshot.paramMap.get('id');
    if (id) await this.load(projectId, id, null);
  }

  private async load(projectId: string, id: string, version: number | null): Promise<void> {
    const d = await firstValueFrom(
      this.api.getTool({ projectId, toolId: id, version: version ?? undefined }),
    );
    this.detail.set(d);
    this.testVersion.set(d.latest_version);
    const v = d.version_detail;
    const draft = blank();
    draft.name = d.name;
    draft.kind = d.kind as Draft['kind'];
    draft.description = v.description;
    draft.timeout_s = v.timeout_s;
    draft.requires_approval = v.requires_approval;
    if (d.kind === 'http') {
      const cfg = v.config as unknown as HttpCfg;
      draft.method = cfg.method;
      draft.url = cfg.url;
      draft.credential = cfg.credential ?? null;
      draft.response_max_chars = cfg.response_max_chars ?? 8000;
      draft.headers = Object.entries(cfg.headers ?? {})
        .map(([k, val]) => `${k}: ${val}`)
        .join('\n');
      draft.params = (cfg.params ?? []).map((p) => ({
        name: p.name,
        in: p.in,
        type: p.type,
        description: p.description ?? '',
        required: !!p.required,
        enum: (p.enum ?? []).join(', '),
      }));
    } else if (d.kind === 'sql') {
      const cfg = v.config as unknown as SqlCfg;
      draft.datasource = cfg.datasource;
      draft.query = cfg.query;
      draft.row_limit = cfg.row_limit ?? 100;
      draft.sqlParams = (cfg.params ?? []).map((p) => ({
        name: p.name,
        type: p.type,
        description: p.description ?? '',
        required: !!p.required,
      }));
    } else if (d.kind === 'python') {
      const cfg = v.config as unknown as {
        code: string;
        memory_mb?: number;
        allowed_hosts?: string[];
        params?: SqlParamRow[];
      };
      draft.code = cfg.code;
      draft.memory_mb = cfg.memory_mb ?? 128;
      draft.pyHosts = (cfg.allowed_hosts ?? []).join('\n');
      draft.pyParams = (cfg.params ?? []).map((p) => ({
        name: p.name,
        type: p.type,
        description: p.description ?? '',
        required: !!p.required,
      }));
    } else {
      draft.mcp = v.config;
    }
    this.draft = draft;
    this.values = {};
  }

  protected addParam(): void {
    this.draft.params = [
      ...this.draft.params,
      { name: '', in: 'query', type: 'string', description: '', required: false, enum: '' },
    ];
  }

  protected removeParam(i: number): void {
    this.draft.params = this.draft.params.filter((_, n) => n !== i);
  }

  protected addSqlParam(): void {
    this.draft.sqlParams = [
      ...this.draft.sqlParams,
      { name: '', type: 'string', description: '', required: true },
    ];
  }

  protected removeSqlParam(i: number): void {
    this.draft.sqlParams = this.draft.sqlParams.filter((_, n) => n !== i);
  }

  protected addPyParam(): void {
    this.draft.pyParams = [
      ...this.draft.pyParams,
      { name: '', type: 'string', description: '', required: false },
    ];
  }

  protected removePyParam(i: number): void {
    this.draft.pyParams = this.draft.pyParams.filter((_, n) => n !== i);
  }

  private config(): Record<string, unknown> {
    const d = this.draft;
    if (d.kind === 'http') {
      const headers: Record<string, string> = {};
      for (const line of d.headers.split('\n')) {
        const at = line.indexOf(':');
        if (at > 0) headers[line.slice(0, at).trim()] = line.slice(at + 1).trim();
      }
      return {
        kind: 'http',
        method: d.method,
        url: d.url,
        credential: d.credential,
        response_max_chars: d.response_max_chars,
        headers,
        params: d.params.map((p) => ({
          name: p.name,
          in: p.in,
          type: p.type,
          description: p.description,
          required: p.required,
          enum: p.enum.trim()
            ? p.enum
                .split(',')
                .map((x) => x.trim())
                .filter(Boolean)
            : null,
        })),
      };
    }
    if (d.kind === 'sql') {
      return {
        kind: 'sql',
        datasource: d.datasource,
        query: d.query,
        row_limit: d.row_limit,
        params: d.sqlParams,
      };
    }
    if (d.kind === 'python') {
      return {
        kind: 'python',
        code: d.code,
        memory_mb: d.memory_mb,
        allowed_hosts: d.pyHosts
          .split('\n')
          .map((h) => h.trim())
          .filter(Boolean),
        params: d.pyParams,
      };
    }
    return d.mcp ?? {};
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const d = this.draft;
    this.busy.set(true);
    this.message.set(null);
    try {
      const body = {
        description: d.description,
        timeout_s: d.timeout_s,
        requires_approval: d.requires_approval,
        note: d.note,
        config: this.config() as never,
      };
      const current = this.detail();
      if (current) {
        const saved = await firstValueFrom(
          this.api.saveToolVersion({ projectId, toolId: current.id, toolVersionIn: body }),
        );
        this.detail.set(saved);
        this.testVersion.set(saved.latest_version);
        this.message.set({
          ok: true,
          text: $localize`Saved as version ${saved.latest_version}:v:`,
        });
      } else {
        const created = await firstValueFrom(
          this.api.createTool({
            projectId,
            toolCreateIn: { ...body, name: toolName(d.name) },
          }),
        );
        await this.router.navigate(['/tools', created.id, 'edit']);
        await this.load(projectId, created.id, null);
        this.message.set({ ok: true, text: $localize`Tool created` });
      }
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.busy.set(false);
    }
  }

  protected async run(): Promise<void> {
    const projectId = this.projectId();
    const d = this.detail();
    if (!projectId || !d) return;
    this.running.set(true);
    this.testError.set(null);
    try {
      const args = buildArguments(this.fields(), this.values);
      this.result.set(
        await firstValueFrom(
          this.api.tryTool({
            projectId,
            toolId: d.id,
            toolTestIn: { version: this.testVersion(), arguments: args },
          }),
        ),
      );
    } catch (e) {
      this.testError.set(
        e instanceof SyntaxError ? $localize`Invalid JSON in a field` : problemMessage(e),
      );
    } finally {
      this.running.set(false);
    }
  }

  protected pretty(value: unknown): string {
    return JSON.stringify(value, null, 2);
  }
}
