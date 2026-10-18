import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { toDataURL } from 'qrcode';
import { firstValueFrom } from 'rxjs';

import { AuthService, EulaOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { PasswordInput } from '../../shared/password-input';

/** US-1.2.2/5 and US-1.2.4: forced password change on first login (also from the profile). */
@Component({
  selector: 'forge-change-password',
  imports: [FormsModule, NzAlertModule, NzButtonModule, NzCardModule, PasswordInput, RouterLink],
  template: `
    <main class="page">
      <nz-card class="card" nzTitle="Change password" i18n-nzTitle>
        @if (forced()) {
          <nz-alert
            nzType="info"
            nzMessage="You must set a new password before continuing."
            i18n-nzMessage
          />
        }
        @if (error(); as message) {
          <nz-alert nzType="error" [nzMessage]="message" data-testid="step-error" />
        }
        <form class="form" (ngSubmit)="save()">
          <label for="current" i18n>Current password</label>
          <forge-password inputId="current" autocomplete="current-password" [(value)]="current" />
          <label for="next" i18n>New password</label>
          <forge-password inputId="next" autocomplete="new-password" [(value)]="next" />
          <small i18n>At least 12 characters; not a common password; not reused.</small>
          <div class="actions">
            @if (!forced()) {
              <a nz-button nzType="text" routerLink="/" i18n>Cancel</a>
            }
            <button nz-button nzType="primary" type="submit" [nzLoading]="busy()" i18n>Save</button>
          </div>
        </form>
      </nz-card>
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
  imports: [NzAlertModule, NzButtonModule, NzCardModule],
  template: `
    <main class="page">
      <nz-card class="card" nzTitle="Terms of use" i18n-nzTitle>
        @if (eula(); as e) {
          <div class="eula" data-testid="eula-text">{{ e.text }}</div>
          @if (error(); as message) {
            <nz-alert nzType="error" [nzMessage]="message" />
          }
          <div class="actions">
            <button nz-button nzType="text" (click)="session.logout()" i18n>Sign out</button>
            <button
              nz-button
              nzType="primary"
              [nzLoading]="busy()"
              (click)="accept(e.version)"
              i18n
            >
              Accept
            </button>
          </div>
        }
      </nz-card>
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
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzIconModule,
    NzInputModule,
    RouterLink,
  ],
  template: `
    <main class="page">
      <nz-card class="card" nzTitle="Two-factor authentication" i18n-nzTitle>
        @if (error(); as message) {
          <nz-alert nzType="error" [nzMessage]="message" data-testid="step-error" />
        }
        @if (codes(); as list) {
          <nz-alert
            nzType="success"
            nzMessage="Two-factor authentication is on. Save these recovery codes; each works once."
            i18n-nzMessage
          />
          <ol class="codes" data-testid="recovery-codes">
            @for (c of list; track c) {
              <li>{{ c }}</li>
            }
          </ol>
          <div class="actions">
            <button nz-button (click)="download(list)">
              <span nz-icon nzType="download"></span><span i18n>Download</span>
            </button>
            <button nz-button nzType="primary" (click)="session.continueAfterStep()" i18n>
              Continue
            </button>
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
              nz-input
              id="code"
              name="code"
              inputmode="numeric"
              maxlength="6"
              [(ngModel)]="code"
            />
            <div class="actions">
              @if (!required()) {
                <a nz-button nzType="text" routerLink="/" i18n>Cancel</a>
              }
              <button nz-button nzType="primary" type="submit" [nzLoading]="busy()" i18n>
                Enable
              </button>
            </div>
          </form>
        } @else {
          <p i18n>Protect your account with a code from an authenticator app at every sign-in.</p>
          <div class="actions">
            <button nz-button nzType="primary" [nzLoading]="busy()" (click)="start()" i18n>
              Set up
            </button>
          </div>
        }
      </nz-card>
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
