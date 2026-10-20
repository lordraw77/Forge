import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { firstValueFrom } from 'rxjs';

import { UseCasesService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { emptyDefinition } from './usecase-util';

const MAX_REQUIREMENTS = 4000;

/** Build Your Own, page 1 (US-2.8.1): describe the use case in natural language and choose the
 * pattern. The draft is created; the Planner takes over on the next page. */
@Component({
  selector: 'forge-usecase-requirements',
  imports: [FormsModule, RouterLink, NzAlertModule, NzButtonModule, NzInputModule, NzSelectModule],
  template: `
    <div class="head">
      <h2 i18n>1. Requirements</h2>
      <span class="spacer"></span>
      <a nz-button routerLink="../library" i18n>Back to library</a>
    </div>
    <p class="intro" i18n>
      Describe what the use case should do, who uses it and what it produces. The Planner reads this
      to propose the workflow on the next page.
    </p>
    <form class="form narrow-form" (ngSubmit)="create()" data-testid="uc-requirements-form">
      <label for="rq-name" i18n>Name</label>
      <input
        nz-input
        id="rq-name"
        name="name"
        required
        maxlength="80"
        [(ngModel)]="name"
        data-testid="rq-name"
      />
      <label for="rq-text" i18n>What should it do?</label>
      <textarea
        nz-input
        id="rq-text"
        name="requirements"
        rows="10"
        [maxlength]="max"
        [(ngModel)]="requirements"
        data-testid="rq-text"
      ></textarea>
      <small class="muted">{{ requirements.length }} / {{ max }}</small>
      <label for="rq-pattern" i18n>Pattern</label>
      <nz-select nzId="rq-pattern" name="pattern" [(ngModel)]="pattern" data-testid="rq-pattern">
        <nz-option
          nzValue="supervisor"
          nzLabel="Supervisor: a team of agents working in order"
          i18n-nzLabel
        />
        <nz-option
          nzValue="graph"
          nzLabel="Graph: nodes with branches, loops and conditions"
          i18n-nzLabel
        />
      </nz-select>
      @if (error(); as e) {
        <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
      }
      <div class="buttons">
        <button
          nz-button
          nzType="primary"
          type="submit"
          [disabled]="!canCreate()"
          data-testid="rq-create"
          i18n
        >
          Create and plan
        </button>
      </div>
    </form>
  `,
  styles: `
    .narrow-form {
      max-width: 48rem;
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class UseCaseRequirements {
  private readonly api = inject(UseCasesService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);

  protected readonly max = MAX_REQUIREMENTS;
  protected name = '';
  protected requirements = '';
  protected pattern: 'supervisor' | 'graph' = 'graph';
  protected readonly error = signal<string | null>(null);
  private readonly busy = signal(false);
  protected readonly canCreate = computed(() => !this.busy());

  protected async create(): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId || !this.name.trim()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const created = await firstValueFrom(
        this.api.createUseCase({
          projectId,
          useCaseCreateIn: {
            name: this.name.trim(),
            pattern: this.pattern,
            requirements: this.requirements,
            description: this.requirements.slice(0, 200),
            definition: emptyDefinition(this.pattern),
          },
        }),
      );
      await this.router.navigate(['/build', created.id, 'workflow']);
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
