import { DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { CheckboxModule } from 'primeng/checkbox';
import { InputNumberModule } from 'primeng/inputnumber';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { firstValueFrom } from 'rxjs';

import {
  EffectiveLimits,
  ProjectGatewayConfigIn,
  ProjectModelsService,
  UsageOut,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { ModelRegistry } from '../models/model-registry';

type LimitKey =
  | 'tokens_per_day'
  | 'requests_per_minute'
  | 'user_tokens_per_day'
  | 'user_requests_per_minute'
  | 'max_tokens_per_call';

/** Projects Studio → Models: registry, limits within the platform ceilings, usage (E1.9). */
@Component({
  selector: 'forge-project-models',
  imports: [
    DecimalPipe,
    FormsModule,
    ButtonModule,
    CardModule,
    CheckboxModule,
    InputNumberModule,
    MessageModule,
    SelectModule,
    ModelRegistry,
  ],
  template: `
    @if (projectId(); as id) {
      @if (usage(); as u) {
        <section class="usage" data-testid="usage">
          <div>
            <span class="big">{{ u.tokens_today | number }}</span>
            <span class="muted"
              >/ {{ u.tokens_per_day ? (u.tokens_per_day | number) : unlimited }}</span
            >
            <div class="muted" i18n>tokens today (UTC)</div>
          </div>
          <div>
            <span class="big">{{ u.requests_this_minute }}</span>
            <span class="muted">/ {{ u.requests_per_minute || unlimited }}</span>
            <div class="muted" i18n>requests this minute</div>
          </div>
          <div>
            <span class="big">{{ u.calls_today }}</span>
            <div class="muted" i18n>calls today</div>
          </div>
          <div>
            <span class="big">{{ u.cost_today | number: '1.2-4' }}</span>
            <div class="muted" i18n>cost today</div>
          </div>
        </section>
      }

      <forge-model-registry [projectId]="id" [canEdit]="manage()" />

      @if (manage() && config(); as c) {
        <p-card header="Limits and cache" i18n-header data-testid="limits">
          <form class="form" (ngSubmit)="save()">
            <p class="muted" i18n>
              Empty fields inherit the platform value. Limits cannot exceed the platform ceiling.
            </p>
            <div class="grid">
              @for (f of limitFields; track f.key) {
                <div class="field">
                  <label [for]="'lim-' + f.key">{{ f.label }}</label>
                  <p-inputnumber
                    [inputId]="'lim-' + f.key"
                    [name]="f.key"
                    [min]="0"
                    [placeholder]="platformHint(f.key)"
                    [(ngModel)]="draft[f.key]"
                  />
                </div>
              }
              <div class="field">
                <label for="lim-policy" i18n>Above the max tokens per call</label>
                <p-select
                  inputId="lim-policy"
                  name="policy"
                  [options]="policies"
                  optionLabel="label"
                  optionValue="value"
                  [(ngModel)]="draft.max_tokens_policy"
                  appendTo="body"
                />
              </div>
              <div class="field">
                <label for="lim-ttl" i18n>Cache TTL (s)</label>
                <p-inputnumber
                  inputId="lim-ttl"
                  name="ttl"
                  [min]="1"
                  [(ngModel)]="draft.cache_ttl_s"
                />
              </div>
            </div>
            <div class="check">
              <p-checkbox
                inputId="lim-cache"
                name="cache"
                [binary]="true"
                [(ngModel)]="draft.cache_enabled"
              />
              <label for="lim-cache" i18n
                >Cache identical requests (temperature 0 only, unless allowed below)</label
              >
            </div>
            <div class="check">
              <p-checkbox
                inputId="lim-temp"
                name="temp"
                [binary]="true"
                [(ngModel)]="draft.cache_nonzero_temperature"
              />
              <label for="lim-temp" i18n>Also cache requests with temperature above 0</label>
            </div>
            <small class="muted" i18n
              >Effective now: {{ c.effective.tokens_per_day || unlimited }} tokens/day,
              {{ c.effective.requests_per_minute || unlimited }} requests/min,
              {{ c.effective.max_tokens_per_call || unlimited }} max tokens/call</small
            >
            @if (message(); as m) {
              <p-message [severity]="m.ok ? 'success' : 'error'" [text]="m.text" />
            }
            <div class="dialog-actions">
              <p-button type="submit" label="Save" i18n-label data-testid="save-limits" />
            </div>
          </form>
        </p-card>
      }
    }
  `,
  styleUrls: ['../admin/admin.scss', '../models/model-registry.scss', './project-models.scss'],
})
export class ProjectModels {
  private readonly api = inject(ProjectModelsService);
  private readonly session = inject(Session);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly manage = computed(() => this.session.can('project.manage'));
  protected readonly config = signal<{
    platform: EffectiveLimits;
    effective: EffectiveLimits;
  } | null>(null);
  protected readonly usage = signal<UsageOut | null>(null);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly unlimited = $localize`unlimited`;
  protected draft: ProjectGatewayConfigIn = {};
  protected readonly limitFields: { key: LimitKey; label: string }[] = [
    { key: 'tokens_per_day', label: $localize`Tokens per day` },
    { key: 'requests_per_minute', label: $localize`Requests per minute` },
    { key: 'user_tokens_per_day', label: $localize`Tokens per day, per user` },
    { key: 'user_requests_per_minute', label: $localize`Requests per minute, per user` },
    { key: 'max_tokens_per_call', label: $localize`Max tokens per call` },
  ];
  protected readonly policies = [
    { value: null, label: $localize`Platform default` },
    { value: 'clamp', label: $localize`Clamp to the limit` },
    { value: 'reject', label: $localize`Reject the request` },
  ];

  constructor() {
    effect(() => {
      const id = this.projectId();
      const manage = this.manage();
      if (id) void this.load(id, manage);
    });
  }

  private async load(projectId: string, manage: boolean): Promise<void> {
    try {
      this.usage.set(await firstValueFrom(this.api.projectGatewayUsage({ projectId })));
      if (manage) {
        const cfg = await firstValueFrom(this.api.projectGatewayConfig({ projectId }));
        this.draft = { ...cfg.config };
        this.config.set(cfg);
      }
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected platformHint(key: LimitKey): string {
    const value = this.config()?.platform[key];
    return value ? $localize`platform: ${value}:value:` : $localize`platform: unlimited`;
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const body: ProjectGatewayConfigIn = { ...this.draft };
    for (const f of this.limitFields) {
      if (body[f.key] === undefined) body[f.key] = null;
    }
    try {
      const cfg = await firstValueFrom(
        this.api.projectSetGatewayConfig({ projectId, projectGatewayConfigIn: body }),
      );
      this.config.set(cfg);
      this.message.set({ ok: true, text: $localize`Saved; applied to the next request` });
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }
}
