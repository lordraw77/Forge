import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { MultiSelectModule } from 'primeng/multiselect';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';
import { firstValueFrom } from 'rxjs';

import { ApiKeyOut, MemberOut, ProjectsService, RoleOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Notice = { severity: 'success' | 'error' | 'info'; text: string } | null;

/** Projects Studio shell: sub-pages; administration pages only for the Project Admin (Part 2). */
@Component({
  selector: 'forge-projects-studio',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, MessageModule],
  template: `
    <header class="head">
      <h1 i18n>Projects Studio</h1>
    </header>
    @if (session.project(); as p) {
      <nav class="tabs">
        <a routerLink="info" routerLinkActive="active" i18n>Project Info</a>
        @if (session.can('project.manage')) {
          <a routerLink="users" routerLinkActive="active" i18n>Users</a>
          <a routerLink="roles" routerLinkActive="active" i18n>Roles</a>
        }
      </nav>
      <router-outlet />
    } @else {
      <p-message severity="info" i18n
        >You are not a member of any project yet. Ask a Super Admin to add you.</p-message
      >
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class ProjectsStudio {
  protected readonly session = inject(Session);
}

/** US-1.1.4: members list, invite (up to 500), change role, remove. */
@Component({
  selector: 'forge-project-users',
  imports: [
    DatePipe,
    FormsModule,
    ButtonModule,
    DialogModule,
    MessageModule,
    SelectModule,
    TableModule,
    TextareaModule,
  ],
  template: `
    <div class="head">
      <input
        class="search"
        placeholder="Search by name"
        i18n-placeholder
        [(ngModel)]="query"
        (ngModelChange)="load()"
      />
      <span class="spacer"></span>
      <p-button
        label="Invite users"
        i18n-label
        icon="pi pi-user-plus"
        (onClick)="inviteOpen = true"
        data-testid="invite"
      />
    </div>
    @if (notice(); as n) {
      <p-message [severity]="n.severity" [text]="n.text" data-testid="notice" />
    }
    <p-table [value]="members()" dataKey="user_id" styleClass="p-datatable-sm">
      <ng-template #header>
        <tr>
          <th i18n>Full Name</th>
          <th i18n>Username</th>
          <th i18n>Email</th>
          <th i18n>Role Name</th>
          <th i18n>Last Active</th>
          <th></th>
        </tr>
      </ng-template>
      <ng-template #body let-m>
        <tr [attr.data-member]="m.username">
          <td>{{ m.full_name }}</td>
          <td>{{ m.username }}</td>
          <td>{{ m.email }}</td>
          <td>
            <p-select
              [options]="roles()"
              optionLabel="name"
              optionValue="id"
              [ngModel]="m.role_name_id"
              (ngModelChange)="changeRole(m, $event)"
              appendTo="body"
            />
          </td>
          <td>{{ m.last_active_at | date: 'short' }}</td>
          <td class="actions">
            <p-button
              size="small"
              [text]="true"
              severity="danger"
              label="Remove"
              i18n-label
              (onClick)="remove(m)"
            />
          </td>
        </tr>
      </ng-template>
    </p-table>

    <p-dialog
      header="Invite users"
      i18n-header
      [modal]="true"
      [(visible)]="inviteOpen"
      [style]="{ width: '30rem' }"
    >
      <form class="form" (ngSubmit)="invite()">
        <label for="inv-ids" i18n>Usernames or emails (one per line, up to 500)</label>
        <textarea pTextarea id="inv-ids" name="ids" rows="5" [(ngModel)]="identifiers"></textarea>
        <label for="inv-role" i18n>Role Name</label>
        <p-select
          inputId="inv-role"
          name="role"
          [options]="roles()"
          optionLabel="name"
          optionValue="id"
          [(ngModel)]="inviteRole"
          appendTo="body"
        />
        <div class="dialog-actions">
          <p-button type="submit" label="Invite" i18n-label data-testid="send-invite" />
        </div>
      </form>
    </p-dialog>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ProjectUsers {
  private readonly api = inject(ProjectsService);
  private readonly session = inject(Session);
  protected readonly members = signal<MemberOut[]>([]);
  protected readonly roles = signal<RoleOut[]>([]);
  protected readonly notice = signal<Notice>(null);
  protected query = '';
  protected inviteOpen = false;
  protected identifiers = '';
  protected inviteRole = '';
  private readonly projectId = computed(() => this.session.project()?.id ?? null);

  constructor() {
    effect(() => {
      const id = this.projectId();
      if (id) void this.reload(id);
    });
  }

  private async reload(projectId: string): Promise<void> {
    const roles = await firstValueFrom(this.api.listRoles({ projectId }));
    this.roles.set(roles.filter((r) => r.status === 'active'));
    this.inviteRole = roles.find((r) => r.role_type === 'end_user')?.id ?? roles[0]?.id ?? '';
    await this.load();
  }

  async load(): Promise<void> {
    const projectId = this.projectId();
    if (projectId) {
      this.members.set(
        await firstValueFrom(this.api.listMembers({ projectId, q: this.query || undefined })),
      );
    }
  }

  protected async invite(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const ids = this.identifiers
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      const results = await firstValueFrom(
        this.api.inviteMembers({
          projectId,
          inviteIn: { identifiers: ids, role_name_id: this.inviteRole },
        }),
      );
      const added = results.filter((r) => r.status === 'added').length;
      const unknown = results.filter((r) => r.status === 'unknown_user').map((r) => r.identifier);
      this.inviteOpen = false;
      this.identifiers = '';
      this.notice.set({
        severity: unknown.length ? 'info' : 'success',
        text:
          $localize`${added}:count: added.` +
          (unknown.length ? ' ' + $localize`Unknown users: ${unknown.join(', ')}:users:` : ''),
      });
      await this.load();
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }

  protected async changeRole(m: MemberOut, roleNameId: string): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.api.updateMember({
          projectId,
          userId: m.user_id,
          memberUpdateIn: { role_name_id: roleNameId },
        }),
      );
      this.notice.set({
        severity: 'success',
        text: $localize`Role updated for ${m.username}:name:`,
      });
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
    await this.load();
  }

  protected async remove(m: MemberOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(this.api.removeMember({ projectId, userId: m.user_id }));
      await this.load();
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }
}

