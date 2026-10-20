import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzRadioModule } from 'ng-zorro-antd/radio';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  BundlesService,
  DataService,
  ImportReport,
  PipelineOut,
  PromptOut,
  PromptsService,
  RagService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Versions = 'all' | 'published' | 'latest';
type Policy = 'new_version' | 'rename' | 'skip';

/** Projects Studio → Export / Import: move prompts, RAG pipelines and configuration between
 * projects and Forge installations, with versions and conflict policies. */
@Component({
  selector: 'forge-project-bundles',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzCheckboxModule,
    NzIconModule,
    NzRadioModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (notice(); as n) {
      <nz-alert
        [nzType]="n.ok ? 'success' : 'error'"
        [nzMessage]="n.text"
        nzShowIcon
        data-testid="bundle-notice"
      />
    }

    <nz-card nzSize="small" nzTitle="Export" i18n-nzTitle>
      <p class="muted" i18n>
        Choose what to export. The bundle is a JSON file; prompts and RAG pipelines keep their
        versions and tags. It holds no secrets, files or documents: data collections are exported as
        definitions only.
      </p>
      <div class="form">
        <label for="ex-prompts" i18n>Prompts</label>
        <nz-select
          nzMode="multiple"
          nzId="ex-prompts"
          data-testid="export-prompts"
          [(ngModel)]="selPrompts"
        >
          @for (p of prompts(); track p.id) {
            <nz-option [nzValue]="p.id" [nzLabel]="p.name + ' (v' + p.latest_version + ')'" />
          }
        </nz-select>
        <label for="ex-rag" i18n>RAG pipelines</label>
        <nz-select nzMode="multiple" nzId="ex-rag" data-testid="export-rag" [(ngModel)]="selRag">
          @for (p of pipelines(); track p.id) {
            <nz-option [nzValue]="p.id" [nzLabel]="p.name + ' (v' + p.latest_version + ')'" />
          }
        </nz-select>
        <label for="ex-versions" i18n>Versions to include</label>
        <nz-select nzId="ex-versions" [(ngModel)]="versions">
          <nz-option nzValue="all" nzLabel="All versions" i18n-nzLabel />
          <nz-option nzValue="published" nzLabel="Published version only" i18n-nzLabel />
          <nz-option nzValue="latest" nzLabel="Latest version only" i18n-nzLabel />
        </nz-select>
        <label nz-checkbox nzId="ex-deps" [(ngModel)]="deps" name="deps">
          <span i18n>Include the prompts and collections used by the selected pipelines</span>
        </label>
        @if (canManage()) {
          <label nz-checkbox nzId="ex-aliases" [(ngModel)]="withAliases" name="aliases">
            <span i18n>Project model aliases</span>
          </label>
          <label nz-checkbox nzId="ex-limits" [(ngModel)]="withLimits" name="limits">
            <span i18n>Project limits and cache settings</span>
          </label>
          <label nz-checkbox nzId="ex-guardrails" [(ngModel)]="withGuardrails" name="guardrails">
            <span i18n>Guardrail policy (rules; guard models by name)</span>
          </label>
          <label nz-checkbox nzId="ex-groups" [(ngModel)]="withGroups" name="groups">
            <span i18n>User groups (names only, without members)</span>
          </label>
        }
        <div class="dialog-actions">
          <button
            nz-button
            nzType="primary"
            [nzLoading]="busy()"
            (click)="export()"
            data-testid="export-run"
            i18n
          >
            Export bundle
          </button>
        </div>
      </div>
    </nz-card>

    <nz-card nzSize="small" nzTitle="Import" i18n-nzTitle>
      <p class="muted" i18n>
        Bundles from older Forge versions are upgraded automatically; a bundle from a newer version
        is refused. Always check the preview before importing.
      </p>
      <div class="form">
        <label for="im-file" i18n>Bundle file</label>
        <input
          id="im-file"
          type="file"
          accept="application/json,.json"
          data-testid="import-file"
          (change)="pick($event)"
        />
        <strong i18n>If a name already exists</strong>
        <nz-radio-group
          [(ngModel)]="policy"
          (ngModelChange)="preview()"
          data-testid="import-policy"
        >
          <label nz-radio nzValue="new_version" nzId="im-new"
            ><span i18n>Add the versions to the existing item</span></label
          >
          <label nz-radio nzValue="rename" nzId="im-rename"
            ><span i18n>Import under a new name</span></label
          >
          <label nz-radio nzValue="skip" nzId="im-skip"><span i18n>Skip it</span></label>
        </nz-radio-group>
        @if (report(); as r) {
          <div data-testid="import-report">
            <p>
              <strong>{{ r.dry_run ? previewLabel : resultLabel }}</strong>
              @if (r.migrated_from) {
                <nz-tag nzColor="blue" i18n>upgraded from format {{ r.migrated_from }}</nz-tag>
              }
            </p>
            <nz-table
              [nzData]="r.items"
              nzSize="small"
              [nzShowPagination]="false"
              [nzPageSize]="1000"
            >
              <thead>
                <tr>
                  <th i18n>Type</th>
                  <th i18n>Name</th>
                  <th i18n>Result</th>
                  <th i18n>Versions</th>
                  <th i18n>Notes</th>
                </tr>
              </thead>
              <tbody>
                @for (i of r.items; track $index) {
                  <tr [attr.data-import]="i.name">
                    <td>{{ i.kind }}</td>
                    <td>
                      {{ i.name }}
                      @if (i.target && i.target !== i.name) {
                        <span class="muted"> → {{ i.target }}</span>
                      }
                    </td>
                    <td>
                      <nz-tag [nzColor]="color(i.action)">{{ i.action }}</nz-tag>
                    </td>
                    <td>{{ i.versions ?? 0 }}</td>
                    <td>
                      @if (i.error) {
                        <span class="error">{{ i.error }}</span>
                      }
                      @for (w of i.warnings ?? []; track w) {
                        <div class="muted small">{{ w }}</div>
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </nz-table>
            @if (r.dry_run) {
              <div class="dialog-actions">
                <button
                  nz-button
                  nzType="primary"
                  [disabled]="!importable()"
                  [nzLoading]="busy()"
                  (click)="apply()"
                  data-testid="import-apply"
                  i18n
                >
                  Import
                </button>
              </div>
            }
          </div>
        }
      </div>
    </nz-card>
  `,
  styleUrls: ['../admin/admin.scss'],
  styles: [
    `
      nz-card {
        display: block;
        margin-bottom: 1rem;
        max-width: 56rem;
      }
      .muted {
        color: var(--forge-text-muted-color);
      }
      .small {
        font-size: 0.8rem;
      }
      .error {
        color: var(--forge-red-500, #cf1322);
      }
      nz-radio-group {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
      }
    `,
  ],
})
export class ProjectBundles {
  private readonly api = inject(BundlesService);
  private readonly promptsApi = inject(PromptsService);
  private readonly ragApi = inject(RagService);
  private readonly data = inject(DataService);
  private readonly session = inject(Session);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly canManage = computed(() => this.session.can('project.manage'));
  protected readonly prompts = signal<PromptOut[]>([]);
  protected readonly pipelines = signal<PipelineOut[]>([]);
  protected readonly notice = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly report = signal<ImportReport | null>(null);
  protected readonly busy = signal(false);
  protected readonly previewLabel = $localize`Preview (nothing has been changed yet)`;
  protected readonly resultLabel = $localize`Import result`;
  protected selPrompts: string[] = [];
  protected selRag: string[] = [];
  protected versions: Versions = 'all';
  protected deps = true;
  protected withAliases = false;
  protected withLimits = false;
  protected withGroups = false;
  protected withGuardrails = false;
  protected policy: Policy = 'new_version';
  private bundle: Record<string, unknown> | null = null;
  private pendingFile: string | null = null;

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      if (projectId) void this.load(projectId);
    });
  }

  private async load(projectId: string): Promise<void> {
    try {
      const [prompts, pipelines] = await Promise.all([
        firstValueFrom(this.promptsApi.listPrompts({ projectId, limit: 200 })),
        firstValueFrom(this.ragApi.listRagPipelines({ projectId, limit: 200 })),
      ]);
      this.prompts.set(prompts.items);
      this.pipelines.set(pipelines.items);
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async export(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.busy.set(true);
    this.notice.set(null);
    try {
      const bundle = await firstValueFrom(
        this.api.exportBundle({
          projectId,
          exportIn: {
            prompts: this.selPrompts.map((id) => ({ id, versions: this.versions })),
            rag_pipelines: this.selRag.map((id) => ({ id, versions: this.versions })),
            include_dependencies: this.deps,
            model_aliases: this.withAliases,
            gateway_config: this.withLimits,
            user_groups: this.withGroups,
            guardrail_policy: this.withGuardrails,
          },
        }),
      );
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `forge-bundle-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')}.json`;
      a.click();
      URL.revokeObjectURL(url);
      const items = (bundle['items'] as unknown[]).length;
      this.notice.set({ ok: true, text: $localize`Exported ${items}:n: items.` });
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.busy.set(false);
    }
  }

  protected async pick(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    this.report.set(null);
    this.bundle = null;
    if (!file) return;
    try {
      this.bundle = JSON.parse(await file.text()) as Record<string, unknown>;
      this.pendingFile = file.name;
    } catch {
      this.notice.set({ ok: false, text: $localize`The file is not valid JSON.` });
      return;
    }
    await this.preview();
  }

  protected async preview(): Promise<void> {
    if (!this.bundle) return;
    await this.run(true);
  }

  protected async apply(): Promise<void> {
    await this.run(false);
  }

  private async run(dryRun: boolean): Promise<void> {
    const projectId = this.projectId();
    if (!projectId || !this.bundle) return;
    this.busy.set(true);
    this.notice.set(null);
    try {
      const report = await firstValueFrom(
        this.api.importBundle({
          projectId,
          importIn: { bundle: this.bundle, policy: this.policy, dry_run: dryRun },
        }),
      );
      this.report.set(report);
      if (!dryRun) {
        this.notice.set({
          ok: true,
          text: $localize`Imported from ${this.pendingFile ?? ''}:file:.`,
        });
        this.bundle = null;
        await this.load(projectId);
      }
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.busy.set(false);
    }
  }

  protected importable(): boolean {
    const r = this.report();
    return (
      !!r &&
      r.items.some((i) => ['created', 'new_versions', 'renamed', 'updated'].includes(i.action))
    );
  }

  protected color(action: string): string {
    return (
      (
        {
          created: 'success',
          new_versions: 'blue',
          renamed: 'blue',
          updated: 'success',
          reused: 'default',
          skipped: 'default',
          forbidden: 'warning',
          error: 'error',
        } as Record<string, string>
      )[action] ?? 'default'
    );
  }
}
