import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, OnDestroy, computed, effect, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTimelineModule } from 'ng-zorro-antd/timeline';
import { firstValueFrom } from 'rxjs';

import { RunStepOut, UseCaseRunDetail, UseCaseRunOut, UseCasesService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { TERMINAL, durationSeconds, pretty, statusColor, statusLabel } from './catalog-util';

const STEP_COLOR: Record<string, string> = { running: 'blue', succeeded: 'green', failed: 'red' };

/** A run for an end user (US-2.9.2, US-2.9.3): progress over SSE (a refreshed page picks a
 * running run up), the result, the files, re-run, and the other runs of the session. */
@Component({
  selector: 'forge-catalog-run',
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzTagModule,
    NzTimelineModule,
  ],
  template: `
    <div class="head">
      <a nz-button nzType="link" [routerLink]="back()" i18n>← Back</a>
    </div>
    @if (error(); as e) {
      <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
    }
    @if (run(); as r) {
      <div class="title-row">
        <h2>
          {{ r.use_case_name }} <span class="muted">v{{ r.version }}</span>
        </h2>
        <nz-tag [nzColor]="color(r.status)" data-testid="run-status">{{ label(r.status) }}</nz-tag>
        <span class="spacer"></span>
        @if (!finished()) {
          <button nz-button nzDanger (click)="stop()" data-testid="run-stop" i18n>Stop</button>
        }
        <button nz-button [disabled]="!finished()" (click)="again()" data-testid="run-again">
          <nz-icon nzType="reload" /><span i18n>Run again</span>
        </button>
      </div>
      <p class="muted">
        {{ r.created_at | date: 'medium' }} · {{ r.steps }} <span i18n>steps</span> ·
        {{ r.tokens_in + r.tokens_out | number }} <span i18n>tokens</span> ·
        {{ r.cost | number: '1.2-4' }} USD
        @if (seconds(); as s) {
          · {{ s }} s
        }
      </p>

      @if (r.status === 'failed') {
        <nz-alert
          nzType="error"
          nzShowIcon
          nzMessage="The run failed"
          i18n-nzMessage
          [nzDescription]="r.error ?? r.error_code ?? ''"
          data-testid="run-failure"
        />
      } @else if (r.status === 'cancelled') {
        <nz-alert nzType="warning" nzShowIcon nzMessage="The run was stopped" i18n-nzMessage />
      }

      <div class="cols">
        <section>
          <h3 i18n>Progress</h3>
          <nz-timeline data-testid="run-timeline">
            @for (s of steps(); track s.step_key) {
              <nz-timeline-item [nzColor]="stepColor(s.status)">
                <strong>{{ s.node_id }}</strong>
                <span class="muted">
                  {{ s.status === 'running' ? '…' : s.latency_ms + ' ms' }}</span
                >
              </nz-timeline-item>
            } @empty {
              <nz-timeline-item i18n>Waiting to start…</nz-timeline-item>
            }
          </nz-timeline>
        </section>
        <section>
          <h3 i18n>Result</h3>
          @if (result(); as out) {
            <pre class="result" data-testid="run-result">{{ out }}</pre>
          } @else if (finished()) {
            <p class="muted" i18n>This run has no result.</p>
          } @else {
            <p class="muted" i18n>The result appears here when the run is done.</p>
          }
          @if (fileError(); as fe) {
            <nz-alert nzType="warning" nzShowIcon [nzMessage]="fe" />
          }
          @if (r.status === 'succeeded') {
            <h3 i18n>Downloads</h3>
            <div class="downloads" data-testid="run-files">
              @for (f of r.files ?? []; track f.id) {
                <a nz-button [href]="fileUrl(f.id)" [attr.data-testid]="'file-' + f.name" download>
                  <nz-icon nzType="download" />{{ f.name }}
                </a>
              }
              <a nz-button [href]="exportUrl('markdown')" data-testid="export-md" download>
                <nz-icon nzType="download" /><span i18n>Markdown</span>
              </a>
              <a nz-button [href]="exportUrl('json')" data-testid="export-json" download>
                <nz-icon nzType="download" />JSON
              </a>
            </div>
          }
        </section>
      </div>

      @if (siblings().length > 1) {
        <h3 i18n>This session</h3>
        <ul class="session" data-testid="run-session">
          @for (s of siblings(); track s.id) {
            <li>
              <a [routerLink]="['/catalog/runs', s.id]">{{ s.created_at | date: 'short' }}</a>
              <nz-tag [nzColor]="color(s.status)">{{ label(s.status) }}</nz-tag>
              @if (s.id === r.id) {
                <span class="muted" i18n>(this run)</span>
              }
            </li>
          }
        </ul>
      }
    }
  `,
  styles: `
    .title-row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem;
    }
    .title-row h2 {
      margin: 0;
    }
    .cols {
      display: grid;
      grid-template-columns: minmax(14rem, 20rem) 1fr;
      gap: 1.5rem;
      align-items: start;
    }
    .result {
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid var(--forge-surface-200);
      border-radius: 6px;
      padding: 0.75rem;
    }
    .downloads {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    @media (max-width: 800px) {
      .cols {
        grid-template-columns: 1fr;
      }
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class CatalogRun implements OnDestroy {
  private readonly api = inject(UseCasesService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly session = inject(Session);

  protected readonly run = signal<UseCaseRunDetail | null>(null);
  protected readonly siblings = signal<UseCaseRunOut[]>([]);
  protected readonly error = signal<string | null>(null);
  private readonly stepMap = signal<Record<string, RunStepOut>>({});
  private source: EventSource | null = null;
  private runId = '';

  protected readonly steps = computed(() => Object.values(this.stepMap()));
  protected readonly finished = computed(() => {
    const r = this.run();
    return !!r && TERMINAL.has(r.status);
  });
  protected readonly result = computed(() => {
    const out = this.run()?.outputs?.['result'];
    return out === undefined || out === null ? null : pretty(out);
  });
  protected readonly fileError = computed(() => {
    const fe = this.run()?.outputs?.['file_error'];
    return typeof fe === 'string' ? fe : null;
  });
  protected readonly seconds = computed(() => {
    const r = this.run();
    return r ? durationSeconds(r) : null;
  });
  protected readonly back = computed(() => {
    const id = this.run()?.use_case_id;
    return id ? ['/catalog', id] : ['/catalog'];
  });

  constructor() {
    effect(() => {
      if (this.session.project()) void this.load();
    });
    // Navigating from one run to another (re-run, session list) reuses this component.
    this.route.paramMap.subscribe(() => {
      if (this.session.project()) void this.load();
    });
  }

  ngOnDestroy(): void {
    this.source?.close();
  }

  protected color = statusColor;
  protected label = statusLabel;

  protected stepColor(status: string): string {
    return STEP_COLOR[status] ?? 'gray';
  }

  protected fileUrl(fileId: string): string {
    return `/api/v1/projects/${this.session.project()!.id}/use-case-files/${fileId}/content`;
  }

  protected exportUrl(format: 'markdown' | 'json'): string {
    return `/api/v1/projects/${this.session.project()!.id}/use-case-runs/${this.runId}/export?format=${format}`;
  }

  private async load(): Promise<void> {
    const id = this.route.snapshot.paramMap.get('runId')!;
    if (id === this.runId && this.run()) return;
    this.source?.close();
    this.runId = id;
    const projectId = this.session.project()!.id;
    try {
      const run = await firstValueFrom(this.api.getUseCaseRun({ projectId, runId: id }));
      this.apply(run);
      this.error.set(null);
      if (!TERMINAL.has(run.status)) this.follow(projectId, id);
      void this.loadSession(projectId, run);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  private async loadSession(projectId: string, run: UseCaseRunDetail): Promise<void> {
    if (!run.session_id) {
      this.siblings.set([]);
      return;
    }
    const list = await firstValueFrom(
      this.api.listUseCaseRuns({ projectId, sessionId: run.session_id }),
    );
    this.siblings.set([...list].reverse());
  }

  private apply(run: UseCaseRunDetail): void {
    this.run.set(run);
    const map: Record<string, RunStepOut> = {};
    for (const s of run.step_list ?? []) map[s.step_key] = s;
    this.stepMap.set(map);
  }

  private follow(projectId: string, id: string): void {
    const source = new EventSource(`/api/v1/projects/${projectId}/use-case-runs/${id}/events`);
    this.source = source;
    const parse = (e: Event): { run?: UseCaseRunDetail; step?: RunStepOut } =>
      JSON.parse((e as MessageEvent<string>).data);
    source.addEventListener('run', (e) => {
      const { run } = parse(e);
      if (run) this.run.update((cur) => ({ ...(cur as UseCaseRunDetail), ...run }));
    });
    source.addEventListener('step', (e) => {
      const { step } = parse(e);
      if (step) this.stepMap.update((m) => ({ ...m, [step.step_key]: step }));
    });
    source.addEventListener('done', (e) => {
      const { run } = parse(e);
      if (run) this.apply(run);
      source.close();
      void this.loadSession(projectId, this.run()!);
    });
    source.onerror = () => {
      // The server ends the stream after `done`; a closed connection that is not a reconnect
      // (for example a 404) is not retried.
      if (source.readyState === EventSource.CLOSED) source.close();
    };
  }

  protected async stop(): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    try {
      this.apply(await firstValueFrom(this.api.cancelUseCaseRun({ projectId, runId: this.runId })));
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async again(): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    try {
      const next = await firstValueFrom(this.api.rerunUseCaseRun({ projectId, runId: this.runId }));
      await this.router.navigate(['/catalog/runs', next.id]);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
