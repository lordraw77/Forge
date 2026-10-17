import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { PasswordModule } from 'primeng/password';
import { toDataURL } from 'qrcode';
import { firstValueFrom } from 'rxjs';

import { AuthService, EulaOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** US-1.2.2/5 and US-1.2.4: forced password change on first login (also from the profile). */
@Component({
  selector: 'forge-change-password',
  imports: [FormsModule, ButtonModule, CardModule, MessageModule, PasswordModule, RouterLink],
  template: `
    <main class="page">
      <p-card styleClass="card" header="Change password" i18n-header>
        @if (forced()) {
          <p-message severity="info" i18n>You must set a new password before continuing.</p-message>
        }
        @if (error(); as message) {
          <p-message severity="error" [text]="message" data-testid="step-error" />
        }
        <form class="form" (ngSubmit)="save()">
          <label for="current" i18n>Current password</label>
          <p-password
            inputId="current"
            name="current"
            [(ngModel)]="current"
            [feedback]="false"
            [toggleMask]="true"
            autocomplete="current-password"
            [fluid]="true"
          />
          <label for="next" i18n>New password</label>
          <p-password
            inputId="next"
            name="next"
            [(ngModel)]="next"
            [feedback]="false"
            [toggleMask]="true"
            autocomplete="new-password"
            [fluid]="true"
          />
          <small i18n>At least 12 characters; not a common password; not reused.</small>
          <div class="actions">
            @if (!forced()) {
              <a routerLink="/" class="p-button p-button-text" i18n>Cancel</a>
            }
            <p-button type="submit" label="Save" i18n-label [loading]="busy()" />
          </div>
        </form>
      </p-card>
    </main>
  `,
  styleUrl: './account-steps.scss',
})
export class ChangePassword {
  private readonly auth = inject(AuthService);
  private readonly session = inject(Session);
  protected current = '';
  protected next = '';
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly forced = signal(this.session.me()?.flags.password_change_required ?? false);

  protected async save(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(
        this.auth.changePassword({
          passwordChangeIn: { current_password: this.current, new_password: this.next },
        }),
      );
      await this.session.continueAfterStep();
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}

/** Part 2, Authentication: EULA acceptance on first login. */
@Component({
  selector: 'forge-eula',
  imports: [ButtonModule, CardModule, MessageModule],
  template: `
    <main class="page">
      <p-card styleClass="card" header="Terms of use" i18n-header>
        @if (eula(); as e) {
          <div class="eula" data-testid="eula-text">{{ e.text }}</div>
          @if (error(); as message) {
            <p-message severity="error" [text]="message" />
          }
          <div class="actions">
            <p-button
              label="Sign out"
              i18n-label
              severity="secondary"
              [text]="true"
              (onClick)="session.logout()"
            />
            <p-button label="Accept" i18n-label [loading]="busy()" (onClick)="accept(e.version)" />
          </div>
        }
      </p-card>
    </main>
  `,
  styleUrl: './account-steps.scss',
})
export class Eula implements OnInit {
  private readonly auth = inject(AuthService);
  protected readonly session = inject(Session);
  protected readonly eula = signal<EulaOut | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  async ngOnInit(): Promise<void> {
    this.eula.set(await firstValueFrom(this.auth.getEula()));
  }

  protected async accept(version: string): Promise<void> {
    this.busy.set(true);
    try {
      await firstValueFrom(this.auth.acceptEula({ eulaAcceptIn: { version } }));
      await this.session.continueAfterStep();
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}

/** US-1.2.3: TOTP enrolment (forced by policy, or optional from the profile). */
@Component({
  selector: 'forge-totp-enroll',
  imports: [FormsModule, ButtonModule, CardModule, InputTextModule, MessageModule, RouterLink],
  template: `
    <main class="page">
      <p-card styleClass="card" header="Two-factor authentication" i18n-header>
        @if (error(); as message) {
          <p-message severity="error" [text]="message" data-testid="step-error" />
        }
        @if (codes(); as list) {
          <p-message severity="success" i18n
            >Two-factor authentication is on. Save these recovery codes; each works once.</p-message
          >
          <ol class="codes" data-testid="recovery-codes">
            @for (c of list; track c) {
              <li>{{ c }}</li>
            }
          </ol>
          <div class="actions">
            <p-button
              label="Download"
              i18n-label
              severity="secondary"
              icon="pi pi-download"
              (onClick)="download(list)"
            />
            <p-button label="Continue" i18n-label (onClick)="session.continueAfterStep()" />
          </div>
        } @else if (secret(); as s) {
          <p i18n>Scan the QR code with an authenticator app, then enter the 6-digit code.</p>
          @if (qr(); as image) {
            <img class="qr" [src]="image" alt="TOTP QR code" i18n-alt />
          }
          <p>
            <small i18n>Or enter this key manually:</small>
            <code data-testid="totp-secret">{{ s }}</code>
          </p>
          <form class="form" (ngSubmit)="confirm()">
            <label for="code" i18n>Authentication code</label>
            <input
              pInputText
              id="code"
              name="code"
              inputmode="numeric"
              maxlength="6"
              [(ngModel)]="code"
            />
            <div class="actions">
              @if (!required()) {
                <a routerLink="/" class="p-button p-button-text" i18n>Cancel</a>
              }
              <p-button type="submit" label="Enable" i18n-label [loading]="busy()" />
            </div>
          </form>
        } @else {
          <p i18n>Protect your account with a code from an authenticator app at every sign-in.</p>
          <div class="actions">
            <p-button label="Set up" i18n-label [loading]="busy()" (onClick)="start()" />
          </div>
        }
      </p-card>
    </main>
  `,
  styleUrl: './account-steps.scss',
})
export class TotpEnroll {
  private readonly auth = inject(AuthService);
  protected readonly session = inject(Session);
  protected code = '';
  protected readonly secret = signal<string | null>(null);
  protected readonly qr = signal<string | null>(null);
  protected readonly codes = signal<string[] | null>(null);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly required = signal(this.session.me()?.flags.totp_enrollment_required ?? false);

  protected async start(): Promise<void> {
    await this.run(async () => {
      const res = await firstValueFrom(this.auth.totpEnroll());
      this.secret.set(res.secret);
      this.qr.set(await toDataURL(res.otpauth_uri, { margin: 1, width: 200 }));
    });
  }

  protected async confirm(): Promise<void> {
    await this.run(async () => {
      const res = await firstValueFrom(this.auth.totpConfirm({ codeIn: { code: this.code } }));
      this.codes.set(res.recovery_codes);
    });
  }

  protected download(list: string[]): void {
    const blob = new Blob([list.join('\n') + '\n'], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'forge-recovery-codes.txt';
    a.click();
    URL.revokeObjectURL(a.href);
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await action();
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
