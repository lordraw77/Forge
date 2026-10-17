import { DatePipe, JsonPipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { firstValueFrom } from 'rxjs';

import { AdminService, AuditOut } from '../../api-client';

/** Super Admin console → Audit log (US-1.1.3). */
@Component({
  selector: 'forge-admin-audit',
  imports: [
    DatePipe,
    JsonPipe,
    FormsModule,
    ButtonModule,
    InputTextModule,
    SelectModule,
    TableModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Audit log</h1>
      <span class="spacer"></span>
      <input
        pInputText
        placeholder="Action, e.g. project.create"
        i18n-placeholder
        [(ngModel)]="action"
      />
      <p-select [options]="ranges" optionLabel="label" optionValue="hours" [(ngModel)]="hours" />
      <p-button label="Apply" i18n-label (onClick)="load()" />
    </header>
    <p-table
      [value]="events()"
      dataKey="id"
      styleClass="p-datatable-sm"
      [paginator]="true"
      [rows]="25"
    >
      <ng-template #header>
        <tr>
          <th i18n>Time</th>
          <th i18n>Actor</th>
          <th i18n>Action</th>
          <th i18n>Target</th>
          <th i18n>Details</th>
          <th i18n>IP</th>
        </tr>
      </ng-template>
      <ng-template #body let-e>
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
      </ng-template>
    </p-table>
  `,
  styleUrl: './admin.scss',
})
export class AdminAudit implements OnInit {
  private readonly admin = inject(AdminService);
  protected readonly events = signal<AuditOut[]>([]);
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
