import { Component } from '@angular/core';

import { ModelRegistry } from '../models/model-registry';

/** Super Admin console → Model providers (spec Admin Console; US-1.9.1). */
@Component({
  selector: 'forge-admin-models',
  imports: [ModelRegistry],
  template: `
    <header class="head"><h1 i18n>Model providers</h1></header>
    <p class="intro" i18n>
      External models available to the platform. Enable each model for the projects that may use it;
      aliases defined here are the defaults of every project.
    </p>
    <forge-model-registry />
  `,
  styleUrl: './admin.scss',
})
export class AdminModels {}
