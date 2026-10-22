import { Routes } from '@angular/router';

import {
  authGuard,
  builderGuard,
  guestGuard,
  signedInGuard,
  superAdminGuard,
  useCaseBuilderGuard,
} from './core/guards';
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
    // Public: the one-time link of an approval email (the token is the credential).
    path: 'approve/:token',
    title: 'Approval · Forge',
    loadComponent: () => import('./pages/approvals/approve-link').then((m) => m.ApproveLink),
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
            path: 'data',
            loadComponent: () => import('./pages/projects/project-data').then((m) => m.ProjectData),
          },
          {
            path: 'sources',
            loadComponent: () =>
              import('./pages/projects/project-sources').then((m) => m.ProjectSources),
          },
          {
            path: 'sync',
            loadComponent: () => import('./pages/projects/project-sync').then((m) => m.ProjectSync),
          },
          {
            path: 'transfer',
            loadComponent: () =>
              import('./pages/projects/project-bundles').then((m) => m.ProjectBundles),
          },
          {
            path: 'data/:id',
            loadComponent: () => import('./pages/projects/project-data').then((m) => m.ProjectData),
          },
          {
            path: 'users',
            loadComponent: () =>
              import('./pages/projects/projects-studio').then((m) => m.ProjectUsers),
          },
          {
            path: 'groups',
            loadComponent: () =>
              import('./pages/projects/project-groups').then((m) => m.ProjectGroups),
          },
          {
            path: 'roles',
            loadComponent: () =>
              import('./pages/projects/projects-studio').then((m) => m.ProjectRoles),
          },
          {
            path: 'catalogs',
            loadComponent: () =>
              import('./pages/projects/project-catalogs').then((m) => m.ProjectCatalogs),
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
            path: 'auth',
            loadComponent: () => import('./pages/admin/admin-auth').then((m) => m.AdminAuth),
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
          {
            path: 'email',
            loadComponent: () => import('./pages/admin/admin-email').then((m) => m.AdminEmail),
          },
          {
            path: 'insights',
            loadComponent: () =>
              import('./pages/admin/admin-insights').then((m) => m.AdminInsights),
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
        path: 'rag',
        title: 'RAG Studio · Forge',
        loadComponent: () => import('./pages/rag/rag-studio').then((m) => m.RagStudio),
        children: [
          { path: '', pathMatch: 'full', redirectTo: 'library' },
          {
            path: 'library',
            loadComponent: () => import('./pages/rag/rag-library').then((m) => m.RagLibrary),
          },
          {
            path: 'new',
            loadComponent: () => import('./pages/rag/rag-editor').then((m) => m.RagEditor),
          },
          {
            path: ':id/edit',
            loadComponent: () => import('./pages/rag/rag-editor').then((m) => m.RagEditor),
          },
          {
            path: ':id/test',
            loadComponent: () => import('./pages/rag/rag-test').then((m) => m.RagTest),
          },
        ],
      },
      {
        path: 'agents',
        title: 'Agentic AI Studio · Forge',
        loadComponent: () => import('./pages/agents/agent-studio').then((m) => m.AgentStudio),
        children: [
          {
            path: '',
            pathMatch: 'full',
            loadComponent: () => import('./pages/agents/agent-studio').then((m) => m.AgentsIndex),
          },
          {
            path: 'library',
            canActivate: [builderGuard],
            loadComponent: () => import('./pages/agents/agent-library').then((m) => m.AgentLibrary),
          },
          {
            path: 'new',
            canActivate: [builderGuard],
            loadComponent: () => import('./pages/agents/agent-editor').then((m) => m.AgentEditor),
          },
          {
            path: 'chat',
            loadComponent: () => import('./pages/agents/agent-chat').then((m) => m.AgentChat),
          },
          {
            path: 'runs',
            loadComponent: () => import('./pages/agents/agent-runs').then((m) => m.AgentRuns),
          },
          {
            path: 'runs/:runId',
            loadComponent: () =>
              import('./pages/agents/agent-run-view').then((m) => m.AgentRunView),
          },
          {
            path: ':id/edit',
            canActivate: [builderGuard],
            loadComponent: () => import('./pages/agents/agent-editor').then((m) => m.AgentEditor),
          },
        ],
      },
      {
        path: 'tools',
        title: 'Tools Studio · Forge',
        loadComponent: () => import('./pages/tools/tool-studio').then((m) => m.ToolStudio),
        children: [
          {
            path: '',
            pathMatch: 'full',
            loadComponent: () => import('./pages/tools/tool-studio').then((m) => m.ToolsIndex),
          },
          {
            path: 'library',
            canActivate: [builderGuard],
            loadComponent: () => import('./pages/tools/tool-library').then((m) => m.ToolLibrary),
          },
          {
            path: 'new',
            canActivate: [builderGuard],
            loadComponent: () => import('./pages/tools/tool-editor').then((m) => m.ToolEditor),
          },
          {
            path: 'connections',
            canActivate: [builderGuard],
            loadComponent: () =>
              import('./pages/tools/tool-connections').then((m) => m.ToolConnections),
          },
          {
            path: 'calls',
            canActivate: [builderGuard],
            loadComponent: () => import('./pages/tools/tool-calls').then((m) => m.ToolCalls),
          },
          {
            path: ':id/edit',
            canActivate: [builderGuard],
            loadComponent: () => import('./pages/tools/tool-editor').then((m) => m.ToolEditor),
          },
        ],
      },
      {
        path: 'governance',
        title: 'Guardrails · Forge',
        loadComponent: () =>
          import('./pages/governance/guardrails-page').then((m) => m.GuardrailsPage),
      },
      {
        path: 'analytics',
        title: 'Analytics · Forge',
        loadComponent: () =>
          import('./pages/analytics/analytics-page').then((m) => m.AnalyticsPage),
      },
      {
        path: 'approvals',
        title: 'Approvals · Forge',
        loadComponent: () =>
          import('./pages/approvals/approvals-page').then((m) => m.ApprovalsPage),
      },
      {
        path: 'catalog',
        title: 'Use Cases Catalog · Forge',
        children: [
          {
            path: '',
            pathMatch: 'full',
            loadComponent: () => import('./pages/catalog/catalog-page').then((m) => m.CatalogPage),
          },
          {
            path: 'runs',
            loadComponent: () => import('./pages/catalog/catalog-runs').then((m) => m.CatalogRuns),
          },
          {
            path: 'runs/:runId',
            loadComponent: () => import('./pages/catalog/catalog-run').then((m) => m.CatalogRun),
          },
          {
            path: ':id',
            loadComponent: () =>
              import('./pages/catalog/catalog-use-case').then((m) => m.CatalogUseCasePage),
          },
        ],
      },
      {
        path: 'build',
        title: 'Build Your Own Use Case · Forge',
        loadComponent: () => import('./pages/usecases/usecase-studio').then((m) => m.UseCaseStudio),
        children: [
          {
            path: '',
            pathMatch: 'full',
            loadComponent: () =>
              import('./pages/usecases/usecase-studio').then((m) => m.UseCasesIndex),
          },
          {
            path: 'library',
            canActivate: [useCaseBuilderGuard],
            loadComponent: () =>
              import('./pages/usecases/usecase-library').then((m) => m.UseCaseLibrary),
          },
          {
            path: 'new',
            canActivate: [useCaseBuilderGuard],
            loadComponent: () =>
              import('./pages/usecases/usecase-requirements').then((m) => m.UseCaseRequirements),
          },
          {
            path: 'settings/new',
            canActivate: [useCaseBuilderGuard],
            loadComponent: () =>
              import('./pages/usecases/usecase-editor').then((m) => m.UseCaseEditor),
          },
          {
            path: ':id/workflow',
            canActivate: [useCaseBuilderGuard],
            loadComponent: () =>
              import('./pages/usecases/usecase-workflow').then((m) => m.UseCaseWorkflow),
          },
          {
            path: ':id/test',
            canActivate: [useCaseBuilderGuard],
            loadComponent: () => import('./pages/usecases/usecase-test').then((m) => m.UseCaseTest),
          },
          {
            path: ':id/edit',
            canActivate: [useCaseBuilderGuard],
            loadComponent: () =>
              import('./pages/usecases/usecase-editor').then((m) => m.UseCaseEditor),
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
      ...MENU.filter(
        (entry) =>
          ![
            'projects',
            'prompts',
            'rag',
            'agents',
            'tools',
            'build',
            'catalog',
            'governance',
            'analytics',
          ].includes(entry.path),
      ).map((entry) => ({
        path: entry.path,
        component: Placeholder,
        title: `${entry.label} · Forge`,
        data: { title: entry.label, phase: entry.phase },
      })),
    ],
  },
  { path: '**', redirectTo: '' },
];
