import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { AgentOut, AgentRunOut, AgentUsageOut, AgentsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { statusColor, statusLabel } from './agent-util';

type Status = AgentRunOut['status'] | null;

/** Runs (US-1.20.1): every run for `run.read`, the user's own for `run.read.own`. */
@Component({
  selector: 'forge-agent-runs',
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (usage(); as u) {
      <div class="usage" data-testid="runs-usage">
        <div>
          <span class="big">{{ u.total_runs | number }}</span
          ><span class="muted" i18n>Runs (30 days)</span>
        </div>
        <div>
          <span class="big">{{ u.total_tokens | number }}</span
          ><span class="muted" i18n>Tokens</span>
        </div>
        <div>
          <span class="big">{{ +u.total_cost | number: '1.2-4' }}</span
          ><span class="muted" i18n>Cost (USD)</span>
        </div>
      </div>
    }
    <div class="head">
      <nz-select
        class="filter-status"
        nzId="runs-agent"
        nzAllowClear
        nzPlaceHolder="All agents"
        i18n-nzPlaceHolder
        aria-label="Agent"
        i18n-aria-label
        data-testid="runs-agent"
        [(ngModel)]="agentId"
        (ngModelChange)="reload()"
      >
        @for (a of agents(); track a.id) {
          <nz-option [nzValue]="a.id" [nzLabel]="a.name" />
        }
      </nz-select>
      <nz-select
        class="filter-status"
        nzId="runs-status"
        aria-label="Status"
        i18n-aria-label
        data-testid="runs-status"
        [(ngModel)]="status"
        (ngModelChange)="reload()"
      >
        <nz-option [nzValue]="null" nzLabel="All statuses" i18n-nzLabel />
        <nz-option nzValue="succeeded" nzLabel="Succeeded" i18n-nzLabel />
        <nz-option nzValue="failed" nzLabel="Failed" i18n-nzLabel />
        <nz-option nzValue="cancelled" nzLabel="Stopped" i18n-nzLabel />
        <nz-option nzValue="running" nzLabel="Running" i18n-nzLabel />
      </nz-select>
      <span class="spacer"></span>
      <button nz-button (click)="reload()" data-testid="runs-refresh" i18n>Refresh</button>
    </div>
    @if (message(); as m) {
      <nz-alert nzType="error" [nzMessage]="m" nzShowIcon />
    }
    <nz-table
      #table
      [nzData]="items()"
      nzSize="small"
      [nzShowPagination]="false"
      [nzPageSize]="1000"
      [nzLoading]="loading()"
      [nzNoResult]="empty"
      data-testid="runs-table"
    >
      <thead>
        <tr>
          <th i18n>When</th>
          <th i18n>Agent</th>
          <th i18n>Question</th>
          <th i18n>Status</th>
          <th i18n>Steps</th>
          <th i18n>Tokens</th>
          <th i18n>Cost</th>
          <th i18n>Latency</th>
        </tr>
      </thead>
      <tbody>
        @for (r of table.data; track r.id) {
          <tr [attr.data-testid]="'run-row'">
            <td>
              <a [routerLink]="['/agents/runs', r.id]">{{ r.created_at | date: 'short' }}</a>
            </td>
            <td>
              {{ r.agent_name }} <span class="muted">v{{ r.agent_version }}</span>
            </td>
            <td class="clip">{{ r.input }}</td>
            <td>
              <nz-tag [nzColor]="color(r.status)">{{ label(r.status) }}</nz-tag>
            </td>
            <td>{{ r.steps }}</td>
            <td>{{ r.total_tokens | number }}</td>
            <td>{{ +r.cost | number: '1.2-6' }}</td>
            <td>{{ r.latency_ms ?? 0 | number }} ms</td>
          </tr>
        }
      </tbody>
    </nz-table>
    <ng-template #empty>
      <div class="muted" i18n>No runs yet.</div>
    </ng-template>
    @if (nextCursor()) {
      <div class="dialog-actions">
        <button nz-button (click)="more()" data-testid="load-more" i18n>Load more</button>
      </div>
    }
  `,
  styleUrls: ['../admin/admin.scss', '../prompts/prompts.scss', './agents.scss'],
})
export class AgentRuns {
  private readonly api = inject(AgentsService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly items = signal<AgentRunOut[]>([]);
  protected readonly agents = signal<AgentOut[]>([]);
  protected readonly usage = signal<AgentUsageOut | null>(null);
  protected readonly nextCursor = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly message = signal<string | null>(null);
  protected agentId: string | null = null;
  protected status: Status = null;
  protected readonly color = statusColor;
  protected readonly label = statusLabel;
  private seq = 0;

  constructor() {
    effect(() => {
      if (this.projectId()) void this.start();
    });
  }

  private async start(): Promise<void> {
    const projectId = this.projectId()!;
    void this.reload();
    if (this.session.can('asset.write')) {
      try {
        this.agents.set(
          (await firstValueFrom(this.api.listAgents({ projectId, limit: 200 }))).items,
        );
      } catch {
        // the filter just stays empty
      }
    }
    if (this.session.can('run.read')) {
      try {
        const since = new Date(Date.now() - 30 * 86_400_000).toISOString();
        this.usage.set(await firstValueFrom(this.api.agentUsage({ projectId, since })));
      } catch {
        this.usage.set(null);
      }
    }
  }

  private params(cursor: string | null) {
    return {
      projectId: this.projectId()!,
      agentId: this.agentId,
      status: this.status,
      cursor,
      limit: 50,
    };
  }

  protected async reload(): Promise<void> {
    if (!this.projectId()) return;
    const seq = ++this.seq;
    this.loading.set(true);
    try {
      const page = await firstValueFrom(this.api.listRuns(this.params(null)));
      if (seq !== this.seq) return;
      this.items.set(page.items);
      this.nextCursor.set(page.next_cursor);
      this.message.set(null);
    } catch (e) {
      this.message.set(problemMessage(e));
    } finally {
      if (seq === this.seq) this.loading.set(false);
    }
  }

  protected async more(): Promise<void> {
    const cursor = this.nextCursor();
    if (!cursor) return;
    try {
      const page = await firstValueFrom(this.api.listRuns(this.params(cursor)));
      this.items.update((list) => [...list, ...page.items]);
      this.nextCursor.set(page.next_cursor);
    } catch (e) {
      this.message.set(problemMessage(e));
    }
  }

  protected open(r: AgentRunOut): void {
    void this.router.navigate(['/agents/runs', r.id]);
  }
}
