import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  AgentsService,
  DataService,
  ExposureOut,
  McpService,
  PromptsService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Kind = 'agent' | 'prompt' | 'collection';
interface Pick {
  id: string;
  name: string;
}

/** The project's exposure list: agents, data collections and prompts offered to MCP clients. */
@Component({
  selector: 'forge-mcp-exposed',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    <div class="head">
      <input
        nz-input
        class="filter"
        placeholder="Search by name"
        i18n-placeholder
        aria-label="Search"
        i18n-aria-label
        [(ngModel)]="search"
        data-testid="mcp-search"
      />
      <nz-select
        class="filter-status"
        nzAllowClear
        nzPlaceHolder="Any kind"
        i18n-nzPlaceHolder
        aria-label="Kind"
        i18n-aria-label
        [(ngModel)]="kindFilter"
      >
        <nz-option nzValue="agent" nzLabel="Agent" i18n-nzLabel />
        <nz-option nzValue="collection" nzLabel="Data collection" i18n-nzLabel />
        <nz-option nzValue="prompt" nzLabel="Prompt" i18n-nzLabel />
      </nz-select>
      <nz-select
        class="filter-status"
        nzAllowClear
        nzPlaceHolder="Any status"
        i18n-nzPlaceHolder
        aria-label="Status"
        i18n-aria-label
        [(ngModel)]="statusFilter"
      >
        <nz-option nzValue="active" nzLabel="Active" i18n-nzLabel />
        <nz-option nzValue="inactive" nzLabel="Inactive" i18n-nzLabel />
      </nz-select>
      <span class="spacer"></span>
      <button nz-button nzType="primary" (click)="openExpose()" data-testid="mcp-add" i18n>
        Expose an asset
      </button>
    </div>
    <p class="intro" i18n>
      External MCP clients (Claude Desktop, Claude Code, IDE assistants) can use what you expose
      here: agents become tools that answer, data collections become search tools, prompts become
      MCP prompts. Clients that use a paused item do not see it.
    </p>
    <nz-table [nzData]="visible()" nzSize="small" [nzShowPagination]="false">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Kind</th>
          <th i18n>Asset</th>
          <th i18n>Description</th>
          <th i18n>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (e of visible(); track e.id) {
          <tr>
            <td>
              <code>{{ e.name }}</code>
            </td>
            <td>
              <nz-tag>{{ e.kind }}</nz-tag>
            </td>
            <td>{{ e.asset_name }}</td>
            <td>{{ e.description }}</td>
            <td>
              @if (e.enabled) {
                <nz-tag nzColor="green" i18n>Active</nz-tag>
              } @else {
                <nz-tag i18n>Inactive</nz-tag>
              }
            </td>
            <td class="actions">
              <button nz-button nzSize="small" (click)="tryIt(e)" data-testid="mcp-try" i18n>
                Try
              </button>
              <button nz-button nzSize="small" (click)="openEdit(e)" i18n>Edit</button>
              <button nz-button nzSize="small" (click)="toggle(e)">
                {{ e.enabled ? pauseLabel : resumeLabel }}
              </button>
              <button nz-button nzSize="small" nzDanger (click)="remove(e)" i18n>Remove</button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="6" class="muted" i18n>Nothing is exposed yet.</td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="formOpen"
      [nzTitle]="editing ? editTitle : newTitle"
      [nzFooter]="null"
      [nzWidth]="560"
    >
      <ng-container *nzModalContent>
        <div class="form">
          @if (!editing) {
            <label i18n>Kind</label>
            <nz-select nzId="mcp-kind" [(ngModel)]="form.kind" (ngModelChange)="form.asset = ''">
              <nz-option nzValue="agent" nzLabel="Agent" i18n-nzLabel />
              <nz-option nzValue="collection" nzLabel="Data collection" i18n-nzLabel />
              <nz-option nzValue="prompt" nzLabel="Prompt" i18n-nzLabel />
            </nz-select>
            <label i18n>Asset</label>
            <nz-select nzId="mcp-asset" nzShowSearch [(ngModel)]="form.asset">
              @for (a of assets; track a.id) {
                <nz-option [nzValue]="a.id" [nzLabel]="a.name" />
              }
            </nz-select>
          }
          <label for="mcp-name" i18n>Name (optional)</label>
          <input nz-input id="mcp-name" maxlength="64" [(ngModel)]="form.name" />
          <label for="mcp-desc" i18n>Description (optional)</label>
          <textarea nz-input id="mcp-desc" rows="3" [(ngModel)]="form.description"></textarea>
        </div>
        <p class="hint" i18n>
          The name is what the client sees: letters, digits, “_” and “-”, up to 64. The description
          is what the client’s model reads to decide when to use it.
        </p>
        @if (formError()) {
          <nz-alert nzType="error" [nzMessage]="formError()" />
        }
        <div class="footer">
          <button nz-button (click)="formOpen = false" i18n>Cancel</button>
          <button nz-button nzType="primary" (click)="save()" data-testid="mcp-save" i18n>
            Save
          </button>
        </div>
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
    }
    code {
      font-family: monospace;
      font-size: 0.8rem;
    }
    .hint {
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
export class McpExposed {
  private readonly mcp = inject(McpService);
  private readonly agentApi = inject(AgentsService);
  private readonly promptApi = inject(PromptsService);
  private readonly dataApi = inject(DataService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  protected readonly session = inject(Session);

  protected readonly newTitle = $localize`Expose an asset`;
  protected readonly editTitle = $localize`Edit exposure`;
  protected readonly pauseLabel = $localize`Pause`;
  protected readonly resumeLabel = $localize`Resume`;
  protected readonly exposures = signal<ExposureOut[]>([]);
  protected readonly agents = signal<Pick[]>([]);
  protected readonly prompts = signal<Pick[]>([]);
  protected readonly collections = signal<Pick[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly formError = signal<string | null>(null);
  protected readonly searchText = signal('');
  protected readonly kind = signal<Kind | null>(null);
  protected readonly status = signal<'active' | 'inactive' | null>(null);
  protected formOpen = false;
  protected editing: ExposureOut | null = null;
  protected form = { kind: 'agent' as Kind, asset: '', name: '', description: '' };
  private readonly projectId = computed(() => this.session.project()?.id ?? null);

  /** The assets of the chosen kind. A getter, not a computed: `form` is a plain field. */
  protected get assets(): Pick[] {
    return this.form.kind === 'agent'
      ? this.agents()
      : this.form.kind === 'prompt'
        ? this.prompts()
        : this.collections();
  }
  protected readonly visible = computed(() => {
    const text = this.searchText().trim().toLowerCase();
    const kind = this.kind();
    const status = this.status();
    return this.exposures().filter(
      (e) =>
        (!text ||
          e.name.toLowerCase().includes(text) ||
          e.asset_name.toLowerCase().includes(text)) &&
        (!kind || e.kind === kind) &&
        (!status || e.enabled === (status === 'active')),
    );
  });

  // The filters are plain fields in the template (ngModel); these setters feed the signals.
  protected get search(): string {
    return this.searchText();
  }
  protected set search(value: string) {
    this.searchText.set(value ?? '');
  }
  protected get kindFilter(): Kind | null {
    return this.kind();
  }
  protected set kindFilter(value: Kind | null) {
    this.kind.set(value ?? null);
  }
  protected get statusFilter(): 'active' | 'inactive' | null {
    return this.status();
  }
  protected set statusFilter(value: 'active' | 'inactive' | null) {
    this.status.set(value ?? null);
  }

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
  }

  async load(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const [exposures, agents, prompts, collections] = await Promise.all([
        firstValueFrom(this.mcp.listExposures({ projectId })),
        firstValueFrom(this.agentApi.listAgents({ projectId, limit: 200 })),
        firstValueFrom(this.promptApi.listPrompts({ projectId, limit: 200 })),
        firstValueFrom(this.dataApi.listCollections({ projectId })),
      ]);
      this.exposures.set(exposures);
      this.agents.set(
        agents.items
          .filter((a) => a.status === 'published')
          .map((a) => ({ id: a.id, name: a.name })),
      );
      this.prompts.set(prompts.items.map((p) => ({ id: p.id, name: p.name })));
      this.collections.set(collections.map((c) => ({ id: c.id, name: c.name })));
      this.error.set(null);
      this.openRequested();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  /** `?expose=prompt:<id>` (from a library's "Add to MCP Servers") opens the form filled in. */
  private openRequested(): void {
    const wanted = this.route.snapshot.queryParamMap.get('expose');
    if (!wanted) return;
    const [kind, asset] = wanted.split(':');
    void this.router.navigate([], { queryParams: { expose: null }, replaceUrl: true });
    if (kind !== 'agent' && kind !== 'prompt' && kind !== 'collection') return;
    this.openExpose();
    this.form.kind = kind;
    this.form.asset = asset ?? '';
  }

  protected openExpose(): void {
    this.editing = null;
    this.form = { kind: 'agent', asset: '', name: '', description: '' };
    this.formError.set(null);
    this.formOpen = true;
  }

  protected openEdit(e: ExposureOut): void {
    this.editing = e;
    this.form = { kind: e.kind, asset: e.asset_id, name: e.name, description: e.description };
    this.formError.set(null);
    this.formOpen = true;
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const f = this.form;
    try {
      if (this.editing) {
        await firstValueFrom(
          this.mcp.updateExposure({
            projectId,
            exposureId: this.editing.id,
            exposureUpdateIn: { name: f.name.trim() || undefined, description: f.description },
          }),
        );
      } else {
        if (!f.asset) {
          this.formError.set($localize`Pick the asset to expose.`);
          return;
        }
        await firstValueFrom(
          this.mcp.addExposure({
            projectId,
            exposureIn: {
              [`${f.kind}_id`]: f.asset,
              name: f.name.trim() || null,
              description: f.description.trim() || null,
            },
          }),
        );
      }
      this.formOpen = false;
      await this.load();
    } catch (err) {
      this.formError.set(problemMessage(err));
    }
  }

  protected async toggle(e: ExposureOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.mcp.updateExposure({
          projectId,
          exposureId: e.id,
          exposureUpdateIn: { enabled: !e.enabled },
        }),
      );
      await this.load();
    } catch (err) {
      this.error.set(problemMessage(err));
    }
  }

  protected async remove(e: ExposureOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(this.mcp.removeExposure({ projectId, exposureId: e.id }));
      await this.load();
    } catch (err) {
      this.error.set(problemMessage(err));
    }
  }

  protected tryIt(e: ExposureOut): void {
    void this.router.navigate(['/mcp', 'try'], { queryParams: { name: e.name } });
  }
}
