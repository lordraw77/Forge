import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { ToolCallOut, ToolOut, ToolsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** Call log: every test and agent call, with status, timing and redacted input and output
 * (US-2.2.8). */
@Component({
  selector: 'forge-tool-calls',
  imports: [DatePipe, FormsModule, NzAlertModule, NzSelectModule, NzTableModule, NzTagModule],
  template: `
    <div class="head">
      <nz-select
        class="filter"
        nzId="call-tool"
        nzAllowClear
        nzPlaceHolder="All tools"
        i18n-nzPlaceHolder
        aria-label="Tool"
        i18n-aria-label
        [(ngModel)]="toolId"
        (ngModelChange)="reload()"
      >
        @for (t of tools(); track t.id) {
          <nz-option [nzValue]="t.id" [nzLabel]="t.name" />
        }
      </nz-select>
      <nz-select
        class="filter-status"
        nzId="call-status"
        nzAllowClear
        nzPlaceHolder="Any status"
        i18n-nzPlaceHolder
        aria-label="Status"
        i18n-aria-label
        [(ngModel)]="status"
        (ngModelChange)="reload()"
      >
        <nz-option nzValue="ok" nzLabel="ok" />
        <nz-option nzValue="error" nzLabel="error" />
      </nz-select>
      <nz-select
        class="filter-status"
        nzId="call-source"
        nzAllowClear
        nzPlaceHolder="Any source"
        i18n-nzPlaceHolder
        aria-label="Source"
        i18n-aria-label
        [(ngModel)]="source"
        (ngModelChange)="reload()"
      >
        <nz-option nzValue="test" nzLabel="test" />
        <nz-option nzValue="agent" nzLabel="agent" />
      </nz-select>
    </div>
    @if (error(); as e) {
      <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
    }
    <nz-table
      #table
      [nzData]="calls()"
      nzSize="small"
      [nzShowPagination]="false"
      [nzPageSize]="1000"
      data-testid="call-table"
    >
      <thead>
        <tr>
          <th i18n>Time</th>
          <th i18n>Tool</th>
          <th i18n>Source</th>
          <th i18n>Status</th>
          <th i18n>Latency</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (c of table.data; track c.id) {
          <tr>
            <td>{{ c.created_at | date: 'medium' }}</td>
            <td>
              {{ c.tool_name }} <span class="muted">v{{ c.version }}</span>
            </td>
            <td>{{ c.source }}</td>
            <td>
              <nz-tag [nzColor]="c.status === 'ok' ? 'success' : 'error'">{{ c.status }}</nz-tag>
              @if (c.error_code) {
                <span class="muted">{{ c.error_code }}</span>
              }
            </td>
            <td>{{ c.latency_ms }} ms</td>
            <td class="actions">
              <button
                type="button"
                class="link"
                (click)="toggle(c)"
                [attr.aria-expanded]="open() === c.id"
                i18n
              >
                Details
              </button>
            </td>
          </tr>
          @if (open() === c.id) {
            <tr>
              <td colspan="6">
                <h4 i18n>Input</h4>
                <pre>{{ pretty(c.input) }}</pre>
                <h4 i18n>Output</h4>
                <pre>{{ pretty(c.output) }}</pre>
              </td>
            </tr>
          }
        } @empty {
          <tr>
            <td colspan="6" class="muted" i18n>No calls yet.</td>
          </tr>
        }
      </tbody>
    </nz-table>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ToolCalls {
  private readonly api = inject(ToolsService);
  private readonly session = inject(Session);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly calls = signal<ToolCallOut[]>([]);
  protected readonly tools = signal<ToolOut[]>([]);
  protected readonly open = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);
  protected toolId: string | null = null;
  protected status: 'ok' | 'error' | null = null;
  protected source: 'test' | 'agent' | null = null;

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      if (!projectId) return;
      void firstValueFrom(this.api.listTools({ projectId })).then((p) => this.tools.set(p.items));
      void this.reload();
    });
  }

  protected async reload(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      this.calls.set(
        await firstValueFrom(
          this.api.listToolCalls({
            projectId,
            toolId: this.toolId ?? undefined,
            status: this.status ?? undefined,
            source: this.source ?? undefined,
          }),
        ),
      );
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected toggle(c: ToolCallOut): void {
    this.open.set(this.open() === c.id ? null : c.id);
  }

  protected pretty(value: unknown): string {
    return JSON.stringify(value, null, 2);
  }
}
