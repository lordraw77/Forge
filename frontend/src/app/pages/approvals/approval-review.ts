import { Component, computed, effect, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzTagModule } from 'ng-zorro-antd/tag';

import { ApprovalDetail, DecisionIn } from '../../api-client';
import { MonacoEditor } from '../../shared/monaco-editor';

/** The text to check, or the planned tool call, in an editor; Approve, Approve with changes and
 * Reject (feedback required). Used by the Approvals page and by the public email-link page. */
@Component({
  selector: 'forge-approval-review',
  imports: [FormsModule, MonacoEditor, NzAlertModule, NzButtonModule, NzInputModule, NzTagModule],
  template: `
    <p class="meta">
      <strong>{{ detail().use_case_name }}</strong>
      <span class="muted"> · {{ detail().node_id }}</span>
      <nz-tag>{{ detail().kind === 'tool_call' ? labelCall : labelText }}</nz-tag>
    </p>
    @if (detail().instructions) {
      <nz-alert
        nzType="info"
        [nzMessage]="detail().instructions"
        data-testid="review-instructions"
      />
    }
    @if (detail().kind === 'tool_call') {
      <p class="muted" i18n>
        The tool <code>{{ tool() }}</code> will be called with these arguments (JSON). Edit them if
        needed.
      </p>
    }
    <forge-monaco
      [(value)]="text"
      [language]="detail().kind === 'tool_call' ? 'json' : 'plaintext'"
      [readOnly]="!open()"
      height="16rem"
      label="Content under review"
      i18n-label
      data-testid="review-editor"
    />
    @if (open()) {
      <label for="review-comment" i18n>Comment (required to reject)</label>
      <textarea
        nz-input
        id="review-comment"
        rows="2"
        maxlength="2000"
        [ngModel]="comment()"
        (ngModelChange)="comment.set($event)"
        data-testid="review-comment"
      ></textarea>
      @if (problem() || error(); as e) {
        <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
      }
      <div class="buttons">
        <button
          nz-button
          nzDanger
          [disabled]="busy() || !comment().trim()"
          (click)="reject()"
          data-testid="review-reject"
          i18n
        >
          Reject
        </button>
        <button
          nz-button
          nzType="primary"
          [disabled]="busy()"
          (click)="approve()"
          data-testid="review-approve"
        >
          @if (changed()) {
            <span i18n>Approve with changes</span>
          } @else {
            <span i18n>Approve</span>
          }
        </button>
      </div>
    } @else {
      <nz-alert
        [nzType]="detail().status === 'approved' ? 'success' : 'warning'"
        [nzMessage]="statusText()"
        [nzDescription]="detail().comment ?? ''"
        nzShowIcon
        data-testid="review-decided"
      />
    }
  `,
  styles: `
    .meta {
      margin-bottom: 0.5rem;
    }
    .buttons {
      display: flex;
      justify-content: flex-end;
      gap: 0.5rem;
      margin-top: 0.75rem;
    }
    label {
      display: block;
      margin-top: 0.75rem;
      font-weight: 600;
    }
  `,
})
export class ApprovalReview {
  readonly detail = input.required<ApprovalDetail>();
  readonly busy = input(false);
  readonly error = input<string | null>(null);
  readonly decided = output<DecisionIn>();

  protected readonly labelCall = $localize`Tool call`;
  protected readonly labelText = $localize`Text`;
  protected text = signal('');
  protected readonly comment = signal('');
  protected readonly problem = signal<string | null>(null);
  protected readonly open = computed(() => this.detail().status === 'pending');
  private readonly original = computed(() => {
    const d = this.detail();
    return d.kind === 'tool_call'
      ? JSON.stringify((d.content as { args?: unknown }).args ?? {}, null, 2)
      : String((d.content as { text?: unknown }).text ?? '');
  });
  protected readonly changed = computed(() => this.text() !== this.original());
  protected readonly tool = computed(() =>
    String((this.detail().content as { tool?: unknown }).tool ?? ''),
  );

  constructor() {
    effect(() => {
      this.text.set(this.original());
      this.comment.set('');
      this.problem.set(null);
    });
  }

  protected statusText(): string {
    switch (this.detail().status) {
      case 'approved':
        return $localize`Approved`;
      case 'rejected':
        return $localize`Rejected`;
      case 'expired':
        return $localize`Expired: nobody decided in time`;
      default:
        return $localize`Cancelled: the run was stopped`;
    }
  }

  protected approve(): void {
    this.problem.set(null);
    const decision: DecisionIn = { decision: 'approve', comment: this.comment().trim() };
    if (this.changed()) {
      if (this.detail().kind === 'tool_call') {
        try {
          const parsed: unknown = JSON.parse(this.text());
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
          decision.edited = parsed as never;
        } catch {
          this.problem.set($localize`The arguments must be a JSON object`);
          return;
        }
      } else {
        decision.edited = this.text() as never;
      }
    }
    this.decided.emit(decision);
  }

  protected reject(): void {
    if (!this.comment().trim()) return;
    this.decided.emit({ decision: 'reject', comment: this.comment().trim() });
  }
}
