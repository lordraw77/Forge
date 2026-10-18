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
            path: 'models',
            loadComponent: () =>
              import('./pages/projects/project-models').then((m) => m.ProjectModels),
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
            path: 'models',
            loadComponent: () => import('./pages/admin/admin-models').then((m) => m.AdminModels),
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
        path: 'prompts',
        title: 'Prompt Studio · Forge',
        loadComponent: () => import('./pages/prompts/prompt-studio').then((m) => m.PromptStudio),
        children: [
          { path: '', pathMatch: 'full', redirectTo: 'library' },
          {
            path: 'library',
            loadComponent: () =>
              import('./pages/prompts/prompt-library').then((m) => m.PromptLibrary),
          },
          {
            path: 'new',
            loadComponent: () =>
              import('./pages/prompts/prompt-editor').then((m) => m.PromptEditor),
          },
          {
            path: ':id/edit',
            loadComponent: () =>
              import('./pages/prompts/prompt-editor').then((m) => m.PromptEditor),
          },
          {
            path: ':id/test',
            loadComponent: () => import('./pages/prompts/prompt-test').then((m) => m.PromptTest),
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
      ...MENU.filter((entry) => !['projects', 'prompts'].includes(entry.path)).map((entry) => ({
        path: entry.path,
        component: Placeholder,
        title: `${entry.label} · Forge`,
        data: { title: entry.label, phase: entry.phase },
      })),
    ],
  },
  { path: '**', redirectTo: '' },
];
