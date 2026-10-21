import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  CollectionOut,
  DataService,
  SourceOut,
  SourceRunOut,
  SourcesService,
  SourceTypes,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

const EXAMPLES: Record<string, string> = {
  s3: '{"endpoint": "https://s3.example.com", "bucket": "docs", "prefix": "manuals/", "include": ["*.pdf", "*.md"]}',
  url: '{"start_urls": ["https://docs.example.com/"], "max_pages": 100, "max_depth": 2, "include_regex": "^https://docs\\\\.example\\\\.com/"}',
  rest: '{"url": "https://api.example.com/articles", "items_path": "data", "id_field": "id", "title_field": "title", "content_fields": ["body"], "version_field": "updated_at", "next_path": "links.next"}',
  sql: '{"datasource": "name-of-a-project-data-source", "query": "SELECT id, title, body FROM articles", "id_column": "id", "title_column": "title", "content_columns": ["body"]}',
  git: '{"repo_url": "https://git.example.com/team/docs.git", "ref": "main", "subpath": "docs", "include": ["*.md"]}',
  filesystem: '{"path": "/mnt/shares/policies", "recursive": true, "include": ["*.pdf", "*.docx"]}',
};
const CREDENTIAL_EXAMPLES: Record<string, string> = {
  s3: '{"access_key": "…", "secret_key": "…"}',
  url: '{"bearer_token": "…"}  or  {"headers": {"X-Api-Key": "…"}}',
  rest: '{"bearer_token": "…"}  or  {"headers": {"X-Api-Key": "…"}}',
  sql: '',
  git: '{"token": "…"}  or  {"username": "…", "password": "…"}',
  filesystem: '',
};

