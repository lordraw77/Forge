import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { ActivityItem, AnalyticsService } from '../../api-client';
import { Session } from '../../core/session';

/** Home (spec Part 2): greeting, two main actions, recent activity of the last 15 days. */
@Component({
  selector: 'forge-home',
  imports: [DatePipe, RouterLink, NzButtonModule, NzCardModule, NzIconModule, NzTagModule],
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
      @if (items().length) {
        <div class="tiles">
          @for (a of items().slice(0, 4); track a.ref_id) {
            <nz-card nzSize="small" [nzTitle]="a.title">
              <p class="empty">{{ a.at | date: 'medium' }} · {{ a.project_name }}</p>
              <nz-tag>{{ a.status }}</nz-tag>
            </nz-card>
          }
        </div>
        @if (items().length > 4) {
          <details>
            <summary i18n>View All</summary>
            @for (a of items().slice(4); track a.ref_id) {
              <p>
                {{ a.at | date: 'short' }} · {{ a.title }} · {{ a.project_name }}
                <nz-tag>{{ a.status }}</nz-tag>
              </p>
            }
          </details>
        }
      } @else {
        <p class="empty" i18n>No Recent Activity</p>
      }
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
    .tiles {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }
    .empty {
      color: var(--forge-text-muted-color);
    }
  `,
})
export class Home implements OnInit {
  private readonly session = inject(Session);
  private readonly api = inject(AnalyticsService);
  protected readonly items = signal<ActivityItem[]>([]);
  protected readonly userName = computed(
    () => this.session.user()?.full_name || this.session.user()?.username || '',
  );

  async ngOnInit(): Promise<void> {
    try {
      this.items.set(await firstValueFrom(this.api.myActivity({ days: 15, limit: 50 })));
    } catch {
      // the home page works without the activity list
    }
  }
}
