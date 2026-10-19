import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  AdminProjectOut,
  AdminService,
  IdentityService,
  IdpMappingOut,
  IdpOut,
  IdpTestUserOut,
  ProjectsService,
  RoleOut,
} from '../../api-client';
import { problemMessage } from '../../core/http';

type Kind = 'ldap' | 'oidc';

interface Draft {
  id: string | null;
  kind: Kind;
  name: string;
  secret: string;
  enabled: boolean;
  // LDAP
  url: string;
  start_tls: boolean;
  allow_insecure: boolean;
  ca_pem: string;
  bind_dn: string;
  user_base_dn: string;
  user_filter: string;
  username_attr: string;
  email_attr: string;
  name_attr: string;
  active_directory: boolean;
  group_base_dn: string;
  group_filter: string;
  sync_interval_min: number;
  // OIDC
  issuer: string;
  client_id: string;
  scopes: string;
  username_claim: string;
  email_claim: string;
  name_claim: string;
  groups_claim: string;
  button_label: string;
}

const LDAP_KEYS = [
  'url',
  'start_tls',
  'allow_insecure',
  'ca_pem',
  'bind_dn',
  'user_base_dn',
  'user_filter',
  'username_attr',
  'email_attr',
  'name_attr',
  'active_directory',
  'group_base_dn',
  'group_filter',
  'sync_interval_min',
] as const;
const OIDC_KEYS = [
  'issuer',
  'client_id',
  'scopes',
  'username_claim',
  'email_claim',
  'name_claim',
  'groups_claim',
  'button_label',
  'allow_insecure',
] as const;

function blank(kind: Kind): Draft {
  return {
    id: null,
    kind,
    name: '',
    secret: '',
    enabled: false,
    url: '',
    start_tls: false,
    allow_insecure: false,
    ca_pem: '',
    bind_dn: '',
    user_base_dn: '',
    user_filter: '(|(uid={login})(sAMAccountName={login})(mail={login}))',
    username_attr: 'uid',
    email_attr: 'mail',
    name_attr: 'cn',
    active_directory: false,
    group_base_dn: '',
    group_filter: '(member={dn})',
    sync_interval_min: 60,
    issuer: '',
    client_id: '',
    scopes: 'openid email profile',
    username_claim: 'preferred_username',
    email_claim: 'email',
    name_claim: 'name',
    groups_claim: 'groups',
    button_label: '',
  };
}

