import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { A11yModule } from '@angular/cdk/a11y';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
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
    A11yModule,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Users</h1>
      <nz-input-wrapper class="search">
        <span nzInputPrefix nz-icon nzType="search"></span>
        <input
          nz-input
          placeholder="Search"
          i18n-placeholder
          [(ngModel)]="query"
          (ngModelChange)="load()"
        />
      </nz-input-wrapper>
      <button nz-button nzType="primary" (click)="openCreate()" data-testid="new-user">
        <span nz-icon nzType="plus"></span><span i18n>New user</span>
      </button>
    </header>

    @if (notice(); as n) {
      <nz-alert [nzType]="n.severity" [nzMessage]="n.text" nzShowIcon data-testid="notice" />
    }

    <nz-table [nzData]="users()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
      <thead>
        <tr>
          <th i18n>Username</th>
          <th i18n>Full name</th>
          <th i18n>Email</th>
          <th i18n>Status</th>
          <th i18n>Last active</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (u of users(); track u.id) {
          <tr [attr.data-user]="u.username">
            <td>
              {{ u.username }}
              @if (u.is_super_admin) {
                <nz-tag nzColor="warning">Super Admin</nz-tag>
              }
            </td>
            <td>{{ u.full_name }}</td>
            <td>{{ u.email }}</td>
            <td>
              @if (isLocked(u)) {
                <nz-tag nzColor="error" i18n>Locked</nz-tag>
              } @else {
                <nz-tag [nzColor]="u.status === 'active' ? 'success' : 'default'">{{
                  u.status
                }}</nz-tag>
              }
            </td>
            <td>{{ u.last_active_at | date: 'short' }}</td>
            <td class="actions">
              @if (isLocked(u)) {
                <button nz-button nzType="link" nzSize="small" (click)="unlock(u)" i18n>
                  Unlock
                </button>
              }
              <button nz-button nzType="link" nzSize="small" (click)="reset(u)" i18n>
                Reset password
              </button>
              @if (u.id !== session.user()?.id) {
                <button nz-button nzType="link" nzSize="small" (click)="toggle(u)">
                  {{ u.status === 'active' ? disableLabel : enableLabel }}
                </button>
              }
            </td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="createOpen"
      nzTitle="New user"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="448"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="create()">
          <label for="u-username" i18n>Username</label>
          <input
            nz-input
            id="u-username"
            name="username"
            cdkFocusInitial
            [(ngModel)]="draft.username"
            required
          />
          <label for="u-email" i18n>Email</label>
          <input
            nz-input
            id="u-email"
            name="email"
            type="email"
            [(ngModel)]="draft.email"
            required
          />
          <label for="u-name" i18n>Full name</label>
          <input nz-input id="u-name" name="full_name" [(ngModel)]="draft.full_name" />
          <label nz-checkbox nzId="u-super" name="super" [(ngModel)]="draft.is_super_admin">
            <span i18n>Super Admin</span>
          </label>
          <small i18n
            >A temporary password is generated; the user must change it at first sign-in.</small
          >
          @if (dialogError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" />
          }
          <div class="dialog-actions">
            <button nz-button nzType="primary" type="submit" data-testid="create-user" i18n>
              Create
            </button>
          </div>
        </form>
      </ng-container>
    </nz-modal>
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
