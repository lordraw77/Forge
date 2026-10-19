import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { A11yModule } from '@angular/cdk/a11y';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzDatePickerModule } from 'ng-zorro-antd/date-picker';
import { NzDropdownModule } from 'ng-zorro-antd/dropdown';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';
import { firstValueFrom } from 'rxjs';

import { PromptOut, PromptsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type StatusFilter = 'draft' | 'published' | null;

/** Prompt Library (US-1.11.2): filters, row actions Try / Edit / Duplicate / Delete. */
@Component({
  selector: 'forge-prompt-library',
  imports: [
    A11yModule,
    DatePipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzDatePickerModule,
    NzDropdownModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule,
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
        data-testid="prompt-search"
        [(ngModel)]="query"
        (ngModelChange)="reload()"
      />
      <nz-select
        class="filter-status"
        nzId="prompt-status"
        aria-label="Status"
        i18n-aria-label
        data-testid="prompt-status"
        [(ngModel)]="status"
        (ngModelChange)="reload()"
      >
        <nz-option [nzValue]="null" nzLabel="All statuses" i18n-nzLabel />
        <nz-option nzValue="published" nzLabel="Published" i18n-nzLabel />
        <nz-option nzValue="draft" nzLabel="Draft" i18n-nzLabel />
      </nz-select>
      <nz-select
        class="filter-status"
        nzId="prompt-range"
        aria-label="Time range"
        i18n-aria-label
        data-testid="prompt-range"
        [(ngModel)]="range"
        (ngModelChange)="pickPreset()"
      >
        @for (r of ranges; track r.days) {
          <nz-option [nzValue]="r.days" [nzLabel]="r.label" />
        }
      </nz-select>
      <nz-range-picker
        aria-label="Custom date range"
        i18n-aria-label
        [nzPlaceHolder]="rangeLabels"
        [(ngModel)]="custom"
        (ngModelChange)="pickCustom()"
      />
      <span class="spacer"></span>
      <a nz-button nzType="primary" routerLink="../new" data-testid="new-prompt">
        <nz-icon nzType="plus" /><span i18n>New Prompt</span>
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
      [nzNoResult]="empty"
      data-testid="prompt-table"
    >
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Status</th>
          <th i18n>Version</th>
          <th i18n>Model</th>
          <th i18n>Updated</th>
          <th class="actions"><span class="sr-only" i18n>Actions</span></th>
        </tr>
      </thead>
      <tbody>
        @for (p of table.data; track p.id) {
          <tr [attr.data-testid]="'prompt-row-' + p.name">
            <td>
              <a [routerLink]="['..', p.id, 'edit']">{{ p.name }}</a>
              @if (p.description) {
                <div class="muted small">{{ p.description }}</div>
              }
            </td>
            <td>
              @if (p.status === 'published') {
                <nz-tag nzColor="success" i18n>Published</nz-tag>
              } @else {
                <nz-tag i18n>Draft</nz-tag>
              }
            </td>
            <td>
              v{{ p.latest_version }}
              @if (p.published_version && p.published_version !== p.latest_version) {
                <span class="muted" i18n>(published v{{ p.published_version }})</span>
              }
            </td>
            <td>{{ p.model }}</td>
            <td>{{ p.updated_at | date: 'short' }}</td>
            <td class="actions">
              <button
                nz-button
                nzType="text"
                nz-dropdown
                [nzDropdownMenu]="menu"
                nzTrigger="click"
                aria-label="Actions"
                i18n-aria-label
                [attr.data-testid]="'prompt-actions-' + p.name"
              >
                <nz-icon nzType="more" />
              </button>
              <nz-dropdown-menu #menu="nzDropdownMenu">
                <ul nz-menu>
                  <li
                    nz-menu-item
                    role="menuitem"
                    tabindex="0"
                    (click)="go(p, 'test')"
                    (keydown.enter)="go(p, 'test')"
                    data-testid="action-try"
                    i18n
                  >
                    Try
                  </li>
                  <li
                    nz-menu-item
                    role="menuitem"
                    tabindex="0"
                    (click)="go(p, 'edit')"
                    (keydown.enter)="go(p, 'edit')"
                    data-testid="action-edit"
                    i18n
                  >
                    Edit
                  </li>
                  <li
                    nz-menu-item
                    role="menuitem"
                    tabindex="0"
                    (click)="openDuplicate(p)"
                    (keydown.enter)="openDuplicate(p)"
                    data-testid="action-duplicate"
                    i18n
                  >
                    Duplicate
                  </li>
                  <li
                    nz-menu-item
                    nzDisabled
                    nz-tooltip
                    nzTooltipTitle="Available with MCP Studio (Phase 3)"
                    i18n-nzTooltipTitle
                    i18n
                  >
                    Add to MCP Servers
                  </li>
                  <li nz-menu-divider></li>
                  <li
                    nz-menu-item
                    role="menuitem"
                    tabindex="0"
                    nzDanger
                    (click)="deleting.set(p)"
                    (keydown.enter)="deleting.set(p)"
                    data-testid="action-delete"
                    i18n
                  >
                    Delete
                  </li>
                </ul>
              </nz-dropdown-menu>
            </td>
          </tr>
        }
      </tbody>
    </nz-table>
    <ng-template #empty>
      <div class="muted" i18n>No prompts yet. Create one with New Prompt.</div>
    </ng-template>
    @if (nextCursor()) {
      <div class="dialog-actions">
        <button nz-button (click)="more()" data-testid="load-more" i18n>Load more</button>
      </div>
    }

    <nz-modal
      [nzVisible]="!!duplicating()"
      nzTitle="Duplicate prompt"
      i18n-nzTitle
      [nzFooter]="null"
      (nzOnCancel)="duplicating.set(null)"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="duplicate()">
          <label for="dup-name" i18n>New name</label>
          <input
            nz-input
            id="dup-name"
            name="name"
            cdkFocusInitial
            required
            maxlength="120"
            data-testid="duplicate-name"
            [(ngModel)]="duplicateName"
          />
          <div class="dialog-actions">
            <button nz-button type="button" (click)="duplicating.set(null)" i18n>Cancel</button>
            <button
              nz-button
              nzType="primary"
              type="submit"
              [disabled]="!duplicateName.trim()"
              data-testid="duplicate-confirm"
              i18n
            >
              Duplicate
            </button>
          </div>
        </form>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!deleting()"
      nzTitle="Delete prompt"
      i18n-nzTitle
      [nzFooter]="null"
      (nzOnCancel)="deleting.set(null)"
    >
      <ng-container *nzModalContent>
        <p i18n>Delete “{{ deleting()?.name }}” and all its versions? This cannot be undone.</p>
        <div class="dialog-actions">
          <button nz-button (click)="deleting.set(null)" cdkFocusInitial i18n>No</button>
          <button
            nz-button
            nzDanger
            nzType="primary"
            (click)="remove()"
            data-testid="delete-yes"
            i18n
          >
            Yes
          </button>
        </div>
      </ng-container>
    </nz-modal>
  `,
  styleUrls: ['../admin/admin.scss', './prompts.scss'],
})
export class PromptLibrary {
  private readonly api = inject(PromptsService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly items = signal<PromptOut[]>([]);
  protected readonly nextCursor = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly duplicating = signal<PromptOut | null>(null);
  protected readonly deleting = signal<PromptOut | null>(null);
  protected readonly ranges = [
    { days: 0, label: $localize`Any time` },
    { days: 1, label: $localize`Last 24 hours` },
    { days: 7, label: $localize`Last 7 days` },
    { days: 30, label: $localize`Last 30 days` },
    { days: 90, label: $localize`Last 90 days` },
  ];
  protected query = '';
  protected status: StatusFilter = null;
  /** Updated within the last N days; 0 = any time. A custom range replaces the preset. */
  protected range = 0;
  protected custom: Date[] = [];
  protected readonly rangeLabels = [$localize`Updated from`, $localize`to`];
  protected duplicateName = '';
  private seq = 0;

  constructor() {
    effect(() => {
      if (this.projectId()) void this.reload();
    });
  }

  protected pickPreset(): void {
    this.custom = [];
    void this.reload();
  }

  protected pickCustom(): void {
    this.range = 0;
    void this.reload();
  }

  private params(cursor: string | null) {
    let since = this.range ? new Date(Date.now() - this.range * 86_400_000).toISOString() : null;
    let until: string | null = null;
    if (this.custom?.length === 2) {
      const [from, to] = this.custom.map((d) => new Date(d));
      from.setHours(0, 0, 0, 0);
      to.setHours(23, 59, 59, 999);
      since = from.toISOString();
      until = to.toISOString();
    }
    return {
      projectId: this.projectId()!,
      status: this.status,
      q: this.query.trim() || null,
      since,
      until,
      cursor,
      limit: 50,
    };
  }

  protected async reload(): Promise<void> {
    if (!this.projectId()) return;
    const seq = ++this.seq;
    this.loading.set(true);
    try {
      const page = await firstValueFrom(this.api.listPrompts(this.params(null)));
      if (seq !== this.seq) return; // a newer filter change won
      this.items.set(page.items);
      this.nextCursor.set(page.next_cursor);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      if (seq === this.seq) this.loading.set(false);
    }
  }

  protected async more(): Promise<void> {
    const cursor = this.nextCursor();
    if (!cursor) return;
    try {
      const page = await firstValueFrom(this.api.listPrompts(this.params(cursor)));
      this.items.update((list) => [...list, ...page.items]);
      this.nextCursor.set(page.next_cursor);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected go(p: PromptOut, page: 'edit' | 'test'): void {
    void this.router.navigate(['/prompts', p.id, page]);
  }

  protected openDuplicate(p: PromptOut): void {
    this.duplicateName = $localize`${p.name}:name: (copy)`;
    this.duplicating.set(p);
  }

  protected async duplicate(): Promise<void> {
    const source = this.duplicating();
    if (!source) return;
    try {
      const copy = await firstValueFrom(
        this.api.duplicatePrompt({
          projectId: this.projectId()!,
          promptId: source.id,
          duplicateIn: { name: this.duplicateName.trim() },
        }),
      );
      this.duplicating.set(null);
      this.message.set({ ok: true, text: $localize`Created “${copy.name}:name:”.` });
      await this.reload();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async remove(): Promise<void> {
    const target = this.deleting();
    if (!target) return;
    try {
      await firstValueFrom(
        this.api.deletePrompt({ projectId: this.projectId()!, promptId: target.id }),
      );
      this.deleting.set(null);
      this.message.set({ ok: true, text: $localize`Deleted “${target.name}:name:”.` });
      await this.reload();
    } catch (e) {
      this.deleting.set(null);
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }
}
