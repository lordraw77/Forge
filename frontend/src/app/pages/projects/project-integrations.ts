import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
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

import { AgentsService, WidgetOut, WidgetService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

interface Pick {
  id: string;
  name: string;
}

/** Projects Studio → Integrations (E3.5): the embeddable chat widgets. The MCP server is in
 * MCP Studio. */
@Component({
  selector: 'forge-project-integrations',
  imports: [
    FormsModule,
    RouterLink,
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
    <p class="intro" i18n>
      The project’s MCP server, what it exposes and how to connect to it are in
      <a routerLink="/mcp" data-testid="mcp-link">MCP Studio</a>.
    </p>

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
  private readonly widgetApi = inject(WidgetService);
  private readonly agentApi = inject(AgentsService);
  protected readonly session = inject(Session);

  protected readonly newTitle = $localize`New widget`;
  protected readonly editTitle = $localize`Edit widget`;
  protected readonly widgets = signal<WidgetOut[]>([]);
  protected readonly agents = signal<Pick[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly formError = signal<string | null>(null);
  protected readonly embed = signal<WidgetOut | null>(null);
  protected widgetOpen = false;
  protected editing: WidgetOut | null = null;
  protected widget = this.blankWidget();
  private readonly projectId = computed(() => this.session.project()?.id ?? null);

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
      const agents = await firstValueFrom(this.agentApi.listAgents({ projectId, limit: 200 }));
      this.agents.set(
        agents.items
          .filter((a) => a.status === 'published')
          .map((a) => ({ id: a.id, name: a.name })),
      );
      if (this.session.can('project.manage')) {
        this.widgets.set(await firstValueFrom(this.widgetApi.listWidgets({ projectId })));
      }
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
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
