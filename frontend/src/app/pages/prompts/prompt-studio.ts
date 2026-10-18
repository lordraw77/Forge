import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';

import { Session } from '../../core/session';

/** Prompt Studio shell: "New" and "Library" tabs (spec Part 2, Interface). */
@Component({
  selector: 'forge-prompt-studio',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NzAlertModule],
  template: `
    <header class="head">
      <h1 i18n>Prompt Studio</h1>
    </header>
    @if (!session.project()) {
      <nz-alert
        nzType="info"
        nzMessage="You are not a member of any project yet. Ask a Super Admin to add you."
        i18n-nzMessage
      />
    } @else if (!session.can('asset.write')) {
      <nz-alert
        nzType="warning"
        nzMessage="Prompt Studio needs the Builder or Project Admin role in this project."
        i18n-nzMessage
      />
    } @else {
      <nav class="tabs">
        <a routerLink="new" routerLinkActive="active" data-testid="tab-new" i18n>New Prompt</a>
        <a routerLink="library" routerLinkActive="active" data-testid="tab-library" i18n
          >Prompt Library</a
        >
      </nav>
      <router-outlet />
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class PromptStudio {
  protected readonly session = inject(Session);
}
