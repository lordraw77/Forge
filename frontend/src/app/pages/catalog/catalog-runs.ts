import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { UseCaseRunOut, UseCasesService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { durationSeconds, statusColor, statusLabel } from './catalog-util';

/** Run history (US-2.9.2 #3, #4): the user's own runs; operators, builders and admins see the
 * runs of the whole project. Re-run repeats the inputs on the version live now. */
@Component({
  selector: 'forge-catalog-runs',
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
    <div class="head">
      <a nz-button nzType="link" routerLink="/catalog" i18n>← Catalog</a>
      <h1 i18n>Runs</h1>
      <span class="spacer"></span>
      <nz-select
        class="filter"
        nzAllowClear
        nzPlaceHolder="Status"
        i18n-nzPlaceHolder
        [ngModel]="status()"
        (ngModelChange)="status.set($event)"
        data-testid="runs-status"
      >
        @for (s of statuses; track s) {
          <nz-option [nzValue]="s" [nzLabel]="label(s)" />
        }
      </nz-select>
    </div>
    @if (error(); as e) {
      <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
    }
    <nz-table #table [nzData]="runs()" nzSize="small" [nzPageSize]="20" data-testid="runs-table">
      <thead>
        <tr>
          <th i18n>Started</th>
          <th i18n>Use case</th>
          <th i18n>Status</th>
          <th i18n>Version</th>
          <th i18n>Time</th>
          <th i18n>Cost</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (r of table.data; track r.id) {
          <tr>
            <td>{{ r.created_at | date: 'medium' }}</td>
            <td>{{ r.use_case_name }}</td>
            <td>
              <nz-tag [nzColor]="color(r.status)">{{ label(r.status) }}</nz-tag>
            </td>
            <td>{{ r.version === null ? 'draft' : 'v' + r.version }}</td>
            <td>{{ seconds(r) ?? '—' }}{{ seconds(r) !== null ? ' s' : '' }}</td>
            <td>{{ r.cost | number: '1.2-4' }} USD</td>
            <td class="actions">
              <a nz-button nzSize="small" [routerLink]="['/catalog/runs', r.id]" i18n>Open</a>
              <button
                nz-button
                nzSize="small"
                [disabled]="r.mode !== 'published'"
                (click)="rerun(r)"
                i18n
              >
                Run again
              </button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="7" class="muted" i18n>No runs yet.</td>
          </tr>
        }
      </tbody>
    </nz-table>
  `,
  styleUrl: '../admin/admin.scss',
})
export class CatalogRuns {
  private readonly api = inject(UseCasesService);
  private readonly router = inject(Router);
  private readonly session = inject(Session);

  protected readonly statuses = ['running', 'succeeded', 'failed', 'cancelled'] as const;
  protected readonly status = signal<(typeof this.statuses)[number] | null>(null);
  protected readonly runs = signal<UseCaseRunOut[]>([]);
  protected readonly error = signal<string | null>(null);

  constructor() {
    effect(() => {
      const status = this.status();
      if (this.session.project()) void this.load(status);
    });
  }

  protected color = statusColor;
  protected label = statusLabel;
  protected seconds = durationSeconds;

  private async load(status: string | null): Promise<void> {
    const projectId = this.session.project()!.id;
    try {
      this.runs.set(
        await firstValueFrom(
          this.api.listUseCaseRuns({
            projectId,
            status: (status ?? undefined) as 'running' | undefined,
            limit: 200,
          }),
        ),
      );
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async rerun(r: UseCaseRunOut): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    try {
      const next = await firstValueFrom(this.api.rerunUseCaseRun({ projectId, runId: r.id }));
      await this.router.navigate(['/catalog/runs', next.id]);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
