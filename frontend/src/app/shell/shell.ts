import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { MenuItem } from 'primeng/api';
import { AvatarModule } from 'primeng/avatar';
import { ButtonModule } from 'primeng/button';
import { MenuModule } from 'primeng/menu';
import { SelectModule } from 'primeng/select';
import { TagModule } from 'primeng/tag';

import { currentLocale, setLocale } from '../core/locale';
import { Session } from '../core/session';
import { SystemService } from '../core/system.service';
import { MENU } from './menu';

@Component({
  selector: 'forge-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    FormsModule,
    AvatarModule,
    ButtonModule,
    MenuModule,
    SelectModule,
    TagModule,
  ],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell {
  protected readonly menu = MENU;
  protected readonly system = inject(SystemService);
  protected readonly session = inject(Session);
  private readonly router = inject(Router);
  protected readonly menuOpen = signal(false);

  protected readonly initials = computed(() => {
    const u = this.session.user();
    const source = (u?.full_name || u?.username || '?').trim();
    return source
      .split(/\s+/)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() ?? '')
      .join('');
  });

  protected readonly roleLabel = computed(() =>
    this.session.user()?.is_super_admin
      ? $localize`Super Admin`
      : (this.session.project()?.role_name ?? $localize`User`),
  );

  protected readonly profileItems = computed<MenuItem[]>(() => {
    const user = this.session.user();
    const items: MenuItem[] = [];
    if (user?.auth_source === 'local') {
      items.push({
        label: $localize`Change password`,
        icon: 'pi pi-lock',
        command: () => this.go('/account/password'),
      });
    }
    items.push({
      label: $localize`Two-factor authentication`,
      icon: 'pi pi-shield',
      command: () => this.go('/account/totp'),
      disabled: user?.totp_enabled ?? false,
    });
    items.push({
      label: currentLocale() === 'it' ? 'English' : 'Italiano',
      icon: 'pi pi-globe',
      command: () => setLocale(currentLocale() === 'it' ? 'en' : 'it'),
    });
    if (user?.is_super_admin) {
      items.push({
        label: $localize`Super Admin Console`,
        icon: 'pi pi-cog',
        command: () => this.go('/admin'),
      });
    }
    items.push({ separator: true });
    items.push({
      label: $localize`Log out`,
      icon: 'pi pi-sign-out',
      command: () => void this.session.logout(),
    });
    return items;
  });

  protected toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }

  private go(url: string): void {
    this.menuOpen.set(false);
    void this.router.navigateByUrl(url);
  }
}
