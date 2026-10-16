import { Routes } from '@angular/router';

import { Home } from './pages/home/home';
import { Placeholder } from './pages/placeholder/placeholder';
import { MENU } from './shell/menu';
import { Shell } from './shell/shell';

export const routes: Routes = [
  {
    path: '',
    component: Shell,
    children: [
      { path: '', component: Home, title: 'Forge' },
      {
        // Phase 0 spike E0.5c, not in the menu: graph use-case editor on Foblex Flow.
        path: 'spikes/graph-editor',
        title: 'Graph editor spike · Forge',
        loadComponent: () =>
          import('./spikes/graph-editor/graph-editor').then((m) => m.GraphEditor),
      },
      ...MENU.map((entry) => ({
        path: entry.path,
        component: Placeholder,
        title: `${entry.label} · Forge`,
        data: { title: entry.label, phase: entry.phase },
      })),
    ],
  },
  { path: '**', redirectTo: '' },
];
