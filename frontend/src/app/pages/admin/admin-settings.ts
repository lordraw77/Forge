import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { CheckboxModule } from 'primeng/checkbox';
import { InputNumberModule } from 'primeng/inputnumber';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { TextareaModule } from 'primeng/textarea';
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
};

/** Super Admin console → Settings: GUI for runtime settings, applied without restart (ADR-007). */
@Component({
  selector: 'forge-admin-settings',
  imports: [
    FormsModule,
    ButtonModule,
    CardModule,
    CheckboxModule,
    InputNumberModule,
    InputTextModule,
    MessageModule,
    TextareaModule,
  ],
  template: `
    <header class="head"><h1 i18n>Settings</h1></header>
    <div class="settings">
      @for (s of settings(); track s.key) {
        <p-card [header]="labels[s.key] || s.key" [attr.data-setting]="s.key">
          <form class="form" (ngSubmit)="save(s)">
            @for (f of fields(s); track f.key) {
              <label [for]="s.key + f.key">{{ f.label }}</label>
              @switch (f.kind) {
                @case ('number') {
                  <p-inputnumber
                    [inputId]="s.key + f.key"
                    [name]="f.key"
                    [(ngModel)]="s.value[f.key]"
                  />
                }
                @case ('boolean') {
                  <p-checkbox
                    [inputId]="s.key + f.key"
                    [name]="f.key"
                    [binary]="true"
                    [(ngModel)]="s.value[f.key]"
                  />
                }
                @case ('longtext') {
                  <textarea
                    pTextarea
                    [id]="s.key + f.key"
                    [name]="f.key"
                    rows="4"
                    [(ngModel)]="s.value[f.key]"
                  ></textarea>
                }
                @default {
                  <input
                    pInputText
                    [id]="s.key + f.key"
                    [name]="f.key"
                    [(ngModel)]="s.value[f.key]"
                  />
                }
              }
            }
            <small i18n>Version {{ s.version }}</small>
            @if (messages()[s.key]; as m) {
              <p-message [severity]="m.ok ? 'success' : 'error'" [text]="m.text" />
            }
            <div class="dialog-actions"><p-button type="submit" label="Save" i18n-label /></div>
          </form>
        </p-card>
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
