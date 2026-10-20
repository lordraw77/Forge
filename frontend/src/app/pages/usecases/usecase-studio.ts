import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';

import { Session } from '../../core/session';

/** Build Your Own Use Case (M2.3a): the use case library and editor. Builders only. */
@Component({
  selector: 'forge-usecase-studio',
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NzAlertModule],
  template: `
    <header class="head">
      <h1 i18n>Build Your Own Use Case</h1>
    </header>
    @if (!session.project()) {
      <nz-alert
        nzType="info"
        nzMessage="You are not a member of any project yet. Ask a Super Admin to add you."
        i18n-nzMessage
      />
    } @else if (!session.can('usecase.build')) {
      <nz-alert
        nzType="warning"
        nzMessage="Your role cannot build use cases in this project."
        i18n-nzMessage
      />
    } @else {
      <nav class="tabs">
        <a routerLink="library" routerLinkActive="active" data-testid="uc-tab-library" i18n
          >Use case library</a
        >
      </nav>
      <router-outlet />
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class UseCaseStudio {
  protected readonly session = inject(Session);
}

@Component({ selector: 'forge-usecases-index', template: '' })
export class UseCasesIndex {
  constructor() {
    void inject(Router).navigate(['/build', 'library'], { replaceUrl: true });
  }
}