/** Super Admin console → Authentication: LDAP/AD and OIDC providers, group mappings (E2.1). */
@Component({
  selector: 'forge-admin-auth',
  imports: [
    DatePipe,
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzIconModule,
    NzInputModule,
    NzInputNumberModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Authentication</h1>
      <button nz-button (click)="openNew('oidc')" data-testid="new-oidc">
        <span nz-icon nzType="plus"></span><span i18n>OIDC provider</span>
      </button>
      <button nz-button nzType="primary" (click)="openNew('ldap')" data-testid="new-ldap">
        <span nz-icon nzType="plus"></span><span i18n>LDAP / Active Directory</span>
      </button>
    </header>

    @if (notice(); as n) {
      <nz-alert [nzType]="n.severity" [nzMessage]="n.text" nzShowIcon data-testid="notice" />
    }

    <label
      nz-checkbox
      nzId="local-login"
      [ngModel]="localLogin()"
      (ngModelChange)="setLocalLogin($event)"
    >
      <span i18n>Allow local login (Super Admins can always sign in locally)</span>
    </label>

    <nz-table [nzData]="providers()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Type</th>
          <th i18n>Status</th>
          <th i18n>Users</th>
          <th i18n>Last sync</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (p of providers(); track p.id) {
          <tr [attr.data-provider]="p.name">
            <td>{{ p.name }}</td>
            <td>{{ p.kind === 'ldap' ? 'LDAP / AD' : 'OIDC' }}</td>
            <td>
              <nz-tag [nzColor]="p.enabled ? 'success' : 'default'">
                {{ p.enabled ? enabledLabel : disabledLabel }}
              </nz-tag>
            </td>
            <td>{{ p.user_count }}</td>
            <td>{{ p.last_sync_at | date: 'short' }}</td>
            <td class="actions">
              <button nz-button nzType="link" nzSize="small" (click)="test(p)" i18n>Test</button>
              @if (p.kind === 'ldap') {
                <button nz-button nzType="link" nzSize="small" (click)="openTestUser(p)" i18n>
                  Test user
                </button>
                <button nz-button nzType="link" nzSize="small" (click)="sync(p)" i18n>
                  Sync now
                </button>
              }
              <button nz-button nzType="link" nzSize="small" (click)="openMappings(p)" i18n>
                Groups
              </button>
              <button nz-button nzType="link" nzSize="small" (click)="openEdit(p)" i18n>
                Edit
              </button>
              <button nz-button nzType="link" nzSize="small" (click)="toggle(p)">
                {{ p.enabled ? disableLabel : enableLabel }}
              </button>
              <button nz-button nzType="link" nzSize="small" nzDanger (click)="remove(p)" i18n>
                Delete
              </button>
            </td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="editOpen"
      [nzTitle]="draft.id ? editTitle : newTitle"
      [nzFooter]="null"
      [nzWidth]="640"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="save()">
          <label for="p-name" i18n>Name</label>
          <input nz-input id="p-name" name="name" [(ngModel)]="draft.name" required />

          @if (draft.kind === 'ldap') {
            <label for="p-url" i18n>Server URL (ldap:// or ldaps://)</label>
            <input nz-input id="p-url" name="url" [(ngModel)]="draft.url" required />
            <label nz-checkbox nzId="p-start_tls" name="start_tls" [(ngModel)]="draft.start_tls">
              <span i18n>Use StartTLS</span>
            </label>
            <label
              nz-checkbox
              nzId="p-allow-ldap"
              name="allow_insecure"
              [(ngModel)]="draft.allow_insecure"
            >
              <span i18n>Allow unencrypted connection (lab only)</span>
            </label>
            <label for="p-ca" i18n>CA certificate (PEM, optional)</label>
            <textarea
              nz-input
              id="p-ca"
              name="ca_pem"
              rows="3"
              [(ngModel)]="draft.ca_pem"
            ></textarea>
            <label for="p-bind" i18n>Bind DN</label>
            <input nz-input id="p-bind" name="bind_dn" [(ngModel)]="draft.bind_dn" required />
            <label for="p-secret" i18n>Bind password</label>
            <input
              nz-input
              id="p-secret"
              name="secret"
              type="password"
              autocomplete="new-password"
              [placeholder]="draft.id ? keepPlaceholder : ''"
              [(ngModel)]="draft.secret"
            />
            <label for="p-ubase" i18n>User base DN</label>
            <input
              nz-input
              id="p-ubase"
              name="user_base_dn"
              [(ngModel)]="draft.user_base_dn"
              required
            />
            <label for="p-ufilter" i18n>User filter ({{ '{login}' }} is the typed login)</label>
            <input nz-input id="p-ufilter" name="user_filter" [(ngModel)]="draft.user_filter" />
            <span class="label" i18n>Username, email and display name attributes</span>
            <div class="row">
              <input nz-input name="username_attr" [(ngModel)]="draft.username_attr" />
              <input nz-input name="email_attr" [(ngModel)]="draft.email_attr" />
              <input nz-input name="name_attr" [(ngModel)]="draft.name_attr" />
            </div>
            <label
              nz-checkbox
              nzId="p-active_directory"
              name="active_directory"
              [(ngModel)]="draft.active_directory"
            >
              <span i18n>Active Directory (honour disabled accounts)</span>
            </label>
            <label for="p-gbase" i18n>Group base DN (empty: read memberOf from the user)</label>
            <input nz-input id="p-gbase" name="group_base_dn" [(ngModel)]="draft.group_base_dn" />
            <label for="p-gfilter" i18n>Group filter ({{ '{dn}' }} is the user DN)</label>
            <input nz-input id="p-gfilter" name="group_filter" [(ngModel)]="draft.group_filter" />
            <label for="p-sync" i18n>Sync every (minutes, 0 = off)</label>
            <nz-input-number
              nzId="p-sync"
              name="sync_interval_min"
              [nzMin]="0"
              [(ngModel)]="draft.sync_interval_min"
            />
            <small i18n>LDAP does not use the outbound HTTP proxy of the platform.</small>
          } @else {
            <label for="p-issuer" i18n>Issuer URL</label>
            <input nz-input id="p-issuer" name="issuer" [(ngModel)]="draft.issuer" required />
            <label for="p-cid" i18n>Client ID</label>
            <input nz-input id="p-cid" name="client_id" [(ngModel)]="draft.client_id" required />
            <label for="p-csecret" i18n>Client secret</label>
            <input
              nz-input
              id="p-csecret"
              name="secret"
              type="password"
              autocomplete="new-password"
              [placeholder]="draft.id ? keepPlaceholder : ''"
              [(ngModel)]="draft.secret"
            />
            <label for="p-scopes" i18n>Scopes</label>
            <input nz-input id="p-scopes" name="scopes" [(ngModel)]="draft.scopes" />
            <span class="label" i18n>Claims: username, email, name, groups</span>
            <div class="row">
              <input nz-input name="username_claim" [(ngModel)]="draft.username_claim" />
              <input nz-input name="email_claim" [(ngModel)]="draft.email_claim" />
              <input nz-input name="name_claim" [(ngModel)]="draft.name_claim" />
              <input nz-input name="groups_claim" [(ngModel)]="draft.groups_claim" />
            </div>
            <label for="p-label" i18n>Button label</label>
            <input nz-input id="p-label" name="button_label" [(ngModel)]="draft.button_label" />
            <label
              nz-checkbox
              nzId="p-allow-oidc"
              name="allow_insecure"
              [(ngModel)]="draft.allow_insecure"
            >
              <span i18n>Allow http:// issuer (lab only)</span>
            </label>
            @if (redirectUri) {
              <small i18n>Redirect URI to register at the provider: {{ redirectUri }}</small>
            }
          }

          @if (error(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
          }
          <div class="buttons">
            <button nz-button type="button" (click)="editOpen = false" i18n>Cancel</button>
            <button nz-button nzType="primary" type="submit" [nzLoading]="busy()" i18n>Save</button>
          </div>
        </form>
      </ng-container>
    </nz-modal>

    <nz-modal
      [(nzVisible)]="testUserOpen"
      nzTitle="Test user"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="560"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="runTestUser()">
          <label for="tu-login" i18n>Login</label>
          <input nz-input id="tu-login" name="login" [(ngModel)]="testLogin" required />
          <button nz-button nzType="primary" type="submit" [nzLoading]="busy()" i18n>
            Look up
          </button>
        </form>
        @if (testResult(); as r) {
          @if (r.found) {
            <dl class="found" data-testid="test-user-result">
              <dt i18n>Username</dt>
              <dd>{{ r.username }}</dd>
              <dt i18n>Email</dt>
              <dd>{{ r.email }}</dd>
              <dt i18n>Name</dt>
              <dd>{{ r.full_name }}</dd>
              <dt i18n>Groups</dt>
              <dd>{{ (r.groups ?? []).join(', ') }}</dd>
            </dl>
          } @else {
            <nz-alert nzType="warning" nzMessage="User not found" i18n-nzMessage />
          }
        }
      </ng-container>
    </nz-modal>

    <nz-modal
      [(nzVisible)]="mapOpen"
      nzTitle="Group mappings"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="760"
    >
      <ng-container *nzModalContent>
        <nz-table
          [nzData]="mappings()"
          nzSize="small"
          [nzShowPagination]="false"
          [nzPageSize]="1000"
        >
          <thead>
            <tr>
              <th i18n>External group</th>
              <th i18n>Project</th>
              <th i18n>Role Name</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (m of mappings(); track m.id) {
              <tr>
                <td>{{ m.external_group }}</td>
                <td>{{ m.project_name }}</td>
                <td>{{ m.role_name }} ({{ m.role_type }})</td>
                <td class="actions">
                  <button
                    nz-button
                    nzType="link"
                    nzSize="small"
                    nzDanger
                    (click)="deleteMapping(m)"
                    i18n
                  >
                    Delete
                  </button>
                </td>
              </tr>
            }
          </tbody>
        </nz-table>
        <form class="row add" (ngSubmit)="addMapping()">
          <input
            nz-input
            name="group"
            placeholder="Group DN or name"
            i18n-placeholder
            [(ngModel)]="newGroup"
            required
          />
          <nz-select
            name="project"
            nzPlaceHolder="Project"
            i18n-nzPlaceHolder
            [ngModel]="newProject"
            (ngModelChange)="pickProject($event)"
          >
            @for (p of projects(); track p.id) {
              <nz-option [nzValue]="p.id" [nzLabel]="p.name" />
            }
          </nz-select>
          <nz-select name="role" nzPlaceHolder="Role Name" i18n-nzPlaceHolder [(ngModel)]="newRole">
            @for (r of roles(); track r.id) {
              <nz-option [nzValue]="r.id" [nzLabel]="r.name + ' (' + r.role_type + ')'" />
            }
          </nz-select>
          <button nz-button nzType="primary" type="submit" [disabled]="!newGroup || !newRole" i18n>
            Add
          </button>
        </form>
        @if (error(); as e) {
          <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
        }
      </ng-container>
    </nz-modal>
  `,
  styleUrl: './admin.scss',
})
export class AdminAuth implements OnInit {
  private readonly identity = inject(IdentityService);
  private readonly admin = inject(AdminService);
  private readonly projectsApi = inject(ProjectsService);

  protected readonly providers = signal<IdpOut[]>([]);
  protected readonly mappings = signal<IdpMappingOut[]>([]);
  protected readonly projects = signal<AdminProjectOut[]>([]);
  protected readonly roles = signal<RoleOut[]>([]);
  protected readonly localLogin = signal(true);
  protected readonly notice = signal<{ severity: 'success' | 'error'; text: string } | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly testResult = signal<IdpTestUserOut | null>(null);

  protected draft: Draft = blank('ldap');
  protected editOpen = false;
  protected testUserOpen = false;
  protected mapOpen = false;
  protected testLogin = '';
  protected newGroup = '';
  protected newProject: string | null = null;
  protected newRole: string | null = null;
  protected redirectUri: string | null = null;
  private current: IdpOut | null = null;

  protected readonly enabledLabel = $localize`enabled`;
  protected readonly disabledLabel = $localize`disabled`;
  protected readonly enableLabel = $localize`Enable`;
  protected readonly disableLabel = $localize`Disable`;
  protected readonly editTitle = $localize`Edit provider`;
  protected readonly newTitle = $localize`New provider`;
  protected readonly keepPlaceholder = $localize`Leave empty to keep the current one`;

  ngOnInit(): void {
    void this.load();
  }

  protected async load(): Promise<void> {
    this.providers.set(await firstValueFrom(this.identity.listIdentityProviders()));
    const settings = await firstValueFrom(this.admin.listSettings());
    const local = settings.find((s) => s.key === 'auth.local_login');
    this.localLogin.set(local ? local.value['enabled'] !== false : true);
  }

  protected async setLocalLogin(enabled: boolean): Promise<void> {
    await this.attempt(async () => {
      await firstValueFrom(
        this.admin.putSetting({ key: 'auth.local_login', requestBody: { enabled } }),
      );
      this.localLogin.set(enabled);
    });
    await this.load();
  }

  protected openNew(kind: Kind): void {
    this.draft = blank(kind);
    this.redirectUri = null;
    this.error.set(null);
    this.editOpen = true;
  }

  protected openEdit(p: IdpOut): void {
    const d = blank(p.kind as Kind);
    d.id = p.id;
    d.name = p.name;
    d.enabled = p.enabled;
    Object.assign(d, p.config);
    this.draft = d;
    this.redirectUri = p.redirect_uri ?? null;
    this.error.set(null);
    this.editOpen = true;
  }

  protected async save(): Promise<void> {
    const d = this.draft;
    const keys: readonly string[] = d.kind === 'ldap' ? LDAP_KEYS : OIDC_KEYS;
    const config: Record<string, unknown> = {};
    for (const k of keys) config[k] = (d as unknown as Record<string, unknown>)[k];
    this.busy.set(true);
    this.error.set(null);
    try {
      if (d.id) {
        await firstValueFrom(
          this.identity.updateIdentityProvider({
            providerId: d.id,
            idpUpdateIn: { name: d.name, config, secret: d.secret || null },
          }),
        );
      } else {
        await firstValueFrom(
          this.identity.createIdentityProvider({
            idpIn: { kind: d.kind, name: d.name, config, secret: d.secret || null },
          }),
        );
      }
      this.editOpen = false;
      await this.load();
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async toggle(p: IdpOut): Promise<void> {
    await this.attempt(async () => {
      await firstValueFrom(
        this.identity.updateIdentityProvider({
          providerId: p.id,
          idpUpdateIn: { enabled: !p.enabled },
        }),
      );
    });
    await this.load();
  }

  protected async remove(p: IdpOut): Promise<void> {
    if (!confirm($localize`Delete provider ${p.name}:name:?`)) return;
    await this.attempt(async () => {
      await firstValueFrom(this.identity.deleteIdentityProvider({ providerId: p.id }));
    });
    await this.load();
  }

  protected async test(p: IdpOut): Promise<void> {
    await this.attempt(async () => {
      const r = await firstValueFrom(this.identity.checkIdentityProvider({ providerId: p.id }));
      this.notice.set({ severity: r.ok ? 'success' : 'error', text: r.message });
    });
  }

  protected async sync(p: IdpOut): Promise<void> {
    await this.attempt(async () => {
      const t = await firstValueFrom(this.identity.syncIdentityProvider({ providerId: p.id }));
      this.notice.set({
        severity: 'success',
        text: $localize`Sync done: ${t['seen']}:seen: users, ${t['added']}:added: added, ${t['removed']}:removed: removed, ${t['disabled']}:disabled: disabled`,
      });
    });
    await this.load();
  }

  protected openTestUser(p: IdpOut): void {
    this.current = p;
    this.testLogin = '';
    this.testResult.set(null);
    this.testUserOpen = true;
  }

  protected async runTestUser(): Promise<void> {
    if (!this.current) return;
    await this.attempt(async () => {
      this.testResult.set(
        await firstValueFrom(
          this.identity.checkIdentityProviderUser({
            providerId: this.current!.id,
            idpTestUserIn: { login: this.testLogin },
          }),
        ),
      );
    });
  }

  protected async openMappings(p: IdpOut): Promise<void> {
    this.current = p;
    this.error.set(null);
    this.newGroup = '';
    this.newProject = null;
    this.newRole = null;
    this.roles.set([]);
    this.projects.set(await firstValueFrom(this.admin.listProjects({})));
    await this.reloadMappings();
    this.mapOpen = true;
  }

  private async reloadMappings(): Promise<void> {
    if (!this.current) return;
    this.mappings.set(
      await firstValueFrom(this.identity.listMappings({ providerId: this.current.id })),
    );
  }

  protected async pickProject(projectId: string): Promise<void> {
    this.newProject = projectId;
    this.newRole = null;
    this.roles.set(
      (await firstValueFrom(this.projectsApi.listRoles({ projectId }))).filter(
        (r) => r.status === 'active',
      ),
    );
  }

  protected async addMapping(): Promise<void> {
    if (!this.current || !this.newProject || !this.newRole) return;
    this.error.set(null);
    try {
      await firstValueFrom(
        this.identity.createMapping({
          providerId: this.current.id,
          idpMappingIn: {
            external_group: this.newGroup,
            project_id: this.newProject,
            role_name_id: this.newRole,
          },
        }),
      );
      this.newGroup = '';
      await this.reloadMappings();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async deleteMapping(m: IdpMappingOut): Promise<void> {
    await firstValueFrom(this.identity.deleteMapping({ mappingId: m.id }));
    await this.reloadMappings();
  }

  private async attempt(action: () => Promise<void>): Promise<void> {
    this.notice.set(null);
    try {
      await action();
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }
}
