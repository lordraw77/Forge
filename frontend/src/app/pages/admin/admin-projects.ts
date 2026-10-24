import { DatePipe } from '@angular/common';
import { ChangeDetectorRef, Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { A11yModule } from '@angular/cdk/a11y';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { AdminProjectOut, AdminService, UserOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** Super Admin console → Projects (US-1.1.2). */
@Component({
  selector: 'forge-admin-projects',
  imports: [
    A11yModule,
    DatePipe,
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Projects</h1>
      <span class="spacer"></span>
      <button nz-button nzType="primary" (click)="openCreate()" data-testid="new-project">
        <span nz-icon nzType="plus"></span><span i18n>New project</span>
      </button>
    </header>

    @if (notice(); as n) {
      <nz-alert [nzType]="n.severity" [nzMessage]="n.text" nzShowIcon data-testid="notice" />
    }

    <nz-table [nzData]="projects()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Description</th>
          <th i18n>Members</th>
          <th i18n>Status</th>
          <th i18n>Created</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (p of projects(); track p.id) {
          <tr [attr.data-project]="p.name">
            <td>{{ p.name }}</td>
            <td>{{ p.description }}</td>
            <td>{{ p.member_count }}</td>
            <td>
              <nz-tag [nzColor]="p.status === 'active' ? 'success' : 'default'">{{
                p.status
              }}</nz-tag>
            </td>
            <td>{{ p.created_at | date: 'mediumDate' }}</td>
            <td class="actions">
              @if (p.status === 'active') {
                <button
                  nz-button
                  nzType="link"
                  nzSize="small"
                  (click)="setStatus(p, 'archive')"
                  i18n
                >
                  Archive
                </button>
              } @else {
                <button
                  nz-button
                  nzType="link"
                  nzSize="small"
                  (click)="setStatus(p, 'restore')"
                  i18n
                >
                  Restore
                </button>
              }
              <button nz-button nzType="link" nzDanger nzSize="small" (click)="openDelete(p)" i18n>
                Delete
              </button>
            </td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="createOpen"
      nzTitle="New project"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="480"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="create()">
          <label for="p-name" i18n>Name</label>
          <input
            nz-input
            id="p-name"
            name="name"
            cdkFocusInitial
            [(ngModel)]="draft.name"
            required
          />
          <label for="p-desc" i18n>Description</label>
          <textarea
            nz-input
            id="p-desc"
            name="description"
            rows="3"
            [(ngModel)]="draft.description"
          ></textarea>
          <label for="p-admin" i18n>Project Admin</label>
          <nz-select
            nzId="p-admin"
            name="admin"
            [nzShowSearch]="true"
            [(ngModel)]="draft.admin_user_id"
            nzPlaceHolder="Select a user"
            i18n-nzPlaceHolder
          >
            @for (u of users(); track u.id) {
              <nz-option [nzValue]="u.id" [nzLabel]="u.username + ' · ' + u.email" />
            }
          </nz-select>
          @if (dialogError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" />
          }
          <div class="dialog-actions">
            <button nz-button nzType="primary" type="submit" data-testid="create-project" i18n>
              Create
            </button>
          </div>
        </form>
      </ng-container>
    </nz-modal>

    <nz-modal
      [(nzVisible)]="deleteOpen"
      nzTitle="Delete project"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="480"
    >
      <ng-container *nzModalContent>
        @if (toDelete(); as p) {
          <form class="form" (ngSubmit)="remove(p)">
            <nz-alert
              nzType="warning"
              nzMessage="The project, its data and operations will be deleted. Members lose access immediately."
              i18n-nzMessage
            />
            <label for="d-reason" i18n>Reason</label>
            <textarea
              nz-input
              id="d-reason"
              name="reason"
              rows="2"
              cdkFocusInitial
              [(ngModel)]="reason"
            ></textarea>
            <label for="d-confirm" i18n>Type "{{ p.name }}" to confirm</label>
            <input nz-input id="d-confirm" name="confirm" [(ngModel)]="confirmName" />
            @if (dialogError(); as e) {
              <nz-alert nzType="error" [nzMessage]="e" />
            }
            <div class="dialog-actions">
              <button
                nz-button
                nzType="primary"
                nzDanger
                type="submit"
                [disabled]="confirmName !== p.name || reason.length < 5"
                i18n
              >
                Delete
              </button>
            </div>
          </form>
        }
      </ng-container>
    </nz-modal>
  `,
  styleUrl: './admin.scss',
})
export class AdminProjects implements OnInit {
  private readonly cdr = inject(ChangeDetectorRef);
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
    this.cdr.markForCheck(); // zoneless: a plain field set after an `await` schedules no check
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
