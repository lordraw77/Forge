import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { firstValueFrom } from 'rxjs';

import { NotificationsService, SmtpOut } from '../../api-client';
import { problemMessage } from '../../core/http';

/** Super Admin console → Email: the mail server used for approval notifications. */
@Component({
  selector: 'forge-admin-email',
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
    <header class="head"><h1 i18n>Email</h1></header>
    <p class="intro" i18n>
      Approval requests are sent by email to the people who may decide them. Leave the host empty to
      send nothing; the Approvals page works without email.
    </p>
    @if (current(); as c) {
      @if (c.source === 'environment') {
        <nz-alert
          nzType="info"
          nzMessage="These values come from the FORGE_SMTP_* environment. Saving here replaces them."
          i18n-nzMessage
        />
      }
    }
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    <nz-card>
      <form class="form" (ngSubmit)="save()">
        <label for="smtp-host" i18n>Server</label>
        <input
          nz-input
          id="smtp-host"
          name="host"
          [(ngModel)]="form.host"
          data-testid="smtp-host"
        />
        <label for="smtp-port" i18n>Port</label>
        <nz-input-number
          nzId="smtp-port"
          name="port"
          [(ngModel)]="form.port"
          [nzMin]="1"
          [nzMax]="65535"
        />
        <label for="smtp-user" i18n>User name</label>
        <input
          nz-input
          id="smtp-user"
          name="username"
          [(ngModel)]="form.username"
          autocomplete="off"
        />
        <label for="smtp-pass" i18n>Password</label>
        <input
          nz-input
          id="smtp-pass"
          name="password"
          type="password"
          autocomplete="new-password"
          [(ngModel)]="password"
          [placeholder]="current()?.has_password ? passwordKept : ''"
        />
        <label for="smtp-from" i18n>Sender address</label>
        <input nz-input id="smtp-from" name="from" [(ngModel)]="form.from_address" />
        <label></label>
        <label nz-checkbox name="starttls" [(ngModel)]="form.starttls" i18n>Use STARTTLS</label>
        <label></label>
        <div class="row">
          <button nz-button nzType="primary" type="submit" data-testid="smtp-save" i18n>
            Save
          </button>
          @if (current()?.has_password) {
            <button nz-button type="button" (click)="removePassword()" i18n>Remove password</button>
          }
        </div>
      </form>
    </nz-card>
    @if (saved()) {
      <nz-card nzTitle="Send a test message" i18n-nzTitle>
        <div class="row">
          <input nz-input name="to" type="email" [(ngModel)]="to" placeholder="you@example.com" />
          <button nz-button (click)="test()" [disabled]="!to" data-testid="smtp-test" i18n>
            Send
          </button>
        </div>
        @if (result(); as r) {
          <nz-alert
            [nzType]="r.ok ? 'success' : 'error'"
            [nzMessage]="r.ok ? sentText : (r.error ?? '')"
          />
        }
      </nz-card>
    }
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
      max-width: 40rem;
    }
  `,
})
export class AdminEmail implements OnInit {
  private readonly api = inject(NotificationsService);
  protected readonly current = signal<SmtpOut | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly result = signal<{ ok: boolean; error?: string | null } | null>(null);
  protected readonly saved = signal(false);
  protected readonly passwordKept = $localize`(unchanged)`;
  protected readonly sentText = $localize`Message sent`;
  protected form = {
    host: '',
    port: 587,
    username: '',
    from_address: 'forge@localhost',
    starttls: true,
  };
  protected password = '';
  protected to = '';

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  private async load(): Promise<void> {
    try {
      const c = await firstValueFrom(this.api.getSmtp());
      this.current.set(c);
      this.form = {
        host: c.host,
        port: c.port,
        username: c.username,
        from_address: c.from_address,
        starttls: c.starttls,
      };
      this.saved.set(c.source !== 'none');
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  private async put(password: string | null): Promise<void> {
    this.error.set(null);
    try {
      this.current.set(
        await firstValueFrom(this.api.putSmtp({ smtpIn: { ...this.form, password } })),
      );
      this.password = '';
      this.saved.set(this.current()?.source !== 'none');
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  async save(): Promise<void> {
    await this.put(this.password ? this.password : null);
  }

  async removePassword(): Promise<void> {
    await this.put('');
  }

  async test(): Promise<void> {
    this.result.set(null);
    try {
      this.result.set(await firstValueFrom(this.api.sendTestMail({ smtpTestIn: { to: this.to } })));
    } catch (e) {
      this.result.set({ ok: false, error: problemMessage(e) });
    }
  }
}
