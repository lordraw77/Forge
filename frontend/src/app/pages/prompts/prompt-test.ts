import { DatePipe, DecimalPipe, JsonPipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TextFieldModule } from '@angular/cdk/text-field';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzDrawerModule } from 'ng-zorro-antd/drawer';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  PromptDetail,
  PromptsService,
  RunDetail,
  RunOut,
  Variable,
  VersionOut,
  VersionSummary,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { ModelOption, ModelOptions } from './model-options';

/** Text files only: binary formats need the ingestion parsers of RAG (M1.4). */
const MAX_FILE_BYTES = 256 * 1024;

/** Test page (US-1.11.1), publish (US-1.11.2) and side-by-side comparison (US-1.12.1). */
@Component({
  selector: 'forge-prompt-test',
  imports: [
    DatePipe,
    DecimalPipe,
    JsonPipe,
    FormsModule,
    TextFieldModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzDrawerModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (prompt(); as p) {
      <div class="editor">
        <div class="title-row">
          <h2>{{ p.name }}</h2>
          <nz-tag [nzColor]="p.status === 'published' ? 'success' : 'default'">
            @if (p.status === 'published') {
              <span i18n>Published v{{ p.published_version }}</span>
            } @else {
              <span i18n>Draft</span>
            }
          </nz-tag>
          <span class="spacer"></span>
          <nz-select
            class="version-select"
            aria-label="Version"
            i18n-aria-label
            data-testid="test-version"
            [ngModel]="versionNo()"
            (ngModelChange)="selectVersion($event)"
          >
            @for (v of versions(); track v.version) {
              <nz-option [nzValue]="v.version" [nzLabel]="versionLabel(v)" />
            }
          </nz-select>
          <a nz-button [routerLink]="['/prompts', p.id, 'edit']" i18n>Edit</a>
          <button
            nz-button
            nzType="primary"
            [disabled]="p.published_version === versionNo()"
            (click)="publish()"
            data-testid="publish"
          >
            @if (p.published_version === versionNo()) {
              <span i18n>Published</span>
            } @else {
              <span i18n>Publish v{{ versionNo() }}</span>
            }
          </button>
        </div>

        @if (message(); as m) {
          <nz-alert
            [nzType]="m.ok ? 'success' : 'error'"
            [nzMessage]="m.text"
            nzShowIcon
            data-testid="test-message"
          />
        }

        <nz-card nzSize="small" nzTitle="Input" i18n-nzTitle>
          <div class="vars">
            @for (v of variables(); track v.name) {
              <div class="field">
                <label [for]="'tv-' + v.name">
                  {{ v.name }}
                  @if (v.required) {
                    <span class="required" aria-hidden="true">*</span>
                  }
                </label>
                @if (v.is_file) {
                  <input
                    type="file"
                    accept=".txt,.md,.csv,.json,.xml,.html,.yaml,.yml,text/*"
                    [id]="'tv-' + v.name"
                    [attr.data-testid]="'test-var-' + v.name"
                    (change)="readFile(v, $event)"
                  />
                  @if (values[v.name]) {
                    <small class="muted" i18n
                      >{{ values[v.name].length | number }} characters loaded</small
                    >
                  }
                } @else {
                  <textarea
                    nz-input
                    [id]="'tv-' + v.name"
                    [attr.data-testid]="'test-var-' + v.name"
                    cdkTextareaAutosize
                    cdkAutosizeMinRows="1"
                    cdkAutosizeMaxRows="8"
                    [(ngModel)]="values[v.name]"
                    (ngModelChange)="changed()"
                  ></textarea>
                }
              </div>
            } @empty {
              <p class="muted" i18n>This prompt has no variables.</p>
            }
          </div>
          <div class="bar">
            <button
              nz-button
              nzType="primary"
              [disabled]="!ready() || running()"
              [nzLoading]="running()"
              (click)="proceed()"
              data-testid="proceed"
              i18n
            >
              Proceed
            </button>
          </div>
        </nz-card>

        @if (result(); as r) {
          <nz-card
            class="result-card"
            nzSize="small"
            [nzTitle]="resultTitle"
            [nzExtra]="traceLink"
            data-testid="result"
          >
            <ng-template #resultTitle>
              <span i18n>Result</span>
              @if (r.status === 'error') {
                <nz-tag nzColor="error" i18n>Failed</nz-tag>
              }
            </ng-template>
            <ng-template #traceLink>
              <button
                nz-button
                nzType="link"
                nzSize="small"
                type="button"
                (click)="openTrace(r)"
                data-testid="trace-link"
                i18n
              >
                Trace
              </button>
            </ng-template>
            @if (r.status === 'ok') {
              <pre class="output" data-testid="result-output">{{ r.output }}</pre>
            } @else {
              <nz-alert
                nzType="error"
                [nzMessage]="r.error_code ?? ''"
                [nzDescription]="r.error ?? ''"
              />
            }
            <div class="stats" data-testid="result-stats">
              <div>
                <span class="big">{{ r.latency_ms | number }} ms</span
                ><span class="muted" i18n>Latency</span>
              </div>
              <div>
                <span class="big">{{ +r.cost | number: '1.2-6' }}</span
                ><span class="muted" i18n>Cost</span>
              </div>
              <div>
                <span class="big">{{ r.usage.total_tokens | number }}</span
                ><span class="muted" i18n
                  >Total usage ({{ r.usage.tokens_in }} in / {{ r.usage.tokens_out }} out)</span
                >
              </div>
              <div>
                <span class="big">{{ r.model_name ?? r.model }}</span
                ><span class="muted" i18n>Model</span>
              </div>
            </div>
          </nz-card>
        }

        <nz-card nzSize="small" nzTitle="Compare models" i18n-nzTitle data-testid="compare">
          <p class="muted" i18n>
            Run this version with the same input on 2 to 4 models, side by side.
          </p>
          <div class="head">
            <nz-select
              class="full"
              nzMode="multiple"
              [nzMaxMultipleCount]="4"
              nzPlaceHolder="Choose 2 to 4 models"
              i18n-nzPlaceHolder
              aria-label="Models to compare"
              i18n-aria-label
              data-testid="compare-models"
              [(ngModel)]="compareModels"
            >
              @for (o of models(); track o.value) {
                <nz-option [nzValue]="o.value" [nzLabel]="o.label" />
              }
            </nz-select>
            <button
              nz-button
              [disabled]="!ready() || compareModels.length < 2 || comparing()"
              [nzLoading]="comparing()"
              (click)="compare()"
              data-testid="compare-run"
              i18n
            >
              Compare
            </button>
          </div>
          @if (comparison().length) {
            <div class="compare">
              @for (r of comparison(); track r.id) {
                <nz-card
                  nzSize="small"
                  [nzTitle]="r.model"
                  [nzExtra]="cmpTrace"
                  [attr.data-testid]="'compare-' + r.model"
                >
                  <ng-template #cmpTrace>
                    <button
                      nz-button
                      nzType="link"
                      nzSize="small"
                      type="button"
                      (click)="openTrace(r)"
                      i18n
                    >
                      Trace
                    </button>
                  </ng-template>
                  @if (r.status === 'ok') {
                    <pre class="output">{{ r.output }}</pre>
                  } @else {
                    <nz-alert
                      nzType="error"
                      [nzMessage]="r.error_code ?? ''"
                      [nzDescription]="r.error ?? ''"
                    />
                  }
                  <div class="stats">
                    <div>
                      <span class="big">{{ r.latency_ms | number }} ms</span
                      ><span class="muted" i18n>Latency</span>
                    </div>
                    <div>
                      <span class="big">{{ r.usage.total_tokens | number }}</span
                      ><span class="muted" i18n>Tokens</span>
                    </div>
                    <div>
                      <span class="big">{{ +r.cost | number: '1.2-6' }}</span
                      ><span class="muted" i18n>Cost</span>
                    </div>
                  </div>
                </nz-card>
              }
            </div>
          }
        </nz-card>

        <nz-card nzSize="small" nzTitle="Recent runs" i18n-nzTitle>
          <nz-table [nzData]="runs()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="50">
            <thead>
              <tr>
                <th i18n>When</th>
                <th i18n>Kind</th>
                <th i18n>Version</th>
                <th i18n>Model</th>
                <th i18n>Status</th>
                <th i18n>Latency</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (r of runs(); track r.id) {
                <tr>
                  <td>{{ r.created_at | date: 'short' }}</td>
                  <td>{{ r.kind }}</td>
                  <td>v{{ r.version }}</td>
                  <td>{{ r.model_name ?? r.model }}</td>
                  <td>
                    <nz-tag [nzColor]="r.status === 'ok' ? 'success' : 'error'">{{
                      r.status
                    }}</nz-tag>
                  </td>
                  <td>{{ r.latency_ms | number }} ms</td>
                  <td class="actions">
                    <button
                      nz-button
                      nzType="link"
                      nzSize="small"
                      type="button"
                      (click)="openTrace(r)"
                      i18n
                    >
                      Trace
                    </button>
                  </td>
                </tr>
              }
            </tbody>
          </nz-table>
        </nz-card>
      </div>

      <nz-drawer
        [nzVisible]="!!trace()"
        nzTitle="Call trace"
        i18n-nzTitle
        [nzWidth]="640"
        (nzOnClose)="trace.set(null)"
      >
        <ng-container *nzDrawerContent>
          @if (trace(); as t) {
            <div class="form" data-testid="trace">
              <div class="stats">
                <div>
                  <span class="big">{{ t.latency_ms | number }} ms</span
                  ><span class="muted" i18n>Latency</span>
                </div>
                <div>
                  <span class="big">{{ +t.cost | number: '1.2-6' }}</span
                  ><span class="muted" i18n>Cost</span>
                </div>
                <div>
                  <span class="big">{{ t.usage.total_tokens | number }}</span
                  ><span class="muted" i18n>Total usage</span>
                </div>
              </div>
              <strong i18n>Input</strong>
              <div class="messages" data-testid="trace-input">
                @for (m of t.messages; track $index) {
                  <div class="message">
                    <div class="role">{{ m?.['role'] }}</div>
                    <pre class="output">{{ m?.['content'] }}</pre>
                  </div>
                }
              </div>
              <strong i18n>Output</strong>
              @if (t.status === 'ok') {
                <pre class="output" data-testid="trace-output">{{ t.output }}</pre>
              } @else {
                <nz-alert
                  nzType="error"
                  [nzMessage]="t.error_code ?? ''"
                  [nzDescription]="t.error ?? ''"
                />
              }
              <strong i18n>Metadata</strong>
              <pre class="json" data-testid="trace-metadata">{{ metadata(t) | json }}</pre>
            </div>
          }
        </ng-container>
      </nz-drawer>
    } @else if (message(); as m) {
      <nz-alert nzType="error" [nzMessage]="m.text" nzShowIcon />
    }
  `,
  styleUrls: ['../admin/admin.scss', './prompts.scss'],
  styles: [
    `
      .version-select {
        width: 12rem;
      }
      .required {
        color: var(--forge-danger-color, #cf1322);
      }
    `,
  ],
})
export class PromptTest {
  readonly id = input.required<string>();

  private readonly api = inject(PromptsService);
  private readonly session = inject(Session);
  private readonly modelOptions = inject(ModelOptions);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly prompt = signal<PromptDetail | null>(null);
  protected readonly version = signal<VersionOut | null>(null);
  protected readonly versionNo = computed(() => this.version()?.version ?? null);
  protected readonly versions = signal<VersionSummary[]>([]);
  protected readonly variables = computed<Variable[]>(() => this.version()?.variables ?? []);
  protected readonly models = signal<ModelOption[]>([]);
  protected readonly result = signal<RunDetail | null>(null);
  protected readonly comparison = signal<RunDetail[]>([]);
  protected readonly runs = signal<RunOut[]>([]);
  protected readonly trace = signal<RunDetail | null>(null);
  protected readonly running = signal(false);
  protected readonly comparing = signal(false);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  /** Bumped on input so `ready` re-evaluates (values is a plain object for ngModel). */
  private readonly tick = signal(0);
  protected values: Record<string, string> = {};
  protected compareModels: string[] = [];

  protected readonly ready = computed(() => {
    this.tick();
    return this.variables().every((v) => !v.required || !!(this.values[v.name] ?? '').trim());
  });

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      const id = this.id();
      if (projectId && id) void this.load(projectId, id);
    });
  }

  private async load(projectId: string, promptId: string): Promise<void> {
    try {
      const [prompt, versions, models] = await Promise.all([
        firstValueFrom(this.api.getPrompt({ projectId, promptId })),
        firstValueFrom(this.api.listPromptVersions({ projectId, promptId })),
        this.modelOptions.load(projectId),
      ]);
      this.prompt.set(prompt);
      this.versions.set(versions);
      this.models.set(models);
      this.setVersion(prompt.version);
      await this.loadRuns();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  private setVersion(v: VersionOut): void {
    this.version.set(v);
    const previous = this.values;
    this.values = {};
    for (const variable of v.variables) {
      this.values[variable.name] = previous[variable.name] ?? variable.value ?? '';
    }
    this.tick.update((n) => n + 1);
  }

  protected versionLabel(v: VersionSummary): string {
    return v.tags.length ? `v${v.version} (${v.tags.join(', ')})` : `v${v.version}`;
  }

  protected async selectVersion(n: number): Promise<void> {
    const p = this.prompt();
    if (!p) return;
    try {
      this.setVersion(
        await firstValueFrom(
          this.api.getPromptVersion({ projectId: this.projectId()!, promptId: p.id, version: n }),
        ),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected readFile(v: Variable, event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      this.message.set({ ok: false, text: $localize`The file is larger than 256 KB.` });
      return;
    }
    void file.text().then((text) => {
      this.values[v.name] = text;
      this.tick.update((n) => n + 1);
    });
  }

  protected changed(): void {
    this.tick.update((n) => n + 1);
  }

  private payload(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const v of this.variables()) out[v.name] = this.values[v.name] ?? '';
    return out;
  }

  protected async proceed(): Promise<void> {
    const p = this.prompt();
    if (!p) return;
    this.running.set(true);
    this.message.set(null);
    try {
      const run = await firstValueFrom(
        this.api.testPrompt({
          projectId: this.projectId()!,
          promptId: p.id,
          runIn: { version: this.versionNo(), variables: this.payload() },
        }),
      );
      this.result.set(run);
      await this.loadRuns();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.running.set(false);
    }
  }

  protected async compare(): Promise<void> {
    const p = this.prompt();
    if (!p) return;
    this.comparing.set(true);
    this.message.set(null);
    try {
      this.comparison.set(
        await firstValueFrom(
          this.api.playgroundPrompt({
            projectId: this.projectId()!,
            promptId: p.id,
            playgroundIn: {
              version: this.versionNo(),
              variables: this.payload(),
              models: this.compareModels,
            },
          }),
        ),
      );
      await this.loadRuns();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.comparing.set(false);
    }
  }

  protected async publish(): Promise<void> {
    const p = this.prompt();
    if (!p) return;
    try {
      const updated = await firstValueFrom(
        this.api.publishPrompt({
          projectId: this.projectId()!,
          promptId: p.id,
          publishIn: { version: this.versionNo() },
        }),
      );
      this.prompt.set(updated);
      this.versions.set(
        await firstValueFrom(
          this.api.listPromptVersions({ projectId: this.projectId()!, promptId: p.id }),
        ),
      );
      this.message.set({
        ok: true,
        text: $localize`Version ${updated.published_version}:n: is published to the Prompt Library.`,
      });
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  private async loadRuns(): Promise<void> {
    const p = this.prompt();
    if (!p) return;
    this.runs.set(
      await firstValueFrom(
        this.api.listPromptRuns({ projectId: this.projectId()!, promptId: p.id, limit: 20 }),
      ),
    );
  }

  protected async openTrace(run: RunOut): Promise<void> {
    try {
      this.trace.set(
        await firstValueFrom(
          this.api.getPromptRun({ projectId: this.projectId()!, runId: run.id }),
        ),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected metadata(t: RunDetail): Record<string, unknown> {
    return {
      run_id: t.id,
      kind: t.kind,
      prompt_version: t.version,
      model_requested: t.model,
      model_used: t.model_name,
      status: t.status,
      finish_reason: t.finish_reason,
      tokens_in: t.usage.tokens_in,
      tokens_out: t.usage.tokens_out,
      cost: t.cost,
      cache_hit: t.cache_hit,
      params: t.params,
      variables: t.variables,
      llm_call_id: t.llm_call_id,
      created_at: t.created_at,
    };
  }
}
