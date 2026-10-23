import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  EvalCaseIn,
  EvalComparison,
  EvalDatasetOut,
  EvalRunDetail,
  EvalRunOut,
  EvaluationsService,
  PromptsService,
  RagService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

const PRESETS: Record<string, string> = {
  prompt: JSON.stringify(
    [
      { type: 'contains' },
      { type: 'json_schema', schema: { type: 'object' }, name: 'valid_json' },
      { type: 'judge', model: 'default-chat', criteria: 'The answer is polite and correct.' },
    ],
    null,
    2,
  ),
  rag: JSON.stringify(
    [
      { type: 'recall_at_k', k: 5 },
      { type: 'mrr' },
      { type: 'faithfulness', model: 'default-chat' },
      { type: 'relevance', model: 'default-chat' },
      { type: 'correctness', model: 'default-chat' },
    ],
    null,
    2,
  ),
};
const CASE_HINT: Record<string, string> = {
  prompt:
    '[{"input": {"customer": "Anna"}, "expected": "refund"}]  or CSV with a header: customer,topic,expected',
  rag: '[{"input": {"query": "How is a backup restored?"}, "expected": "forge restore", "expected_sources": ["manual.md"]}]  or CSV: query,expected,expected_sources (sources separated by ;)',
};

interface Target {
  id: string;
  name: string;
  latest: number;
}

/** Governance → Evaluations (US-3.2.x): datasets of cases, evaluation runs of a prompt or RAG
 * pipeline, results per case, and a comparison of two runs. */
