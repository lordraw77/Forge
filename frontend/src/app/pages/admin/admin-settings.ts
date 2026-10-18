import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { firstValueFrom } from 'rxjs';

import { AdminService, SettingOut } from '../../api-client';
import { problemMessage } from '../../core/http';

interface Field {
  key: string;
  label: string;
  kind: 'number' | 'boolean' | 'text' | 'longtext';
}

const LABELS: Record<string, string> = {
  'auth.password_policy': $localize`Password policy`,
  'auth.lockout': $localize`Account lockout`,
  'auth.session': $localize`Sessions`,
  'auth.totp': $localize`Two-factor authentication`,
  'system.eula': $localize`Terms of use (EULA)`,
  'gateway.limits': $localize`LLM gateway limits (0 = unlimited)`,
};

/** Super Admin console → Settings: GUI for runtime settings, applied without restart (ADR-007). */
@Component({
  selector: 'forge-admin-settings',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzCheckboxModule,
    NzInputModule,
    NzInputNumberModule,
  ],
  template: `
    <header class="head"><h1 i18n>Settings</h1></header>
    <div class="settings">
      @for (s of settings(); track s.key) {
        <nz-card [nzTitle]="labels[s.key] || s.key" [attr.data-setting]="s.key">
          <form class="form" (ngSubmit)="save(s)">
            @for (f of fields(s); track f.key) {
              @switch (f.kind) {
                @case ('number') {
                  <label [for]="s.key + f.key">{{ f.label }}</label>
                  <nz-input-number
                    [nzId]="s.key + f.key"
                    [name]="f.key"
                    [(ngModel)]="s.value[f.key]"
                  />
                }
                @case ('boolean') {
                  <label
                    nz-checkbox
                    [nzId]="s.key + f.key"
                    [name]="f.key"
                    [(ngModel)]="s.value[f.key]"
                  >
                    {{ f.label }}
                  </label>
                }
                @case ('longtext') {
                  <label [for]="s.key + f.key">{{ f.label }}</label>
                  <textarea
                    nz-input
                    [id]="s.key + f.key"
                    [name]="f.key"
                    rows="4"
                    [(ngModel)]="s.value[f.key]"
                  ></textarea>
                }
                @default {
                  <label [for]="s.key + f.key">{{ f.label }}</label>
                  <input
                    nz-input
                    [id]="s.key + f.key"
                    [name]="f.key"
                    [(ngModel)]="s.value[f.key]"
                  />
                }
              }
            }
            <small i18n>Version {{ s.version }}</small>
            @if (messages()[s.key]; as m) {
              <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" />
            }
            <div class="dialog-actions">
              <button nz-button nzType="primary" type="submit" i18n>Save</button>
            </div>
          </form>
        </nz-card>
      }
    </div>
  `,
  styleUrl: './admin.scss',
})
export class AdminSettings implements OnInit {
  private readonly admin = inject(AdminService);
  protected readonly settings = signal<SettingOut[]>([]);
  protected readonly messages = signal<Record<string, { ok: boolean; text: string }>>({});
  protected readonly labels = LABELS;

  async ngOnInit(): Promise<void> {
    this.settings.set(await firstValueFrom(this.admin.listSettings()));
  }

  protected fields(s: SettingOut): Field[] {
    return Object.entries(s.value).map(([key, value]) => ({
      key,
      label: key.replace(/_/g, ' '),
      kind:
        typeof value === 'number'
          ? 'number'
          : typeof value === 'boolean'
            ? 'boolean'
            : key === 'text'
              ? 'longtext'
              : 'text',
    }));
  }

  protected async save(s: SettingOut): Promise<void> {
    try {
      const saved = await firstValueFrom(
        this.admin.putSetting({ key: s.key, requestBody: s.value }),
      );
      this.settings.update((list) => list.map((x) => (x.key === s.key ? saved : x)));
      this.messages.update((m) => ({
        ...m,
        [s.key]: { ok: true, text: $localize`Saved; applied without restart` },
      }));
    } catch (e) {
      this.messages.update((m) => ({ ...m, [s.key]: { ok: false, text: problemMessage(e) } }));
    }
  }
}
