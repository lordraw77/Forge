import { DatePipe } from '@angular/common';
import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSwitchModule } from 'ng-zorro-antd/switch';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { ApprovalDetail, ApprovalOut, ApprovalsService, DecisionIn } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { ApprovalReview } from './approval-review';

const COLOR: Record<string, string> = {
  pending: 'warning',
  approved: 'success',
  rejected: 'error',
  expired: 'default',
  cancelled: 'default',
};

/** Approvals inbox (US-2.10.3): what waits for a decision from the user's Role Name, and the
 * latest decided ones. */
@Component({
  selector: 'forge-approvals',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    ApprovalReview,
    NzAlertModule,
    NzButtonModule,
    NzModalModule,
    NzSwitchModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <h1 i18n>Approvals</h1>
      <span class="spacer"></span>
      <span class="check">
        <nz-switch
          aria-label="Show decided"
          [ngModel]="showDecided()"
          (ngModelChange)="showDecided.set($event)"
        />
        <span i18n>Show decided</span>
      </span>
      <a nz-button routerLink="/catalog" i18n>Catalog</a>
    </div>
    @if (!session.project()) {
      <nz-alert
        nzType="info"
        nzMessage="You are not a member of any project yet. Ask a Super Admin to add you."
        i18n-nzMessage
      />
    } @else if (!session.can('approval.decide')) {
      <nz-alert
        nzType="warning"
        nzMessage="Your role cannot decide approvals in this project."
        i18n-nzMessage
      />
    } @else {
      @if (error(); as e) {
        <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
      }
      <nz-table #table [nzData]="items()" nzSize="small" [nzPageSize]="20" data-testid="approvals">
        <thead>
          <tr>
            <th i18n>Use case</th>
            <th i18n>Step</th>
            <th i18n>Asked</th>
            <th i18n>Expires</th>
            <th i18n>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (a of table.data; track a.id) {
            <tr [attr.data-testid]="'approval-' + a.id">
              <td>{{ a.use_case_name }}</td>
              <td>{{ a.node_id }}</td>
              <td>{{ a.requested_at | date: 'short' }}</td>
              <td>{{ a.expires_at | date: 'short' }}</td>
              <td>
                <nz-tag [nzColor]="color(a.status)">{{ a.status }}</nz-tag>
              </td>
              <td class="actions">
                <button nz-button nzSize="small" (click)="open(a)" data-testid="approval-open">
                  @if (a.status === 'pending') {
                    <span i18n>Review</span>
                  } @else {
                    <span i18n>View</span>
                  }
                </button>
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="6" class="muted" i18n data-testid="approvals-empty">
                Nothing is waiting for you.
              </td>
            </tr>
          }
        </tbody>
      </nz-table>
    }

    <nz-modal
      [nzVisible]="!!current()"
      (nzVisibleChange)="$event || current.set(null)"
      nzTitle="Review"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="760"
    >
      <ng-container *nzModalContent>
        @if (current(); as d) {
          <forge-approval-review
            [detail]="d"
            [busy]="busy()"
            [error]="decideError()"
            (decided)="decide($event)"
          />
        }
      </ng-container>
    </nz-modal>
  `,
  styleUrl: '../admin/admin.scss',
})
export class ApprovalsPage {
  private readonly api = inject(ApprovalsService);
  protected readonly session = inject(Session);

  protected readonly items = signal<ApprovalOut[]>([]);
  protected readonly showDecided = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly current = signal<ApprovalDetail | null>(null);
  protected readonly busy = signal(false);
  protected readonly decideError = signal<string | null>(null);

  constructor() {
    effect(() => {
      const pendingOnly = !this.showDecided();
      if (this.session.project() && this.session.can('approval.decide')) {
        void this.load(pendingOnly);
      }
    });
  }

  protected color(status: string): string {
    return COLOR[status] ?? 'default';
  }

  private async load(pendingOnly: boolean): Promise<void> {
    const projectId = this.session.project()!.id;
    try {
      this.items.set(
        await firstValueFrom(this.api.listApprovals({ projectId, pending: pendingOnly })),
      );
      this.error.set(null);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async open(a: ApprovalOut): Promise<void> {
    const projectId = this.session.project()!.id;
    this.decideError.set(null);
    try {
      this.current.set(await firstValueFrom(this.api.getApproval({ projectId, approvalId: a.id })));
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected async decide(body: DecisionIn): Promise<void> {
    const d = this.current();
    if (!d) return;
    this.busy.set(true);
    this.decideError.set(null);
    try {
      await firstValueFrom(
        this.api.decide({
          projectId: this.session.project()!.id,
          approvalId: d.id,
          decisionIn: body,
        }),
      );
      this.current.set(null);
      await this.load(!this.showDecided());
    } catch (e) {
      this.decideError.set(problemMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