/** Part 2, Roles and access: Role Name bound to a Role Type, status. */
@Component({
  selector: 'forge-project-roles',
  imports: [
    FormsModule,
    ButtonModule,
    InputTextModule,
    MessageModule,
    SelectModule,
    TableModule,
    TagModule,
  ],
  template: `
    @if (notice(); as n) {
      <p-message [severity]="n.severity" [text]="n.text" />
    }
    <form class="head" (ngSubmit)="create()">
      <input
        pInputText
        name="name"
        placeholder="New role name"
        i18n-placeholder
        [(ngModel)]="name"
      />
      <p-select
        name="type"
        [options]="types"
        optionLabel="label"
        optionValue="key"
        [(ngModel)]="type"
      />
      <p-button type="submit" label="Add role" i18n-label icon="pi pi-plus" />
    </form>
    <p-table [value]="roles()" dataKey="id" styleClass="p-datatable-sm">
      <ng-template #header>
        <tr>
          <th i18n>Role Name</th>
          <th i18n>Role Type</th>
          <th i18n>Status</th>
          <th></th>
        </tr>
      </ng-template>
      <ng-template #body let-r>
        <tr>
          <td>{{ r.name }}</td>
          <td>{{ typeLabel(r.role_type) }}</td>
          <td>
            <p-tag
              [value]="r.status"
              [severity]="r.status === 'active' ? 'success' : 'secondary'"
            />
          </td>
          <td class="actions">
            <p-button
              size="small"
              [text]="true"
              [label]="r.status === 'active' ? deactivate : activate"
              (onClick)="toggle(r)"
            />
          </td>
        </tr>
      </ng-template>
    </p-table>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ProjectRoles {
  private readonly api = inject(ProjectsService);
  private readonly session = inject(Session);
  protected readonly roles = signal<RoleOut[]>([]);
  protected readonly notice = signal<Notice>(null);
  protected name = '';
  protected type: 'project_admin' | 'builder' | 'operator' | 'end_user' | 'viewer' = 'end_user';
  protected readonly deactivate = $localize`Deactivate`;
  protected readonly activate = $localize`Activate`;
  protected readonly types = [
    { key: 'project_admin', label: $localize`Project Admin` },
    { key: 'builder', label: $localize`Builder` },
    { key: 'operator', label: $localize`Operator` },
    { key: 'end_user', label: $localize`User` },
    { key: 'viewer', label: $localize`Viewer` },
  ];

  constructor() {
    effect(() => {
      if (this.session.project()) void this.load();
    });
  }

  protected typeLabel(key: string): string {
    return this.types.find((t) => t.key === key)?.label ?? key;
  }

  private async load(): Promise<void> {
    const projectId = this.session.project()?.id;
    if (projectId) this.roles.set(await firstValueFrom(this.api.listRoles({ projectId })));
  }

  protected async create(): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.api.createRole({ projectId, roleCreateIn: { name: this.name, role_type: this.type } }),
      );
      this.name = '';
      await this.load();
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }

  protected async toggle(r: RoleOut): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    const status = r.status === 'active' ? 'inactive' : 'active';
    await firstValueFrom(
      this.api.updateRole({ projectId, roleId: r.id, roleUpdateIn: { status } }),
    );
    await this.load();
  }
}

/** Project Info: details and API tokens (US-1.1.2/2). */
@Component({
  selector: 'forge-project-info',
  imports: [
    DatePipe,
    FormsModule,
    ButtonModule,
    InputTextModule,
    MessageModule,
    MultiSelectModule,
    TableModule,
    TagModule,
  ],
  template: `
    @if (session.project(); as p) {
      <section class="info">
        <h2>{{ p.name }}</h2>
        <p>{{ p.description }}</p>
        <p>
          <span i18n>Your role:</span> <strong>{{ p.role_name ?? p.role_type }}</strong>
        </p>
      </section>
      @if (session.can('project.manage')) {
        <h3 i18n>API tokens</h3>
        @if (created(); as token) {
          <p-message severity="success" data-testid="new-token">
            <span i18n>Copy this token now; it will not be shown again:</span>&nbsp;<code>{{
              token
            }}</code>
          </p-message>
        }
        @if (error(); as e) {
          <p-message severity="error" [text]="e" />
        }
        <form class="head" (ngSubmit)="create()">
          <input
            pInputText
            name="name"
            placeholder="Token name"
            i18n-placeholder
            [(ngModel)]="name"
          />
          <p-multiselect
            name="scopes"
            [options]="p.permissions"
            [(ngModel)]="scopes"
            placeholder="Scopes"
            i18n-placeholder
          />
          <p-button type="submit" label="Create token" i18n-label icon="pi pi-key" />
        </form>
        <p-table [value]="keys()" dataKey="id" styleClass="p-datatable-sm">
          <ng-template #header>
            <tr>
              <th i18n>Name</th>
              <th i18n>Prefix</th>
              <th i18n>Scopes</th>
              <th i18n>Last used</th>
              <th i18n>Status</th>
              <th></th>
            </tr>
          </ng-template>
          <ng-template #body let-k>
            <tr>
              <td>{{ k.name }}</td>
              <td>
                <code>fk_{{ k.prefix }}_…</code>
              </td>
              <td>{{ k.scopes.join(', ') }}</td>
              <td>{{ k.last_used_at | date: 'short' }}</td>
              <td>
                @if (k.revoked_at) {
                  <p-tag value="Revoked" i18n-value severity="secondary" />
                } @else {
                  <p-tag value="Active" i18n-value severity="success" />
                }
              </td>
              <td class="actions">
                @if (!k.revoked_at) {
                  <p-button
                    size="small"
                    [text]="true"
                    severity="danger"
                    label="Revoke"
                    i18n-label
                    (onClick)="revoke(k)"
                  />
                }
              </td>
            </tr>
          </ng-template>
        </p-table>
      }
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class ProjectInfo {
  private readonly api = inject(ProjectsService);
  protected readonly session = inject(Session);
  protected readonly keys = signal<ApiKeyOut[]>([]);
  protected readonly created = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);
  protected name = '';
  protected scopes: string[] = ['usecase.run', 'run.read'];

  constructor() {
    effect(() => {
      if (this.session.project() && this.session.can('project.manage')) void this.load();
    });
  }

  private async load(): Promise<void> {
    const projectId = this.session.project()?.id;
    if (projectId) this.keys.set(await firstValueFrom(this.api.listKeys({ projectId })));
  }

  protected async create(): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    try {
      const res = await firstValueFrom(
        this.api.createKey({
          projectId,
          apiKeyCreateIn: { name: this.name || 'API key', scopes: this.scopes },
        }),
      );
      this.created.set(res.token);
      this.error.set(null);
      this.name = '';
      await this.load();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async revoke(k: ApiKeyOut): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    await firstValueFrom(this.api.revokeKey({ projectId, keyId: k.id }));
    await this.load();
  }
}
