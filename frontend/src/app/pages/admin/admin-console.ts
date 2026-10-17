import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

/** Super Admin Console container (Part 2: Users, Projects, Role Types…; spec Admin Console). */
@Component({
  selector: 'forge-admin-console',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  template: `
    <nav class="tabs" aria-label="Super Admin Console" i18n-aria-label>
      <a routerLink="users" routerLinkActive="active" i18n>Users</a>
      <a routerLink="projects" routerLinkActive="active" i18n>Projects</a>
      <a routerLink="settings" routerLinkActive="active" i18n>Settings</a>
      <a routerLink="audit" routerLinkActive="active" i18n>Audit log</a>
    </nav>
    <router-outlet />
  `,
  styleUrl: './admin.scss',
})
export class AdminConsole {}
