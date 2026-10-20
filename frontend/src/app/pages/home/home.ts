import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';

import { Session } from '../../core/session';

/** Home (spec Part 2): greeting, two main actions, recent activity of the last 15 days. */
@Component({
  selector: 'forge-home',
  imports: [RouterLink, NzButtonModule, NzCardModule, NzIconModule],
  template: `
    <section class="hero">
      <h1 i18n>Welcome, {{ userName() }}</h1>
      <p i18n>Build, publish and run generative and agentic AI solutions.</p>
      <div class="actions">
        <a nz-button nzType="primary" routerLink="/catalog" data-testid="home-catalog">
          <span nz-icon nzType="appstore"></span><span i18n>Explore Use Cases Catalog</span>
        </a>
        <a nz-button routerLink="/build" data-testid="home-build">
          <span nz-icon nzType="apartment"></span><span i18n>Build Your Own Use Case</span>
        </a>
      </div>
    </section>

    <nz-card nzTitle="Recent activity" i18n-nzTitle>
      <p class="empty" i18n>No Recent Activity</p>
    </nz-card>
  `,
  styles: `
    .hero {
      margin-bottom: 1.5rem;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
    }
    .empty {
      color: var(--forge-text-muted-color);
    }
  `,
})
export class Home {
  private readonly session = inject(Session);
  protected readonly userName = computed(
    () => this.session.user()?.full_name || this.session.user()?.username || '',
  );
}
