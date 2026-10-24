import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { OpenApiPreviewOut, ToolOut, ToolsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { toolName } from './tool-util';

/** Tool Library: filters, Try, Edit, Publish, Duplicate, Delete, OpenAPI import (US-2.2.1/5). */
@Component({
  selector: 'forge-tool-library',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <input
        nz-input
        class="search"
        placeholder="Search by name"
        i18n-placeholder
        aria-label="Search by name"
        i18n-aria-label
        data-testid="tool-search"
        [(ngModel)]="query"
        (ngModelChange)="reload()"
      />
      <nz-select
        class="filter-status"
        nzId="tool-kind"
        aria-label="Type"
        i18n-aria-label
        [(ngModel)]="kind"
        (ngModelChange)="reload()"
      >
        <nz-option [nzValue]="null" nzLabel="All types" i18n-nzLabel />
        <nz-option nzValue="http" nzLabel="HTTP" />
        <nz-option nzValue="sql" nzLabel="SQL" />
        <nz-option nzValue="mcp" nzLabel="MCP" />
        <nz-option nzValue="python" nzLabel="Python" />
      </nz-select>
      <nz-select
        class="filter-status"
        nzId="tool-status"
        aria-label="Status"
        i18n-aria-label
        [(ngModel)]="published"
        (ngModelChange)="reload()"
      >
        <nz-option [nzValue]="null" nzLabel="All statuses" i18n-nzLabel />
        <nz-option [nzValue]="true" nzLabel="Published" i18n-nzLabel />
        <nz-option [nzValue]="false" nzLabel="Draft" i18n-nzLabel />
      </nz-select>
      <span class="spacer"></span>
      <button nz-button (click)="openImport()" data-testid="import-openapi">
        <nz-icon nzType="cloud-upload" /><span i18n>Import OpenAPI</span>
      </button>
      <a nz-button nzType="primary" routerLink="../new" data-testid="new-tool">
        <nz-icon nzType="plus" /><span i18n>New tool</span>
      </a>
    </div>

    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }

    <nz-table
      #table
      [nzData]="items()"
      nzSize="small"
      [nzShowPagination]="false"
      [nzPageSize]="1000"
      [nzLoading]="loading()"
      data-testid="tool-table"
    >
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Type</th>
          <th i18n>Status</th>
          <th i18n>Version</th>
          <th i18n>Flags</th>
          <th i18n>Updated</th>
          <th class="actions"><span class="sr-only" i18n>Actions</span></th>
        </tr>
      </thead>
      <tbody>
        @for (t of table.data; track t.id) {
          <tr [attr.data-testid]="'tool-row-' + t.name">
            <td>
              <a [routerLink]="['..', t.id, 'edit']">{{ t.name }}</a>
              <div class="muted small">{{ t.description }}</div>
            </td>
            <td>{{ t.kind.toUpperCase() }}</td>
            <td>
              @if (t.published_version) {
                <nz-tag nzColor="success" i18n>Published</nz-tag>
              } @else {
                <nz-tag i18n>Draft</nz-tag>
              }
            </td>
            <td>
              v{{ t.latest_version }}
              @if (t.published_version && t.published_version !== t.latest_version) {
                <span class="muted" i18n>(published v{{ t.published_version }})</span>
              }
            </td>
            <td>
              @if (t.writes) {
                <nz-tag nzColor="warning" i18n>Can change data</nz-tag>
              }
              @if (t.requires_approval) {
                <nz-tag nzColor="processing" i18n>Needs approval</nz-tag>
              }
            </td>
            <td>{{ t.updated_at | date: 'short' }}</td>
            <td class="actions">
              <a nz-button nzType="link" nzSize="small" [routerLink]="['..', t.id, 'edit']" i18n
                >Try / Edit</a
              >
              <button nz-button nzType="link" nzSize="small" (click)="togglePublish(t)">
                {{ t.published_version ? unpublishLabel : publishLabel }}
              </button>
              <button nz-button nzType="link" nzSize="small" (click)="openDuplicate(t)" i18n>
                Duplicate
              </button>
              <button nz-button nzType="link" nzSize="small" nzDanger (click)="remove(t)" i18n>
                Delete
              </button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="7" class="muted" i18n>No tools yet.</td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="dupOpen"
      nzTitle="Duplicate tool"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="420"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="duplicate()">
          <label for="dup-name" i18n>Name of the copy</label>
          <input nz-input id="dup-name" name="name" [(ngModel)]="dupName" required />
          @if (dupError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
          }
          <button nz-button nzType="primary" type="submit" i18n>Duplicate</button>
        </form>
      </ng-container>
    </nz-modal>

    <nz-modal
      [(nzVisible)]="importOpen"
      nzTitle="Import from OpenAPI"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="760"
    >
      <ng-container *nzModalContent>
        <div class="form">
          <label for="oa-file" i18n>OpenAPI document (JSON or YAML, up to 2 MB)</label>
          <input id="oa-file" type="file" accept=".json,.yaml,.yml" (change)="readFile($event)" />
          <textarea
            nz-input
            rows="6"
            name="document"
            aria-label="OpenAPI document"
            i18n-aria-label
            [(ngModel)]="document"
          ></textarea>
          <button nz-button (click)="preview()" [disabled]="!document" i18n>Read operations</button>
          @if (spec(); as s) {
            <h3>{{ s.title }}</h3>
            <label for="oa-base" i18n>Base URL</label>
            <input
              nz-input
              id="oa-base"
              name="base"
              [(ngModel)]="baseUrl"
              placeholder="https://api.example.com"
            />
            @if (s.security.length) {
              <small i18n>Security schemes: {{ s.security.join(', ') }}. Pick a credential:</small>
            }
            <nz-select
              nzId="oa-credential"
              nzAllowClear
              nzPlaceHolder="Credential (optional)"
              i18n-nzPlaceHolder
              [(ngModel)]="credential"
            >
              @for (c of credentials(); track c) {
                <nz-option [nzValue]="c" [nzLabel]="c" />
              }
            </nz-select>
            @for (o of s.operations; track o.operation_id) {
              <label
                nz-checkbox
                [nzId]="'op-' + o.operation_id"
                [ngModel]="picked().has(o.operation_id)"
                (ngModelChange)="pick(o.operation_id, $event)"
              >
                <strong>{{ o.method }}</strong> {{ o.path }}
                <span class="muted">→ {{ o.name }}</span>
                <span class="muted small">{{ o.summary }}</span>
              </label>
            }
            <button
              nz-button
              nzType="primary"
              (click)="runImport()"
              [disabled]="!picked().size"
              i18n
            >
              Import selected
            </button>
          }
          @if (importError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
          }
        </div>
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ToolLibrary {
  private readonly api = inject(ToolsService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly items = signal<ToolOut[]>([]);
  protected readonly loading = signal(false);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected query = '';
  protected kind: string | null = null;
  protected published: boolean | null = null;

  protected dupOpen = false;
  protected dupName = '';
  protected readonly dupError = signal<string | null>(null);
  private dupSource: ToolOut | null = null;

  protected importOpen = false;
  protected document = '';
  protected baseUrl = '';
  protected credential: string | null = null;
  protected readonly spec = signal<OpenApiPreviewOut | null>(null);
  protected readonly picked = signal<Set<string>>(new Set());
  protected readonly credentials = signal<string[]>([]);
  protected readonly importError = signal<string | null>(null);

  protected readonly publishLabel = $localize`Publish`;
  protected readonly unpublishLabel = $localize`Unpublish`;

  constructor() {
    effect(() => {
      if (this.projectId()) void this.reload();
    });
  }

  protected async reload(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.loading.set(true);
    try {
      const page = await firstValueFrom(
        this.api.listTools({
          projectId,
          q: this.query || undefined,
          kind: (this.kind as 'http' | 'sql' | 'mcp' | 'python' | null) ?? undefined,
          published: this.published ?? undefined,
        }),
      );
      this.items.set(page.items);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.loading.set(false);
    }
  }

  protected async togglePublish(t: ToolOut): Promise<void> {
    const projectId = this.projectId()!;
    await this.attempt(async () => {
      if (t.published_version) {
        await firstValueFrom(this.api.unpublishTool({ projectId, toolId: t.id }));
      } else {
        await firstValueFrom(this.api.publishTool({ projectId, toolId: t.id, toolPublishIn: {} }));
      }
    });
    await this.reload();
  }

  protected openDuplicate(t: ToolOut): void {
    this.dupSource = t;
    this.dupName = toolName(`${t.name}_copy`);
    this.dupError.set(null);
    this.dupOpen = true;
  }

  protected async duplicate(): Promise<void> {
    if (!this.dupSource) return;
    try {
      await firstValueFrom(
        this.api.duplicateTool({
          projectId: this.projectId()!,
          toolId: this.dupSource.id,
          toolDuplicateIn: { name: this.dupName },
        }),
      );
      this.dupOpen = false;
      await this.reload();
    } catch (e) {
      this.dupError.set(problemMessage(e));
    }
  }

  protected async remove(t: ToolOut): Promise<void> {
    if (!confirm($localize`Delete tool ${t.name}:name:?`)) return;
    await this.attempt(async () => {
      await firstValueFrom(this.api.deleteTool({ projectId: this.projectId()!, toolId: t.id }));
    });
    await this.reload();
  }

  // ---- OpenAPI import
  protected async openImport(): Promise<void> {
    this.document = '';
    this.baseUrl = '';
    this.credential = null;
    this.spec.set(null);
    this.picked.set(new Set());
    this.importError.set(null);
    // Open first: without zone.js a plain field set after an `await` does not schedule a change
    // detection, so the dialog stayed closed until some unrelated event.
    this.importOpen = true;
    const creds = await firstValueFrom(this.api.listCredentials({ projectId: this.projectId()! }));
    this.credentials.set(creds.map((c) => c.name));
  }

  protected async readFile(event: Event): Promise<void> {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (file) this.document = await file.text();
  }

  protected async preview(): Promise<void> {
    this.importError.set(null);
    try {
      const s = await firstValueFrom(
        this.api.previewOpenapi({
          projectId: this.projectId()!,
          openApiPreviewIn: { document: this.document },
        }),
      );
      this.spec.set(s);
      this.baseUrl = s.base_urls[0] ?? '';
      this.picked.set(new Set(s.operations.map((o) => o.operation_id)));
    } catch (e) {
      this.importError.set(problemMessage(e));
    }
  }

  protected pick(id: string, on: boolean): void {
    const next = new Set(this.picked());
    if (on) next.add(id);
    else next.delete(id);
    this.picked.set(next);
  }

  protected async runImport(): Promise<void> {
    this.importError.set(null);
    try {
      const res = await firstValueFrom(
        this.api.importOpenapi({
          projectId: this.projectId()!,
          openApiImportIn: {
            document: this.document,
            operations: [...this.picked()],
            base_url: this.baseUrl || null,
            credential: this.credential,
          },
        }),
      );
      this.importOpen = false;
      this.message.set({
        ok: res.skipped.length === 0,
        text: $localize`Imported ${res.created.length}:created: tools, skipped ${res.skipped.length}:skipped:`,
      });
      await this.reload();
    } catch (e) {
      this.importError.set(problemMessage(e));
    }
  }

  private async attempt(action: () => Promise<void>): Promise<void> {
    this.message.set(null);
    try {
      await action();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected go(t: ToolOut): void {
    void this.router.navigate(['/tools', t.id, 'edit']);
  }
}
