import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzCardModule } from 'ng-zorro-antd/card';
import { firstValueFrom } from 'rxjs';

import { ApprovalDetail, ApprovalsService, DecisionIn } from '../../api-client';
import { problemMessage } from '../../core/http';
import { ApprovalReview } from './approval-review';

/** The page behind the one-time link of an approval email (US-2.10.4): no sign-in, the token is
 * the credential. Opening it only shows the content; nothing is decided until a button is used. */
@Component({
  selector: 'forge-approve-link',
  imports: [ApprovalReview, NzAlertModule, NzCardModule],
  template: `
    <main class="page">
      <h1>Forge</h1>
      <nz-card>
        @if (gone()) {
          <nz-alert
            nzType="warning"
            nzShowIcon
            nzMessage="This link is not valid any more"
            i18n-nzMessage
            nzDescription="It was already used, the approval was decided or has expired."
            i18n-nzDescription
            data-testid="link-gone"
          />
        } @else if (detail(); as d) {
          <forge-approval-review
            [detail]="d"
            [busy]="busy()"
            [error]="error()"
            (decided)="decide($event)"
          />
        }
      </nz-card>
    </main>
  `,
  styles: `
    .page {
      max-width: 52rem;
      margin: 2rem auto;
      padding: 0 1rem;
    }
  `,
})
export class ApproveLink {
  private readonly api = inject(ApprovalsService);
  private readonly token = inject(ActivatedRoute).snapshot.paramMap.get('token') ?? '';

  protected readonly detail = signal<ApprovalDetail | null>(null);
  protected readonly gone = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      this.detail.set(await firstValueFrom(this.api.viewLink({ token: this.token })));
    } catch {
      this.gone.set(true);
    }
  }

  protected async decide(body: DecisionIn): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      this.detail.set(
        await firstValueFrom(this.api.decideLink({ token: this.token, decisionIn: body })),
      );
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
