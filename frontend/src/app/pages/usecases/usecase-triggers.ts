import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { TriggerOut, TriggersService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

type Kind = 'cron' | 'interval' | 'webhook';

/** Triggers of one use case (E3.9): cron and interval schedules, and inbound webhook tokens.
 * A trigger runs the published version as the Builder who created it. */
@Component({
  selector: 'forge-usecase-triggers',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzInputModule,
    NzInputNumberModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <h2 i18n>Triggers</h2>
      <span class="spacer"></span>
      <a nz-button routerLink="../../library" i18n>Back to library</a>
      <button nz-button nzType="primary" (click)="openNew()" data-testid="trigger-new" i18n>
        New trigger
      </button>
    </div>
    <p class="intro" i18n>
      A trigger starts a run of the published version without anyone clicking: on a schedule, or
      when another system calls its URL. Runs appear in the run history under the user who created
      the trigger.
    </p>
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    <nz-table [nzData]="triggers()" nzSize="small" [nzShowPagination]="false">
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>When</th>
          <th i18n>Next run</th>
          <th i18n>Last run</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (t of triggers(); track t.id) {
          <tr>
            <td>{{ t.name }}</td>
            <td>
              @if (!t.enabled) {
                <nz-tag i18n>Paused</nz-tag>
              }
              @switch (t.kind) {
                @case ('cron') {
                  <code>{{ t.cron }}</code> ({{ t.timezone }})
                }
                @case ('interval') {
                  <span i18n>every {{ t.interval_minutes }} min</span>
                }
                @default {
                  <span i18n>Inbound URL</span>
                }
              }
            </td>
            <td>{{ t.next_run_at ? (t.next_run_at | date: 'short') : '–' }}</td>
            <td>
              @if (t.last_run_at) {
                <nz-tag [nzColor]="t.last_status === 'started' ? 'green' : 'red'">{{
                  t.last_status
                }}</nz-tag>
                {{ t.last_run_at | date: 'short' }}
                @if (t.last_error) {
                  <div class="err">{{ t.last_error }}</div>
                }
              } @else {
                –
              }
            </td>
            <td class="actions">
              <button nz-button nzSize="small" (click)="fire(t)" i18n>Run now</button>
              <button nz-button nzSize="small" (click)="toggle(t)">
                {{ t.enabled ? pauseLabel : resumeLabel }}
              </button>
              <button nz-button nzSize="small" (click)="openEdit(t)" i18n>Edit</button>
              @if (t.kind === 'webhook') {
                <button nz-button nzSize="small" (click)="rotate(t)" i18n>New token</button>
              }
              <button nz-button nzSize="small" nzDanger (click)="deleting.set(t)" i18n>
                Delete
              </button>
            </td>
          </tr>
        } @empty {
          <tr>
            <td colspan="5" class="muted" i18n>No triggers yet.</td>
          </tr>
        }
      </tbody>
    </nz-table>

    @if (notice()) {
      <nz-alert
        nzType="success"
        [nzMessage]="notice()"
        nzCloseable
        (nzOnClose)="notice.set(null)"
      />
    }

    <nz-modal
      [(nzVisible)]="formOpen"
      [nzTitle]="editing ? editTitle : newTitle"
      [nzFooter]="null"
      [nzWidth]="640"
    >
      <ng-container *nzModalContent>
        <div class="form">
          <label for="trg-name" i18n>Name</label>
          <input nz-input id="trg-name" maxlength="80" [(ngModel)]="form.name" />
          <label i18n>Kind</label>
          <nz-select nzId="trg-kind" [(ngModel)]="form.kind" [nzDisabled]="!!editing">
            <nz-option nzValue="cron" nzLabel="Cron schedule" i18n-nzLabel />
            <nz-option nzValue="interval" nzLabel="Every N minutes" i18n-nzLabel />
            <nz-option
              nzValue="webhook"
              nzLabel="Inbound URL (called by another system)"
              i18n-nzLabel
            />
          </nz-select>
          @if (form.kind === 'cron') {
            <label for="trg-cron" i18n>Cron (minute hour day month weekday)</label>
            <input
              nz-input
              id="trg-cron"
              class="mono"
              placeholder="0 8 * * 1-5"
              [(ngModel)]="form.cron"
            />
            <label for="trg-tz" i18n>Time zone</label>
            <input nz-input id="trg-tz" placeholder="Europe/Rome" [(ngModel)]="form.timezone" />
          }
          @if (form.kind === 'interval') {
            <label i18n>Every (minutes)</label>
            <nz-input-number [(ngModel)]="form.interval" [nzMin]="1" [nzMax]="525600" />
          }
          <label for="trg-inputs" i18n>Inputs (JSON)</label>
          <textarea
            nz-input
            id="trg-inputs"
            rows="5"
            class="mono"
            [(ngModel)]="form.inputs"
          ></textarea>
          @if (form.kind === 'webhook') {
            <span></span>
            <span class="hint" i18n
              >The caller can add or override inputs in the request body.</span
            >
          }
          <label for="trg-hook" i18n>Completion webhook (optional)</label>
          <input nz-input id="trg-hook" placeholder="https://" [(ngModel)]="form.webhook_url" />
          <label></label>
          <label nz-checkbox [(ngModel)]="form.enabled" i18n>Enabled</label>
        </div>
        @if (formError()) {
          <nz-alert nzType="error" [nzMessage]="formError()" />
        }
        <div class="footer">
          <button nz-button (click)="formOpen = false" i18n>Cancel</button>
          <button nz-button nzType="primary" (click)="save()" data-testid="trigger-save" i18n>
            Save
          </button>
        </div>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!token()"
      nzTitle="Inbound URL"
      i18n-nzTitle
      (nzOnCancel)="token.set(null)"
      [nzFooter]="null"
      [nzWidth]="640"
    >
      <ng-container *nzModalContent>
        <p i18n>
          Copy the URL now: the token is shown only once. Anyone who has it can start this use case,
          so keep it secret and use “New token” if it leaks.
        </p>
        <input nz-input readonly class="mono" [value]="tokenUrl()" data-testid="trigger-url" />
        <p class="hint" i18n>
          Call it with POST. Optional body: a JSON object with an “inputs” field.
        </p>
        <div class="footer">
          <button nz-button nzType="primary" (click)="token.set(null)" i18n>Done</button>
        </div>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!deleting()"
      nzTitle="Delete trigger"
      i18n-nzTitle
      (nzOnCancel)="deleting.set(null)"
      [nzFooter]="null"
    >
      <ng-container *nzModalContent>
        <p i18n>Delete “{{ deleting()?.name }}”? Runs it already started are kept.</p>
        <div class="footer">
          <button nz-button (click)="deleting.set(null)" i18n>Cancel</button>
          <button nz-button nzDanger nzType="primary" (click)="remove()" i18n>Delete</button>
        </div>
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
  styles: `
    .form {
      display: grid;
      grid-template-columns: 14rem 1fr;
      gap: 0.5rem 0.75rem;
      align-items: start;
    }
    .mono {
      font-family: monospace;
      font-size: 0.8rem;
    }
    .hint,
    .err {
      color: var(--forge-text-muted-color);
      font-size: 0.8rem;
    }
    .footer {
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      margin-top: 1rem;
    }
  `,
})
export class UseCaseTriggers {
  private readonly api = inject(TriggersService);
  private readonly route = inject(ActivatedRoute);
  protected readonly session = inject(Session);

  protected readonly newTitle = $localize`New trigger`;
  protected readonly editTitle = $localize`Edit trigger`;
  protected readonly pauseLabel = $localize`Pause`;
  protected readonly resumeLabel = $localize`Resume`;
  protected readonly triggers = signal<TriggerOut[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly formError = signal<string | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly deleting = signal<TriggerOut | null>(null);
  protected readonly token = signal<string | null>(null);
  protected readonly tokenUrl = computed(() =>
    this.token() ? `${window.location.origin}/api/v1/triggers/${this.token()}` : '',
  );
  protected formOpen = false;
  protected editing: TriggerOut | null = null;
  protected form = this.blank();
  private readonly useCaseId = this.route.snapshot.paramMap.get('id')!;
  private readonly projectId = computed(() => this.session.project()?.id ?? null);

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
  }

  private blank() {
    return {
      name: '',
      kind: 'cron' as Kind,
      cron: '0 8 * * 1-5',
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      interval: 60 as number | null,
      inputs: '{}',
      webhook_url: '',
      enabled: true,
    };
  }

  async load(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      this.triggers.set(
        await firstValueFrom(this.api.listTriggers({ projectId, useCaseId: this.useCaseId })),
      );
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected openNew(): void {
    this.editing = null;
    this.form = this.blank();
    this.formError.set(null);
    this.formOpen = true;
  }

  protected openEdit(t: TriggerOut): void {
    this.editing = t;
    this.form = {
      name: t.name,
      kind: t.kind as Kind,
      cron: t.cron ?? '',
      timezone: t.timezone,
      interval: t.interval_minutes ?? null,
      inputs: JSON.stringify(t.inputs, null, 2),
      webhook_url: t.webhook_url ?? '',
      enabled: t.enabled,
    };
    this.formError.set(null);
    this.formOpen = true;
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    let inputs: Record<string, unknown>;
    try {
      inputs = this.form.inputs.trim() ? JSON.parse(this.form.inputs) : {};
      if (typeof inputs !== 'object' || Array.isArray(inputs)) throw new Error();
    } catch {
      this.formError.set($localize`The inputs are not a valid JSON object.`);
      return;
    }
    const f = this.form;
    const schedule =
      f.kind === 'cron'
        ? { cron: f.cron.trim(), timezone: f.timezone.trim() || 'UTC' }
        : f.kind === 'interval'
          ? { interval_minutes: f.interval }
          : {};
    try {
      if (this.editing) {
        await firstValueFrom(
          this.api.updateTrigger({
            projectId,
            triggerId: this.editing.id,
            triggerUpdateIn: {
              name: f.name,
              inputs,
              enabled: f.enabled,
              ...schedule,
              ...(f.webhook_url.trim()
                ? { webhook_url: f.webhook_url.trim() }
                : { clear_webhook: true }),
            },
          }),
        );
      } else {
        const created = await firstValueFrom(
          this.api.createTrigger({
            projectId,
            useCaseId: this.useCaseId,
            triggerIn: {
              name: f.name,
              kind: f.kind,
              inputs,
              enabled: f.enabled,
              webhook_url: f.webhook_url.trim() || null,
              ...schedule,
            },
          }),
        );
        if (created.token) this.token.set(created.token);
      }
      this.formOpen = false;
      await this.load();
    } catch (e) {
      this.formError.set(problemMessage(e));
    }
  }

  protected async toggle(t: TriggerOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.api.updateTrigger({
          projectId,
          triggerId: t.id,
          triggerUpdateIn: { enabled: !t.enabled },
        }),
      );
      await this.load();
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async fire(t: TriggerOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(this.api.fireTrigger({ projectId, triggerId: t.id }));
      this.notice.set($localize`Run started. Follow it in the run history.`);
      await this.load();
    } catch (e) {
      this.error.set(problemMessage(e));
      await this.load();
    }
  }

  protected async rotate(t: TriggerOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const out = await firstValueFrom(this.api.rotateTriggerToken({ projectId, triggerId: t.id }));
      this.token.set(out.token ?? null);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async remove(): Promise<void> {
    const projectId = this.projectId();
    const t = this.deleting();
    if (!projectId || !t) return;
    try {
      await firstValueFrom(this.api.deleteTrigger({ projectId, triggerId: t.id }));
      this.deleting.set(null);
      await this.load();
    } catch (e) {
      this.deleting.set(null);
      this.error.set(problemMessage(e));
    }
  }
}
