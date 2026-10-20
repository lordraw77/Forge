import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzSwitchModule } from 'ng-zorro-antd/switch';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  GuardrailEventOut,
  GuardrailRuleIn,
  GuardrailTestOut,
  GuardrailsService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

interface RuleRow {
  type: string;
  stage: string[];
  action: string;
  failMode: string | null;
  params: string;
}

const TYPES = [
  'pii',
  'injection',
  'secrets',
  'urls',
  'banned_topics',
  'json_schema',
  'toxicity',
  'grounding',
];
const STAGES = ['input', 'output', 'tool_args'];
const ACTIONS = ['log', 'mask', 'rewrite', 'block', 'escalate'];
const HINT: Record<string, string> = {
  pii: '{"entities": ["EMAIL", "CREDIT_CARD", "IBAN", "IT_FISCAL_CODE", "PHONE", "IP_ADDRESS", "PERSON", "LOCATION", "ORGANIZATION"]}',
  injection: '{"model": "guard-or-chat-model", "judge": false, "threshold": 0.8}',
  secrets: '{"patterns": ["regex"]}',
  urls: '{"allow": ["example.com"], "deny": ["evil.test"]}',
  banned_topics: '{"terms": ["term one", "term two"]}',
  json_schema: '{"schema": {"type": "object", "required": ["answer"]}}',
  toxicity: '{"model": "guard-model-name", "categories": []}',
  grounding: '{"model": "chat-model-name"}',
};

/** Guardrail policy of the project (US-2.11.1): optional, off until a Project Admin saves rules.
 * Rules, a dry run on sample text, and the intervention log. */
