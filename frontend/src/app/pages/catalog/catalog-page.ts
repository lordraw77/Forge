import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzAvatarModule } from 'ng-zorro-antd/avatar';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzEmptyModule } from 'ng-zorro-antd/empty';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { CatalogUseCase, UseCasesService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { CatalogView, filterCatalog } from './catalog-util';

/** Use Cases Catalog (US-2.9.1): the use cases the user's Role Name may run, as cards with
 * search, category and tag filters, favorites and recently used. */
@Component({
  selector: 'forge-catalog',
  imports: [
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzAvatarModule,
    NzButtonModule,
    NzCardModule,
    NzEmptyModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzTagModule,
  ],
  template: `
    <header class="head">
      <h1 i18n>Use Cases Catalog</h1>
      <span class="spacer"></span>
      @if (session.can('approval.decide')) {
        <a nz-button routerLink="/approvals" data-testid="catalog-approvals-link" i18n>Approvals</a>
      }
      <a nz-button routerLink="/catalog/runs" data-testid="catalog-runs-link" i18n>My runs</a>
    </header>
    @if (!session.project()) {
      <nz-alert
        nzType="info"
        nzMessage="You are not a member of any project yet. Ask a Super Admin to add you."
        i18n-nzMessage
      />
    } @else if (!session.can('usecase.run')) {
      <nz-alert
        nzType="warning"
        nzMessage="Your role cannot run use cases in this project."
        i18n-nzMessage
      />
    } @else {
      @if (error(); as e) {
        <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
      }
      <div class="toolbar">
        <div class="views" data-testid="catalog-views">
          @for (v of views; track v.id) {
            <button
              nz-button
              [nzType]="view() === v.id ? 'primary' : 'default'"
              (click)="view.set(v.id)"
            >
              {{ v.label }}
            </button>
          }
        </div>
        <input
          nz-input
          class="search"
          placeholder="Search use cases"
          i18n-placeholder
          [ngModel]="q()"
          (ngModelChange)="q.set($event)"
          data-testid="catalog-search"
        />
        <nz-select
          class="filter"
          nzAllowClear
          nzPlaceHolder="Category"
          i18n-nzPlaceHolder
          [ngModel]="categoryId()"
          (ngModelChange)="categoryId.set($event)"
          data-testid="catalog-category"
        >
          @for (c of categories(); track c.id) {
            <nz-option [nzValue]="c.id" [nzLabel]="c.label" />
          }
        </nz-select>
        <nz-select
          class="filter"
          nzAllowClear
          nzPlaceHolder="Tag"
          i18n-nzPlaceHolder
          [ngModel]="tag()"
          (ngModelChange)="tag.set($event)"
          data-testid="catalog-tag"
        >
          @for (t of tags(); track t) {
            <nz-option [nzValue]="t" [nzLabel]="t" />
          }
        </nz-select>
      </div>

      @if (!loading() && !items().length) {
        <nz-empty
          nzNotFoundContent="No use case is available for your role yet. Ask a Project Admin to enable a catalog for it."
          i18n-nzNotFoundContent
          data-testid="catalog-empty"
        />
      } @else if (!loading() && !shown().length) {
        <nz-empty nzNotFoundContent="Nothing matches" i18n-nzNotFoundContent />
      }
      <div class="grid" data-testid="catalog-grid">
        @for (u of shown(); track u.id) {
          <nz-card
            class="card"
            [attr.data-testid]="'uc-card-' + u.name"
            (click)="open(u)"
            (keydown.enter)="open(u)"
            tabindex="0"
            role="link"
          >
            <div class="top">
              <nz-avatar [nzText]="u.name.slice(0, 1).toUpperCase()" nzShape="square" />
              <strong class="name">{{ u.name }}</strong>
              <button
                nz-button
                nzType="text"
                nzSize="small"
                class="star"
                [attr.aria-label]="u.favorite ? 'Remove from favorites' : 'Add to favorites'"
                [attr.data-testid]="'fav-' + u.name"
                (click)="toggle(u, $event)"
              >
                <nz-icon nzType="star" [nzTheme]="u.favorite ? 'fill' : 'outline'" />
              </button>
            </div>
            <p class="desc">{{ u.description }}</p>
            <div class="meta">
              <nz-tag>{{ u.category }}</nz-tag>
              @for (t of u.tags; track t) {
                <nz-tag nzColor="blue">{{ t }}</nz-tag>
              }
            </div>
          </nz-card>
        }
      </div>
    }
  `,
  styles: `
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      margin-bottom: 1rem;
    }
    .views {
      display: flex;
      gap: 0.25rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
      gap: 1rem;
    }
    .card {
      cursor: pointer;
    }
    .top {
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .desc {
      min-height: 3rem;
      margin: 0.6rem 0;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class CatalogPage {
  private readonly api = inject(UseCasesService);
  private readonly router = inject(Router);
  protected readonly session = inject(Session);

  protected readonly items = signal<CatalogUseCase[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly q = signal('');
  protected readonly categoryId = signal<string | null>(null);
  protected readonly tag = signal<string | null>(null);
  protected readonly view = signal<CatalogView>('all');
  protected readonly views: { id: CatalogView; label: string }[] = [
    { id: 'all', label: $localize`All` },
    { id: 'favorites', label: $localize`Favorites` },
    { id: 'recent', label: $localize`Recent` },
  ];

  protected readonly shown = computed(() =>
    filterCatalog(this.items(), this.q(), this.categoryId(), this.tag(), this.view()),
  );
  protected readonly categories = computed(() => {
    const seen = new Map<string, string>();
    for (const i of this.items()) seen.set(i.category_id, `${i.catalog} · ${i.category}`);
    return [...seen]
      .map(([id, label]) => ({ id, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  });
  protected readonly tags = computed(() =>
    [...new Set(this.items().flatMap((i) => i.tags))].sort(),
  );

  constructor() {
    effect(() => {
      if (this.session.project() && this.session.can('usecase.run')) void this.load();
    });
  }

  private async load(): Promise<void> {
    const projectId = this.session.project()!.id;
    this.loading.set(true);
    try {
      this.items.set(await firstValueFrom(this.api.catalogUseCases({ projectId })));
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  protected open(u: CatalogUseCase): void {
    void this.router.navigate(['/catalog', u.id]);
  }

  protected async toggle(u: CatalogUseCase, event: Event): Promise<void> {
    event.stopPropagation();
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    const next = !u.favorite;
    this.items.update((list) => list.map((i) => (i.id === u.id ? { ...i, favorite: next } : i)));
    try {
      await firstValueFrom(
        next
          ? this.api.addFavorite({ projectId, useCaseId: u.id })
          : this.api.removeFavorite({ projectId, useCaseId: u.id }),
      );
    } catch (e) {
      this.items.update((list) => list.map((i) => (i.id === u.id ? { ...i, favorite: !next } : i)));
      this.error.set(problemMessage(e));
    }
  }
}
