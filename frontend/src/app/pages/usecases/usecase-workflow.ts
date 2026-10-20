import { CdkDragDrop, DragDropModule, moveItemInArray } from '@angular/cdk/drag-drop';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzSpinModule } from 'ng-zorro-antd/spin';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  MissingAsset,
  UseCaseDetail,
  UseCaseIssueOut,
  UseCaseUpdateIn,
  UseCasesService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { Assets, GraphCanvas } from './graph/graph-canvas';

interface ChatTurn {
  role: 'user' | 'assistant';
  text: string;
  definition?: Record<string, unknown> | null;
  issues?: UseCaseIssueOut[];
  missing?: MissingAsset[];
  applied?: boolean;
}
interface Member {
  agent_ref: string;
  task: string;
}

/** Build Your Own, page 2 (US-2.8.2, 2.8.3, 2.7.1): the Planner chat on the left, the workflow on
 * the right: an ordered agent team (supervisor) or the node canvas (graph). The Planner only
 * proposes; "Apply" puts its definition in the editor and "Save draft" stores it. */
@Component({
  selector: 'forge-usecase-workflow',
  imports: [
    DragDropModule,
    FormsModule,
    RouterLink,
    GraphCanvas,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzSpinModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <h2>
        <span i18n>2. Workflow</span>
        @if (detail(); as d) {
          <span class="muted"> · {{ d.name }}</span>
        }
      </h2>
      <span class="spacer"></span>
      @if (detail(); as d) {
        <a nz-button [routerLink]="['..', 'edit']" i18n>Settings</a>
        <button
          nz-button
          (click)="save()"
          [disabled]="saving() || !dirty()"
          data-testid="wf-save"
          i18n
        >
          Save draft
        </button>
        <a nz-button nzType="primary" [routerLink]="['..', 'test']" data-testid="wf-test" i18n
          >Test workflow</a
        >
      }
    </div>

    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }
    @if (issues().length) {
      <nz-alert nzType="warning" nzShowIcon [nzMessage]="issuesTitle" [nzDescription]="issueList" />
      <ng-template #issuesTitle><span i18n>The workflow needs attention</span></ng-template>
      <ng-template #issueList>
        <ul data-testid="wf-issues">
          @for (i of issues(); track $index) {
            <li>
              @if (i.node) {
                <code>{{ i.node }}</code
                >:
              }
              {{ i.message }}
            </li>
          }
        </ul>
      </ng-template>
    }

    <div class="layout">
      <section class="chat" aria-label="Planner" i18n-aria-label>
        <h3 i18n>Planner</h3>
        <div class="turns" data-testid="planner-turns">
          @for (t of turns(); track $index; let i = $index) {
            <div class="turn" [class.mine]="t.role === 'user'">
              <div class="bubble">{{ t.text }}</div>
              @if (t.missing?.length) {
                <div class="missing" data-testid="planner-missing">
                  <strong i18n>Missing capabilities</strong>
                  @for (m of t.missing; track m.name) {
                    <div>
                      <nz-tag>{{ m.kind }}</nz-tag> {{ m.name }}
                      <span class="muted">{{ m.purpose }}</span>
                      @if (m.kind === 'agent') {
                        <a
                          nz-button
                          nzType="link"
                          nzSize="small"
                          routerLink="/agents/new"
                          target="_blank"
                          i18n
                          >Create agent</a
                        >
                      }
                    </div>
                  }
                </div>
              }
              @if (t.definition) {
                @if (t.issues?.length) {
                  <nz-alert nzType="warning" nzShowIcon [nzMessage]="proposalIssues(t)" />
                }
                <button
                  nz-button
                  nzSize="small"
                  nzType="primary"
                  [disabled]="t.applied"
                  (click)="apply(i)"
                  data-testid="planner-apply"
                >
                  {{ t.applied ? appliedLabel : applyLabel }}
                </button>
              }
            </div>
          } @empty {
            <p class="muted" i18n>Ask the Planner to build the workflow, or to change it.</p>
          }
          @if (thinking()) {
            <nz-spin />
          }
        </div>
        <form (ngSubmit)="send()" class="composer">
          <textarea
            nz-input
            rows="3"
            name="message"
            maxlength="4000"
            [(ngModel)]="draft"
            placeholder="Describe the workflow or the change you want"
            i18n-placeholder
            aria-label="Message to the Planner"
            i18n-aria-label
            data-testid="planner-input"
            (keydown.control.enter)="send()"
          ></textarea>
          <button
            nz-button
            nzType="primary"
            type="submit"
            [disabled]="thinking() || !draft.trim()"
            data-testid="planner-send"
            i18n
          >
            Send
          </button>
        </form>
      </section>

      <section class="work" aria-label="Workflow" i18n-aria-label>
        @if (detail(); as d) {
          @if (d.pattern === 'graph') {
            <div class="canvas-box">
              <forge-graph-canvas
                [(definition)]="definition"
                [issues]="issues()"
                [assets]="assets()"
                (selectedNode)="selected.set($event)"
              />
            </div>
          } @else {
            <h3 i18n>Agent team</h3>
            <div cdkDropList class="team" (cdkDropListDropped)="drop($event)" data-testid="team">
              @for (m of team(); track m.agent_ref + $index; let i = $index) {
                <div cdkDrag class="tile" [attr.data-testid]="'member-' + i">
                  <div class="tile-head">
                    <span cdkDragHandle class="handle"><span nz-icon nzType="holder"></span></span>
                    <strong>{{ i === 0 ? leaderLabel : '#' + (i + 1) }}</strong>
                    <span class="grow">{{ m.agent_ref }}</span>
                    <button
                      nz-button
                      nzType="link"
                      nzDanger
                      nzSize="small"
                      (click)="removeMember(i)"
                      [attr.aria-label]="removeLabel"
                    >
                      <span nz-icon nzType="delete"></span>
                    </button>
                  </div>
                  <textarea
                    nz-input
                    rows="2"
                    [attr.aria-label]="taskLabel"
                    [ngModel]="m.task"
                    (ngModelChange)="setTask(i, $event)"
                    [name]="'task-' + i"
                  ></textarea>
                </div>
              } @empty {
                <p class="muted" i18n>No agents yet: ask the Planner or add one below.</p>
              }
            </div>
            <div class="row add">
              <nz-select
                class="grow"
                nzShowSearch
                [(ngModel)]="newAgent"
                nzPlaceHolder="Add an agent from the library"
                i18n-nzPlaceHolder
                data-testid="member-picker"
              >
                @for (a of assets().agent ?? []; track a.name) {
                  <nz-option
                    [nzValue]="a.name + '@' + a.version"
                    [nzLabel]="a.name + ' v' + a.version"
                  />
                }
              </nz-select>
              <button
                nz-button
                (click)="addMember()"
                [disabled]="!newAgent"
                data-testid="member-add"
                i18n
              >
                Add
              </button>
              <a nz-button nzType="link" routerLink="/agents/new" target="_blank" i18n>New agent</a>
            </div>
          }
        } @else {
          <nz-spin />
        }
      </section>
    </div>
  `,
  styles: `
    .layout {
      display: grid;
      grid-template-columns: minmax(20rem, 26rem) 1fr;
      gap: 1rem;
      align-items: start;
    }
    .chat {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      border: 1px solid var(--forge-surface-200);
      border-radius: 6px;
      padding: 0.75rem;
    }
    .turns {
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
      max-height: 60vh;
      overflow: auto;
    }
    .turn.mine {
      align-items: flex-end;
    }
    .turn {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }
    .bubble {
      background: var(--forge-surface-100);
      border-radius: 8px;
      padding: 0.5rem 0.7rem;
      white-space: pre-wrap;
      max-width: 100%;
    }
    .mine .bubble {
      background: var(--forge-primary-color);
      color: white;
    }
    .composer {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .canvas-box {
      height: 62vh;
    }
    .team {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .tile {
      border: 1px solid var(--forge-surface-300);
      border-radius: 6px;
      padding: 0.5rem 0.75rem;
      background: var(--forge-surface-0);
    }
    .tile-head {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.4rem;
    }
    .handle {
      cursor: grab;
    }
    .grow {
      flex: 1;
    }
    @media (max-width: 1000px) {
      .layout {
        grid-template-columns: 1fr;
      }
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class UseCaseWorkflow {
  private readonly api = inject(UseCasesService);
  private readonly session = inject(Session);
  private readonly route = inject(ActivatedRoute);

  private readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly detail = signal<UseCaseDetail | null>(null);
  protected readonly assets = signal<Assets>({});
  protected definition = signal<Record<string, unknown>>({});
  protected readonly issues = signal<UseCaseIssueOut[]>([]);
  protected readonly turns = signal<ChatTurn[]>([]);
  protected readonly thinking = signal(false);
  protected readonly saving = signal(false);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly selected = signal<string | null>(null);
  protected draft = '';
  protected newAgent: string | null = null;
  private saved = '';

  protected readonly applyLabel = $localize`Apply to workflow`;
  protected readonly appliedLabel = $localize`Applied`;
  protected readonly leaderLabel = $localize`Team Leader`;
  protected readonly removeLabel = $localize`Remove agent`;
  protected readonly taskLabel = $localize`Task`;

  protected readonly dirty = computed(() => JSON.stringify(this.definition()) !== this.saved);
  protected readonly team = computed<Member[]>(() => {
    const agents = this.definition()['agents'];
    return Array.isArray(agents) ? (agents as Member[]) : [];
  });

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
  }

  private async load(): Promise<void> {
    const projectId = this.projectId()!;
    const id = this.route.snapshot.paramMap.get('id')!;
    try {
      const [d, assets] = await Promise.all([
        firstValueFrom(this.api.getUseCase({ projectId, useCaseId: id })),
        firstValueFrom(this.api.useCaseAssets({ projectId })),
      ]);
      this.assets.set(assets as Assets);
      this.adopt(d);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  private adopt(d: UseCaseDetail): void {
    this.detail.set(d);
    this.definition.set(d.definition);
    this.saved = JSON.stringify(d.definition);
    this.issues.set(d.issues ?? []);
  }

  // ---- planner
  protected async send(): Promise<void> {
    const text = this.draft.trim();
    const projectId = this.projectId();
    const d = this.detail();
    if (!text || !projectId || !d || this.thinking()) return;
    const history = this.turns().map((t) => ({ role: t.role, content: t.text }));
    this.turns.update((t) => [...t, { role: 'user', text }]);
    this.draft = '';
    this.thinking.set(true);
    try {
      const plan = await firstValueFrom(
        this.api.planUseCase({
          projectId,
          useCaseId: d.id,
          planIn: { message: text, history, definition: this.definition() },
        }),
      );
      this.turns.update((t) => [
        ...t,
        {
          role: 'assistant',
          text: plan.reply,
          definition: plan.definition,
          issues: plan.issues,
          missing: plan.missing,
        },
      ]);
    } catch (e) {
      this.turns.update((t) => [...t, { role: 'assistant', text: problemMessage(e) }]);
    } finally {
      this.thinking.set(false);
    }
  }

  protected proposalIssues(t: ChatTurn): string {
    return $localize`The proposal still has problems: ${(t.issues ?? []).map((i) => i.message).join('; ')}:issues:`;
  }

  protected apply(index: number): void {
    const turn = this.turns()[index];
    if (!turn?.definition) return;
    this.definition.set(turn.definition);
    this.turns.update((all) => all.map((t, i) => (i === index ? { ...t, applied: true } : t)));
  }

  // ---- team (supervisor)
  protected drop(event: CdkDragDrop<Member[]>): void {
    const next = [...this.team()];
    moveItemInArray(next, event.previousIndex, event.currentIndex);
    this.setTeam(next);
  }

  private setTeam(agents: Member[]): void {
    this.definition.set({ ...this.definition(), agents });
  }

  protected addMember(): void {
    if (!this.newAgent) return;
    this.setTeam([...this.team(), { agent_ref: this.newAgent, task: '' }]);
    this.newAgent = null;
  }

  protected removeMember(i: number): void {
    this.setTeam(this.team().filter((_, k) => k !== i));
  }

  protected setTask(i: number, task: string): void {
    this.setTeam(this.team().map((m, k) => (k === i ? { ...m, task } : m)));
  }

  // ---- save
  protected async save(): Promise<void> {
    const projectId = this.projectId();
    const d = this.detail();
    if (!projectId || !d) return;
    this.saving.set(true);
    this.message.set(null);
    const body: UseCaseUpdateIn = {
      name: d.name,
      description: d.description,
      requirements: d.requirements,
      icon: d.icon,
      tags: d.tags,
      catalog_id: d.catalog_id,
      category_id: d.category_id,
      input_fields: d.input_fields as UseCaseUpdateIn['input_fields'],
      output_type: d.output_type as UseCaseUpdateIn['output_type'],
      output_schema: d.output_schema,
      defaults: d.defaults as UseCaseUpdateIn['defaults'],
      definition: this.definition(),
    };
    try {
      this.adopt(
        await firstValueFrom(
          this.api.updateUseCase({ projectId, useCaseId: d.id, useCaseUpdateIn: body }),
        ),
      );
      this.message.set({ ok: true, text: $localize`Draft saved` });
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.saving.set(false);
    }
  }
}
