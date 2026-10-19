import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { AgentRunDetail, AgentsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { failureLabel, pretty, statusColor, statusLabel } from './agent-util';

/** One run (US-1.20.1): steps with tokens, cost, latency and time to first token; totals that
 * equal the sums of the steps; citations; the failure reason. */
@Component({
  selector: 'forge-agent-run-view',
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzIconModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <a nz-button nzType="link" routerLink="/agents/runs" i18n>← Runs</a>
    </div>
    @if (message(); as m) {
      <nz-alert nzType="error" [nzMessage]="m" nzShowIcon />
    }
    @if (run(); as r) {
      <div class="editor" data-testid="run-view">
        <div class="title-row">
          <h2>
            {{ r.agent_name }} <span class="muted">v{{ r.agent_version }}</span>
          </h2>
          <nz-tag [nzColor]="color(r.status)" data-testid="run-status">{{
            label(r.status)
          }}</nz-tag>
          <span class="spacer"></span>
          <span class="muted small">{{ r.created_at | date: 'medium' }}</span>
        </div>

        @if (r.status === 'failed' || r.status === 'cancelled') {
          <nz-alert
            [nzType]="r.status === 'failed' ? 'error' : 'warning'"
            [nzMessage]="failure(r)"
            [nzDescription]="r.error_code ?? ''"
            nzShowIcon
            data-testid="run-failure"
          />
        }

        <nz-card nzSize="small" nzTitle="Question and answer" i18n-nzTitle>
          <strong i18n>Question</strong>
          <p class="output">{{ r.input }}</p>
          <strong i18n>Answer</strong>
          <p class="output" data-testid="run-output">{{ r.output ?? '' }}</p>
          @if (r.citations.length) {
            <strong i18n>Sources</strong>
            <ol class="sources">
              @for (c of r.citations; track c.chunk_id) {
                <li [value]="c.index">
                  <a [href]="c.url" target="_blank" rel="noopener">{{ c.document_name }}</a>
                  @if (c.page) {
                    <span class="muted" i18n> · page {{ c.page }}</span>
                  }
                  @if (c.pipeline) {
                    <span class="muted"> · {{ c.pipeline }}</span>
                  }
                </li>
              }
            </ol>
          }
          @if (r.feedback; as f) {
            <p class="muted" data-testid="run-feedback">
              <nz-icon [nzType]="f.rating === 'up' ? 'like' : 'dislike'" />
              {{ f.comment }}
            </p>
          }
          <div class="run-meta" data-testid="run-totals">
            <div>
              <span class="big">{{ r.latency_ms ?? 0 | number }} ms</span
              ><span class="muted" i18n>Latency</span>
            </div>
            <div>
              <span class="big">{{ +r.cost | number: '1.2-6' }}</span
              ><span class="muted" i18n>Cost (USD)</span>
            </div>
            <div>
              <span class="big">{{ r.total_tokens | number }}</span
              ><span class="muted" i18n
                >Tokens ({{ r.tokens_in }} in / {{ r.tokens_out }} out)</span
              >
            </div>
            <div>
              <span class="big">{{ r.steps }}</span
              ><span class="muted" i18n>Steps</span>
            </div>
          </div>
        </nz-card>

        <nz-card nzSize="small" nzTitle="Steps" i18n-nzTitle>
          <nz-table
            [nzData]="r.step_list"
            nzSize="small"
            [nzShowPagination]="false"
            [nzPageSize]="1000"
            data-testid="run-steps"
          >
            <thead>
              <tr>
                <th>#</th>
                <th i18n>Step</th>
                <th i18n>Status</th>
                <th i18n>Model</th>
                <th i18n>Tokens in/out</th>
                <th i18n>Cost</th>
                <th i18n>Latency</th>
                <th i18n>First token</th>
              </tr>
            </thead>
            <tbody>
              @for (s of r.step_list; track s.ordinal) {
                <tr>
                  <td>{{ s.ordinal + 1 }}</td>
                  <td>
                    <nz-tag>{{ s.kind === 'tool' ? 'tool' : 'model' }}</nz-tag>
                    {{ s.name }}
                    <details>
                      <summary class="muted small" i18n>Input and output</summary>
                      <pre class="json">{{ show(s.input) }}</pre>
                      <pre class="json">{{ show(s.output) }}</pre>
                    </details>
                  </td>
                  <td>
                    <nz-tag [nzColor]="s.status === 'ok' ? 'success' : 'error'">{{
                      s.error_code ?? s.status
                    }}</nz-tag>
                  </td>
                  <td>{{ s.model_name ?? '–' }}</td>
                  <td>{{ s.tokens_in }} / {{ s.tokens_out }}</td>
                  <td>{{ +s.cost | number: '1.2-6' }}</td>
                  <td>{{ s.latency_ms | number }} ms</td>
                  <td>{{ s.ttft_ms === null ? '–' : (s.ttft_ms | number) + ' ms' }}</td>
                </tr>
              }
            </tbody>
          </nz-table>
        </nz-card>

        <nz-card nzSize="small" nzTitle="Limits" i18n-nzTitle>
          <pre class="json">{{ show(r.limits) }}</pre>
          <p class="muted small">
            <span i18n>Trace id:</span> <code>{{ r.trace_id }}</code>
          </p>
        </nz-card>
      </div>
    }
  `,
  styleUrls: ['../admin/admin.scss', '../prompts/prompts.scss', './agents.scss'],
})
export class AgentRunView {
  readonly runId = input.required<string>();

  private readonly api = inject(AgentsService);
  private readonly session = inject(Session);
  private readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly run = signal<AgentRunDetail | null>(null);
  protected readonly message = signal<string | null>(null);
  protected readonly color = statusColor;
  protected readonly label = statusLabel;
  protected readonly show = pretty;

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      const runId = this.runId();
      if (projectId) void this.load(projectId, runId);
    });
  }

  private async load(projectId: string, runId: string): Promise<void> {
    try {
      this.run.set(await firstValueFrom(this.api.getRun({ projectId, runId })));
      this.message.set(null);
    } catch (e) {
      this.message.set(problemMessage(e));
    }
  }

  protected failure(r: AgentRunDetail): string {
    return failureLabel(r.error_code, r.error);
  }
}
