import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

/** Governance and Evaluation Studio: Guardrails and Evaluations tabs. */
@Component({
  selector: 'forge-governance-shell',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  template: `
    <nav class="tabs">
      <a routerLink="guardrails" routerLinkActive="active" data-testid="tab-guardrails" i18n
        >Guardrails</a
      >
      <a routerLink="evaluations" routerLinkActive="active" data-testid="tab-evaluations" i18n
        >Evaluations</a
      >
    </nav>
    <router-outlet />
  `,
  styleUrl: '../admin/admin.scss',
})
export class GovernanceShell {}
