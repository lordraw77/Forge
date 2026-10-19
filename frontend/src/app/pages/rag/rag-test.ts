import { DatePipe, DecimalPipe, JsonPipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TextFieldModule } from '@angular/cdk/text-field';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzDrawerModule } from 'ng-zorro-antd/drawer';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  PipelineDetail,
  PipelineVersionOut,
  RagRunDetail,
  RagRunOut,
  RagService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

interface RankRow {
  chunk_id: string;
  document: string;
  page: number | null;
  score: number;
  cosine: number;
  ranks: Record<string, number>;
  selected: boolean;
}

/** Test page (US-1.17.1): query → answer with citations, trace; Publish (US-1.17.2). */
@Component({
  selector: 'forge-rag-test',
  imports: [
    DatePipe,
    DecimalPipe,
    JsonPipe,
    FormsModule,
    RouterLink,
    TextFieldModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzDrawerModule,
    NzInputModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (pipeline(); as p) {
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
            [ngModel]="versionNo()"
            (ngModelChange)="versionNo.set($event)"
          >
            @for (v of versions(); track v.version) {
              <nz-option [nzValue]="v.version" [nzLabel]="versionLabel(v)" />
            }
          </nz-select>
          <a nz-button [routerLink]="['/rag', p.id, 'edit']" i18n>Edit</a>
          <button
            nz-button
            nzType="primary"
            [disabled]="p.published_version === versionNo()"
            (click)="publish()"
            data-testid="rag-publish"
          >
            @if (p.published_version === versionNo()) {
              <span i18n>Published</span>
            } @else {
              <span i18n>Publish RAG v{{ versionNo() }}</span>
            }
          </button>
        </div>

        @if (selected(); as v) {
          @if (v.kb_status !== 'ready') {
            <nz-alert
              nzType="info"
              nzShowIcon
              data-testid="rag-index-building"
              nzMessage="The index of this version is still being built; answers use the previous ready index or may be incomplete."
              i18n-nzMessage
            />
          }
        }
        @if (message(); as m) {
          <nz-alert
            [nzType]="m.ok ? 'success' : 'error'"
            [nzMessage]="m.text"
            nzShowIcon
            data-testid="rag-test-message"
          />
        }

        <nz-card nzSize="small" nzTitle="Query" i18n-nzTitle>
          <!-- CDK autosize needs a parent element, not the card's projected slot. -->
          <div>
            <textarea
              nz-input
              class="full"
              aria-label="Question"
              i18n-aria-label
              placeholder="Ask a question about the documents"
              i18n-placeholder
              maxlength="4000"
              data-testid="rag-query"
              cdkTextareaAutosize
              cdkAutosizeMinRows="2"
              cdkAutosizeMaxRows="8"
              [(ngModel)]="query"
            ></textarea>
          </div>
          <div class="bar">
            <button
              nz-button
              nzType="primary"
              [disabled]="!query.trim() || running() || !loaded()"
              [nzLoading]="running()"
              (click)="proceed()"
              data-testid="rag-proceed"
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
            data-testid="rag-result"
          >
            <ng-template #resultTitle>
              <span i18n>Answer</span>
              @if (r.status === 'error') {
                <nz-tag nzColor="error" i18n>Failed</nz-tag>
              }
              @if (r.status === 'no_context') {
                <nz-tag nzColor="warning" i18n>No relevant information</nz-tag>
              }
            </ng-template>
            <ng-template #traceLink>
              <button
                nz-button
                nzType="link"
                nzSize="small"
                type="button"
                (click)="openTrace(r)"
                data-testid="rag-trace-link"
                i18n
              >
                Trace
              </button>
            </ng-template>
            @if (r.status === 'error') {
              <nz-alert
                nzType="error"
                [nzMessage]="r.error_code ?? ''"
                [nzDescription]="r.error ?? ''"
              />
            } @else if (r.status === 'no_context') {
              <p data-testid="rag-answer" i18n>
                No relevant information was found in the knowledge base for this question.
              </p>
            } @else {
              <pre class="output" data-testid="rag-answer">{{ r.answer }}</pre>
            }
            @if (r.citations.length) {
              <h4 i18n>Sources</h4>
              <ol class="citations" data-testid="rag-citations">
                @for (c of r.citations; track c.chunk_id) {
                  <li [value]="c.index">
                    <a [href]="c.url" target="_blank" rel="noopener">
                      {{ c.document_name }}
                      @if (c.page) {
                        <span i18n>, page {{ c.page }}</span>
                      }
                    </a>
                    @if (c.heading_path) {
                      <span class="muted small"> · {{ c.heading_path }}</span>
                    }
                    <span class="muted small"> · {{ c.score | number: '1.2-2' }}</span>
                    <div class="muted small excerpt">{{ c.excerpt }}</div>
                  </li>
                }
              </ol>
            }
            <div class="stats" data-testid="rag-stats">
              <div>
                <span class="big">{{ r.latency_ms | number }} ms</span
                ><span class="muted" i18n>Latency</span>
              </div>
              <div>
                <span class="big">{{ +r.cost | number: '1.2-6' }}</span
                ><span class="muted" i18n>Cost</span>
              </div>
              <div>
                <span class="big">{{ r.total_tokens | number }}</span
                ><span class="muted" i18n
                  >Total usage ({{ r.tokens_in }} in / {{ r.tokens_out }} out)</span
                >
              </div>
              <div>
                <span class="big">{{ r.model_name ?? '–' }}</span
                ><span class="muted" i18n>Model</span>
              </div>
            </div>
          </nz-card>
        }

        <nz-card nzSize="small" nzTitle="Recent runs" i18n-nzTitle>
          <nz-table [nzData]="runs()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="50">
            <thead>
              <tr>
                <th i18n>When</th>
                <th i18n>Question</th>
                <th i18n>Version</th>
                <th i18n>Status</th>
                <th i18n>Latency</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              @for (r of runs(); track r.id) {
                <tr>
                  <td>{{ r.created_at | date: 'short' }}</td>
                  <td>{{ r.query }}</td>
                  <td>v{{ r.version }}</td>
                  <td>
                    <nz-tag
                      [nzColor]="
                        r.status === 'ok' ? 'success' : r.status === 'error' ? 'error' : 'warning'
                      "
                      >{{ r.status }}</nz-tag
                    >
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
        nzTitle="Trace"
        i18n-nzTitle
        [nzWidth]="760"
        (nzOnClose)="trace.set(null)"
      >
        <ng-container *nzDrawerContent>
          @if (trace(); as t) {
            <div class="form" data-testid="rag-trace">
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
                  <span class="big">{{ t.total_tokens | number }}</span
                  ><span class="muted" i18n>Total usage</span>
                </div>
              </div>
              @if (t.retrieval['warning']) {
                <nz-alert nzType="warning" [nzMessage]="t.retrieval['warning']" />
              }
              @if (t.retrieval['rerank_skipped']) {
                <nz-alert nzType="info" [nzMessage]="rerankSkipped" />
                <ng-template #rerankSkipped>
                  <span i18n>Reranker not used: {{ t.retrieval['rerank_skipped'] }}</span>
                </ng-template>
              }
              <strong i18n>Retrieval</strong>
              <nz-table
                [nzData]="ranking(t)"
                nzSize="small"
                [nzShowPagination]="false"
                [nzPageSize]="50"
              >
                <thead>
                  <tr>
                    <th i18n>Document</th>
                    <th i18n>Page</th>
                    <th i18n>Score</th>
                    <th i18n>Cosine</th>
                    <th i18n>Ranks</th>
                    <th i18n>Used</th>
                  </tr>
                </thead>
                <tbody>
                  @for (row of ranking(t); track row.chunk_id) {
                    <tr>
                      <td>{{ row.document }}</td>
                      <td>{{ row.page ?? '' }}</td>
                      <td>{{ row.score | number: '1.3-3' }}</td>
                      <td>{{ row.cosine | number: '1.3-3' }}</td>
                      <td>{{ row.ranks | json }}</td>
                      <td>{{ row.selected ? '✓' : '' }}</td>
                    </tr>
                  }
                </tbody>
              </nz-table>
              <strong i18n>Input</strong>
              <div class="messages">
                @for (m of t.messages; track $index) {
                  <div class="message">
                    <div class="role">{{ m?.['role'] }}</div>
                    <pre class="output">{{ m?.['content'] }}</pre>
                  </div>
                }
              </div>
              <strong i18n>Output</strong>
              <pre class="output">{{ t.answer }}</pre>
              <strong i18n>Metadata</strong>
              <pre class="json" data-testid="rag-trace-metadata">{{ metadata(t) | json }}</pre>
            </div>
          }
        </ng-container>
      </nz-drawer>
    } @else if (message(); as m) {
      <nz-alert nzType="error" [nzMessage]="m.text" nzShowIcon />
    }
  `,
  styleUrls: ['../admin/admin.scss', '../prompts/prompts.scss'],
  styles: [
    `
      .version-select {
        width: 12rem;
      }
      .citations li {
        margin-bottom: 0.5rem;
      }
      .excerpt {
        white-space: pre-wrap;
        max-height: 4.5em;
        overflow: hidden;
      }
    `,
  ],
})
export class RagTest {
  readonly id = input.required<string>();

  private readonly api = inject(RagService);
  private readonly session = inject(Session);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly pipeline = signal<PipelineDetail | null>(null);
  protected readonly versions = signal<PipelineVersionOut[]>([]);
  protected readonly versionNo = signal<number | null>(null);
  protected readonly selected = computed(
    () => this.versions().find((v) => v.version === this.versionNo()) ?? null,
  );
  protected readonly result = signal<RagRunDetail | null>(null);
  protected readonly runs = signal<RagRunOut[]>([]);
  protected readonly trace = signal<RagRunDetail | null>(null);
  protected readonly running = signal(false);
  /** False until the pipeline, its versions and the first runs are loaded. */
  protected readonly loaded = signal(false);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected query = '';

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      const id = this.id();
      if (projectId && id) void this.load(projectId, id);
    });
  }

  private async load(projectId: string, pipelineId: string): Promise<void> {
    this.loaded.set(false);
    try {
      const [p, versions] = await Promise.all([
        firstValueFrom(this.api.getRagPipeline({ projectId, pipelineId })),
        firstValueFrom(this.api.listRagPipelineVersions({ projectId, pipelineId })),
      ]);
      this.pipeline.set(p);
      this.versions.set(versions);
      this.versionNo.set(p.latest_version);
      await this.loadRuns();
      this.loaded.set(true);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected versionLabel(v: PipelineVersionOut): string {
    return v.tags.length ? `v${v.version} (${v.tags.join(', ')})` : `v${v.version}`;
  }

  protected async proceed(): Promise<void> {
    const p = this.pipeline();
    if (!p) return;
    this.running.set(true);
    this.message.set(null);
    try {
      this.result.set(
        await firstValueFrom(
          this.api.queryRagPipeline({
            projectId: this.projectId()!,
            pipelineId: p.id,
            queryIn: { query: this.query.trim(), version: this.versionNo() },
          }),
        ),
      );
      await this.loadRuns();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.running.set(false);
    }
  }

  protected async publish(): Promise<void> {
    const p = this.pipeline();
    if (!p) return;
    try {
      const updated = await firstValueFrom(
        this.api.publishRagPipeline({
          projectId: this.projectId()!,
          pipelineId: p.id,
          pipelinePublishIn: { version: this.versionNo() },
        }),
      );
      this.pipeline.set(updated);
      this.versions.set(
        await firstValueFrom(
          this.api.listRagPipelineVersions({ projectId: this.projectId()!, pipelineId: p.id }),
        ),
      );
      this.message.set({
        ok: true,
        text: $localize`Version ${updated.published_version}:n: is published to the RAG Library.`,
      });
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  private async loadRuns(): Promise<void> {
    const p = this.pipeline();
    if (!p) return;
    this.runs.set(
      await firstValueFrom(
        this.api.listRagRuns({ projectId: this.projectId()!, pipelineId: p.id, limit: 20 }),
      ),
    );
  }

  protected async openTrace(run: RagRunOut): Promise<void> {
    try {
      this.trace.set(
        await firstValueFrom(this.api.getRagRun({ projectId: this.projectId()!, runId: run.id })),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected ranking(t: RagRunDetail): RankRow[] {
    return (t.retrieval['ranking'] as RankRow[] | undefined) ?? [];
  }

  protected metadata(t: RagRunDetail): Record<string, unknown> {
    const retrieval = { ...t.retrieval };
    delete retrieval['ranking'];
    return {
      run_id: t.id,
      pipeline_version: t.version,
      status: t.status,
      model: t.model_name,
      tokens_in: t.tokens_in,
      tokens_out: t.tokens_out,
      cost: t.cost,
      retrieval,
      created_at: t.created_at,
    };
  }
}
