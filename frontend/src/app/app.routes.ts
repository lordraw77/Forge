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
