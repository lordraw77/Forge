import { HttpClient } from '@angular/common/http';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzRadioModule } from 'ng-zorro-antd/radio';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { BundlesService, BundlePlanOut, BundleSyncOut, ProjectsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Versions = 'all' | 'published' | 'latest';
type Policy = 'new_version' | 'rename' | 'skip';

const STATUS_COLOR: Record<string, string> = {
  new: 'green',
  changed: 'blue',
  unchanged: 'default',
  differs: 'orange',
  removed: 'red',
};

/** Projects Studio → Sync: everything `forgectl` does, in the browser — the project's assets as
 * files (a zip you can commit to Git), a comparison with a folder or zip of such files, and an
 * apply that sends only the new items and the versions the project lacks (E3.3). */
@Component({
  selector: 'forge-project-sync',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzCheckboxModule,
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
        data-testid="sync-notice"
      />
    }

    <nz-card nzSize="small" nzTitle="Download the project as files" i18n-nzTitle>
      <p class="muted" i18n>
        A zip with one JSON file per prompt, RAG pipeline, agent, use case and collection, each with
        its version history. Unzip it into a Git repository: the files are the same ones
        <code>forgectl export</code> writes. No secrets, documents or embeddings are included.
      </p>
      <div class="form">
        <label for="sy-versions" i18n>Versions to include</label>
        <nz-select nzId="sy-versions" [(ngModel)]="versions">
          <nz-option nzValue="all" nzLabel="The whole history" i18n-nzLabel />
          <nz-option nzValue="published" nzLabel="Published version only" i18n-nzLabel />
          <nz-option nzValue="latest" nzLabel="Latest version only" i18n-nzLabel />
        </nz-select>
        @if (canManage()) {
          <label nz-checkbox nzId="sy-config" [(ngModel)]="withConfig" name="config">
            <span i18n>Also aliases, limits, user groups and guardrails</span>
          </label>
        }
        <div class="dialog-actions">
          <button
            nz-button
            nzType="primary"
            [nzLoading]="busy()"
            (click)="download()"
            data-testid="sync-download"
            i18n
          >
            Download zip
          </button>
        </div>
      </div>
    </nz-card>

    <nz-card nzSize="small" nzTitle="Compare with files and apply" i18n-nzTitle>
      <p class="muted" i18n>
        Pick the folder you cloned from Git (or a zip of it). Forge compares it with the project
        version by version and sends only what is new or missing. Nothing is deleted, and the
        <code>staging</code> and <code>prod</code> tags never move.
      </p>
      <div class="form">
        <label for="sy-folder" i18n>Folder</label>
        <input
          id="sy-folder"
          type="file"
          webkitdirectory
          data-testid="sync-folder"
          (change)="pick($event)"
        />
        <label for="sy-zip" i18n>or a zip / bundle file</label>
        <input
          id="sy-zip"
          type="file"
          accept=".zip,.json,application/zip,application/json"
          data-testid="sync-zip"
          (change)="pick($event)"
        />
      </div>

      @if (plan(); as p) {
        <div data-testid="sync-plan">
          <p>
            <strong i18n>Comparison with the project</strong>: {{ pending() }}
            <span i18n>to apply</span>, {{ p.summary['unchanged'] ?? 0 }}
            <span i18n>unchanged</span>
            @if (p.summary['differs']) {
              , {{ p.summary['differs'] }} <span i18n>differ outside their versions</span>
            }
            @if (p.summary['removed']) {
              , {{ p.summary['removed'] }} <span i18n>only in the project (never deleted)</span>
            }
          </p>
          <nz-table
            [nzData]="p.items"
            nzSize="small"
            [nzShowPagination]="false"
            [nzPageSize]="2000"
          >
            <thead>
              <tr>
                <th i18n>Type</th>
                <th i18n>Name</th>
                <th i18n>Status</th>
                <th i18n>Versions to send</th>
              </tr>
            </thead>
            <tbody>
              @for (i of p.items; track $index) {
                <tr [attr.data-plan]="i.name">
                  <td>{{ i.kind }}</td>
                  <td>{{ i.name }}</td>
                  <td>
                    <nz-tag [nzColor]="color(i.status)">{{ i.status }}</nz-tag>
                  </td>
                  <td>{{ i.versions_to_send || '' }}</td>
                </tr>
              }
            </tbody>
          </nz-table>
          <div class="form">
            <strong i18n>If a name already exists</strong>
            <nz-radio-group [(ngModel)]="policy" data-testid="sync-policy">
              <label nz-radio nzValue="new_version"
                ><span i18n>Add the missing versions to the existing item</span></label
              >
              <label nz-radio nzValue="rename"><span i18n>Import under a new name</span></label>
              <label nz-radio nzValue="skip"><span i18n>Skip it</span></label>
            </nz-radio-group>
            <div class="dialog-actions">
              <button
                nz-button
                [disabled]="!pending()"
                [nzLoading]="busy()"
                (click)="apply(true)"
                data-testid="sync-preview"
                i18n
              >
                Preview
              </button>
              <button
                nz-button
                nzType="primary"
                [disabled]="!pending()"
                [nzLoading]="busy()"
                (click)="apply(false)"
                data-testid="sync-apply"
                i18n
              >
                Apply changes
              </button>
            </div>
          </div>
        </div>
      }

      @if (result()?.report; as r) {
        <div data-testid="sync-report">
          <p>
            <strong>{{ r.dry_run ? previewLabel : resultLabel }}</strong>
          </p>
          <nz-table
            [nzData]="r.items"
            nzSize="small"
            [nzShowPagination]="false"
            [nzPageSize]="2000"
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
                <tr [attr.data-sync]="i.name">
                  <td>{{ i.kind }}</td>
                  <td>{{ i.name }}</td>
                  <td>
                    <nz-tag [nzColor]="actionColor(i.action)">{{ i.action }}</nz-tag>
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
        </div>
      }
    </nz-card>

    <nz-card nzSize="small" nzTitle="From the command line" i18n-nzTitle>
      <p class="muted" i18n>
        The same operations run in a terminal or a CI job with <code>forgectl</code> (<code
          >pip install ./sdk</code
        >). It signs in with a project API key.
      </p>
      <div class="form">
        <div class="dialog-actions left">
          <button nz-button (click)="makeKey()" [nzLoading]="busy()" data-testid="sync-key" i18n>
            Create a key for the command line
          </button>
        </div>
        @if (token(); as t) {
          <nz-alert
            nzType="warning"
            nzMessage="Copy the key now: it is shown only once."
            i18n-nzMessage
          />
        }
        <pre class="code" data-testid="sync-cli">{{ cliHelp() }}</pre>
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
      .code {
        background: var(--forge-surface-2, rgba(127, 127, 127, 0.12));
        padding: 0.75rem;
        border-radius: var(--forge-border-radius-md, 6px);
        overflow-x: auto;
        font-size: 0.8rem;
        user-select: all;
      }
      .left {
        justify-content: flex-start;
      }
      nz-radio-group {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
      }
    `,
  ],
})
export class ProjectSync {
  private readonly api = inject(BundlesService);
  private readonly projects = inject(ProjectsService);
  private readonly http = inject(HttpClient);
  private readonly session = inject(Session);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly canManage = computed(() => this.session.can('project.manage'));
  protected readonly notice = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly busy = signal(false);
  protected readonly plan = signal<BundlePlanOut | null>(null);
  protected readonly result = signal<BundleSyncOut | null>(null);
  protected readonly token = signal<string | null>(null);
  protected readonly previewLabel = $localize`Preview (nothing has been changed yet)`;
  protected readonly resultLabel = $localize`Result`;
  protected versions: Versions = 'all';
  protected withConfig = false;
  protected policy: Policy = 'new_version';
  private bundle: Record<string, unknown> | null = null;
  private source = '';

  protected readonly pending = computed(() => {
    const s = this.plan()?.summary ?? {};
    return (s['new'] ?? 0) + (s['changed'] ?? 0);
  });

  protected readonly cliHelp = computed(() => {
    const url = window.location.origin;
    const key = this.token() ?? 'fk_…';
    return [
      `export FORGE_URL=${url}`,
      `export FORGE_API_KEY=${key}`,
      `forgectl whoami`,
      `forgectl export forge/            # the files of this project`,
      `forgectl diff forge/ --exit-code  # what differs (fails a CI job)`,
      `forgectl apply forge/ --dry-run   # preview, then without --dry-run`,
    ].join('\n');
  });

  protected color(status: string): string {
    return STATUS_COLOR[status] ?? 'default';
  }

  protected actionColor(action: string): string {
    return (
      (
        {
          created: 'success',
          new_versions: 'blue',
          renamed: 'blue',
          updated: 'success',
          skipped: 'default',
          forbidden: 'warning',
          error: 'error',
        } as Record<string, string>
      )[action] ?? 'default'
    );
  }

  protected async download(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.busy.set(true);
    this.notice.set(null);
    try {
      const blob = await firstValueFrom(
        this.http.post(
          `/api/v1/projects/${projectId}/export/files`,
          { versions: this.versions, config: this.withConfig },
          { responseType: 'blob' },
        ),
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `forge-project-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      this.notice.set({ ok: true, text: $localize`The zip was downloaded.` });
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.busy.set(false);
    }
  }

  protected async pick(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const picked = Array.from(input.files ?? []);
    input.value = '';
    this.plan.set(null);
    this.result.set(null);
    this.bundle = null;
    this.notice.set(null);
    const projectId = this.projectId();
    if (!projectId || !picked.length) return;
    this.busy.set(true);
    try {
      this.source = picked.length === 1 ? picked[0].name : $localize`${picked.length}:n: files`;
      const single = picked.length === 1 ? picked[0] : null;
      if (single && single.name.toLowerCase().endsWith('.json')) {
        // A single bundle file (the format of the Export / Import page).
        this.bundle = JSON.parse(await single.text()) as Record<string, unknown>;
      } else {
        // A picked folder keeps each file's path (`repo/prompts/A.json`) in its name; the server
        // finds the export inside, whatever the folder is called.
        const files = picked.map(
          (f) =>
            new File(
              [f],
              (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name,
              {
                type: f.type,
              },
            ),
        );
        this.bundle = (await firstValueFrom(this.api.readFiles({ projectId, files }))) as Record<
          string,
          unknown
        >;
      }
      this.plan.set(
        await firstValueFrom(
          this.api.syncPlan({ projectId, bundlePlanIn: { bundle: this.bundle } }),
        ),
      );
    } catch (e) {
      this.notice.set({
        ok: false,
        text: e instanceof SyntaxError ? $localize`The file is not valid JSON.` : problemMessage(e),
      });
    } finally {
      this.busy.set(false);
    }
  }

  protected async apply(dryRun: boolean): Promise<void> {
    const projectId = this.projectId();
    if (!projectId || !this.bundle) return;
    this.busy.set(true);
    this.notice.set(null);
    try {
      const out = await firstValueFrom(
        this.api.syncApply({
          projectId,
          bundleSyncIn: { bundle: this.bundle, policy: this.policy, dry_run: dryRun },
        }),
      );
      this.result.set(out);
      if (!dryRun) {
        this.notice.set({ ok: true, text: $localize`Applied from ${this.source}:source:.` });
        this.plan.set(
          await firstValueFrom(
            this.api.syncPlan({ projectId, bundlePlanIn: { bundle: this.bundle } }),
          ),
        );
      }
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.busy.set(false);
    }
  }

  protected async makeKey(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.busy.set(true);
    try {
      const scopes = ['asset.write', 'usecase.run', 'run.read', 'llm.invoke'].filter((s) =>
        this.session.can(s),
      );
      const created = await firstValueFrom(
        this.projects.createKey({
          projectId,
          apiKeyCreateIn: { name: `forgectl ${new Date().toISOString().slice(0, 10)}`, scopes },
        }),
      );
      this.token.set(created.token);
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.busy.set(false);
    }
  }
}
