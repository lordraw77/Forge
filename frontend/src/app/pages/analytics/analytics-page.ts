import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzStatisticModule } from 'ng-zorro-antd/statistic';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { ActivityItem, AnalyticsService, ProjectDashboard } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { BarChart, BarPoint } from '../../shared/bar-chart';

/** Project dashboards (E2.12) and the 180-day activity log (E2.14), for Project Admins. */
@Component({
  selector: 'forge-analytics',
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    BarChart,
    NzAlertModule,
    NzCardModule,
    NzInputModule,
    NzSelectModule,
    NzStatisticModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <h1 i18n>Analytics</h1>
      <span class="spacer"></span>
      <nz-select class="range" [(ngModel)]="days" (ngModelChange)="load()">
        @for (r of ranges; track r.days) {
          <nz-option [nzValue]="r.days" [nzLabel]="r.label" />
        }
      </nz-select>
    </div>
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    @if (d(); as d) {
      <div class="tiles">
        <nz-card
          ><nz-statistic i18n-nzTitle nzTitle="Model calls" [nzValue]="d.totals.calls"
        /></nz-card>
        <nz-card>
          <nz-statistic
            i18n-nzTitle
            nzTitle="Cost (USD)"
            [nzValue]="(d.totals.cost | number: '1.2-4') ?? ''"
          />
        </nz-card>
        <nz-card>
          <nz-statistic
            i18n-nzTitle
            nzTitle="Tokens in / out"
            [nzValue]="d.totals.tokens_in + ' / ' + d.totals.tokens_out"
          />
        </nz-card>
        <nz-card>
          <nz-statistic
            i18n-nzTitle
            nzTitle="Errors"
            [nzValue]="d.totals.errors + ' (' + (errorRate() | number: '1.0-1') + '%)'"
          />
        </nz-card>
        <nz-card>
          <nz-statistic
            i18n-nzTitle
            nzTitle="Latency p50 / p95 (ms)"
            [nzValue]="(d.totals.latency_p50_ms ?? '–') + ' / ' + (d.totals.latency_p95_ms ?? '–')"
          />
        </nz-card>
        <nz-card>
          <nz-statistic i18n-nzTitle nzTitle="Active users" [nzValue]="d.adoption.active_users" />
        </nz-card>
      </div>

      <div class="grid">
        <nz-card nzTitle="Calls over time (errors in red)" i18n-nzTitle>
          <forge-bar-chart [points]="callPoints()" title="Calls over time" />
        </nz-card>
        <nz-card nzTitle="Cost over time" i18n-nzTitle>
          <forge-bar-chart [points]="costPoints()" title="Cost over time" />
        </nz-card>
      </div>

      <nz-card nzTitle="By model" i18n-nzTitle>
        <nz-table [nzData]="d.by_model" nzSize="small" [nzShowPagination]="false">
          <thead>
            <tr>
              <th i18n>Model</th>
              <th i18n>Calls</th>
              <th i18n>Errors</th>
              <th i18n>Fallbacks</th>
              <th i18n>Tokens</th>
              <th i18n>Cost</th>
              <th i18n>p50 / p95 ms</th>
            </tr>
          </thead>
          <tbody>
            @for (m of d.by_model; track m.model) {
              <tr>
                <td>{{ m.model }}</td>
                <td>{{ m.calls }}</td>
                <td>{{ m.errors }}</td>
                <td>{{ m.fallbacks }}</td>
                <td>{{ m.tokens_in + m.tokens_out }}</td>
                <td>{{ m.cost | number: '1.4-4' }}</td>
                <td>{{ m.latency_p50_ms ?? '–' }} / {{ m.latency_p95_ms ?? '–' }}</td>
              </tr>
            }
          </tbody>
        </nz-table>
      </nz-card>

      <div class="grid">
        <nz-card nzTitle="Top users and keys by cost" i18n-nzTitle>
          <nz-table [nzData]="d.by_user" nzSize="small" [nzShowPagination]="false">
            <tbody>
              @for (u of d.by_user; track $index) {
                <tr>
                  <td>{{ u.label }}</td>
                  <td>{{ u.calls }}</td>
                  <td>{{ u.cost | number: '1.4-4' }}</td>
                </tr>
              }
            </tbody>
          </nz-table>
        </nz-card>
        <nz-card nzTitle="Errors" i18n-nzTitle>
          @for (e of d.errors; track e.name) {
            <p>
              <nz-tag nzColor="red">{{ e.count }}</nz-tag> {{ e.name }}
            </p>
          } @empty {
            <p class="muted" i18n>No errors in this period</p>
          }
        </nz-card>
      </div>

      <nz-card nzTitle="Guardrail interventions" i18n-nzTitle>
        <p>
          <strong>{{ d.guardrails.total }}</strong> <span i18n>interventions</span>
          @for (c of d.guardrails.by_check; track c.name) {
            <nz-tag>{{ c.name }}: {{ c.count }}</nz-tag>
          }
          @for (a of d.guardrails.by_action; track a.name) {
            <nz-tag nzColor="orange">{{ a.name }}: {{ a.count }}</nz-tag>
          }
        </p>
        <forge-bar-chart [points]="guardrailPoints()" title="Guardrail interventions" />
      </nz-card>

      <div class="grid">
        <nz-card nzTitle="Adoption" i18n-nzTitle>
          <p>
            <span i18n>Use case runs</span>: {{ d.adoption.use_case_runs }} (<span i18n
              >failed</span
            >
            {{ d.adoption.use_case_failed }}) · <span i18n>Agent runs</span>:
            {{ d.adoption.agent_runs }}
          </p>
          @for (u of d.adoption.top_use_cases; track u.name) {
            <p>
              <nz-tag>{{ u.count }}</nz-tag> {{ u.name }}
            </p>
          }
          @for (u of d.adoption.top_agents; track u.name) {
            <p>
              <nz-tag nzColor="blue">{{ u.count }}</nz-tag> {{ u.name }}
            </p>
          }
        </nz-card>
        <nz-card nzTitle="Run totals against gateway calls" i18n-nzTitle>
          <p>
            <span i18n>Agent runs</span>: {{ d.reconciliation.run_cost | number: '1.4-6' }} USD,
            {{ d.reconciliation.run_tokens }} <span i18n>tokens</span>
          </p>
          <p>
            <span i18n>Gateway calls of their steps</span>:
            {{ d.reconciliation.call_cost | number: '1.4-6' }} USD,
            {{ d.reconciliation.call_tokens }} <span i18n>tokens</span>
          </p>
        </nz-card>
      </div>
    }

    <nz-card nzTitle="Activity log (kept 180 days)" i18n-nzTitle>
      <div class="head">
        <input
          nz-input
          class="filter"
          placeholder="Search action, name, user"
          i18n-placeholder
          [(ngModel)]="query"
          (keyup.enter)="loadActivity()"
        />
        <nz-select class="range" [(ngModel)]="activityHours" (ngModelChange)="loadActivity()">
          <nz-option [nzValue]="24" nzLabel="Last 24 hours" i18n-nzLabel />
          <nz-option [nzValue]="168" nzLabel="Last 7 days" i18n-nzLabel />
          <nz-option [nzValue]="4320" nzLabel="Last 180 days" i18n-nzLabel />
        </nz-select>
      </div>
      <nz-table [nzData]="activity()" nzSize="small" [nzPageSize]="15">
        <thead>
          <tr>
            <th i18n>Time</th>
            <th i18n>Kind</th>
            <th i18n>What</th>
            <th i18n>Who</th>
            <th i18n>Status</th>
          </tr>
        </thead>
        <tbody>
          @for (a of activity(); track $index) {
            <tr>
              <td>{{ a.at | date: 'medium' }}</td>
              <td>{{ a.kind }}</td>
              <td>
                {{ a.title }} <span class="muted">{{ a.detail }}</span>
              </td>
              <td>{{ a.actor }}</td>
              <td>{{ a.status }}</td>
            </tr>
          }
        </tbody>
      </nz-table>
    </nz-card>
  `,
  styleUrl: '../admin/admin.scss',
  styles: `
    .tiles {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr));
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }
    nz-card {
      margin-bottom: 0.75rem;
    }
    .muted {
      color: var(--forge-text-muted-color);
    }
  `,
})
export class AnalyticsPage {
  private readonly api = inject(AnalyticsService);
  protected readonly session = inject(Session);

  protected readonly ranges = [
    { label: $localize`Last 24 hours`, days: 1 },
    { label: $localize`Last 7 days`, days: 7 },
    { label: $localize`Last 30 days`, days: 30 },
    { label: $localize`Last 90 days`, days: 90 },
  ];
  protected days = 7;
  protected activityHours = 168;
  protected query = '';
  protected readonly d = signal<ProjectDashboard | null>(null);
  protected readonly activity = signal<ActivityItem[]>([]);
  protected readonly error = signal<string | null>(null);
  private readonly projectId = computed(() => this.session.project()?.id ?? null);

  protected readonly errorRate = computed(() => {
    const t = this.d()?.totals;
    return t?.calls ? ((t.errors ?? 0) / t.calls) * 100 : 0;
  });
  protected readonly callPoints = computed<BarPoint[]>(() =>
    (this.d()?.series ?? []).map((p) => ({
      label: this.when(p.t),
      value: p.calls,
      overlay: p.errors,
    })),
  );
  protected readonly costPoints = computed<BarPoint[]>(() =>
    (this.d()?.series ?? []).map((p) => ({ label: this.when(p.t), value: p.cost })),
  );
  protected readonly guardrailPoints = computed<BarPoint[]>(() =>
    (this.d()?.guardrails.series ?? []).map((p) => ({
      label: this.when(p.name),
      value: p.count,
    })),
  );

  constructor() {
    effect(() => {
      if (this.projectId() && this.session.can('project.manage')) {
        void this.load();
        void this.loadActivity();
      }
    });
  }

  private when(iso: string): string {
    return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
  }

  async load(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.error.set(null);
    try {
      const since = new Date(Date.now() - this.days * 86_400_000).toISOString();
      this.d.set(await firstValueFrom(this.api.projectDashboard({ projectId, since })));
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  async loadActivity(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const since = new Date(Date.now() - this.activityHours * 3_600_000).toISOString();
      this.activity.set(
        await firstValueFrom(
          this.api.projectActivity({ projectId, since, q: this.query || undefined, limit: 500 }),
        ),
      );
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
