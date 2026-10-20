import { Component, OnDestroy, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTabsModule } from 'ng-zorro-antd/tabs';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTimelineModule } from 'ng-zorro-antd/timeline';
import { firstValueFrom } from 'rxjs';

import {
  CatalogOut,
  RunStepOut,
  UseCaseDetail,
  UseCaseRunDetail,
  UseCasesService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { GraphCanvas } from './graph/graph-canvas';
import { UseCaseTestRun } from './usecase-test-run';

const STEP_COLOR: Record<string, string> = { running: 'blue', succeeded: 'green', failed: 'red' };

/** Build Your Own, page 3 (US-2.8.4): run the draft with its input form, follow every agent in
 * its own pane (live while it streams), the shared timeline and the current node on the graph,
 * then publish. */
@Component({
  selector: 'forge-usecase-test',
  imports: [
    FormsModule,
    RouterLink,
    GraphCanvas,
    UseCaseTestRun,
    NzAlertModule,
    NzButtonModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTabsModule,
    NzTagModule,
    NzTimelineModule,
  ],
  template: `
    <div class="head">
      <h2>
        <span i18n>3. Test workflow</span>
        @if (detail(); as d) {
          <span class="muted"> · {{ d.name }}</span>
        }
      </h2>
      <span class="spacer"></span>
      <a nz-button [routerLink]="['..', 'workflow']" i18n>Back to workflow</a>
      @if (detail(); as d) {
        <button nz-button nzType="primary" (click)="openPublish()" data-testid="test-publish" i18n>
          Publish
        </button>
      }
    </div>
    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }

    @if (detail(); as d) {
      <div class="grid">
        <div>
          <forge-usecase-test-run [useCase]="d" [showSteps]="false" (runChange)="onRun($event)" />
        </div>
        <div>
          @if (d.pattern === 'graph') {
            <h3 i18n>Graph</h3>
            <div class="graph-box">
              <forge-graph-canvas
                [definition]="graphDefinition()"
                [readonly]="true"
                [states]="states()"
              />
            </div>
          }
          <h3 i18n>Timeline</h3>
          <nz-timeline data-testid="test-timeline">
            @for (s of steps(); track s.step_key) {
              <nz-timeline-item [nzColor]="color(s.status)">
                <strong>{{ s.step_key }}</strong> <nz-tag>{{ s.node_type }}</nz-tag> {{ s.status }}
                <span class="muted">{{ s.latency_ms }} ms</span>
              </nz-timeline-item>
            } @empty {
              <nz-timeline-item i18n>Nothing has run yet.</nz-timeline-item>
            }
          </nz-timeline>
        </div>
      </div>

      <h3 i18n>Outputs by step</h3>
      @if (steps().length) {
        <nz-tabs [(nzSelectedIndex)]="tab" data-testid="test-panes">
          @for (s of steps(); track s.step_key) {
            <nz-tab [nzTitle]="s.step_key">
              <pre class="pane" [attr.data-testid]="'pane-' + s.step_key">{{ paneText(s) }}</pre>
            </nz-tab>
          }
        </nz-tabs>
      } @else {
        <p class="muted" i18n>Each agent's output appears here, live while it writes.</p>
      }
    }

    <nz-modal
      [(nzVisible)]="publishOpen"
      nzTitle="Publish use case"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="480"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="publish()" data-testid="publish-form">
          <label for="pb-desc" i18n>Description</label>
          <textarea
            nz-input
            id="pb-desc"
            name="description"
            rows="3"
            maxlength="2000"
            [(ngModel)]="description"
          ></textarea>
          <label for="pb-catalog" i18n>Catalog</label>
          <nz-select
            nzId="pb-catalog"
            name="catalog"
            [(ngModel)]="catalogId"
            (ngModelChange)="categoryId = null"
          >
            @for (c of catalogs(); track c.id) {
              <nz-option [nzValue]="c.id" [nzLabel]="c.name" />
            }
          </nz-select>
          <label for="pb-category" i18n>Category</label>
          <nz-select
            nzId="pb-category"
            name="category"
            [(ngModel)]="categoryId"
            [nzDisabled]="!catalogId"
          >
            @for (k of categories(); track k.id) {
              <nz-option [nzValue]="k.id" [nzLabel]="k.name" />
            }
          </nz-select>
          <label for="pb-note" i18n>Note</label>
          <input nz-input id="pb-note" name="note" maxlength="300" [(ngModel)]="note" />
          @if (publishError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
          }
          <button
            nz-button
            nzType="primary"
            type="submit"
            [disabled]="!catalogId || !categoryId"
            data-testid="publish-confirm"
            i18n
          >
            Publish
          </button>
        </form>
      </ng-container>
    </nz-modal>
  `,
  styles: `
    .grid {
      display: grid;
      grid-template-columns: minmax(18rem, 26rem) 1fr;
      gap: 1.5rem;
      align-items: start;
    }
    .graph-box {
      height: 22rem;
      margin-bottom: 1rem;
    }
    .pane {
      white-space: pre-wrap;
      border: 1px solid var(--forge-surface-200);
      border-radius: 6px;
      padding: 0.75rem;
      min-height: 6rem;
    }
    @media (max-width: 1000px) {
      .grid {
        grid-template-columns: 1fr;
      }
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class UseCaseTest implements OnDestroy {
  private readonly api = inject(UseCasesService);
  private readonly session = inject(Session);
  private readonly route = inject(ActivatedRoute);

  private readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly detail = signal<UseCaseDetail | null>(null);
  protected readonly catalogs = signal<CatalogOut[]>([]);
  protected readonly run = signal<UseCaseRunDetail | null>(null);
  protected readonly live = signal<Record<string, string>>({});
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly publishError = signal<string | null>(null);
  protected tab = 0;
  protected publishOpen = false;
  protected description = '';
  protected catalogId: string | null = null;
  protected categoryId: string | null = null;
  protected note = '';
  private readonly streams = new Map<string, EventSource>();

  protected readonly steps = computed<RunStepOut[]>(() => this.run()?.step_list ?? []);
  protected readonly categories = computed(
    () => this.catalogs().find((c) => c.id === this.catalogId)?.categories ?? [],
  );
  /** The graph that was run (a supervisor team is shown as its compiled chain). */
  protected readonly graphDefinition = computed<Record<string, unknown>>(
    () => (this.run()?.graph as Record<string, unknown> | null) ?? this.detail()?.definition ?? {},
  );
  protected readonly states = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const s of this.steps()) out[s.node_id] = s.status;
    for (const id of (this.run()?.outputs?.['skipped'] as string[] | undefined) ?? [])
      out[id] = 'skipped';
    return out;
  });

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
    // Live output: follow the agent run behind every running step over SSE.
    effect(() => {
      const projectId = this.projectId();
      if (!projectId) return;
      for (const s of this.steps()) {
        if (s.status === 'running' && s.agent_run_id && !this.streams.has(s.step_key)) {
          this.follow(projectId, s.step_key, s.agent_run_id);
        }
      }
    });
  }

  ngOnDestroy(): void {
    for (const es of this.streams.values()) es.close();
  }

  private async load(): Promise<void> {
    const projectId = this.projectId()!;
    const id = this.route.snapshot.paramMap.get('id')!;
    try {
      const d = await firstValueFrom(this.api.getUseCase({ projectId, useCaseId: id }));
      this.detail.set(d);
      this.description = d.description;
      this.catalogId = d.catalog_id;
      this.categoryId = d.category_id;
      this.catalogs.set(await firstValueFrom(this.api.listCatalogs({ projectId })));
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  private follow(projectId: string, key: string, agentRunId: string): void {
    const source = new EventSource(`/api/v1/projects/${projectId}/runs/${agentRunId}/events`);
    this.streams.set(key, source);
    source.addEventListener('run_resumed', () => this.live.update((l) => ({ ...l, [key]: '' })));
    source.addEventListener('token', (e) => {
      const text = String(
        (JSON.parse((e as MessageEvent<string>).data) as { text?: string }).text ?? '',
      );
      this.live.update((l) => ({ ...l, [key]: (l[key] ?? '') + text }));
    });
    const end = (): void => {
      source.close();
    };
    source.addEventListener('done', end);
    source.addEventListener('error', end);
  }

  protected onRun(run: UseCaseRunDetail | null): void {
    if (run && run.id !== this.run()?.id) {
      this.live.set({});
      for (const es of this.streams.values()) es.close();
      this.streams.clear();
    }
    this.run.set(run);
    const running = (run?.step_list ?? []).findIndex((s) => s.status === 'running');
    if (running >= 0) this.tab = running;
  }

  protected color(status: string): string {
    return STEP_COLOR[status] ?? 'gray';
  }

  protected paneText(s: RunStepOut): string {
    if (s.status === 'running') return this.live()[s.step_key] || $localize`Working…`;
    if (s.error) return s.error;
    const value = (s.output as { value?: unknown } | null)?.value;
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }

  protected openPublish(): void {
    this.publishError.set(null);
    this.publishOpen = true;
  }

  protected async publish(): Promise<void> {
    const projectId = this.projectId();
    const d = this.detail();
    if (!projectId || !d || !this.catalogId || !this.categoryId) return;
    this.publishError.set(null);
    try {
      const saved = await firstValueFrom(
        this.api.updateUseCase({
          projectId,
          useCaseId: d.id,
          useCaseUpdateIn: {
            name: d.name,
            description: this.description,
            requirements: d.requirements,
            icon: d.icon,
            tags: d.tags,
            catalog_id: this.catalogId,
            category_id: this.categoryId,
            input_fields: d.input_fields as never,
            output_type: d.output_type as never,
            output_schema: d.output_schema,
            defaults: d.defaults as never,
            definition: d.definition,
          },
        }),
      );
      const published = await firstValueFrom(
        this.api.publishUseCase({
          projectId,
          useCaseId: saved.id,
          useCasePublishIn: { note: this.note },
        }),
      );
      this.detail.set(published);
      this.publishOpen = false;
      this.message.set({
        ok: true,
        text: $localize`Published version ${published.published_version}:version:`,
      });
    } catch (e) {
      this.publishError.set(problemMessage(e));
    }
  }
}
