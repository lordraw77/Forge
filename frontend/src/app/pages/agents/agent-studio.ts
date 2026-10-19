import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';

import { Session } from '../../core/session';

/** Agentic AI Studio shell: New Agent and Agent Library for Builders, Chat for everyone who may
 * run agents, Runs for those who may read runs (spec Part 2, Interface; E1.18 to E1.20). */
@Component({
  selector: 'forge-agent-studio',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NzAlertModule],
  template: `
    <header class="head">
      <h1 i18n>Agentic AI Studio</h1>
    </header>
    @if (!session.project()) {
      <nz-alert
        nzType="info"
        nzMessage="You are not a member of any project yet. Ask a Super Admin to add you."
        i18n-nzMessage
      />
    } @else {
      <nav class="tabs">
        @if (session.can('asset.write')) {
          <a routerLink="new" routerLinkActive="active" data-testid="agent-tab-new" i18n
            >New Agent</a
          >
          <a routerLink="library" routerLinkActive="active" data-testid="agent-tab-library" i18n
            >Agent Library</a
          >
        }
        @if (session.can('usecase.run')) {
          <a routerLink="chat" routerLinkActive="active" data-testid="agent-tab-chat" i18n>Chat</a>
        }
        @if (session.can('run.read') || session.can('run.read.own')) {
          <a routerLink="runs" routerLinkActive="active" data-testid="agent-tab-runs" i18n>Runs</a>
        }
      </nav>
      <router-outlet />
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class AgentStudio {
  protected readonly session = inject(Session);
}

/** `/agents` goes to the first tab the user may open. */
@Component({ selector: 'forge-agents-index', template: '' })
export class AgentsIndex {
  constructor() {
    const session = inject(Session);
    const target = session.can('asset.write')
      ? 'library'
      : session.can('usecase.run')
        ? 'chat'
        : 'runs';
    void inject(Router).navigate(['/agents', target], { replaceUrl: true });
  }
}
