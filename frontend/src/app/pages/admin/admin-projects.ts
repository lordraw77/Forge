import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';
import { firstValueFrom } from 'rxjs';

import { AdminProjectOut, AdminService, UserOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** Super Admin console → Projects (US-1.1.2). */
@Component({
  selector: 'forge-admin-projects',
  imports: [
    DatePipe,
    FormsModule,
    ButtonModule,
    DialogModule,
    InputTextModule,
    MessageModule,
    SelectModule,
    TableModule,
    TagModule,
    TextareaModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Projects</h1>
      <span class="spacer"></span>
      <p-button
        label="New project"
        i18n-label
        icon="pi pi-plus"
        (onClick)="openCreate()"
        data-testid="new-project"
      />
    </header>

    @if (notice(); as n) {
      <p-message [severity]="n.severity" [text]="n.text" data-testid="notice" />
    }

    <p-table [value]="projects()" dataKey="id" styleClass="p-datatable-sm">
      <ng-template #header>
        <tr>
          <th i18n>Name</th>
          <th i18n>Description</th>
          <th i18n>Members</th>
          <th i18n>Status</th>
          <th i18n>Created</th>
          <th></th>
        </tr>
      </ng-template>
      <ng-template #body let-p>
        <tr [attr.data-project]="p.name">
          <td>{{ p.name }}</td>
          <td>{{ p.description }}</td>
          <td>{{ p.member_count }}</td>
          <td>
            <p-tag
              [value]="p.status"
              [severity]="p.status === 'active' ? 'success' : 'secondary'"
            />
          </td>
          <td>{{ p.created_at | date: 'mediumDate' }}</td>
          <td class="actions">
            @if (p.status === 'active') {
              <p-button
                size="small"
                [text]="true"
                label="Archive"
                i18n-label
                (onClick)="setStatus(p, 'archive')"
              />
            } @else {
              <p-button
                size="small"
                [text]="true"
                label="Restore"
                i18n-label
                (onClick)="setStatus(p, 'restore')"
              />
            }
            <p-button
              size="small"
              [text]="true"
              severity="danger"
              label="Delete"
              i18n-label
              (onClick)="openDelete(p)"
            />
          </td>
        </tr>
      </ng-template>
    </p-table>

    <p-dialog
      header="New project"
      i18n-header
      [modal]="true"
      [(visible)]="createOpen"
      [style]="{ width: '30rem' }"
    >
      <form class="form" (ngSubmit)="create()">
        <label for="p-name" i18n>Name</label>
        <input pInputText id="p-name" name="name" [(ngModel)]="draft.name" required />
        <label for="p-desc" i18n>Description</label>
        <textarea
          pTextarea
          id="p-desc"
          name="description"
          rows="3"
          [(ngModel)]="draft.description"
        ></textarea>
        <label for="p-admin" i18n>Project Admin</label>
        <p-select
          inputId="p-admin"
          name="admin"
          [options]="users()"
          optionLabel="username"
          optionValue="id"
          [filter]="true"
          filterBy="username,email,full_name"
          [(ngModel)]="draft.admin_user_id"
          placeholder="Select a user"
          i18n-placeholder
          appendTo="body"
        />
        @if (dialogError(); as e) {
          <p-message severity="error" [text]="e" />
        }
        <div class="dialog-actions">
          <p-button type="submit" label="Create" i18n-label data-testid="create-project" />
        </div>
      </form>
    </p-dialog>

    <p-dialog
      header="Delete project"
      i18n-header
      [modal]="true"
      [(visible)]="deleteOpen"
      [style]="{ width: '30rem' }"
    >
      @if (toDelete(); as p) {
        <form class="form" (ngSubmit)="remove(p)">
          <p-message severity="warn" i18n
            >The project, its data and operations will be deleted. Members lose access
            immediately.</p-message
          >
          <label for="d-reason" i18n>Reason</label>
          <textarea pTextarea id="d-reason" name="reason" rows="2" [(ngModel)]="reason"></textarea>
          <label for="d-confirm" i18n>Type "{{ p.name }}" to confirm</label>
          <input pInputText id="d-confirm" name="confirm" [(ngModel)]="confirmName" />
          @if (dialogError(); as e) {
            <p-message severity="error" [text]="e" />
          }
          <div class="dialog-actions">
            <p-button
              type="submit"
              severity="danger"
              label="Delete"
              i18n-label
              [disabled]="confirmName !== p.name || reason.length < 5"
            />
          </div>
        </form>
      }
    </p-dialog>
  `,
  styleUrl: './admin.scss',
})
export class AdminProjects implements OnInit {
  private readonly admin = inject(AdminService);
  private readonly session = inject(Session);
  protected readonly projects = signal<AdminProjectOut[]>([]);
  protected readonly users = signal<UserOut[]>([]);
  protected readonly notice = signal<{ severity: 'success' | 'error'; text: string } | null>(null);
  protected readonly dialogError = signal<string | null>(null);
  protected readonly toDelete = signal<AdminProjectOut | null>(null);
  protected createOpen = false;
  protected deleteOpen = false;
  protected draft = { name: '', description: '', admin_user_id: '' };
  protected reason = '';
  protected confirmName = '';

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  private async load(): Promise<void> {
    this.projects.set(await firstValueFrom(this.admin.listProjects({})));
  }

  protected async openCreate(): Promise<void> {
    this.draft = { name: '', description: '', admin_user_id: '' };
    this.dialogError.set(null);
    this.users.set(
      (await firstValueFrom(this.admin.listUsers({}))).filter((u) => u.status === 'active'),
    );
    this.createOpen = true;
  }

  protected async create(): Promise<void> {
    try {
      const res = await firstValueFrom(
        this.admin.createProject({ projectCreateIn: { ...this.draft } }),
      );
      this.createOpen = false;
      this.notice.set({
        severity: 'success',
        text: $localize`Project ${res.project.name}:name: created. Project token (shown once): ${res.api_key}:token:`,
      });
      await this.load();
      await this.session.load();
    } catch (e) {
      this.dialogError.set(problemMessage(e));
    }
  }

  protected async setStatus(p: AdminProjectOut, action: 'archive' | 'restore'): Promise<void> {
    try {
      await firstValueFrom(
        action === 'archive'
          ? this.admin.archiveProject({ projectId: p.id })
          : this.admin.restoreProject({ projectId: p.id }),
      );
      await this.load();
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }

  protected openDelete(p: AdminProjectOut): void {
    this.toDelete.set(p);
    this.reason = '';
    this.confirmName = '';
    this.dialogError.set(null);
    this.deleteOpen = true;
  }

  protected async remove(p: AdminProjectOut): Promise<void> {
    try {
      await firstValueFrom(
        this.admin.deleteProject({
          projectId: p.id,
          projectDeleteIn: { reason: this.reason, confirm_name: this.confirmName },
        }),
      );
      this.deleteOpen = false;
      this.notice.set({ severity: 'success', text: $localize`Project ${p.name}:name: deleted` });
      await this.load();
      await this.session.load();
    } catch (e) {
      this.dialogError.set(problemMessage(e));
    }
  }
}
