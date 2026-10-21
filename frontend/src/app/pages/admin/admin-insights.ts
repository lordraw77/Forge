import { DecimalPipe, PercentPipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzStatisticModule } from 'ng-zorro-antd/statistic';
import { NzTableModule } from 'ng-zorro-antd/table';
import { firstValueFrom } from 'rxjs';

import { AnalyticsService, ModelInsights } from '../../api-client';
import { problemMessage } from '../../core/http';
import { BarChart, BarPoint } from '../../shared/bar-chart';

/** Super Admin console → Model Insights: usage, errors, latency and cost of every model
 * across all projects (E2.12). */
@Component({
  selector: 'forge-admin-insights',
  imports: [
    DecimalPipe,
    PercentPipe,
    FormsModule,
    BarChart,
    NzAlertModule,
    NzCardModule,
    NzSelectModule,
    NzStatisticModule,
    NzTableModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Model Insights</h1>
      <span class="spacer"></span>
      <nz-select class="range" [(ngModel)]="days" (ngModelChange)="load()">
        <nz-option [nzValue]="1" nzLabel="Last 24 hours" i18n-nzLabel />
        <nz-option [nzValue]="7" nzLabel="Last 7 days" i18n-nzLabel />
        <nz-option [nzValue]="30" nzLabel="Last 30 days" i18n-nzLabel />
        <nz-option [nzValue]="90" nzLabel="Last 90 days" i18n-nzLabel />
      </nz-select>
    </header>
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    @if (d(); as d) {
      <div class="tiles">
        <nz-card><nz-statistic i18n-nzTitle nzTitle="Calls" [nzValue]="d.totals.calls" /></nz-card>
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
            nzTitle="Latency p50 / p95 (ms)"
            [nzValue]="(d.totals.latency_p50_ms ?? '–') + ' / ' + (d.totals.latency_p95_ms ?? '–')"
          />
        </nz-card>
        <nz-card>
          <nz-statistic i18n-nzTitle nzTitle="Fallbacks" [nzValue]="d.totals.fallbacks" />
        </nz-card>
      </div>
      <nz-card nzTitle="Calls over time (errors in red)" i18n-nzTitle>
        <forge-bar-chart [points]="points()" title="Calls over time" />
      </nz-card>
      <nz-card nzTitle="Models" i18n-nzTitle>
        <nz-table [nzData]="d.models" nzSize="small" [nzPageSize]="20">
          <thead>
            <tr>
              <th i18n>Model</th>
              <th i18n>Projects</th>
              <th i18n>Calls</th>
              <th i18n>Error rate</th>
              <th i18n>Fallbacks</th>
              <th i18n>Cache hits</th>
              <th i18n>Tokens in / out</th>
              <th i18n>Cost</th>
              <th i18n>p50 / p95 ms</th>
              <th i18n>TTFT p50 ms</th>
            </tr>
          </thead>
          <tbody>
            @for (m of d.models; track m.model) {
              <tr>
                <td>{{ m.model }}</td>
                <td>{{ m.projects }}</td>
                <td>{{ m.calls }}</td>
                <td>{{ m.error_rate | percent: '1.0-1' }}</td>
                <td>{{ m.fallbacks }}</td>
                <td>{{ m.cache_hits }}</td>
                <td>{{ m.tokens_in }} / {{ m.tokens_out }}</td>
                <td>{{ m.cost | number: '1.4-4' }}</td>
                <td>{{ m.latency_p50_ms ?? '–' }} / {{ m.latency_p95_ms ?? '–' }}</td>
                <td>{{ m.ttft_p50_ms ?? '–' }}</td>
              </tr>
            }
          </tbody>
        </nz-table>
      </nz-card>
      <div class="grid">
        <nz-card nzTitle="Providers" i18n-nzTitle>
          @for (p of d.providers; track p.provider) {
            <p>
              <strong>{{ p.provider }}</strong> · {{ p.calls }} <span i18n>calls</span> ·
              {{ p.errors }} <span i18n>errors</span> · {{ p.cost | number: '1.4-4' }} USD
            </p>
          }
        </nz-card>
        <nz-card nzTitle="Projects by cost" i18n-nzTitle>
          @for (p of d.top_projects; track p.name) {
            <p>
              <strong>{{ p.name }}</strong> · {{ p.count }} <span i18n>calls</span> ·
              {{ p.extra | number: '1.4-4' }} USD
            </p>
          }
        </nz-card>
      </div>
      <nz-card nzTitle="Registered models with no calls in this period" i18n-nzTitle>
        <p class="muted">{{ d.unused_models.join(', ') || '–' }}</p>
      </nz-card>
    }
  `,
  styleUrl: './admin.scss',
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
    }
    nz-card {
      margin-bottom: 0.75rem;
    }
    .muted {
      color: var(--forge-text-muted-color);
    }
  `,
})
export class AdminInsights implements OnInit {
  private readonly api = inject(AnalyticsService);
  protected days = 7;
  protected readonly d = signal<ModelInsights | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly points = computed<BarPoint[]>(() =>
    (this.d()?.series ?? []).map((p) => ({
      label: new Date(p.t).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }),
      value: p.calls,
      overlay: p.errors,
    })),
  );

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  async load(): Promise<void> {
    this.error.set(null);
    try {
      const since = new Date(Date.now() - this.days * 86_400_000).toISOString();
      this.d.set(await firstValueFrom(this.api.modelInsights({ since })));
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