@Component({
  selector: 'forge-evaluations',
  imports: [
    DatePipe,
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzInputModule,
    NzInputNumberModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (!session.project()) {
      <nz-alert nzType="info" nzMessage="You are not a member of any project yet." i18n-nzMessage />
    } @else if (!session.can('asset.write')) {
      <nz-alert
        nzType="warning"
        nzMessage="Evaluations need the Builder or Project Admin role."
        i18n-nzMessage
      />
    } @else {
      <p class="intro" i18n>
        An evaluation runs a prompt or a RAG pipeline on every case of a dataset and scores each
        answer. Use it before publishing a new version, and compare two runs to see what got worse.
      </p>
      @if (error(); as e) {
        <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
      }

      <div class="head">
        <h2 i18n>Datasets</h2>
        <button nz-button nzType="primary" (click)="openNew()" data-testid="ds-new" i18n>
          New dataset
        </button>
      </div>
      <nz-table [nzData]="datasets()" nzSize="small" [nzShowPagination]="false">
        <thead>
          <tr>
            <th i18n>Name</th>
            <th i18n>Kind</th>
            <th i18n>Cases</th>
            <th i18n>Updated</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (d of datasets(); track d.id) {
            <tr [attr.data-testid]="'ds-' + d.name">
              <td>{{ d.name }}</td>
              <td>
                <nz-tag>{{ d.kind }}</nz-tag>
              </td>
              <td>{{ d.cases }}</td>
              <td>{{ d.updated_at | date: 'short' }}</td>
              <td class="actions">
                <button nz-button nzSize="small" (click)="edit(d)" i18n>Edit</button>
                <button nz-button nzSize="small" nzDanger (click)="deleting.set(d)" i18n>
                  Delete
                </button>
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="5" class="muted" i18n>No datasets yet.</td>
            </tr>
          }
        </tbody>
      </nz-table>

      <h2 i18n>Run an evaluation</h2>
      <div class="form">
        <span i18n>Dataset</span>
        <nz-select [(ngModel)]="runDataset" (ngModelChange)="datasetPicked()" nzId="ev-dataset">
          @for (d of datasets(); track d.id) {
            <nz-option [nzValue]="d.id" [nzLabel]="d.name + ' (' + d.kind + ')'" />
          }
        </nz-select>
        <span i18n>Target</span>
        <nz-select [(ngModel)]="runTarget" nzShowSearch nzId="ev-target">
          @for (t of targets(); track t.id) {
            <nz-option [nzValue]="t.id" [nzLabel]="t.name + ' (latest v' + t.latest + ')'" />
          }
        </nz-select>
        <span i18n>Version (empty = latest)</span>
        <nz-input-number [(ngModel)]="runVersion" [nzMin]="1" />
        <span i18n>Model override (prompts)</span>
        <input nz-input [(ngModel)]="runModel" />
        <span i18n>Label</span>
        <input nz-input [(ngModel)]="runName" />
        <span i18n>Evaluators (JSON)</span>
        <textarea
          nz-input
          rows="8"
          class="mono"
          spellcheck="false"
          [(ngModel)]="evaluators"
          data-testid="ev-evaluators"
        ></textarea>
        <span></span>
        <div>
          <button nz-button nzType="primary" (click)="start()" data-testid="ev-start" i18n>
            Start evaluation
          </button>
          <span class="hint" i18n>
            Types: exact, contains, regex, json_schema, judge (prompts); recall_at_k, mrr,
            faithfulness, relevance, correctness, judge (RAG). Judges need a "model".
          </span>
        </div>
      </div>

      <div class="head">
        <h2 i18n>Runs</h2>
        <button
          nz-button
          [disabled]="picked().length !== 2"
          (click)="compare()"
          data-testid="ev-compare"
          i18n
        >
          Compare the two selected (older = baseline)
        </button>
      </div>
      <nz-table [nzData]="runs()" nzSize="small" [nzPageSize]="10" data-testid="ev-runs">
        <thead>
          <tr>
            <th></th>
            <th i18n>Started</th>
            <th i18n>Dataset</th>
            <th i18n>Target</th>
            <th i18n>Status</th>
            <th i18n>Pass rate</th>
            <th i18n>Cost</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (r of runs(); track r.id) {
            <tr [attr.data-testid]="'run-' + r.id">
              <td>
                <input
                  type="checkbox"
                  [checked]="picked().includes(r.id)"
                  (change)="pick(r.id)"
                  [attr.aria-label]="'Select run ' + r.name"
                />
              </td>
              <td>{{ r.created_at | date: 'short' }}{{ r.name ? ' · ' + r.name : '' }}</td>
              <td>{{ r.dataset_name }}</td>
              <td>{{ r.target_name }} v{{ r.target_version }}</td>
              <td>
                <nz-tag [nzColor]="color(r.status)">{{ r.status }}</nz-tag>
                @if (r.status === 'running' || r.status === 'queued') {
                  {{ r.completed }}/{{ r.total }}
                }
              </td>
              <td>{{ rate(r) }}</td>
              <td>{{ r.summary['cost'] ?? '–' }}</td>
              <td class="actions">
                <button nz-button nzSize="small" (click)="open(r.id)" i18n>Results</button>
                @if (r.status === 'running' || r.status === 'queued') {
                  <button nz-button nzSize="small" (click)="cancel(r)" i18n>Cancel</button>
                } @else {
                  <button nz-button nzSize="small" nzDanger (click)="removeRun(r)" i18n>
                    Delete
                  </button>
                }
              </td>
            </tr>
          }
        </tbody>
      </nz-table>

      @if (detail(); as d) {
        <h2 data-testid="ev-detail">
          {{ d.target_name }} v{{ d.target_version }} · {{ d.dataset_name }}
        </h2>
        <p>
          <nz-tag [nzColor]="color(d.status)">{{ d.status }}</nz-tag>
          <span i18n>passed {{ d.summary['passed'] }} of {{ d.summary['cases'] }}</span>
          · <span i18n>errors {{ d.summary['errors'] }}</span> ·
          <span i18n
            >p50 {{ d.summary['latency_p50_ms'] }} ms, p95
            {{ d.summary['latency_p95_ms'] }} ms</span
          >
          · <span i18n>{{ d.summary['tokens'] }} tokens</span>
        </p>
        <p>
          @for (e of evaluatorStats(d); track e.name) {
            <nz-tag>{{ e.name }}: {{ e.mean ?? '–' }} ({{ e.passed }}/{{ e.scored }})</nz-tag>
          }
        </p>
        <nz-table [nzData]="d.results" nzSize="small" [nzPageSize]="20">
          <thead>
            <tr>
              <th>#</th>
              <th i18n>Input</th>
              <th i18n>Expected</th>
              <th i18n>Output</th>
              <th i18n>Scores</th>
            </tr>
          </thead>
          <tbody>
            @for (r of d.results; track r.id) {
              <tr [class.failed]="!r.passed">
                <td>{{ r.position }}</td>
                <td class="cell">{{ json(r.input) }}</td>
                <td class="cell">{{ r.expected }}</td>
                <td class="cell">{{ r.error ? 'ERROR: ' + r.error : r.output }}</td>
                <td>
                  @for (s of scoreList(r.scores); track s.name) {
                    <nz-tag
                      [nzColor]="s.score === null ? 'default' : s.passed ? 'success' : 'error'"
                    >
                      {{ s.name }}: {{ s.score ?? 'skipped' }}
                    </nz-tag>
                  }
                </td>
              </tr>
            }
          </tbody>
        </nz-table>
      }

      @if (comparison(); as c) {
        <h2 data-testid="ev-comparison" i18n>Comparison</h2>
        <p>
          <span i18n>Pass rate change</span>: <strong>{{ c.pass_rate_delta ?? '–' }}</strong> ·
          <nz-tag nzColor="error"
            >{{ c.regressions }} <ng-container i18n>regressed</ng-container></nz-tag
          >
          <nz-tag nzColor="success"
            >{{ c.improvements }} <ng-container i18n>improved</ng-container></nz-tag
          >
        </p>
        <nz-table [nzData]="c.cases" nzSize="small" [nzPageSize]="20">
          <thead>
            <tr>
              <th>#</th>
              <th i18n>Input</th>
              <th i18n>Baseline</th>
              <th i18n>Candidate</th>
              <th i18n>Change</th>
            </tr>
          </thead>
          <tbody>
            @for (x of c.cases; track x.position) {
              <tr>
                <td>{{ x.position }}</td>
                <td class="cell">{{ json(x.input) }}</td>
                <td class="cell">{{ x.a_output }}</td>
                <td class="cell">{{ x.b_output }}</td>
                <td>
                  <nz-tag
                    [nzColor]="
                      x.change === 'regressed'
                        ? 'error'
                        : x.change === 'improved'
                          ? 'success'
                          : 'default'
                    "
                    >{{ x.change }}</nz-tag
                  >
                </td>
              </tr>
            }
          </tbody>
        </nz-table>
      }
    }

    <nz-modal
      [(nzVisible)]="formOpen"
      [nzTitle]="editing ? 'Edit dataset' : 'New dataset'"
      [nzFooter]="null"
      [nzWidth]="760"
    >
      <ng-container *nzModalContent>
        <div class="form">
          <label for="ds-name" i18n>Name</label>
          <input nz-input id="ds-name" [(ngModel)]="form.name" />
          <span i18n>Kind</span>
          <nz-select [(ngModel)]="form.kind" [nzDisabled]="!!editing" nzId="ds-kind">
            <nz-option nzValue="prompt" nzLabel="Prompt (cases are variables)" />
            <nz-option nzValue="rag" nzLabel="RAG (cases are questions)" />
          </nz-select>
          <span i18n>Description</span>
          <input nz-input [(ngModel)]="form.description" />
          <span i18n>Cases format</span>
          <nz-select [(ngModel)]="form.format">
            <nz-option nzValue="json" nzLabel="JSON array" />
            <nz-option nzValue="jsonl" nzLabel="JSON Lines" />
            <nz-option nzValue="csv" nzLabel="CSV" />
          </nz-select>
          <label for="ds-cases" i18n>Cases</label>
          <textarea
            nz-input
            id="ds-cases"
            rows="12"
            class="mono"
            spellcheck="false"
            [placeholder]="hint()"
            [(ngModel)]="form.cases"
            data-testid="ds-cases"
          ></textarea>
        </div>
        @if (formError()) {
          <nz-alert nzType="error" [nzMessage]="formError()" />
        }
        <div class="footer">
          <button nz-button (click)="formOpen = false" i18n>Cancel</button>
          <button nz-button nzType="primary" (click)="save()" data-testid="ds-save" i18n>
            Save
          </button>
        </div>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!deleting()"
      nzTitle="Delete dataset"
      i18n-nzTitle
      nzOkDanger
      nzOkText="Delete"
      i18n-nzOkText
      (nzOnOk)="confirmDelete()"
      (nzOnCancel)="deleting.set(null)"
    >
      <ng-container *nzModalContent>
        <p i18n>Delete "{{ deleting()?.name }}" and its cases? Past runs keep their results.</p>
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
  styles: `
    .form {
      display: grid;
      grid-template-columns: 14rem 1fr;
      gap: 0.5rem 0.75rem;
      align-items: start;
      max-width: 52rem;
    }
    .mono {
      font-family: monospace;
      font-size: 0.8rem;
    }
    .hint {
      color: var(--forge-text-muted-color);
      font-size: 0.8rem;
      margin-left: 0.75rem;
    }
    .head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 1.5rem;
    }
    .cell {
      max-width: 22rem;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.8rem;
    }
    .failed td {
      background: var(--forge-danger-bg, rgba(255, 77, 79, 0.06));
    }
    .footer {
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      margin-top: 1rem;
    }
  `,
})
export class EvaluationsPage implements OnDestroy {
  private readonly api = inject(EvaluationsService);
  private readonly prompts = inject(PromptsService);
  private readonly rag = inject(RagService);
  protected readonly session = inject(Session);

  private readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly datasets = signal<EvalDatasetOut[]>([]);
  protected readonly runs = signal<EvalRunOut[]>([]);
  protected readonly targets = signal<Target[]>([]);
  protected readonly detail = signal<EvalRunDetail | null>(null);
  protected readonly comparison = signal<EvalComparison | null>(null);
  protected readonly picked = signal<string[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly formError = signal<string | null>(null);
  protected readonly deleting = signal<EvalDatasetOut | null>(null);

  protected runDataset: string | null = null;
  protected runTarget: string | null = null;
  protected runVersion: number | null = null;
  protected runModel = '';
  protected runName = '';
  protected evaluators = PRESETS['prompt'];

  protected formOpen = false;
  protected editing: EvalDatasetOut | null = null;
  protected form = { name: '', kind: 'prompt', description: '', format: 'json', cases: '' };

  private timer: ReturnType<typeof setInterval> | undefined;

  constructor() {
    effect(() => {
      if (this.projectId() && this.session.can('asset.write')) void this.load();
    });
    this.timer = setInterval(() => {
      if (this.runs().some((r) => r.status === 'running' || r.status === 'queued')) {
        void this.refreshRuns();
      }
    }, 2000);
  }

  ngOnDestroy(): void {
    clearInterval(this.timer);
  }

  protected hint(): string {
    return CASE_HINT[this.form.kind];
  }

  protected color(status: string): string {
    return (
      { done: 'success', running: 'processing', queued: 'processing', cancelled: 'default' }[
        status
      ] ?? 'warning'
    );
  }

  protected rate(r: EvalRunOut): string {
    const v = r.summary['pass_rate'];
    return typeof v === 'number' ? `${Math.round(v * 100)}%` : '–';
  }

  protected json(v: unknown): string {
    return JSON.stringify(v);
  }

  protected evaluatorStats(d: EvalRunDetail) {
    const per = (d.summary['evaluators'] ?? {}) as Record<
      string,
      { mean: number | null; scored: number; passed: number }
    >;
    return Object.entries(per).map(([name, v]) => ({ name, ...v }));
  }

  protected scoreList(scores: Record<string, unknown>) {
    return Object.entries(scores).map(([name, v]) => {
      const s = v as { score: number | null; passed: boolean };
      return { name, score: s.score, passed: s.passed };
    });
  }

  private async load(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      this.datasets.set(await firstValueFrom(this.api.evalListDatasets({ projectId })));
      await this.refreshRuns();
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  private async refreshRuns(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      this.runs.set(await firstValueFrom(this.api.evalListRuns({ projectId, limit: 50 })));
      const open = this.detail();
      if (open && (open.status === 'running' || open.status === 'queued')) {
        await this.open(open.id);
      }
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async datasetPicked(): Promise<void> {
    const projectId = this.projectId();
    const d = this.datasets().find((x) => x.id === this.runDataset);
    if (!projectId || !d) return;
    this.evaluators = PRESETS[d.kind];
    this.runTarget = null;
    try {
      if (d.kind === 'prompt') {
        const page = await firstValueFrom(this.prompts.listPrompts({ projectId, limit: 200 }));
        this.targets.set(
          page.items.map((p) => ({ id: p.id, name: p.name, latest: p.latest_version })),
        );
      } else {
        const page = await firstValueFrom(this.rag.listRagPipelines({ projectId, limit: 200 }));
        this.targets.set(
          page.items.map((p) => ({ id: p.id, name: p.name, latest: p.latest_version })),
        );
      }
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async start(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId || !this.runDataset || !this.runTarget) {
      this.error.set('Choose a dataset and a target.');
      return;
    }
    let evaluators: Record<string, unknown>[];
    try {
      evaluators = JSON.parse(this.evaluators);
    } catch {
      this.error.set('The evaluators are not valid JSON.');
      return;
    }
    try {
      await firstValueFrom(
        this.api.evalStartRun({
          projectId,
          evalRunIn: {
            dataset_id: this.runDataset,
            target_id: this.runTarget,
            target_version: this.runVersion ?? undefined,
            model: this.runModel || undefined,
            name: this.runName,
            evaluators,
          },
        }),
      );
      this.error.set(null);
      await this.refreshRuns();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async open(runId: string): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      this.detail.set(await firstValueFrom(this.api.evalGetRun({ projectId, runId })));
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected pick(id: string): void {
    const now = this.picked();
    this.picked.set(now.includes(id) ? now.filter((x) => x !== id) : [...now, id].slice(-2));
  }

  protected async compare(): Promise<void> {
    const projectId = this.projectId();
    const [x, y] = this.picked();
    if (!projectId || !x || !y) return;
    const byId = new Map(this.runs().map((r) => [r.id, r]));
    const older =
      (byId.get(x)?.created_at ?? '') <= (byId.get(y)?.created_at ?? '') ? [x, y] : [y, x];
    try {
      this.comparison.set(
        await firstValueFrom(
          this.api.evalCompareRuns({ projectId, runId: older[0], otherId: older[1] }),
        ),
      );
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async cancel(r: EvalRunOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(this.api.evalCancelRun({ projectId, runId: r.id }));
      await this.refreshRuns();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async removeRun(r: EvalRunOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(this.api.evalDeleteRun({ projectId, runId: r.id }));
      if (this.detail()?.id === r.id) this.detail.set(null);
      this.picked.set(this.picked().filter((x) => x !== r.id));
      await this.refreshRuns();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  // ---- datasets
  protected openNew(): void {
    this.editing = null;
    this.form = { name: '', kind: 'prompt', description: '', format: 'json', cases: '' };
    this.formError.set(null);
    this.formOpen = true;
  }

  protected async edit(d: EvalDatasetOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const full = await firstValueFrom(this.api.evalGetDataset({ projectId, datasetId: d.id }));
      const cases = full.items.map((c) => ({
        input: c.input,
        expected: c.expected,
        expected_sources: c.expected_sources,
      }));
      this.editing = d;
      this.form = {
        name: d.name,
        kind: d.kind,
        description: d.description,
        format: 'json',
        cases: JSON.stringify(cases, null, 2),
      };
      this.formError.set(null);
      this.formOpen = true;
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const f = this.form;
    try {
      // JSON arrays are sent as cases; JSON Lines and CSV go through the server's importer.
      let cases: EvalCaseIn[] | undefined;
      const text = f.cases.trim();
      if (f.format === 'json' && text) cases = JSON.parse(text);
      let id = this.editing?.id;
      if (id) {
        await firstValueFrom(
          this.api.evalUpdateDataset({
            projectId,
            datasetId: id,
            evalDatasetUpdateIn: {
              name: f.name,
              description: f.description,
              cases: f.format === 'json' ? cases : undefined,
            },
          }),
        );
      } else {
        const created = await firstValueFrom(
          this.api.evalCreateDataset({
            projectId,
            evalDatasetIn: {
              name: f.name,
              kind: f.kind as 'prompt' | 'rag',
              description: f.description,
              cases: cases ?? [],
            },
          }),
        );
        id = created.id;
      }
      if (f.format !== 'json' && text) {
        await firstValueFrom(
          this.api.evalImportCases({
            projectId,
            datasetId: id,
            evalImportIn: { format: f.format as 'jsonl' | 'csv', text, replace: true },
          }),
        );
      }
      this.formOpen = false;
      await this.load();
    } catch (e) {
      this.formError.set(
        e instanceof SyntaxError ? 'The cases are not valid JSON.' : problemMessage(e),
      );
    }
  }

  protected async confirmDelete(): Promise<void> {
    const projectId = this.projectId();
    const d = this.deleting();
    if (!projectId || !d) return;
    try {
      await firstValueFrom(this.api.evalDeleteDataset({ projectId, datasetId: d.id }));
      this.deleting.set(null);
      await this.load();
    } catch (e) {
      this.deleting.set(null);
      this.error.set(problemMessage(e));
    }
  }
}
