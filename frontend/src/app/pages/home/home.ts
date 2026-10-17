import { Component, computed, inject } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';

import { Session } from '../../core/session';

/** Home (spec Part 2): greeting, two main actions, recent activity of the last 15 days. */
@Component({
  selector: 'forge-home',
  imports: [ButtonModule, CardModule],
  template: `
    <section class="hero">
      <h1 i18n>Welcome, {{ userName() }}</h1>
      <p i18n>Build, publish and run generative and agentic AI solutions.</p>
      <div class="actions">
        <p-button
          label="Explore Use Cases Catalog"
          i18n-label
          icon="pi pi-th-large"
          [disabled]="true"
        />
        <p-button
          label="Build Your Own Use Case"
          i18n-label
          icon="pi pi-sitemap"
          severity="secondary"
          [disabled]="true"
        />
      </div>
    </section>

    <p-card header="Recent activity" i18n-header>
      <p class="empty" i18n>No Recent Activity</p>
    </p-card>
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
      color: var(--p-text-muted-color);
    }
  `,
})
export class Home {
  private readonly session = inject(Session);
  protected readonly userName = computed(
    () => this.session.user()?.full_name || this.session.user()?.username || '',
  );
}
