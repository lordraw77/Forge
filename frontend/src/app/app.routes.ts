import { Routes } from '@angular/router';

import { authGuard, guestGuard, signedInGuard, superAdminGuard } from './core/guards';
import { ChangePassword, Eula, TotpEnroll } from './pages/account/account-steps';
import { Home } from './pages/home/home';
import { Login } from './pages/login/login';
import { Placeholder } from './pages/placeholder/placeholder';
import { MENU } from './shell/menu';
import { Shell } from './shell/shell';

export const routes: Routes = [
  { path: 'login', component: Login, canActivate: [guestGuard], title: 'Sign in · Forge' },
  {
    path: 'account',
    canActivate: [signedInGuard],
    children: [
      { path: 'password', component: ChangePassword, title: 'Change password · Forge' },
      { path: 'eula', component: Eula, title: 'Terms of use · Forge' },
      { path: 'totp', component: TotpEnroll, title: 'Two-factor authentication · Forge' },
    ],
  },
  {
    path: '',
    component: Shell,
    canActivate: [authGuard],
    children: [
      { path: '', component: Home, title: 'Forge' },
      {
        path: 'projects',
        title: 'Projects Studio · Forge',
        loadComponent: () =>
          import('./pages/projects/projects-studio').then((m) => m.ProjectsStudio),
        children: [
          { path: '', pathMatch: 'full', redirectTo: 'info' },
          {
            path: 'info',
            loadComponent: () =>
              import('./pages/projects/projects-studio').then((m) => m.ProjectInfo),
          },
          {
            path: 'users',
            loadComponent: () =>
              import('./pages/projects/projects-studio').then((m) => m.ProjectUsers),
          },
          {
            path: 'roles',
            loadComponent: () =>
              import('./pages/projects/projects-studio').then((m) => m.ProjectRoles),
          },
        ],
      },
      {
        path: 'admin',
        canActivate: [superAdminGuard],
        title: 'Super Admin Console · Forge',
        loadComponent: () => import('./pages/admin/admin-console').then((m) => m.AdminConsole),
        children: [
          { path: '', pathMatch: 'full', redirectTo: 'users' },
          {
            path: 'users',
            loadComponent: () => import('./pages/admin/admin-users').then((m) => m.AdminUsers),
          },
          {
            path: 'projects',
            loadComponent: () =>
              import('./pages/admin/admin-projects').then((m) => m.AdminProjects),
          },
          {
            path: 'settings',
            loadComponent: () =>
              import('./pages/admin/admin-settings').then((m) => m.AdminSettings),
          },
          {
            path: 'audit',
            loadComponent: () => import('./pages/admin/admin-audit').then((m) => m.AdminAudit),
          },
        ],
      },
      {
        // Phase 0 spike E0.5c, not in the menu: graph use-case editor on Foblex Flow.
        path: 'spikes/graph-editor',
        title: 'Graph editor spike · Forge',
        loadComponent: () =>
          import('./spikes/graph-editor/graph-editor').then((m) => m.GraphEditor),
      },
      ...MENU.filter((entry) => entry.path !== 'projects').map((entry) => ({
        path: entry.path,
        component: Placeholder,
        title: `${entry.label} · Forge`,
        data: { title: entry.label, phase: entry.phase },
      })),
    ],
  },
  { path: '**', redirectTo: '' },
];
