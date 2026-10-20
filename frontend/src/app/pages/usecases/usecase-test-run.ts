import { Component, OnDestroy, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { UseCaseDetail, UseCaseRunDetail, UseCasesService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);
const COLOR: Record<string, string> = {
  queued: 'default',
  running: 'processing',
  waiting_approval: 'warning',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'default',
};

/** Test run of the draft (US-2.6.2): fills the input form, runs the draft on the engine and
 * follows the steps. The full test page with one pane per agent arrives with the builder. */
@Component({
  selector: 'forge-usecase-test-run',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <h3 i18n>Test run</h3>
    <p class="intro" i18n>Runs the current saved draft with the real agents, tools and prompts.</p>
    <form class="form" (ngSubmit)="start()" data-testid="uc-test-form">
      @for (f of fields(); track f.name) {
        <label [attr.for]="'run-' + f.name"
          >{{ f.label || f.name }}{{ f.required ? ' *' : '' }}</label
        >
        @if (f.type === 'long_text') {
          <textarea
            nz-input
            rows="3"
            [id]="'run-' + f.name"
            [name]="f.name"
            [ngModel]="values()[f.name] ?? ''"
            (ngModelChange)="set(f.name, $event)"
          ></textarea>
        } @else {
          <input
            nz-input
            [id]="'run-' + f.name"
            [name]="f.name"
            [type]="f.type === 'number' ? 'number' : 'text'"
            [ngModel]="values()[f.name] ?? ''"
            (ngModelChange)="set(f.name, $event)"
          />
        }
        @if (f.help) {
          <small class="muted">{{ f.help }}</small>
        }
      }
      @if (error(); as e) {
        <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
      }
      <div class="buttons">
        @if (running()) {
          <button nz-button nzDanger type="button" (click)="cancel()" i18n>Stop</button>
        }
        <button
          nz-button
          nzType="primary"
          type="submit"
          [disabled]="running()"
          data-testid="uc-run-test"
        >
          <nz-icon nzType="play-circle" /><span i18n>Run test</span>
        </button>
      </div>
    </form>

    @if (run(); as r) {
      <div class="run" data-testid="uc-run">
        <p>
          <nz-tag [nzColor]="color(r.status)" data-testid="uc-run-status">{{ r.status }}</nz-tag>
          <span class="muted" i18n
            >{{ r.steps }} steps · {{ r.tokens_in + r.tokens_out }} tokens · {{ r.cost }} USD</span
          >
        </p>
        @if (r.error) {
          <nz-alert
            nzType="error"
            nzShowIcon
            [nzMessage]="r.error_code + (r.error_node ? ' (' + r.error_node + ')' : '')"
            [nzDescription]="r.error"
          />
        }
        <nz-table
          [nzData]="r.step_list ?? []"
          nzSize="small"
          [nzShowPagination]="false"
          [nzPageSize]="1000"
        >
          <thead>
            <tr>
              <th i18n>Step</th>
              <th i18n>Type</th>
              <th i18n>Status</th>
              <th i18n>Output</th>
              <th i18n>Time</th>
            </tr>
          </thead>
          <tbody>
            @for (s of r.step_list ?? []; track s.step_key) {
              <tr [attr.data-testid]="'uc-step-' + s.step_key">
                <td>{{ s.step_key }}</td>
                <td>{{ s.node_type }}</td>
                <td>
                  <nz-tag
                    [nzColor]="
                      s.status === 'failed'
                        ? 'error'
                        : s.status === 'running'
                          ? 'processing'
                          : 'success'
                    "
                    >{{ s.status }}</nz-tag
                  >
                </td>
                <td class="clip">{{ preview(s.output) }}{{ s.error ? s.error : '' }}</td>
                <td>{{ s.latency_ms }} ms</td>
              </tr>
            } @empty {
              <tr>
                <td colspan="5" class="muted" i18n>Waiting for the first step…</td>
              </tr>
            }
          </tbody>
        </nz-table>
        @if (result(); as out) {
          <h4 i18n>Result</h4>
          <pre class="result" data-testid="uc-run-result">{{ out }}</pre>
        }
      </div>
    }
  `,
  styles: `
    .clip {
      max-width: 28rem;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .result {
      white-space: pre-wrap;
      border: 1px solid var(--forge-surface-200);
      border-radius: 6px;
      padding: 0.75rem;
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class UseCaseTestRun implements OnDestroy {
  private readonly api = inject(UseCasesService);
  private readonly session = inject(Session);

  readonly useCase = input.required<UseCaseDetail>();
  protected readonly fields = computed(
    () =>
      this.useCase().input_fields as {
        name: string;
        label?: string;
        type: string;
        required?: boolean;
        help?: string;
      }[],
  );
  protected readonly values = signal<Record<string, string>>({});
  protected readonly run = signal<UseCaseRunDetail | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly running = computed(() => {
    const r = this.run();
    return !!r && !TERMINAL.has(r.status);
  });
  protected readonly result = computed(() => {
    const out = this.run()?.outputs?.['result'];
    if (out === undefined || out === null) return null;
    return typeof out === 'string' ? out : JSON.stringify(out, null, 2);
  });
  private timer: ReturnType<typeof setTimeout> | null = null;

  ngOnDestroy(): void {
    if (this.timer) clearTimeout(this.timer);
  }

  protected color(status: string): string {
    return COLOR[status] ?? 'default';
  }

  protected set(name: string, value: string): void {
    this.values.update((v) => ({ ...v, [name]: value }));
  }

  protected preview(value: unknown): string {
    const raw = (value as { value?: unknown } | null)?.value;
    if (raw === undefined || raw === null) return '';
    return (typeof raw === 'string' ? raw : JSON.stringify(raw)).slice(0, 300);
  }

  private inputs(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const f of this.fields()) {
      const raw = this.values()[f.name];
      if (raw === undefined || raw === '') continue;
      out[f.name] = f.type === 'number' ? Number(raw) : raw;
    }
    return out;
  }

  protected async start(): Promise<void> {
    this.error.set(null);
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    try {
      const started = await firstValueFrom(
        this.api.startUseCaseRun({
          projectId,
          useCaseId: this.useCase().id,
          runStartIn: { inputs: this.inputs(), mode: 'test' },
        }),
      );
      this.run.set(started);
      this.poll(started.id);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  private poll(runId: string): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      const projectId = this.session.project()?.id;
      if (!projectId) return;
      try {
        const r = await firstValueFrom(this.api.getUseCaseRun({ projectId, runId }));
        this.run.set(r);
        if (!TERMINAL.has(r.status)) this.poll(runId);
      } catch (e) {
        this.error.set(problemMessage(e));
      }
    }, 1200);
  }

  protected async cancel(): Promise<void> {
    const r = this.run();
    const projectId = this.session.project()?.id;
    if (!r || !projectId) return;
    try {
      this.run.set(await firstValueFrom(this.api.cancelUseCaseRun({ projectId, runId: r.id })));
      this.poll(r.id);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
