import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  CatalogAccessItem,
  CatalogOut,
  ProjectsService,
  RoleOut,
  UseCasesService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** Catalogs, categories and which Role Names see them (US-2.4.1, 2.4.2). Project Admins only. */
@Component({
  selector: 'forge-project-catalogs',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzTagModule,
  ],
  template: `
    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }

    <h3 i18n>Catalogs</h3>
    <p class="intro" i18n>
      Use cases are published into a category of a catalog. The built-in catalogs can be disabled
      but not renamed or deleted.
    </p>
    <div class="catalogs" data-testid="catalog-list">
      @for (c of catalogs(); track c.id) {
        <section class="catalog" [attr.data-testid]="'catalog-' + c.name">
          <header>
            <strong>{{ c.name }}</strong>
            @if (c.builtin) {
              <nz-tag i18n>Built-in</nz-tag>
            }
            @if (c.status === 'disabled') {
              <nz-tag nzColor="warning" i18n>Disabled</nz-tag>
            }
            <span class="muted small">{{ c.description }}</span>
            <span class="spacer"></span>
            <button nz-button nzSize="small" (click)="toggle(c)">
              {{ c.status === 'active' ? disableLabel : enableLabel }}
            </button>
            @if (!c.builtin) {
              <button nz-button nzSize="small" nzDanger (click)="removeCatalog(c)" i18n>
                Delete
              </button>
            }
          </header>
          <ul>
            @for (k of c.categories; track k.id) {
              <li>
                {{ k.name }}
                <span class="muted small" i18n>{{ k.use_cases }} use cases</span>
                <button
                  nz-button
                  nzType="link"
                  nzSize="small"
                  (click)="renameCategory(k.id, k.name)"
                  i18n
                >
                  Rename
                </button>
                <button
                  nz-button
                  nzType="link"
                  nzDanger
                  nzSize="small"
                  (click)="removeCategory(k.id, k.name)"
                  i18n
                >
                  Delete
                </button>
              </li>
            } @empty {
              <li class="muted" i18n>No categories yet.</li>
            }
          </ul>
          <div class="row">
            <input
              nz-input
              [attr.aria-label]="categoryLabel"
              [placeholder]="categoryLabel"
              [name]="'new-cat-' + c.id"
              [ngModel]="newCategory()[c.id] ?? ''"
              (ngModelChange)="setNewCategory(c.id, $event)"
              [attr.data-testid]="'new-category-' + c.name"
            />
            <button
              nz-button
              (click)="addCategory(c)"
              [disabled]="!(newCategory()[c.id] ?? '').trim()"
              i18n
            >
              Add category
            </button>
          </div>
        </section>
      }
    </div>
    <div class="row add">
      <input
        nz-input
        placeholder="New catalog name"
        i18n-placeholder
        aria-label="New catalog name"
        i18n-aria-label
        data-testid="new-catalog-name"
        [(ngModel)]="newCatalog"
      />
      <button
        nz-button
        nzType="primary"
        (click)="addCatalog()"
        [disabled]="!newCatalog.trim()"
        data-testid="add-catalog"
        i18n
      >
        Add catalog
      </button>
    </div>

    <h3 i18n>Access by role</h3>
    <p class="intro" i18n>
      Choose a role, then the catalogs its members can see. Enabling a catalog enables all its
      categories; untick the ones to hide. Builders and Project Admins always see everything.
    </p>
    <div class="row">
      <nz-select
        class="filter"
        nzId="access-role"
        aria-label="Role"
        i18n-aria-label
        [(ngModel)]="roleId"
        (ngModelChange)="loadAccess()"
      >
        @for (r of roles(); track r.id) {
          <nz-option [nzValue]="r.id" [nzLabel]="r.name" />
        }
      </nz-select>
    </div>
    @if (access().length) {
      <div class="access" data-testid="access-matrix">
        @for (c of catalogs(); track c.id) {
          <div class="access-row">
            <label
              nz-checkbox
              [nzId]="'access-' + c.id"
              [ngModel]="item(c.id).enabled"
              (ngModelChange)="setEnabled(c.id, $event)"
              [attr.data-testid]="'access-catalog-' + c.name"
              >{{ c.name }}</label
            >
            @if (item(c.id).enabled) {
              <div class="access-cats">
                @for (k of c.categories; track k.id) {
                  <label
                    nz-checkbox
                    [nzId]="'access-' + k.id"
                    [ngModel]="!isExcluded(c.id, k.id)"
                    (ngModelChange)="setCategory(c.id, k.id, $event)"
                    >{{ k.name }}</label
                  >
                }
              </div>
            }
          </div>
        }
      </div>
      <div class="buttons">
        <button nz-button nzType="primary" (click)="saveAccess()" data-testid="save-access" i18n>
          Save access
        </button>
      </div>
    }
  `,
  styles: `
    .catalog {
      border: 1px solid var(--forge-surface-200);
      border-radius: 6px;
      padding: 0.75rem;
      margin-bottom: 0.75rem;
    }
    .catalog header {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .catalog ul {
      margin: 0.5rem 0;
      padding-left: 1.25rem;
    }
    .access-row {
      margin-bottom: 0.5rem;
    }
    .access-cats {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin: 0.25rem 0 0 1.5rem;
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class ProjectCatalogs {
  private readonly api = inject(UseCasesService);
  private readonly projects = inject(ProjectsService);
  private readonly session = inject(Session);

  private readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly catalogs = signal<CatalogOut[]>([]);
  protected readonly roles = signal<RoleOut[]>([]);
  protected readonly access = signal<CatalogAccessItem[]>([]);
  protected readonly newCategory = signal<Record<string, string>>({});
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected newCatalog = '';
  protected roleId = '';

  protected readonly disableLabel = $localize`Disable`;
  protected readonly enableLabel = $localize`Enable`;
  protected readonly categoryLabel = $localize`New category name`;

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
  }

  private async load(): Promise<void> {
    const projectId = this.projectId()!;
    await this.reload();
    const roles = (await firstValueFrom(this.projects.listRoles({ projectId }))).filter(
      (r) => r.status === 'active',
    );
    this.roles.set(roles);
    this.roleId = roles.find((r) => r.role_type === 'end_user')?.id ?? roles[0]?.id ?? '';
    await this.loadAccess();
  }

  private async reload(): Promise<void> {
    this.catalogs.set(
      await firstValueFrom(this.api.listCatalogs({ projectId: this.projectId()! })),
    );
  }

  protected setNewCategory(id: string, value: string): void {
    this.newCategory.update((m) => ({ ...m, [id]: value }));
  }

  private async act(action: () => Promise<void>): Promise<void> {
    this.message.set(null);
    try {
      await action();
      await this.reload();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async addCatalog(): Promise<void> {
    await this.act(async () => {
      await firstValueFrom(
        this.api.createCatalog({
          projectId: this.projectId()!,
          catalogIn: { name: this.newCatalog.trim() },
        }),
      );
      this.newCatalog = '';
      await this.loadAccess();
    });
  }

  protected async toggle(c: CatalogOut): Promise<void> {
    await this.act(async () => {
      await firstValueFrom(
        this.api.updateCatalog({
          projectId: this.projectId()!,
          catalogId: c.id,
          catalogIn: {
            name: c.name,
            description: c.description,
            status: c.status === 'active' ? 'disabled' : 'active',
          },
        }),
      );
    });
  }

  protected async removeCatalog(c: CatalogOut): Promise<void> {
    if (!confirm($localize`Delete catalog ${c.name}:name:?`)) return;
    await this.act(async () => {
      await firstValueFrom(
        this.api.deleteCatalog({ projectId: this.projectId()!, catalogId: c.id }),
      );
      await this.loadAccess();
    });
  }

  protected async addCategory(c: CatalogOut): Promise<void> {
    const name = (this.newCategory()[c.id] ?? '').trim();
    await this.act(async () => {
      await firstValueFrom(
        this.api.createCategory({
          projectId: this.projectId()!,
          catalogId: c.id,
          categoryIn: { name },
        }),
      );
      this.setNewCategory(c.id, '');
    });
  }

  protected async renameCategory(id: string, current: string): Promise<void> {
    const name = prompt($localize`New name for category ${current}:name:`, current)?.trim();
    if (!name || name === current) return;
    await this.act(async () => {
      await firstValueFrom(
        this.api.updateCategory({
          projectId: this.projectId()!,
          categoryId: id,
          categoryIn: { name },
        }),
      );
    });
  }

  protected async removeCategory(id: string, name: string): Promise<void> {
    if (!confirm($localize`Delete category ${name}:name:?`)) return;
    await this.act(async () => {
      await firstValueFrom(
        this.api.deleteCategory({ projectId: this.projectId()!, categoryId: id }),
      );
      await this.loadAccess();
    });
  }

  // ---- access
  protected async loadAccess(): Promise<void> {
    if (!this.roleId) return;
    try {
      const out = await firstValueFrom(
        this.api.getCatalogAccess({ projectId: this.projectId()!, roleNameId: this.roleId }),
      );
      this.access.set(
        out.catalogs.map((c) => ({ ...c, excluded_categories: c.excluded_categories ?? [] })),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected item(catalogId: string): CatalogAccessItem {
    return (
      this.access().find((a) => a.catalog_id === catalogId) ?? {
        catalog_id: catalogId,
        enabled: false,
        excluded_categories: [],
      }
    );
  }

  private update(catalogId: string, change: (i: CatalogAccessItem) => CatalogAccessItem): void {
    const known = this.access().some((a) => a.catalog_id === catalogId);
    const next = known ? this.access() : [...this.access(), this.item(catalogId)];
    this.access.set(next.map((a) => (a.catalog_id === catalogId ? change(a) : a)));
  }

  protected setEnabled(catalogId: string, enabled: boolean): void {
    this.update(catalogId, (i) => ({ ...i, enabled, excluded_categories: [] }));
  }

  protected isExcluded(catalogId: string, categoryId: string): boolean {
    return (this.item(catalogId).excluded_categories ?? []).includes(categoryId);
  }

  protected setCategory(catalogId: string, categoryId: string, shown: boolean): void {
    this.update(catalogId, (i) => {
      const rest = (i.excluded_categories ?? []).filter((k) => k !== categoryId);
      return { ...i, excluded_categories: shown ? rest : [...rest, categoryId] };
    });
  }

  protected async saveAccess(): Promise<void> {
    this.message.set(null);
    try {
      await firstValueFrom(
        this.api.setCatalogAccess({
          projectId: this.projectId()!,
          roleNameId: this.roleId,
          catalogAccessIn: { catalogs: this.access() },
        }),
      );
      this.message.set({ ok: true, text: $localize`Access saved` });
      await this.loadAccess();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }
}
