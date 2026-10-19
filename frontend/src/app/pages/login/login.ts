import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzInputModule } from 'ng-zorro-antd/input';
import { firstValueFrom } from 'rxjs';

import { AuthService, PublicProviderOut } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { PasswordInput } from '../../shared/password-input';

/** US-1.2.2 / US-1.2.3: username or email + password, then TOTP or a recovery code. */
@Component({
  selector: 'forge-login',
  imports: [FormsModule, NzAlertModule, NzButtonModule, NzCardModule, NzInputModule, PasswordInput],
  templateUrl: './login.html',
  styleUrl: './login.scss',
})
export class Login implements OnInit {
  private readonly auth = inject(AuthService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected loginName = '';
  protected password = '';
  protected code = '';
  protected useRecovery = false;
  protected readonly challenge = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly providers = signal<PublicProviderOut[]>([]);
  protected readonly localLogin = signal(true);

  async ngOnInit(): Promise<void> {
    const failed = this.route.snapshot.queryParamMap.get('error');
    if (failed === 'oidc_failed')
      this.error.set($localize`Sign-in with the identity provider failed`);
    if (failed === 'oidc_unavailable')
      this.error.set($localize`The identity provider is unreachable`);
    try {
      const options = await firstValueFrom(this.auth.signInOptions());
      this.providers.set(options.providers);
      this.localLogin.set(options.local_login);
    } catch {
      // The password form still works without the list.
    }
  }

  protected oidcUrl(id: string): string {
    return `/api/v1/auth/oidc/${id}/start`;
  }

  protected async submit(): Promise<void> {
    await this.run(async () => {
      const res = await firstValueFrom(
        this.auth.login({ loginIn: { login: this.loginName, password: this.password } }),
      );
      if (res.status === 'totp_required') {
        this.challenge.set(res.challenge ?? null);
        this.password = '';
      } else {
        await this.done();
      }
    });
  }

  protected async submitCode(): Promise<void> {
    const challenge = this.challenge();
    if (!challenge) return;
    await this.run(async () => {
      await firstValueFrom(
        this.auth.loginTotp({
          loginTotpIn: this.useRecovery
            ? { challenge, recovery_code: this.code }
            : { challenge, code: this.code },
        }),
      );
      await this.done();
    });
  }

  protected restart(): void {
    this.challenge.set(null);
    this.code = '';
    this.error.set(null);
  }

  private async done(): Promise<void> {
    const me = await this.session.load();
    const step = this.session.pendingStep(me?.flags);
    const returnUrl = this.route.snapshot.queryParamMap.get('returnUrl');
    await this.router.navigateByUrl(step ?? (returnUrl?.startsWith('/') ? returnUrl : '/'));
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      await action();
    } catch (e) {
      this.error.set(problemMessage(e, $localize`Sign-in failed`));
      if (this.challenge()) this.code = '';
    } finally {
      this.busy.set(false);
    }
  }
}
