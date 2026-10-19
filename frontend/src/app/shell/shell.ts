import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NzAvatarModule } from 'ng-zorro-antd/avatar';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzDropdownModule } from 'ng-zorro-antd/dropdown';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzMenuModule } from 'ng-zorro-antd/menu';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';

import { setThemeMode, ThemeMode, themeMode } from '../core/theme-mode';
import { currentLocale, setLocale } from '../core/locale';
import { Session } from '../core/session';
import { SystemService } from '../core/system.service';
import { MENU } from './menu';

interface ProfileItem {
  label: string;
  icon: string;
  run: () => void;
  disabled?: boolean;
  divider?: boolean;
}

@Component({
  selector: 'forge-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    FormsModule,
    NzAvatarModule,
    NzButtonModule,
    NzDropdownModule,
    NzIconModule,
    NzMenuModule,
    NzSelectModule,
    NzTagModule,
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

  protected readonly profileItems = computed<ProfileItem[]>(() => {
    const user = this.session.user();
    const items: ProfileItem[] = [];
    if (user?.auth_source === 'local') {
      items.push({
        label: $localize`Change password`,
        icon: 'lock',
        run: () => this.go('/account/password'),
      });
    }
    items.push({
      label: $localize`Two-factor authentication`,
      icon: 'safety-certificate',
      run: () => this.go('/account/totp'),
      disabled: user?.totp_enabled ?? false,
    });
    const mode = themeMode();
    const next: ThemeMode = mode === 'light' ? 'dark' : mode === 'dark' ? 'system' : 'light';
    const names: Record<ThemeMode, string> = {
      light: $localize`Light`,
      dark: $localize`Dark`,
      system: $localize`System`,
    };
    items.push({
      label: $localize`Theme: ${names[mode]}:mode: → ${names[next]}:next:`,
      icon: 'bg-colors',
      run: () => setThemeMode(next),
    });
    items.push({
      label: currentLocale() === 'it' ? 'English' : 'Italiano',
      icon: 'global',
      run: () => setLocale(currentLocale() === 'it' ? 'en' : 'it'),
    });
    if (user?.is_super_admin) {
      items.push({
        label: $localize`Super Admin Console`,
        icon: 'setting',
        run: () => this.go('/admin'),
      });
    }
    items.push({
      label: $localize`Log out`,
      icon: 'logout',
      run: () => void this.session.logout(),
      divider: true,
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
