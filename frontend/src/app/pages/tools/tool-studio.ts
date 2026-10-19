import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';

import { Session } from '../../core/session';

/** Tools Studio shell (M2.2): Library, Connections (credentials, egress policy, data sources,
 * MCP servers) and the call log. Builders only: tools need `asset.write`. */
@Component({
  selector: 'forge-tool-studio',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NzAlertModule],
  template: `
    <header class="head">
      <h1 i18n>Tools Studio</h1>
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
        nzMessage="Your role cannot manage tools in this project."
        i18n-nzMessage
      />
    } @else {
      <nav class="tabs">
        <a routerLink="library" routerLinkActive="active" data-testid="tool-tab-library" i18n
          >Tool Library</a
        >
        <a
          routerLink="connections"
          routerLinkActive="active"
          data-testid="tool-tab-connections"
          i18n
          >Connections</a
        >
        <a routerLink="calls" routerLinkActive="active" data-testid="tool-tab-calls" i18n
          >Call log</a
        >
      </nav>
      <router-outlet />
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class ToolStudio {
  protected readonly session = inject(Session);
}

@Component({ selector: 'forge-tools-index', template: '' })
export class ToolsIndex {
  constructor() {
    void inject(Router).navigate(['/tools', 'library'], { replaceUrl: true });
  }
}