/** Projects Studio → Sources: scheduled external sources that fill a data collection (E2.13). */
@Component({
  selector: 'forge-project-sources',
  imports: [
    DatePipe,
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzInputModule,
    NzInputNumberModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <p class="intro" i18n>
        A source copies documents from outside Forge into a data collection on a schedule. Only what
        changed is fetched again; files removed at the source are removed here too.
      </p>
      <button nz-button nzType="primary" (click)="openNew()" data-testid="source-new" i18n>
        New source
      </button>
    </div>
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    <nz-table [nzData]="sources()" nzSize="small" [nzShowPagination]="false">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Type</th>
          <th i18n>Collection</th>
          <th i18n>Schedule</th>
          <th i18n>Last sync</th>
          <th i18n>Documents</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (s of sources(); track s.id) {
          <tr>
            <td>{{ s.name }}</td>
            <td>
              <nz-tag>{{ s.type }}</nz-tag>
            </td>
            <td>{{ s.collection_name }}</td>
            <td>
              @if (!s.enabled) {
                <nz-tag i18n>Paused</nz-tag>
              } @else if (s.interval_minutes) {
                <span i18n>every {{ s.interval_minutes }} min</span>
              } @else {
                <span i18n>Manual</span>
              }
            </td>
            <td>
              @if (s.status === 'running') {
                <nz-tag nzColor="processing" i18n>Running</nz-tag>
              } @else if (s.last_run_at) {
                <nz-tag [nzColor]="color(s.last_status)">{{ s.last_status }}</nz-tag>
                {{ s.last_run_at | date: 'short' }}
                @if (s.last_error) {
                  <div class="err">{{ s.last_error }}</div>
                }
              } @else {
                –
              }
            </td>
            <td>{{ s.documents }}</td>
            <td class="actions">
              <button
                nz-button
                nzSize="small"
                (click)="sync(s)"
                [disabled]="s.status === 'running'"
                i18n
              >
                Sync now
              </button>
              <button nz-button nzSize="small" (click)="showRuns(s)" i18n>Runs</button>
              <button nz-button nzSize="small" (click)="openEdit(s)" i18n>Edit</button>
              <button nz-button nzSize="small" nzDanger (click)="deleting.set(s)" i18n>
                Delete
              </button>
            </td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="formOpen"
      [nzTitle]="editing ? editTitle : newTitle"
      [nzFooter]="null"
      [nzWidth]="720"
    >
      <ng-container *nzModalContent>
        <div class="form">
          <label for="src-name" i18n>Name</label>
          <input nz-input id="src-name" [(ngModel)]="form.name" />
          <label i18n>Collection</label>
          <nz-select
            nzId="src-collection"
            [(ngModel)]="form.collection_id"
            [nzDisabled]="!!editing"
          >
            @for (c of collections(); track c.id) {
              <nz-option [nzValue]="c.id" [nzLabel]="c.name" />
            }
          </nz-select>
          <label i18n>Type</label>
          <nz-select
            nzId="src-type"
            [(ngModel)]="form.type"
            [nzDisabled]="!!editing"
            (ngModelChange)="typeChanged()"
          >
            @for (t of types()?.types ?? []; track t) {
              <nz-option
                [nzValue]="t"
                [nzLabel]="t"
                [nzDisabled]="t === 'filesystem' && !types()?.filesystem_enabled"
              />
            }
          </nz-select>
          <label for="src-config" i18n>Configuration (JSON)</label>
          <textarea
            nz-input
            id="src-config"
            rows="6"
            class="mono"
            [(ngModel)]="form.config"
          ></textarea>
          @if (credentialHint()) {
            <label for="src-creds" i18n>Credentials (JSON, write-only)</label>
            <textarea
              nz-input
              id="src-creds"
              rows="3"
              class="mono"
              [(ngModel)]="form.credentials"
              [placeholder]="credentialHint()"
            ></textarea>
            @if (editing?.has_credentials) {
              <span class="hint" i18n>Stored credentials are kept unless you type new ones.</span>
            }
          }
          <label i18n>Every (minutes, empty = manual only)</label>
          <nz-input-number [(ngModel)]="form.interval" [nzMin]="5" />
          <label></label>
          <label nz-checkbox [(ngModel)]="form.enabled" i18n>Enabled</label>
          <label></label>
          <label nz-checkbox [(ngModel)]="form.delete_missing" i18n>
            Delete documents that disappear from the source
          </label>
        </div>
        @if (formError()) {
          <nz-alert nzType="error" [nzMessage]="formError()" />
        }
        <div class="footer">
          <button nz-button (click)="formOpen = false" i18n>Cancel</button>
          <button nz-button nzType="primary" (click)="save()" data-testid="source-save" i18n>
            Save
          </button>
        </div>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!deleting()"
      nzTitle="Delete source"
      i18n-nzTitle
      (nzOnCancel)="deleting.set(null)"
      [nzFooter]="null"
    >
      <ng-container *nzModalContent>
        <p i18n>
          The source stops syncing. Do you also want to delete the {{ deleting()?.documents }}
          documents it brought in?
        </p>
        <div class="footer">
          <button nz-button (click)="deleting.set(null)" i18n>Cancel</button>
          <button nz-button (click)="remove(false)" i18n>Keep documents</button>
          <button nz-button nzDanger nzType="primary" (click)="remove(true)" i18n>
            Delete documents too
          </button>
        </div>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!runsFor()"
      [nzTitle]="runsFor()?.name ?? ''"
      (nzOnCancel)="runsFor.set(null)"
      [nzFooter]="null"
      [nzWidth]="860"
    >
      <ng-container *nzModalContent>
        <nz-table [nzData]="runs()" nzSize="small" [nzShowPagination]="false">
          <thead>
            <tr>
              <th i18n>Started</th>
              <th i18n>Trigger</th>
              <th i18n>Status</th>
              <th i18n>Added</th>
              <th i18n>Updated</th>
              <th i18n>Unchanged</th>
              <th i18n>Deleted</th>
              <th i18n>Skipped</th>
              <th i18n>Failed</th>
            </tr>
          </thead>
          <tbody>
            @for (r of runs(); track r.id) {
              <tr>
                <td>{{ r.started_at | date: 'short' }}</td>
                <td>{{ r.trigger }}</td>
                <td>
                  <nz-tag [nzColor]="color(r.status)">{{ r.status }}</nz-tag>
                </td>
                <td>{{ r.added }}</td>
                <td>{{ r.updated }}</td>
                <td>{{ r.unchanged }}</td>
                <td>{{ r.deleted }}</td>
                <td>{{ r.skipped }}</td>
                <td>{{ r.failed }}</td>
              </tr>
              @if (r.message || r.errors.length) {
                <tr>
                  <td colspan="9" class="err">
                    {{ r.message }}
                    @for (e of r.errors.slice(0, 5); track $index) {
                      <div>{{ e?.['item'] }}: {{ e?.['error'] }}</div>
                    }
                  </td>
                </tr>
              }
            }
          </tbody>
        </nz-table>
        @if (needsConfirmation()) {
          <p class="err" i18n>
            The last run found many documents missing at the source and deleted none. If this is
            expected, sync again with deletion confirmed.
          </p>
          <button nz-button nzDanger (click)="confirmDelete()" i18n>Sync and delete them</button>
        }
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
  styles: `
    .intro {
      flex: 1;
      margin: 0;
    }
    .form {
      display: grid;
      grid-template-columns: 14rem 1fr;
      gap: 0.5rem 0.75rem;
      align-items: start;
    }
    .mono {
      font-family: monospace;
      font-size: 0.8rem;
    }
    .hint,
    .err {
      color: var(--forge-text-muted-color);
      font-size: 0.8rem;
    }
    .footer {
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      margin-top: 1rem;
    }
  `,
})
export class ProjectSources {
  private readonly api = inject(SourcesService);
  private readonly data = inject(DataService);
  protected readonly session = inject(Session);

  protected readonly newTitle = $localize`New source`;
  protected readonly editTitle = $localize`Edit source`;
  protected readonly sources = signal<SourceOut[]>([]);
  protected readonly collections = signal<CollectionOut[]>([]);
  protected readonly types = signal<SourceTypes | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly formError = signal<string | null>(null);
  protected readonly deleting = signal<SourceOut | null>(null);
  protected readonly runsFor = signal<SourceOut | null>(null);
  protected readonly runs = signal<SourceRunOut[]>([]);
  protected formOpen = false;
  protected editing: SourceOut | null = null;
  protected form = this.blank();
  private readonly projectId = computed(() => this.session.project()?.id ?? null);

  protected readonly credentialHint = computed(() => CREDENTIAL_EXAMPLES[this.form.type] ?? '');
  protected readonly needsConfirmation = computed(() =>
    (this.runs()[0]?.message ?? '').includes('nothing was deleted'),
  );

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
  }

  private blank() {
    return {
      name: '',
      collection_id: '',
      type: 's3',
      config: EXAMPLES['s3'],
      credentials: '',
      interval: null as number | null,
      enabled: true,
      delete_missing: true,
    };
  }

  protected color(status: string | null): string {
    return status === 'success' ? 'green' : status === 'partial' ? 'orange' : 'red';
  }

  async load(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const [sources, collections, types] = await Promise.all([
        firstValueFrom(this.api.listSources({ projectId })),
        firstValueFrom(this.data.listCollections({ projectId })),
        firstValueFrom(this.api.sourceTypes({ projectId })),
      ]);
      this.sources.set(sources);
      this.collections.set(collections);
      this.types.set(types);
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected typeChanged(): void {
    if (!this.editing) this.form.config = EXAMPLES[this.form.type] ?? '{}';
  }

  protected openNew(): void {
    this.editing = null;
    this.form = this.blank();
    this.formError.set(null);
    this.formOpen = true;
  }

  protected openEdit(s: SourceOut): void {
    this.editing = s;
    this.form = {
      name: s.name,
      collection_id: s.collection_id,
      type: s.type,
      config: JSON.stringify(s.config, null, 2),
      credentials: '',
      interval: s.interval_minutes,
      enabled: s.enabled,
      delete_missing: s.delete_missing,
    };
    this.formError.set(null);
    this.formOpen = true;
  }

  private parse(text: string, what: string): Record<string, unknown> | null {
    if (!text.trim()) return {};
    try {
      const value = JSON.parse(text);
      if (value && typeof value === 'object' && !Array.isArray(value)) return value;
    } catch {
      // reported below
    }
    this.formError.set($localize`${what} must be a JSON object`);
    return null;
  }

  async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const config = this.parse(this.form.config, $localize`Configuration`);
    const credentials = this.parse(this.form.credentials, $localize`Credentials`);
    if (config === null || credentials === null) return;
    const hasCredentials = this.form.credentials.trim() !== '';
    try {
      if (this.editing) {
        await firstValueFrom(
          this.api.updateSource({
            projectId,
            sourceId: this.editing.id,
            sourceUpdateIn: {
              name: this.form.name,
              config,
              credentials: hasCredentials ? credentials : undefined,
              interval_minutes: this.form.interval ?? undefined,
              clear_schedule: this.form.interval === null,
              enabled: this.form.enabled,
              delete_missing: this.form.delete_missing,
            },
          }),
        );
      } else {
        await firstValueFrom(
          this.api.createSource({
            projectId,
            sourceIn: {
              name: this.form.name,
              collection_id: this.form.collection_id,
              type: this.form.type,
              config,
              credentials: hasCredentials ? credentials : undefined,
              interval_minutes: this.form.interval,
              enabled: this.form.enabled,
              delete_missing: this.form.delete_missing,
            },
          }),
        );
      }
      this.formOpen = false;
      await this.load();
    } catch (e) {
      this.formError.set(problemMessage(e));
    }
  }

  async sync(s: SourceOut, allowMassDelete = false): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.api.syncSource({
          projectId,
          sourceId: s.id,
          syncIn: { allow_mass_delete: allowMassDelete },
        }),
      );
      await this.load();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  async showRuns(s: SourceOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.runsFor.set(s);
    this.runs.set(await firstValueFrom(this.api.sourceRuns({ projectId, sourceId: s.id })));
  }

  async confirmDelete(): Promise<void> {
    const s = this.runsFor();
    if (!s) return;
    this.runsFor.set(null);
    await this.sync(s, true);
  }

  async remove(withDocuments: boolean): Promise<void> {
    const projectId = this.projectId();
    const s = this.deleting();
    if (!projectId || !s) return;
    try {
      await firstValueFrom(
        this.api.deleteSource({ projectId, sourceId: s.id, deleteDocuments: withDocuments }),
      );
      this.deleting.set(null);
      await this.load();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
