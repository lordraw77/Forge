import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { AuthService, FlagsOut, MeOut, MyProjectOut } from '../api-client';

const PROJECT_KEY = 'forge.project';

/** Current user, first-login flags and project selection (signals; zoneless-friendly). */
@Injectable({ providedIn: 'root' })
export class Session {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly me = signal<MeOut | null>(null);
  readonly user = computed(() => this.me()?.user ?? null);
  readonly projects = computed<MyProjectOut[]>(() => this.me()?.projects ?? []);
  private readonly selectedId = signal<string | null>(this.storedProject());
  readonly project = computed<MyProjectOut | null>(() => {
    const list = this.projects();
    return list.find((p) => p.id === this.selectedId()) ?? list[0] ?? null;
  });

  /** Load /me; returns null when not signed in. */
  async load(): Promise<MeOut | null> {
    try {
      const me = await firstValueFrom(this.auth.me());
      this.me.set(me);
      return me;
    } catch {
      this.me.set(null);
      return null;
    }
  }

  /** The first pending account step, or null when the account is ready. */
  pendingStep(flags: FlagsOut | undefined = this.me()?.flags): string | null {
    if (!flags) return null;
    if (flags.password_change_required) return '/account/password';
    if (flags.eula_required) return '/account/eula';
    if (flags.totp_enrollment_required) return '/account/totp';
    return null;
  }

  selectProject(id: string): void {
    this.selectedId.set(id);
    try {
      localStorage.setItem(PROJECT_KEY, id);
    } catch {
      // storage unavailable (private mode): selection still works for this tab
    }
  }

  can(permission: string): boolean {
    return this.project()?.permissions.includes(permission) ?? false;
  }

  async continueAfterStep(): Promise<void> {
    const me = await this.load();
    await this.router.navigateByUrl(this.pendingStep(me?.flags) ?? '/');
  }

  async logout(): Promise<void> {
    try {
      await firstValueFrom(this.auth.logout());
    } finally {
      this.me.set(null);
      await this.router.navigateByUrl('/login');
    }
  }

  private storedProject(): string | null {
    try {
      return localStorage.getItem(PROJECT_KEY);
    } catch {
      return null;
    }
  }
}
