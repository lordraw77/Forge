import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { UseCaseOut, UseCasesService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Status = 'draft' | 'testing' | 'published' | 'deprecated';
const STATUS_COLOR: Record<string, string> = {
  draft: 'default',
  testing: 'processing',
  published: 'success',
  deprecated: 'warning',
};

/** Use case library: filters, Edit, Duplicate, Delete (US-2.5.1). */
@Component({
  selector: 'forge-usecase-library',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <input
        nz-input
        class="search"
        placeholder="Search by name"
        i18n-placeholder
        aria-label="Search by name"
        i18n-aria-label
        data-testid="uc-search"
        [(ngModel)]="query"
        (ngModelChange)="reload()"
      />
      <nz-select
        class="filter-status"
        nzId="uc-status"
        aria-label="Status"
        i18n-aria-label
        [(ngModel)]="status"
        (ngModelChange)="reload()"
      >
        <nz-option [nzValue]="null" nzLabel="All statuses" i18n-nzLabel />
        <nz-option nzValue="draft" nzLabel="Draft" i18n-nzLabel />
        <nz-option nzValue="testing" nzLabel="Testing" i18n-nzLabel />
        <nz-option nzValue="published" nzLabel="Published" i18n-nzLabel />
        <nz-option nzValue="deprecated" nzLabel="Deprecated" i18n-nzLabel />
      </nz-select>
      <span class="spacer"></span>
      <a nz-button nzType="primary" routerLink="../new" data-testid="new-usecase">
        <nz-icon nzType="plus" /><span i18n>New use case</span>
      </a>
    </div>

    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }

    <nz-table
      #table
      [nzData]="items()"
      nzSize="small"
      [nzShowPagination]="false"
      [nzPageSize]="1000"
      [nzLoading]="loading()"
      data-testid="uc-table"
    >
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Pattern</th>
          <th i18n>Catalog</th>
          <th i18n>Status</th>
          <th i18n>Version</th>
          <th i18n>Updated</th>
          <th class="actions"><span class="sr-only" i18n>Actions</span></th>
        </tr>
      </thead>
      <tbody>
        @for (u of table.data; track u.id) {
          <tr [attr.data-testid]="'uc-row-' + u.name">
            <td>
              <a [routerLink]="['..', u.id, 'workflow']">{{ u.name }}</a>
              <div class="muted small">{{ u.description }}</div>
            </td>
            <td>{{ u.pattern === 'graph' ? graphLabel : teamLabel }}</td>
            <td>
              @if (u.catalog) {
                {{ u.catalog }} / {{ u.category }}
              } @else {
                <span class="muted" i18n>Not filed</span>
              }
            </td>
            <td>
              <nz-tag [nzColor]="color(u.status)">{{ statusLabel(u.status) }}</nz-tag>
              @if (u.dirty && u.published_version) {
                <nz-tag nzColor="orange" i18n>Unpublished changes</nz-tag>
              }
            </td>
            <td>
              @if (u.published_version) {
                v{{ u.published_version }}
              } @else {
                <span class="muted">—</span>
              }
            </td>
            <td>{{ u.updated_at | date: 'short' }}</td>
            <td class="actions">
              <a nz-button nzType="link" nzSize="small" [routerLink]="['..', u.id, 'workflow']" i18n
                >Workflow</a
              >
              <a nz-button nzType="link" nzSize="small" [routerLink]="['..', u.id, 'edit']" i18n
                >Settings</a
              >
              <button nz-button nzType="link" nzSize="small" (click)="openDuplicate(u)" i18n>
                Duplicate
              </button>
              <button nz-button nzType="link" nzSize="small" nzDanger (click)="remove(u)" i18n>
                Delete
              </button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="7" class="muted" i18n>No use cases yet.</td>
          </tr>
        }
      </tbody>
    </nz-table>

    <nz-modal
      [(nzVisible)]="dupOpen"
      nzTitle="Duplicate use case"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="420"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="duplicate()">
          <label for="dup-name" i18n>Name of the copy</label>
          <input nz-input id="dup-name" name="name" [(ngModel)]="dupName" required />
          @if (dupError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
          }
          <button nz-button nzType="primary" type="submit" i18n>Duplicate</button>
        </form>
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
})
export class UseCaseLibrary {
  private readonly api = inject(UseCasesService);
  private readonly session = inject(Session);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly items = signal<UseCaseOut[]>([]);
  protected readonly loading = signal(false);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected query = '';
  protected status: Status | null = null;

  protected dupOpen = false;
  protected dupName = '';
  protected readonly dupError = signal<string | null>(null);
  private dupSource: UseCaseOut | null = null;

  protected readonly graphLabel = $localize`Graph`;
  protected readonly teamLabel = $localize`Agent team`;
  private readonly labels: Record<string, string> = {
    draft: $localize`Draft`,
    testing: $localize`Testing`,
    published: $localize`Published`,
    deprecated: $localize`Deprecated`,
  };

  constructor() {
    effect(() => {
      if (this.projectId()) void this.reload();
    });
  }

  protected color(status: string): string {
    return STATUS_COLOR[status] ?? 'default';
  }

  protected statusLabel(status: string): string {
    return this.labels[status] ?? status;
  }

  protected async reload(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.loading.set(true);
    try {
      const page = await firstValueFrom(
        this.api.listUseCases({
          projectId,
          q: this.query || undefined,
          status: this.status ?? undefined,
        }),
      );
      this.items.set(page.items);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.loading.set(false);
    }
  }

  protected openDuplicate(u: UseCaseOut): void {
    this.dupSource = u;
    this.dupName = `${u.name} (copy)`;
    this.dupError.set(null);
    this.dupOpen = true;
  }

  protected async duplicate(): Promise<void> {
    if (!this.dupSource) return;
    try {
      await firstValueFrom(
        this.api.duplicateUseCase({
          projectId: this.projectId()!,
          useCaseId: this.dupSource.id,
          useCaseDuplicateIn: { name: this.dupName },
        }),
      );
      this.dupOpen = false;
      await this.reload();
    } catch (e) {
      this.dupError.set(problemMessage(e));
    }
  }

  protected async remove(u: UseCaseOut): Promise<void> {
    if (!confirm($localize`Delete use case ${u.name}:name:?`)) return;
    this.message.set(null);
    try {
      await firstValueFrom(
        this.api.deleteUseCase({ projectId: this.projectId()!, useCaseId: u.id }),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
    await this.reload();
  }
}
