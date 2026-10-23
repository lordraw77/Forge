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
  AgentsService,
  DataService,
  ExposureOut,
  McpService,
  PromptsService,
  WidgetOut,
  WidgetService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Kind = 'agent' | 'prompt' | 'collection';
interface Pick {
  id: string;
  name: string;
}

/** Projects Studio → Integrations (E3.5): what the project's MCP server offers to external
 * clients, and the embeddable chat widgets. */
@Component({
  selector: 'forge-project-integrations',
  imports: [
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
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    <div class="head">
      <h2 i18n>MCP server</h2>
      <span class="spacer"></span>
      <button nz-button nzType="primary" (click)="openExpose()" data-testid="mcp-add" i18n>
        Expose an asset
      </button>
    </div>
    <p class="intro" i18n>
      External MCP clients (Claude Desktop, Claude Code, IDE assistants) can use what you expose
      here: agents become tools that answer, data collections become search tools, prompts become
      MCP prompts. They sign in with a project API key that has the “usecase.run” scope.
    </p>
    <p>
      <span i18n>Endpoint:</span> <code data-testid="mcp-url">{{ mcpUrl() }}</code>
    </p>
    <p class="hint">
      <code
        >claude mcp add --transport http forge {{ mcpUrl() }} --header "Authorization: Bearer
        fk_…"</code
      >
    </p>
    <nz-table [nzData]="exposures()" nzSize="small" [nzShowPagination]="false">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Kind</th>
          <th i18n>Asset</th>
          <th i18n>Description</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (e of exposures(); track e.id) {
          <tr>
            <td>
              <code>{{ e.name }}</code>
              @if (!e.enabled) {
                <nz-tag i18n>Paused</nz-tag>
              }
            </td>
            <td>
              <nz-tag>{{ e.kind }}</nz-tag>
            </td>
            <td>{{ e.asset_name }}</td>
            <td>{{ e.description }}</td>
            <td class="actions">
              <button nz-button nzSize="small" (click)="toggleExposure(e)">
                {{ e.enabled ? pauseLabel : resumeLabel }}
              </button>
              <button nz-button nzSize="small" nzDanger (click)="unexpose(e)" i18n>Remove</button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="5" class="muted" i18n>Nothing is exposed yet.</td>
          </tr>
        }
      </tbody>
    </nz-table>

    @if (session.can('project.manage')) {
      <div class="head">
        <h2 i18n>Chat widgets</h2>
        <span class="spacer"></span>
        <button nz-button nzType="primary" (click)="openWidget(null)" data-testid="widget-new" i18n>
          New widget
        </button>
      </div>
      <p class="intro" i18n>
        A widget puts a chat bubble on another website, connected to one published agent. Visitors
        need no account: they are limited by the sites you allow, a rate per visitor and a daily
        cap. Their conversations run as you.
      </p>
      <nz-table [nzData]="widgets()" nzSize="small" [nzShowPagination]="false">
        <thead>
          <tr>
            <th i18n>Name</th>
            <th i18n>Agent</th>
            <th i18n>Allowed sites</th>
            <th i18n>Limits</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (w of widgets(); track w.id) {
            <tr>
              <td>
                {{ w.name }}
                @if (!w.enabled) {
                  <nz-tag i18n>Off</nz-tag>
                }
              </td>
              <td>{{ w.agent_name }}</td>
              <td>{{ w.allowed_origins.join(', ') || '–' }}</td>
              <td>
                <span i18n>{{ w.messages_per_minute }}/min, {{ w.daily_limit }}/day</span>
              </td>
              <td class="actions">
                <button nz-button nzSize="small" (click)="embed.set(w)" i18n>Embed code</button>
                <button nz-button nzSize="small" (click)="openWidget(w)" i18n>Edit</button>
                <button nz-button nzSize="small" nzDanger (click)="removeWidget(w)" i18n>
                  Delete
                </button>
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="5" class="muted" i18n>No widgets yet.</td>
            </tr>
          }
        </tbody>
      </nz-table>
    }

    <nz-modal
      [(nzVisible)]="exposeOpen"
      nzTitle="Expose an asset"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="560"
    >
      <ng-container *nzModalContent>
        <div class="form">
          <label i18n>Kind</label>
          <nz-select nzId="mcp-kind" [(ngModel)]="expose.kind" (ngModelChange)="expose.asset = ''">
            <nz-option nzValue="agent" nzLabel="Agent" i18n-nzLabel />
            <nz-option nzValue="collection" nzLabel="Data collection" i18n-nzLabel />
            <nz-option nzValue="prompt" nzLabel="Prompt" i18n-nzLabel />
          </nz-select>
          <label i18n>Asset</label>
          <nz-select nzId="mcp-asset" nzShowSearch [(ngModel)]="expose.asset">
            @for (a of assets(); track a.id) {
              <nz-option [nzValue]="a.id" [nzLabel]="a.name" />
            }
          </nz-select>
          <label for="mcp-name" i18n>Name (optional)</label>
          <input nz-input id="mcp-name" maxlength="64" [(ngModel)]="expose.name" />
          <label for="mcp-desc" i18n>Description (optional)</label>
          <textarea nz-input id="mcp-desc" rows="3" [(ngModel)]="expose.description"></textarea>
        </div>
        @if (formError()) {
          <nz-alert nzType="error" [nzMessage]="formError()" />
        }
        <div class="footer">
          <button nz-button (click)="exposeOpen = false" i18n>Cancel</button>
          <button nz-button nzType="primary" (click)="saveExpose()" data-testid="mcp-save" i18n>
            Expose
          </button>
        </div>
      </ng-container>
    </nz-modal>

    <nz-modal
      [(nzVisible)]="widgetOpen"
      [nzTitle]="editing ? editTitle : newTitle"
      [nzFooter]="null"
      [nzWidth]="640"
    >
      <ng-container *nzModalContent>
        <div class="form">
          <label for="wd-name" i18n>Name</label>
          <input nz-input id="wd-name" maxlength="80" [(ngModel)]="widget.name" />
          <label i18n>Agent (published)</label>
          <nz-select nzId="wd-agent" nzShowSearch [(ngModel)]="widget.agent_id">
            @for (a of agents(); track a.id) {
              <nz-option [nzValue]="a.id" [nzLabel]="a.name" />
            }
          </nz-select>
          <label for="wd-title" i18n>Window title</label>
          <input nz-input id="wd-title" maxlength="80" [(ngModel)]="widget.title" />
          <label for="wd-welcome" i18n>Welcome message</label>
          <textarea
            nz-input
            id="wd-welcome"
            rows="2"
            maxlength="500"
            [(ngModel)]="widget.welcome"
          ></textarea>
          <label for="wd-origins" i18n>Allowed sites (one per line)</label>
          <textarea
            nz-input
            id="wd-origins"
            rows="3"
            class="mono"
            placeholder="https://intranet.example.com"
            [(ngModel)]="widget.origins"
          ></textarea>
          <label i18n>Messages per minute, per visitor</label>
          <nz-input-number [(ngModel)]="widget.perMinute" [nzMin]="1" [nzMax]="600" />
          <label i18n>Messages per day, all visitors</label>
          <nz-input-number [(ngModel)]="widget.perDay" [nzMin]="1" [nzMax]="1000000" />
          <label></label>
          <label nz-checkbox [(ngModel)]="widget.enabled" i18n>Enabled</label>
        </div>
        @if (formError()) {
          <nz-alert nzType="error" [nzMessage]="formError()" />
        }
        <div class="footer">
          <button nz-button (click)="widgetOpen = false" i18n>Cancel</button>
          <button nz-button nzType="primary" (click)="saveWidget()" data-testid="widget-save" i18n>
            Save
          </button>
        </div>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!embed()"
      nzTitle="Embed code"
      i18n-nzTitle
      (nzOnCancel)="embed.set(null)"
      [nzFooter]="null"
      [nzWidth]="720"
    >
      <ng-container *nzModalContent>
        <p i18n>
          Paste this before the closing body tag of the page. The site must be in the allowed list.
        </p>
        <textarea nz-input readonly rows="3" class="mono" data-testid="widget-snippet">{{
          snippet()
        }}</textarea>
        <div class="footer">
          <button nz-button nzType="primary" (click)="embed.set(null)" i18n>Done</button>
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
    .mono,
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
export class ProjectIntegrations {
  private readonly mcp = inject(McpService);
  private readonly widgetApi = inject(WidgetService);
  private readonly agentApi = inject(AgentsService);
  private readonly promptApi = inject(PromptsService);
  private readonly dataApi = inject(DataService);
  protected readonly session = inject(Session);

  protected readonly newTitle = $localize`New widget`;
  protected readonly editTitle = $localize`Edit widget`;
  protected readonly pauseLabel = $localize`Pause`;
  protected readonly resumeLabel = $localize`Resume`;
  protected readonly exposures = signal<ExposureOut[]>([]);
  protected readonly widgets = signal<WidgetOut[]>([]);
  protected readonly agents = signal<Pick[]>([]);
  protected readonly prompts = signal<Pick[]>([]);
  protected readonly collections = signal<Pick[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly formError = signal<string | null>(null);
  protected readonly embed = signal<WidgetOut | null>(null);
  protected exposeOpen = false;
  protected widgetOpen = false;
  protected editing: WidgetOut | null = null;
  protected expose = { kind: 'agent' as Kind, asset: '', name: '', description: '' };
  protected widget = this.blankWidget();
  private readonly projectId = computed(() => this.session.project()?.id ?? null);

  protected readonly mcpUrl = computed(
    () => `${window.location.origin}/api/v1/projects/${this.projectId()}/mcp`,
  );
  protected readonly assets = computed(() =>
    this.expose.kind === 'agent'
      ? this.agents()
      : this.expose.kind === 'prompt'
        ? this.prompts()
        : this.collections(),
  );
  protected readonly snippet = computed(() => {
    const w = this.embed();
    return w
      ? `<script src="${window.location.origin}/api/v1/widget/widget.js" data-key="${w.public_key}" defer></script>`
      : '';
  });

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
  }

  private blankWidget() {
    return {
      name: '',
      agent_id: '',
      title: '',
      welcome: '',
      origins: '',
      perMinute: 10,
      perDay: 1000,
      enabled: true,
    };
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
      if (this.session.can('project.manage')) {
        this.widgets.set(await firstValueFrom(this.widgetApi.listWidgets({ projectId })));
      }
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected openExpose(): void {
    this.expose = { kind: 'agent', asset: '', name: '', description: '' };
    this.formError.set(null);
    this.exposeOpen = true;
  }

  protected async saveExpose(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId || !this.expose.asset) {
      this.formError.set($localize`Pick the asset to expose.`);
      return;
    }
    const e = this.expose;
    try {
      await firstValueFrom(
        this.mcp.addExposure({
          projectId,
          exposureIn: {
            [`${e.kind}_id`]: e.asset,
            name: e.name.trim() || null,
            description: e.description.trim() || null,
          },
        }),
      );
      this.exposeOpen = false;
      await this.load();
    } catch (err) {
      this.formError.set(problemMessage(err));
    }
  }

  protected async toggleExposure(e: ExposureOut): Promise<void> {
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

  protected async unexpose(e: ExposureOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(this.mcp.removeExposure({ projectId, exposureId: e.id }));
      await this.load();
    } catch (err) {
      this.error.set(problemMessage(err));
    }
  }

  protected openWidget(w: WidgetOut | null): void {
    this.editing = w;
    this.widget = w
      ? {
          name: w.name,
          agent_id: w.agent_id,
          title: w.title,
          welcome: w.welcome,
          origins: w.allowed_origins.join('\n'),
          perMinute: w.messages_per_minute,
          perDay: w.daily_limit,
          enabled: w.enabled,
        }
      : this.blankWidget();
    this.formError.set(null);
    this.widgetOpen = true;
  }

  protected async saveWidget(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const w = this.widget;
    const body = {
      name: w.name,
      agent_id: w.agent_id,
      title: w.title,
      welcome: w.welcome,
      allowed_origins: w.origins
        .split('\n')
        .map((o) => o.trim())
        .filter(Boolean),
      messages_per_minute: w.perMinute,
      daily_limit: w.perDay,
      enabled: w.enabled,
    };
    try {
      if (this.editing) {
        await firstValueFrom(
          this.widgetApi.updateWidget({
            projectId,
            widgetId: this.editing.id,
            widgetUpdateIn: body,
          }),
        );
      } else {
        const created = await firstValueFrom(
          this.widgetApi.createWidget({ projectId, widgetIn: body }),
        );
        this.embed.set(created);
      }
      this.widgetOpen = false;
      await this.load();
    } catch (err) {
      this.formError.set(problemMessage(err));
    }
  }

  protected async removeWidget(w: WidgetOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(this.widgetApi.deleteWidget({ projectId, widgetId: w.id }));
      await this.load();
    } catch (err) {
      this.error.set(problemMessage(err));
    }
  }
}
