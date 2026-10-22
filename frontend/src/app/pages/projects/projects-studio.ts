import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
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

import { ApiKeyOut, GroupOut, MemberOut, ProjectsService, RoleOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Notice = { severity: 'success' | 'error' | 'info'; text: string } | null;

/** Projects Studio shell: sub-pages; administration pages only for the Project Admin (Part 2). */
@Component({
  selector: 'forge-projects-studio',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NzAlertModule],
  template: `
    <header class="head">
      <h1 i18n>Projects Studio</h1>
    </header>
    @if (session.project(); as p) {
      <nav class="tabs">
        <a routerLink="info" routerLinkActive="active" i18n>Project Info</a>
        <a routerLink="models" routerLinkActive="active" i18n>Models</a>
        @if (session.can('asset.write')) {
          <a routerLink="data" routerLinkActive="active" data-testid="tab-data" i18n>Data</a>
          <a routerLink="sources" routerLinkActive="active" data-testid="tab-sources" i18n
            >Sources</a
          >
          <a routerLink="sync" routerLinkActive="active" data-testid="tab-sync" i18n>Sync</a>
          <a routerLink="transfer" routerLinkActive="active" data-testid="tab-transfer" i18n
            >Export / Import</a
          >
        }
        @if (session.can('project.manage')) {
          <a routerLink="users" routerLinkActive="active" i18n>Users</a>
          <a routerLink="groups" routerLinkActive="active" data-testid="tab-groups" i18n>Groups</a>
          <a routerLink="roles" routerLinkActive="active" i18n>Roles</a>
          <a routerLink="catalogs" routerLinkActive="active" data-testid="tab-catalogs" i18n
            >Catalogs</a
          >
        }
      </nav>
      <router-outlet />
    } @else {
      <nz-alert
        nzType="info"
        nzMessage="You are not a member of any project yet. Ask a Super Admin to add you."
        i18n-nzMessage
      />
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
  ],
  template: `
    <div class="head">
      <input
        nz-input
        class="search"
        placeholder="Search by name"
        i18n-placeholder
        [(ngModel)]="query"
        (ngModelChange)="load()"
      />
      <span class="spacer"></span>
      <button nz-button nzType="primary" (click)="inviteOpen = true" data-testid="invite">
        <span nz-icon nzType="user-add"></span><span i18n>Invite users</span>
      </button>
    </div>
    @if (notice(); as n) {
      <nz-alert [nzType]="n.severity" [nzMessage]="n.text" nzShowIcon data-testid="notice" />
    }
    <nz-table [nzData]="members()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
      <thead>
        <tr>
          <th i18n>Full Name</th>
          <th i18n>Username</th>
          <th i18n>Email</th>
          <th i18n>Role Name</th>
          <th i18n>Groups</th>
          <th i18n>Last Active</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (m of members(); track m.user_id) {
          <tr [attr.data-member]="m.username">
            <td>{{ m.full_name }}</td>
            <td>{{ m.username }}</td>
            <td>{{ m.email }}</td>
            <td>
              <nz-select
                class="role"
                [ngModel]="m.role_name_id"
                (ngModelChange)="changeRole(m, $event)"
              >
                @for (r of roles(); track r.id) {
                  <nz-option [nzValue]="r.id" [nzLabel]="r.name" />
                }
              </nz-select>
            </td>
            <td>
              <nz-select
                class="groups"
                nzMode="multiple"
                nzPlaceHolder="No groups"
                i18n-nzPlaceHolder
                [attr.aria-label]="groupsLabel"
                [attr.data-testid]="'groups-' + m.username"
                [ngModel]="m.group_ids"
                (ngModelChange)="changeGroups(m, $event)"
              >
                @for (g of groups(); track g.id) {
                  <nz-option [nzValue]="g.id" [nzLabel]="g.name" />
                }
              </nz-select>
            </td>
            <td>{{ m.last_active_at | date: 'short' }}</td>
            <td class="actions">
              <button nz-button nzType="link" nzDanger nzSize="small" (click)="remove(m)" i18n>
                Remove
              </button>
            </td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="inviteOpen"
      nzTitle="Invite users"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="480"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="invite()">
          <label for="inv-ids" i18n>Usernames or emails (one per line, up to 500)</label>
          <textarea
            nz-input
            id="inv-ids"
            name="ids"
            rows="5"
            cdkFocusInitial
            [(ngModel)]="identifiers"
          ></textarea>
          <label for="inv-role" i18n>Role Name</label>
          <nz-select nzId="inv-role" name="role" [(ngModel)]="inviteRole">
            @for (r of roles(); track r.id) {
              <nz-option [nzValue]="r.id" [nzLabel]="r.name" />
            }
          </nz-select>
          <div class="dialog-actions">
            <button nz-button nzType="primary" type="submit" data-testid="send-invite" i18n>
              Invite
            </button>
          </div>
        </form>
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ProjectUsers {
  private readonly api = inject(ProjectsService);
  private readonly session = inject(Session);
  protected readonly members = signal<MemberOut[]>([]);
  protected readonly roles = signal<RoleOut[]>([]);
  protected readonly notice = signal<Notice>(null);
  protected readonly groupsLabel = $localize`Groups`;
  protected readonly groups = signal<GroupOut[]>([]);
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
    this.groups.set(await firstValueFrom(this.api.listGroups({ projectId })));
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

  protected async changeGroups(m: MemberOut, groups: string[]): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.api.updateMember({
          projectId,
          userId: m.user_id,
          memberUpdateIn: { group_ids: groups },
        }),
      );
      this.notice.set({
        severity: 'success',
        text: $localize`Groups updated for ${m.username}:name:`,
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
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (notice(); as n) {
      <nz-alert [nzType]="n.severity" [nzMessage]="n.text" />
    }
    <form class="head" (ngSubmit)="create()">
      <input
        nz-input
        class="search"
        name="name"
        placeholder="New role name"
        i18n-placeholder
        [(ngModel)]="name"
      />
      <nz-select class="range" name="type" [(ngModel)]="type">
        @for (t of types; track t.key) {
          <nz-option [nzValue]="t.key" [nzLabel]="t.label" />
        }
      </nz-select>
      <button nz-button nzType="primary" type="submit">
        <span nz-icon nzType="plus"></span><span i18n>Add role</span>
      </button>
    </form>
    <nz-table [nzData]="roles()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
      <thead>
        <tr>
          <th i18n>Role Name</th>
          <th i18n>Role Type</th>
          <th i18n>Status</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (r of roles(); track r.id) {
          <tr>
            <td>{{ r.name }}</td>
            <td>{{ typeLabel(r.role_type) }}</td>
            <td>
              <nz-tag [nzColor]="r.status === 'active' ? 'success' : 'default'">{{
                r.status
              }}</nz-tag>
            </td>
            <td class="actions">
              <button nz-button nzType="link" nzSize="small" (click)="toggle(r)">
                {{ r.status === 'active' ? deactivate : activate }}
              </button>
            </td>
          </tr>
        }
      </tbody>
    </nz-table>
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
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
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
          <nz-alert nzType="success" [nzMessage]="tokenMessage" data-testid="new-token" />
          <ng-template #tokenMessage>
            <span i18n>Copy this token now; it will not be shown again:</span>&nbsp;<code>{{
              token
            }}</code>
          </ng-template>
        }
        @if (error(); as e) {
          <nz-alert nzType="error" [nzMessage]="e" />
        }
        <form class="head" (ngSubmit)="create()">
          <input
            nz-input
            class="search"
            name="name"
            placeholder="Token name"
            i18n-placeholder
            [(ngModel)]="name"
          />
          <nz-select
            class="scopes"
            name="scopes"
            nzMode="multiple"
            [(ngModel)]="scopes"
            nzPlaceHolder="Scopes"
            i18n-nzPlaceHolder
          >
            @for (perm of p.permissions; track perm) {
              <nz-option [nzValue]="perm" [nzLabel]="perm" />
            }
          </nz-select>
          <button nz-button nzType="primary" type="submit">
            <span nz-icon nzType="key"></span><span i18n>Create token</span>
          </button>
        </form>
        <nz-table [nzData]="keys()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
          <thead>
            <tr>
              <th i18n>Name</th>
              <th i18n>Prefix</th>
              <th i18n>Scopes</th>
              <th i18n>Last used</th>
              <th i18n>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (k of keys(); track k.id) {
              <tr>
                <td>{{ k.name }}</td>
                <td>
                  <code>fk_{{ k.prefix }}_…</code>
                </td>
                <td>{{ k.scopes.join(', ') }}</td>
                <td>{{ k.last_used_at | date: 'short' }}</td>
                <td>
                  @if (k.revoked_at) {
                    <nz-tag i18n>Revoked</nz-tag>
                  } @else {
                    <nz-tag nzColor="success" i18n>Active</nz-tag>
                  }
                </td>
                <td class="actions">
                  @if (!k.revoked_at) {
                    <button
                      nz-button
                      nzType="link"
                      nzDanger
                      nzSize="small"
                      (click)="revoke(k)"
                      i18n
                    >
                      Revoke
                    </button>
                  }
                </td>
              </tr>
            }
          </tbody>
        </nz-table>
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
