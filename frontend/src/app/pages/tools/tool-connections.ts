import { ChangeDetectorRef, Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  CredentialOut,
  DataSourceOut,
  McpServerOut,
  McpToolOut,
  ToolsService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** Connections: egress policy, credentials, data sources and MCP servers (US-2.2.2, 2.2.3, 2.2.6,
 * 2.2.7). Project Admins edit; Builders see the names they can use in tools. */
@Component({
  selector: 'forge-tool-connections',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzInputModule,
    NzInputNumberModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }
    @if (!isAdmin()) {
      <nz-alert
        nzType="info"
        nzMessage="Only Project Admins change connections. You can use the ones listed here in your tools."
        i18n-nzMessage
      />
    }

    <h2 i18n>Allowed hosts (egress)</h2>
    <p class="muted" i18n>
      HTTP tools, MCP servers and data sources can reach only these hosts. An empty list allows
      nothing. Use *.example.com for subdomains.
    </p>
    @if (allowPrivate()) {
      <nz-alert
        nzType="warning"
        nzMessage="This deployment allows private addresses (FORGE_TOOLS_ALLOW_PRIVATE_HOSTS)."
        i18n-nzMessage
      />
    }
    <form class="form" (ngSubmit)="savePolicy()">
      <textarea
        nz-input
        rows="3"
        name="hosts"
        aria-label="Allowed hosts, one per line"
        i18n-aria-label
        data-testid="policy-hosts"
        [disabled]="!isAdmin()"
        [(ngModel)]="hosts"
      ></textarea>
      @if (isAdmin()) {
        <button nz-button nzType="primary" type="submit" data-testid="save-policy" i18n>
          Save hosts
        </button>
      }
    </form>

    <h2 i18n>Credentials</h2>
    <nz-table
      [nzData]="credentials()"
      nzSize="small"
      [nzShowPagination]="false"
      [nzPageSize]="1000"
    >
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Kind</th>
          <th i18n>Used by</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (c of credentials(); track c.id) {
          <tr>
            <td>{{ c.name }}</td>
            <td>{{ c.kind }}{{ c.param_name ? ' (' + c.param_name + ')' : '' }}</td>
            <td>{{ c.used_by }}</td>
            <td class="actions">
              @if (isAdmin()) {
                <button
                  nz-button
                  nzType="link"
                  nzSize="small"
                  nzDanger
                  (click)="deleteCredential(c)"
                  i18n
                >
                  Delete
                </button>
              }
            </td>
          </tr>
        }
      </tbody>
    </nz-table>
    @if (isAdmin()) {
      <form class="row add" (ngSubmit)="addCredential()">
        <input
          nz-input
          name="cname"
          placeholder="Name"
          i18n-placeholder
          aria-label="Name"
          i18n-aria-label
          [(ngModel)]="cred.name"
          required
        />
        <nz-select
          nzId="cred-kind"
          name="ckind"
          [(ngModel)]="cred.kind"
          aria-label="Kind"
          i18n-aria-label
        >
          <nz-option nzValue="bearer" nzLabel="Bearer token" />
          <nz-option nzValue="basic" nzLabel="Basic (user and password)" />
          <nz-option nzValue="header" nzLabel="Header" />
          <nz-option nzValue="query" nzLabel="Query parameter" />
        </nz-select>
        @if (cred.kind === 'basic') {
          <input
            nz-input
            name="cuser"
            placeholder="User"
            i18n-placeholder
            aria-label="User"
            i18n-aria-label
            [(ngModel)]="cred.username"
          />
        }
        @if (cred.kind === 'header' || cred.kind === 'query') {
          <input
            nz-input
            name="cparam"
            placeholder="Header or parameter name"
            i18n-placeholder
            aria-label="Parameter name"
            i18n-aria-label
            [(ngModel)]="cred.param_name"
          />
        }
        <input
          nz-input
          name="csecret"
          type="password"
          autocomplete="new-password"
          placeholder="Secret"
          i18n-placeholder
          aria-label="Secret"
          i18n-aria-label
          [(ngModel)]="cred.secret"
          required
        />
        <button nz-button nzType="primary" type="submit" data-testid="add-credential" i18n>
          Add
        </button>
      </form>
    }

    <h2 i18n>Data sources (PostgreSQL)</h2>
    <nz-table [nzData]="sources()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Host</th>
          <th i18n>Database</th>
          <th i18n>TLS</th>
          <th i18n>Used by</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (d of sources(); track d.id) {
          <tr>
            <td>{{ d.name }}</td>
            <td>{{ d.host }}:{{ d.port }}</td>
            <td>{{ d.database }} ({{ d.username }})</td>
            <td>{{ d.ssl_mode }}</td>
            <td>{{ d.used_by }}</td>
            <td class="actions">
              @if (isAdmin()) {
                <button
                  nz-button
                  nzType="link"
                  nzSize="small"
                  nzDanger
                  (click)="deleteSource(d)"
                  i18n
                >
                  Delete
                </button>
              }
            </td>
          </tr>
        }
      </tbody>
    </nz-table>
    @if (isAdmin()) {
      <form class="row add" (ngSubmit)="addSource()">
        <input
          nz-input
          name="dsname"
          placeholder="Name"
          i18n-placeholder
          aria-label="Name"
          i18n-aria-label
          [(ngModel)]="ds.name"
          required
        />
        <input
          nz-input
          name="dshost"
          placeholder="Host"
          i18n-placeholder
          aria-label="Host"
          i18n-aria-label
          [(ngModel)]="ds.host"
          required
        />
        <nz-input-number
          nzId="ds-port"
          name="dsport"
          [nzMin]="1"
          [nzMax]="65535"
          [(ngModel)]="ds.port"
        />
        <input
          nz-input
          name="dsdb"
          placeholder="Database"
          i18n-placeholder
          aria-label="Database"
          i18n-aria-label
          [(ngModel)]="ds.database"
          required
        />
        <input
          nz-input
          name="dsuser"
          placeholder="User"
          i18n-placeholder
          aria-label="User"
          i18n-aria-label
          [(ngModel)]="ds.username"
          required
        />
        <input
          nz-input
          name="dspass"
          type="password"
          autocomplete="new-password"
          placeholder="Password"
          i18n-placeholder
          aria-label="Password"
          i18n-aria-label
          [(ngModel)]="ds.password"
          required
        />
        <nz-select
          nzId="ds-ssl"
          name="dsssl"
          [(ngModel)]="ds.ssl_mode"
          aria-label="TLS"
          i18n-aria-label
        >
          <nz-option nzValue="require" nzLabel="TLS (no verification)" i18n-nzLabel />
          <nz-option nzValue="verify-full" nzLabel="TLS (verify certificate)" i18n-nzLabel />
          <nz-option nzValue="disable" nzLabel="No TLS" i18n-nzLabel />
        </nz-select>
        <button nz-button nzType="primary" type="submit" i18n>Add</button>
      </form>
    }

    <h2 i18n>MCP servers</h2>
    <nz-table [nzData]="servers()" nzSize="small" [nzShowPagination]="false" [nzPageSize]="1000">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Transport</th>
          <th i18n>Target</th>
          <th i18n>Used by</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (s of servers(); track s.id) {
          <tr [attr.data-server]="s.name">
            <td>
              {{ s.name }}
              @if (!s.enabled) {
                <nz-tag i18n>disabled</nz-tag>
              }
            </td>
            <td>{{ s.transport }}</td>
            <td>{{ s.url || s.command + ' ' + s.args.join(' ') }}</td>
            <td>{{ s.used_by }}</td>
            <td class="actions">
              <button nz-button nzType="link" nzSize="small" (click)="discover(s)" i18n>
                Discover tools
              </button>
              @if (isAdmin()) {
                <button
                  nz-button
                  nzType="link"
                  nzSize="small"
                  nzDanger
                  (click)="deleteServer(s)"
                  i18n
                >
                  Delete
                </button>
              }
            </td>
          </tr>
        }
      </tbody>
    </nz-table>
    @if (isAdmin()) {
      <form class="row add" (ngSubmit)="addServer()">
        <input
          nz-input
          name="sname"
          placeholder="Name"
          i18n-placeholder
          aria-label="Name"
          i18n-aria-label
          [(ngModel)]="srv.name"
          required
        />
        <nz-select
          nzId="srv-transport"
          name="stransport"
          [(ngModel)]="srv.transport"
          aria-label="Transport"
          i18n-aria-label
        >
          <nz-option nzValue="http" nzLabel="Streamable HTTP" />
          <nz-option nzValue="sse" nzLabel="SSE" />
          <nz-option nzValue="stdio" nzLabel="stdio (allowed commands only)" i18n-nzLabel />
        </nz-select>
        @if (srv.transport === 'stdio') {
          <input
            nz-input
            name="scmd"
            placeholder="Command"
            i18n-placeholder
            aria-label="Command"
            i18n-aria-label
            [(ngModel)]="srv.command"
          />
          <input
            nz-input
            name="sargs"
            placeholder="Arguments, space separated"
            i18n-placeholder
            aria-label="Arguments"
            i18n-aria-label
            [(ngModel)]="srv.args"
          />
        } @else {
          <input
            nz-input
            name="surl"
            placeholder="https://host/mcp"
            aria-label="URL"
            [(ngModel)]="srv.url"
          />
          <nz-select
            nzId="srv-cred"
            name="scred"
            nzAllowClear
            nzPlaceHolder="Credential"
            i18n-nzPlaceHolder
            [(ngModel)]="srv.credential"
          >
            @for (c of credentials(); track c.id) {
              <nz-option [nzValue]="c.name" [nzLabel]="c.name" />
            }
          </nz-select>
        }
        <button nz-button nzType="primary" type="submit" data-testid="add-server" i18n>Add</button>
      </form>
    }

    <nz-modal
      [(nzVisible)]="discoverOpen"
      nzTitle="MCP tools"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="760"
    >
      <ng-container *nzModalContent>
        @for (t of discovered(); track t.name) {
          <label
            nz-checkbox
            [nzId]="'mcp-' + t.name"
            [ngModel]="chosen().has(t.name)"
            (ngModelChange)="choose(t.name, $event)"
          >
            <strong>{{ t.name }}</strong>
            @if (t.imported_as) {
              <nz-tag nzColor="success" i18n>imported as {{ t.imported_as }}</nz-tag>
            }
            @if (t.changed) {
              <nz-tag nzColor="warning" i18n>schema changed</nz-tag>
            }
            <span class="muted small">{{ t.description }}</span>
          </label>
        } @empty {
          <p class="muted" i18n>The server lists no tools.</p>
        }
        @if (discoverError(); as e) {
          <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
        }
        <button nz-button nzType="primary" (click)="importTools()" [disabled]="!chosen().size" i18n>
          Import selected
        </button>
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ToolConnections {
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly api = inject(ToolsService);
  private readonly session = inject(Session);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly isAdmin = computed(() => this.session.can('project.manage'));
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly allowPrivate = signal(false);
  protected readonly credentials = signal<CredentialOut[]>([]);
  protected readonly sources = signal<DataSourceOut[]>([]);
  protected readonly servers = signal<McpServerOut[]>([]);
  protected readonly discovered = signal<McpToolOut[]>([]);
  protected readonly chosen = signal<Set<string>>(new Set());
  protected readonly discoverError = signal<string | null>(null);

  protected hosts = '';
  protected discoverOpen = false;
  private discoverServer: McpServerOut | null = null;

  protected cred = { name: '', kind: 'bearer', secret: '', username: '', param_name: '' };
  protected ds = {
    name: '',
    host: '',
    port: 5432,
    database: '',
    username: '',
    password: '',
    ssl_mode: 'require',
  };
  protected srv = {
    name: '',
    transport: 'http',
    url: '',
    command: '',
    args: '',
    credential: null as string | null,
  };

  constructor() {
    effect(() => {
      if (this.projectId()) void this.reload();
    });
  }

  protected async reload(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const [policy, creds, sources, servers] = await Promise.all([
        firstValueFrom(this.api.getPolicy({ projectId })),
        firstValueFrom(this.api.listCredentials({ projectId })),
        firstValueFrom(this.api.listDatasources({ projectId })),
        firstValueFrom(this.api.listMcpServers({ projectId })),
      ]);
      this.hosts = policy.allowed_hosts.join('\n');
      this.allowPrivate.set(!!policy.allow_private);
      this.credentials.set(creds);
      this.sources.set(sources);
      this.servers.set(servers);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async savePolicy(): Promise<void> {
    await this.attempt(async () => {
      const allowed_hosts = this.hosts
        .split(/[\s,]+/)
        .map((h) => h.trim())
        .filter(Boolean);
      await firstValueFrom(
        this.api.setPolicy({ projectId: this.projectId()!, policyIn: { allowed_hosts } }),
      );
      this.message.set({ ok: true, text: $localize`Allowed hosts saved` });
    });
  }

  protected async addCredential(): Promise<void> {
    await this.attempt(async () => {
      const c = this.cred;
      await firstValueFrom(
        this.api.createCredential({
          projectId: this.projectId()!,
          credentialIn: {
            name: c.name,
            kind: c.kind as 'bearer',
            secret: c.secret,
            username: c.username || null,
            param_name: c.param_name || null,
          },
        }),
      );
      this.cred = { name: '', kind: 'bearer', secret: '', username: '', param_name: '' };
    });
    await this.reload();
  }

  protected async deleteCredential(c: CredentialOut): Promise<void> {
    if (!confirm($localize`Delete credential ${c.name}:name:?`)) return;
    await this.attempt(async () => {
      await firstValueFrom(
        this.api.deleteCredential({ projectId: this.projectId()!, credentialId: c.id }),
      );
    });
    await this.reload();
  }

  protected async addSource(): Promise<void> {
    await this.attempt(async () => {
      await firstValueFrom(
        this.api.createDatasource({
          projectId: this.projectId()!,
          dataSourceIn: { ...this.ds, ssl_mode: this.ds.ssl_mode as 'require' },
        }),
      );
      this.ds = { ...this.ds, name: '', password: '' };
    });
    await this.reload();
  }

  protected async deleteSource(d: DataSourceOut): Promise<void> {
    if (!confirm($localize`Delete data source ${d.name}:name:?`)) return;
    await this.attempt(async () => {
      await firstValueFrom(
        this.api.deleteDatasource({ projectId: this.projectId()!, sourceId: d.id }),
      );
    });
    await this.reload();
  }

  protected async addServer(): Promise<void> {
    await this.attempt(async () => {
      const s = this.srv;
      await firstValueFrom(
        this.api.createMcpServer({
          projectId: this.projectId()!,
          mcpServerIn: {
            name: s.name,
            transport: s.transport as 'http',
            url: s.transport === 'stdio' ? null : s.url,
            command: s.transport === 'stdio' ? s.command : null,
            args: s.transport === 'stdio' ? s.args.split(/\s+/).filter(Boolean) : [],
            credential: s.credential,
            enabled: true,
          },
        }),
      );
      this.srv = { ...this.srv, name: '', url: '', command: '', args: '' };
    });
    await this.reload();
  }

  protected async deleteServer(s: McpServerOut): Promise<void> {
    if (!confirm($localize`Delete MCP server ${s.name}:name:?`)) return;
    await this.attempt(async () => {
      await firstValueFrom(
        this.api.deleteMcpServer({ projectId: this.projectId()!, serverId: s.id }),
      );
    });
    await this.reload();
  }

  protected async discover(s: McpServerOut): Promise<void> {
    this.discoverServer = s;
    this.discoverError.set(null);
    this.chosen.set(new Set());
    try {
      this.discovered.set(
        await firstValueFrom(
          this.api.discoverMcpTools({ projectId: this.projectId()!, serverId: s.id }),
        ),
      );
      this.discoverOpen = true;
      this.cdr.markForCheck(); // zoneless: a plain field set after an `await` schedules no check
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected choose(name: string, on: boolean): void {
    const next = new Set(this.chosen());
    if (on) next.add(name);
    else next.delete(name);
    this.chosen.set(next);
  }

  protected async importTools(): Promise<void> {
    if (!this.discoverServer) return;
    try {
      const created = await firstValueFrom(
        this.api.importMcpTools({
          projectId: this.projectId()!,
          serverId: this.discoverServer.id,
          mcpImportIn: { tools: [...this.chosen()] },
        }),
      );
      this.discoverOpen = false;
      this.message.set({
        ok: true,
        text: $localize`Imported ${created.length}:n: tools into the library`,
      });
      await this.reload();
    } catch (e) {
      this.discoverError.set(problemMessage(e));
    }
  }

  private async attempt(action: () => Promise<void>): Promise<void> {
    this.message.set(null);
    try {
      await action();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }
}
