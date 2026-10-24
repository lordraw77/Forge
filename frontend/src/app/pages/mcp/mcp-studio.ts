import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';

import { Session } from '../../core/session';

/** MCP Studio shell (E3.5): what the project's MCP server offers (Exposed), how an external
 * client connects (Connect) and a console that talks to the server like a client does (Try).
 * Builders only: the list needs `asset.write`. */
@Component({
  selector: 'forge-mcp-studio',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NzAlertModule],
  template: `
    <header class="head">
      <h1 i18n>MCP Studio</h1>
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
        nzMessage="Your role cannot manage the MCP server in this project."
        i18n-nzMessage
      />
    } @else {
      <nav class="tabs">
        <a routerLink="exposed" routerLinkActive="active" data-testid="mcp-tab-exposed" i18n
          >Exposed</a
        >
        <a routerLink="connect" routerLinkActive="active" data-testid="mcp-tab-connect" i18n
          >Connect</a
        >
        <a routerLink="try" routerLinkActive="active" data-testid="mcp-tab-try" i18n>Try</a>
      </nav>
      <router-outlet />
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class McpStudio {
  protected readonly session = inject(Session);
}

@Component({ selector: 'forge-mcp-index', template: '' })
export class McpIndex {
  constructor() {
    void inject(Router).navigate(['/mcp', 'exposed'], { replaceUrl: true });
  }
}
