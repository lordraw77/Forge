import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CheckboxModule } from 'primeng/checkbox';
import { DialogModule } from 'primeng/dialog';
import { IconFieldModule } from 'primeng/iconfield';
import { InputIconModule } from 'primeng/inputicon';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { firstValueFrom } from 'rxjs';

import { AdminService, UserOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** Super Admin console → Users (Part 2, Role hierarchy). */
@Component({
  selector: 'forge-admin-users',
  imports: [
    DatePipe,
    FormsModule,
    ButtonModule,
    CheckboxModule,
    DialogModule,
    IconFieldModule,
    InputIconModule,
    InputTextModule,
    MessageModule,
    TableModule,
    TagModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Users</h1>
      <p-iconfield>
        <p-inputicon styleClass="pi pi-search" />
        <input
          pInputText
          placeholder="Search"
          i18n-placeholder
          [(ngModel)]="query"
          (ngModelChange)="load()"
        />
      </p-iconfield>
      <p-button
        label="New user"
        i18n-label
        icon="pi pi-plus"
        (onClick)="openCreate()"
        data-testid="new-user"
      />
    </header>

    @if (notice(); as n) {
      <p-message [severity]="n.severity" [text]="n.text" data-testid="notice" />
    }

    <p-table [value]="users()" [rowHover]="true" dataKey="id" styleClass="p-datatable-sm">
      <ng-template #header>
        <tr>
          <th i18n>Username</th>
          <th i18n>Full name</th>
          <th i18n>Email</th>
          <th i18n>Status</th>
          <th i18n>Last active</th>
          <th></th>
        </tr>
      </ng-template>
      <ng-template #body let-u>
        <tr [attr.data-user]="u.username">
          <td>
            {{ u.username }}
            @if (u.is_super_admin) {
              <p-tag value="Super Admin" severity="warn" />
            }
          </td>
          <td>{{ u.full_name }}</td>
          <td>{{ u.email }}</td>
          <td>
            @if (isLocked(u)) {
              <p-tag value="Locked" i18n-value severity="danger" />
            } @else {
              <p-tag
                [value]="u.status"
                [severity]="u.status === 'active' ? 'success' : 'secondary'"
              />
            }
          </td>
          <td>{{ u.last_active_at | date: 'short' }}</td>
          <td class="actions">
            @if (isLocked(u)) {
              <p-button
                size="small"
                [text]="true"
                label="Unlock"
                i18n-label
                (onClick)="unlock(u)"
              />
            }
            <p-button
              size="small"
              [text]="true"
              label="Reset password"
              i18n-label
              (onClick)="reset(u)"
            />
            @if (u.id !== session.user()?.id) {
              <p-button
                size="small"
                [text]="true"
                [label]="u.status === 'active' ? disableLabel : enableLabel"
                (onClick)="toggle(u)"
              />
            }
          </td>
        </tr>
      </ng-template>
    </p-table>

    <p-dialog
      header="New user"
      i18n-header
      [modal]="true"
      [(visible)]="createOpen"
      [style]="{ width: '28rem' }"
    >
      <form class="form" (ngSubmit)="create()">
        <label for="u-username" i18n>Username</label>
        <input pInputText id="u-username" name="username" [(ngModel)]="draft.username" required />
        <label for="u-email" i18n>Email</label>
        <input
          pInputText
          id="u-email"
          name="email"
          type="email"
          [(ngModel)]="draft.email"
          required
        />
        <label for="u-name" i18n>Full name</label>
        <input pInputText id="u-name" name="full_name" [(ngModel)]="draft.full_name" />
        <div class="check">
          <p-checkbox
            inputId="u-super"
            name="super"
            [binary]="true"
            [(ngModel)]="draft.is_super_admin"
          />
          <label for="u-super" i18n>Super Admin</label>
        </div>
        <small i18n
          >A temporary password is generated; the user must change it at first sign-in.</small
        >
        @if (dialogError(); as e) {
          <p-message severity="error" [text]="e" />
        }
        <div class="dialog-actions">
          <p-button type="submit" label="Create" i18n-label data-testid="create-user" />
        </div>
      </form>
    </p-dialog>
  `,
  styleUrl: './admin.scss',
})
export class AdminUsers implements OnInit {
  private readonly admin = inject(AdminService);
  protected readonly session = inject(Session);
  protected readonly users = signal<UserOut[]>([]);
  protected readonly notice = signal<{ severity: 'success' | 'error'; text: string } | null>(null);
  protected readonly dialogError = signal<string | null>(null);
  protected query = '';
  protected createOpen = false;
  protected draft = { username: '', email: '', full_name: '', is_super_admin: false };
  protected readonly disableLabel = $localize`Disable`;
  protected readonly enableLabel = $localize`Enable`;

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  async load(): Promise<void> {
    this.users.set(await firstValueFrom(this.admin.listUsers({ q: this.query || undefined })));
  }

  protected isLocked(u: UserOut): boolean {
    return !!u.locked_until && new Date(u.locked_until) > new Date();
  }

  protected openCreate(): void {
    this.draft = { username: '', email: '', full_name: '', is_super_admin: false };
    this.dialogError.set(null);
    this.createOpen = true;
  }

  protected async create(): Promise<void> {
    try {
      const res = await firstValueFrom(this.admin.createUser({ userCreateIn: { ...this.draft } }));
      this.createOpen = false;
      this.notice.set({
        severity: 'success',
        text: $localize`User ${res.user.username}:name: created. Temporary password (shown once): ${res.temporary_password}:password:`,
      });
      await this.load();
    } catch (e) {
      this.dialogError.set(problemMessage(e));
    }
  }

  protected async unlock(u: UserOut): Promise<void> {
    await this.act(
      () => firstValueFrom(this.admin.unlockUser({ userId: u.id })),
      $localize`${u.username}:name: unlocked`,
    );
  }

  protected async reset(u: UserOut): Promise<void> {
    try {
      const res = await firstValueFrom(this.admin.resetPassword({ userId: u.id }));
      this.notice.set({
        severity: 'success',
        text: $localize`Temporary password for ${u.username}:name: (shown once): ${res.temporary_password}:password:`,
      });
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }

  protected async toggle(u: UserOut): Promise<void> {
    const status = u.status === 'active' ? 'disabled' : 'active';
    await this.act(
      () => firstValueFrom(this.admin.updateUser({ userId: u.id, userUpdateIn: { status } })),
      $localize`${u.username}:name: updated`,
    );
  }

  private async act(action: () => Promise<unknown>, success: string): Promise<void> {
    try {
      await action();
      this.notice.set({ severity: 'success', text: success });
      await this.load();
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }
}