@Component({
  selector: 'forge-guardrails',
  imports: [
    DatePipe,
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzSelectModule,
    NzSwitchModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <h1 i18n>Guardrails</h1>
      <span class="spacer"></span>
      <span class="check">
        <nz-switch aria-label="Policy enabled" [(ngModel)]="enabled" data-testid="gr-enabled" />
        <span i18n>Enabled</span>
      </span>
      <button nz-button (click)="add()" data-testid="gr-add" i18n>Add rule</button>
      <button nz-button nzType="primary" (click)="save()" data-testid="gr-save" i18n>Save</button>
    </div>
    @if (!session.project()) {
      <nz-alert nzType="info" nzMessage="You are not a member of any project yet." i18n-nzMessage />
    } @else if (!session.can('project.manage')) {
      <nz-alert
        nzType="warning"
        nzMessage="Only Project Admins manage guardrails."
        i18n-nzMessage
      />
    } @else {
      <nz-alert
        nzType="info"
        nzMessage="Guardrails are optional. Without saved rules, or with the policy disabled, no call is checked."
        i18n-nzMessage
      />
      @if (error(); as e) {
        <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
      }
      @if (saved()) {
        <nz-alert nzType="success" nzMessage="Policy saved." i18n-nzMessage nzShowIcon />
      }
      @if (unavailable().length) {
        <nz-alert
          nzType="warning"
          nzShowIcon
          [nzMessage]="
            'No guard endpoint is configured for: ' +
            unavailable().join(', ') +
            '. Rules that block fail closed.'
          "
          data-testid="gr-unavailable"
        />
      }

      <div class="rules" data-testid="gr-rules">
        @for (r of rules(); track $index; let i = $index) {
          <div class="rule" [attr.data-testid]="'gr-rule-' + i">
            <nz-select [(ngModel)]="r.type" aria-label="Check" (ngModelChange)="hint(r)">
              @for (t of types; track t) {
                <nz-option [nzValue]="t" [nzLabel]="t" />
              }
            </nz-select>
            <nz-select [(ngModel)]="r.stage" nzMode="multiple" aria-label="Stage">
              @for (s of stages; track s) {
                <nz-option [nzValue]="s" [nzLabel]="s" />
              }
            </nz-select>
            <nz-select [(ngModel)]="r.action" aria-label="Action">
              @for (a of actions; track a) {
                <nz-option [nzValue]="a" [nzLabel]="a" />
              }
            </nz-select>
            <nz-select
              [(ngModel)]="r.failMode"
              nzAllowClear
              nzPlaceHolder="Fail mode: default"
              i18n-nzPlaceHolder
              aria-label="Fail mode"
            >
              <nz-option nzValue="open" nzLabel="open" />
              <nz-option nzValue="closed" nzLabel="closed" />
            </nz-select>
            <textarea
              class="params"
              rows="2"
              spellcheck="false"
              aria-label="Parameters (JSON)"
              [(ngModel)]="r.params"
            ></textarea>
            <button nz-button nzDanger nzSize="small" (click)="remove(i)" i18n>Remove</button>
          </div>
        } @empty {
          <p class="muted" data-testid="gr-empty" i18n>No rules: nothing is checked.</p>
        }
      </div>

      <h2 i18n>Try it</h2>
      <div class="try">
        <textarea
          rows="4"
          spellcheck="false"
          aria-label="Sample text"
          [(ngModel)]="sample"
          data-testid="gr-sample"
        ></textarea>
        <nz-select [(ngModel)]="sampleStage" aria-label="Stage of the sample">
          @for (s of stages; track s) {
            <nz-option [nzValue]="s" [nzLabel]="s" />
          }
        </nz-select>
        <button nz-button (click)="dryRun()" data-testid="gr-try" i18n>Check (rules above)</button>
      </div>
      @if (result(); as t) {
        <div data-testid="gr-result">
          <nz-tag [nzColor]="t.action === 'allow' ? 'success' : 'warning'">{{ t.action }}</nz-tag>
          <pre>{{ t.text }}</pre>
          @for (f of t.findings; track $index) {
            <div>{{ f?.['type'] }}: {{ f?.['action'] }} ({{ f?.['label'] }})</div>
          }
        </div>
      }

      <h2 i18n>Interventions</h2>
      <nz-table #table [nzData]="events()" nzSize="small" [nzPageSize]="20" data-testid="gr-events">
        <thead>
          <tr>
            <th i18n>When</th>
            <th i18n>Stage</th>
            <th i18n>Check</th>
            <th i18n>Action</th>
            <th i18n>Found</th>
            <th i18n>Source</th>
          </tr>
        </thead>
        <tbody>
          @for (e of table.data; track e.id) {
            <tr>
              <td>{{ e.created_at | date: 'short' }}</td>
              <td>{{ e.stage }}</td>
              <td>{{ e.check_type }}</td>
              <td>{{ e.action }}</td>
              <td>{{ e.label }}</td>
              <td>{{ e.source }}</td>
            </tr>
          } @empty {
            <tr>
              <td colspan="6" class="muted" i18n>No interventions yet.</td>
            </tr>
          }
        </tbody>
      </nz-table>
    }
  `,
  styleUrl: '../admin/admin.scss',
  styles: `
    .rule {
      display: grid;
      grid-template-columns: 9rem 12rem 8rem 11rem 1fr auto;
      gap: 0.5rem;
      align-items: start;
      margin: 0.5rem 0;
    }
    .params,
    .try textarea {
      font-family: monospace;
      width: 100%;
    }
    .try {
      display: grid;
      gap: 0.5rem;
      max-width: 40rem;
    }
  `,
})
export class GuardrailsPage {
  private readonly api = inject(GuardrailsService);
  protected readonly session = inject(Session);

  protected readonly types = TYPES;
  protected readonly stages = STAGES;
  protected readonly actions = ACTIONS;
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly rules = signal<RuleRow[]>([]);
  protected readonly events = signal<GuardrailEventOut[]>([]);
  protected readonly unavailable = signal<string[]>([]);
  protected readonly result = signal<GuardrailTestOut | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly saved = signal(false);
  protected enabled = true;
  protected sample = '';
  protected sampleStage = 'input';

  constructor() {
    effect(() => {
      if (this.projectId() && this.session.can('project.manage')) void this.load();
    });
  }

  private async load(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const policy = await firstValueFrom(this.api.getGuardrailPolicy({ projectId }));
      this.enabled = policy.enabled;
      this.unavailable.set(policy.unavailable);
      this.rules.set(
        policy.rules.map((r) => ({
          type: r.type,
          stage: r.stage ?? ['input', 'output'],
          action: r.action ?? 'log',
          failMode: r.fail_mode ?? null,
          params: JSON.stringify(r.params ?? {}, null, 2),
        })),
      );
      this.events.set(await firstValueFrom(this.api.events({ projectId, limit: 100 })));
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected add(): void {
    this.rules.update((rs) => [
      ...rs,
      { type: 'pii', stage: ['input'], action: 'mask', failMode: null, params: HINT['pii'] },
    ]);
  }

  protected remove(i: number): void {
    this.rules.update((rs) => rs.filter((_, j) => j !== i));
  }

  /** A new check type starts from an example of its parameters when the box is empty. */
  protected hint(r: RuleRow): void {
    if (!r.params.trim() || r.params.trim() === '{}') r.params = HINT[r.type] ?? '{}';
  }

  private payload(): GuardrailRuleIn[] {
    return this.rules().map((r, i) => {
      let params: Record<string, unknown>;
      try {
        params = JSON.parse(r.params || '{}');
      } catch {
        throw new Error(`Rule ${i + 1}: parameters are not valid JSON`);
      }
      return {
        type: r.type,
        stage: r.stage,
        action: r.action,
        fail_mode: r.failMode,
        params,
      };
    });
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.error.set(null);
    this.saved.set(false);
    try {
      const out = await firstValueFrom(
        this.api.putGuardrailPolicy({
          projectId,
          guardrailPolicyIn: { enabled: this.enabled, rules: this.payload() },
        }),
      );
      this.unavailable.set(out.unavailable);
      this.saved.set(true);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : problemMessage(e));
    }
  }

  protected async dryRun(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.error.set(null);
    try {
      this.result.set(
        await firstValueFrom(
          this.api.testGuardrailPolicy({
            projectId,
            guardrailTestIn: {
              text: this.sample,
              stage: this.sampleStage,
              rules: this.payload(),
            },
          }),
        ),
      );
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : problemMessage(e));
    }
  }
}
