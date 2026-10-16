import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AvatarModule } from 'primeng/avatar';
import { ButtonModule } from 'primeng/button';
import { SelectModule } from 'primeng/select';
import { TagModule } from 'primeng/tag';

import { SystemService } from '../core/system.service';
import { MENU } from './menu';

interface ProjectOption {
  id: string;
  name: string;
}

@Component({
  selector: 'forge-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    FormsModule,
    AvatarModule,
    ButtonModule,
    SelectModule,
    TagModule,
  ],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell {
  protected readonly menu = MENU;
  protected readonly system = inject(SystemService);
  protected readonly menuOpen = signal(false);

  // Placeholder until IAM and tenancy (E1.1, E1.2) provide /api/v1/me and the project list.
  protected readonly user = {
    name: 'Forge Admin',
    initials: 'FA',
    role: 'Super Admin',
    email: 'admin@example.com',
  };
  protected readonly projects: ProjectOption[] = [{ id: 'default', name: 'Default' }];
  protected project = this.projects[0];

  protected toggleMenu(): void {
    this.menuOpen.update((open) => !open);
  }
}
