import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { VaultService, VaultStatus } from '../../api-client';
import { problemMessage } from '../../core/http';

/** Super Admin console → Secrets vault: an external OpenBao / Vault Transit key that wraps the
 * data keys of every stored secret (ADR-006, E3.8). */
@Component({
  selector: 'forge-admin-vault',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzCheckboxModule,
    NzInputModule,
    NzSelectModule,
    NzTagModule,
  ],
  template: `
    <header class="head"><h1 i18n>Secrets vault</h1></header>
    <p class="intro" i18n>
      Provider keys, tokens and passwords are stored encrypted. By default the key that protects
      them is derived from FORGE_MASTER_KEY. Here you can keep that key in an OpenBao or Vault
      Transit engine instead: Forge sends it each secret's data key to wrap and unwrap, and the key
      never leaves the vault.
    </p>
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" nzShowIcon />
    }
    @if (info()) {
      <nz-alert nzType="success" [nzMessage]="info()" nzShowIcon />
    }
    @if (status(); as s) {
      <nz-card nzTitle="State" i18n-nzTitle>
        <p>
          <span i18n>Key used for new secrets</span>:
          <nz-tag data-testid="vault-current">{{ s.current_kek }}</nz-tag>
        </p>
        <p>
          <span i18n>Secrets by key</span>:
          @for (k of kekNames(s); track k) {
            <nz-tag>{{ k }}: {{ s.secrets_by_kek[k] }}</nz-tag>
          } @empty {
            <span i18n>none yet</span>
          }
        </p>
      </nz-card>
    }
    <nz-card nzTitle="Vault" i18n-nzTitle>
      <form class="form" (ngSubmit)="save()">
        <span></span>
        <label
          nz-checkbox
          nzId="v-enabled"
          name="enabled"
          [(ngModel)]="form.enabled"
          data-testid="vault-enabled"
          i18n
          >Enabled</label
        >
        <label for="v-url" i18n>Address</label>
        <input
          nz-input
          id="v-url"
          name="url"
          [(ngModel)]="form.url"
          placeholder="https://openbao.example.com:8200"
          data-testid="vault-url"
        />
        <label for="v-mount" i18n>Transit mount</label>
        <input nz-input id="v-mount" name="mount" [(ngModel)]="form.mount" />
        <label for="v-key" i18n>Key name</label>
        <input nz-input id="v-key" name="key" [(ngModel)]="form.key_name" />
        <label for="v-ns" i18n>Namespace (optional)</label>
        <input nz-input id="v-ns" name="ns" [(ngModel)]="form.namespace" />
        <label for="v-auth" i18n>Sign-in</label>
        <nz-select nzId="v-auth" name="auth" [(ngModel)]="form.auth_method">
          <nz-option nzValue="token" nzLabel="Token" />
          <nz-option nzValue="approle" nzLabel="AppRole" />
        </nz-select>
        @if (form.auth_method === 'approle') {
          <label for="v-role" i18n>Role id</label>
          <input nz-input id="v-role" name="role" [(ngModel)]="form.role_id" />
        }
        <label for="v-cred">{{ form.auth_method === 'token' ? tokenLabel : secretIdLabel }}</label>
        <input
          nz-input
          id="v-cred"
          name="credential"
          type="password"
          autocomplete="new-password"
          [(ngModel)]="credential"
          [placeholder]="status()?.config['has_credential'] ? kept : ''"
          data-testid="vault-credential"
        />
        <label for="v-ca" i18n>CA certificate (PEM, optional)</label>
        <textarea nz-input id="v-ca" name="ca" rows="3" [(ngModel)]="form.ca_pem"></textarea>
        <span></span>
        <label
          nz-checkbox
          nzId="v-active"
          name="active"
          [(ngModel)]="form.active"
          data-testid="vault-active"
          i18n
          >Use the vault for new and re-wrapped secrets</label
        >
        <span></span>
        <div class="row">
          <button nz-button nzType="primary" type="submit" data-testid="vault-save" i18n>
            Save
          </button>
          <button nz-button type="button" (click)="test()" data-testid="vault-test" i18n>
            Test the connection
          </button>
        </div>
      </form>
    </nz-card>
    <nz-card nzTitle="Re-wrap existing secrets" i18n-nzTitle>
      <p class="intro" i18n>
        Moves every secret's data key to the key chosen above (the vault when "use the vault" is on,
        the built-in key when it is off). The secrets themselves are not re-encrypted. Do this after
        switching on, and before switching the vault off.
      </p>
      <button nz-button (click)="rewrap()" data-testid="vault-rewrap" i18n>Re-wrap now</button>
    </nz-card>
  `,
  styleUrl: './admin.scss',
  styles: `
    .intro {
      color: var(--forge-text-muted-color);
    }
    .row {
      display: flex;
      gap: 0.5rem;
    }
    nz-card {
      margin-top: 0.75rem;
      max-width: 44rem;
    }
  `,
})
export class AdminVault implements OnInit {
  private readonly api = inject(VaultService);
  protected readonly status = signal<VaultStatus | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly info = signal<string | null>(null);
  protected readonly kept = $localize`(unchanged)`;
  protected readonly tokenLabel = $localize`Token`;
  protected readonly secretIdLabel = $localize`AppRole secret id`;
  protected credential = '';
  protected form = {
    enabled: false,
    active: false,
    url: '',
    mount: 'transit',
    key_name: 'forge',
    namespace: '',
    auth_method: 'token' as 'token' | 'approle',
    role_id: '',
    ca_pem: '',
  };

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  protected kekNames(s: VaultStatus): string[] {
    return Object.keys(s.secrets_by_kek).sort();
  }

  private async load(): Promise<void> {
    try {
      const s = await firstValueFrom(this.api.getVault());
      this.status.set(s);
      const c = s.config as Record<string, unknown>;
      this.form = {
        enabled: !!c['enabled'],
        active: !!c['active'],
        url: String(c['url'] ?? ''),
        mount: String(c['mount'] ?? 'transit'),
        key_name: String(c['key_name'] ?? 'forge'),
        namespace: String(c['namespace'] ?? ''),
        auth_method: (c['auth_method'] as 'token' | 'approle') ?? 'token',
        role_id: String(c['role_id'] ?? ''),
        ca_pem: this.form.ca_pem,
      };
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async save(): Promise<void> {
    this.error.set(null);
    this.info.set(null);
    try {
      const s = await firstValueFrom(
        this.api.putVault({
          vaultIn: {
            ...this.form,
            credential: this.credential === '' ? undefined : this.credential,
          },
        }),
      );
      this.credential = '';
      this.status.set(s);
      this.info.set($localize`Saved`);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async test(): Promise<void> {
    this.error.set(null);
    this.info.set(null);
    try {
      const r = await firstValueFrom(this.api.checkVault());
      if (r.ok) this.info.set($localize`The vault answered: key ${r.detail?.['key']} works`);
      else this.error.set(r.error ?? $localize`The vault did not answer`);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async rewrap(): Promise<void> {
    this.error.set(null);
    this.info.set(null);
    try {
      const r = await firstValueFrom(this.api.rewrap());
      this.info.set($localize`${r.rewrapped} secret(s) re-wrapped`);
      await this.load();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
