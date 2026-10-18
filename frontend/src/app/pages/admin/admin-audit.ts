import { DatePipe, JsonPipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { firstValueFrom } from 'rxjs';

import { AdminService, AuditOut } from '../../api-client';

/** Super Admin console → Audit log (US-1.1.3). */
@Component({
  selector: 'forge-admin-audit',
  imports: [
    DatePipe,
    JsonPipe,
    FormsModule,
    NzButtonModule,
    NzInputModule,
    NzSelectModule,
    NzTableModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Audit log</h1>
      <span class="spacer"></span>
      <input
        nz-input
        class="filter"
        placeholder="Action, e.g. project.create"
        i18n-placeholder
        [(ngModel)]="action"
      />
      <nz-select class="range" [(ngModel)]="hours">
        @for (r of ranges; track r.hours) {
          <nz-option [nzValue]="r.hours" [nzLabel]="r.label" />
        }
      </nz-select>
      <button nz-button nzType="primary" (click)="load()" i18n>Apply</button>
    </header>
    <nz-table
      [nzData]="events()"
      nzSize="small"
      [nzPageSize]="25"
      (nzCurrentPageDataChange)="page.set($event)"
    >
      <thead>
        <tr>
          <th i18n>Time</th>
          <th i18n>Actor</th>
          <th i18n>Action</th>
          <th i18n>Target</th>
          <th i18n>Details</th>
          <th i18n>IP</th>
        </tr>
      </thead>
      <tbody>
        @for (e of page(); track e.id) {
          <tr>
            <td>{{ e.created_at | date: 'medium' }}</td>
            <td>{{ e.actor_label }}</td>
            <td>
              <code>{{ e.action }}</code>
            </td>
            <td>{{ e.target_type }} {{ e.target_id }}</td>
            <td>
              <code class="diff">{{ e.diff | json }}</code>
            </td>
            <td>{{ e.ip }}</td>
          </tr>
        }
      </tbody>
    </nz-table>
  `,
  styleUrl: './admin.scss',
})
export class AdminAudit implements OnInit {
  private readonly admin = inject(AdminService);
  protected readonly events = signal<AuditOut[]>([]);
  protected readonly page = signal<readonly AuditOut[]>([]);
  protected action = '';
  protected hours = 24;
  protected readonly ranges = [
    { label: $localize`Last 24 hours`, hours: 24 },
    { label: $localize`Last 7 days`, hours: 24 * 7 },
    { label: $localize`Last 30 days`, hours: 24 * 30 },
  ];

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  async load(): Promise<void> {
    const since = new Date(Date.now() - this.hours * 3600_000).toISOString();
    this.events.set(
      await firstValueFrom(
        this.admin.queryAudit({ action: this.action || undefined, since, limit: 500 }),
      ),
    );
  }
}
