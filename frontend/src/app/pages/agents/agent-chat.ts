import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { TextFieldModule } from '@angular/cdk/text-field';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  AgentCitation,
  AgentsService,
  AvailableAgent,
  ConversationOut,
  MessageOut,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { Segment, failureLabel, segments, statusLabel } from './agent-util';

interface LiveStep {
  ordinal: number;
  kind: string;
  name: string;
  status?: string;
  detail?: string;
}

interface Turn {
  runId: string;
  question: string;
  answer: string;
  status: MessageOut['status'];
  steps: LiveStep[];
  citations: AgentCitation[];
  error?: string;
  feedback?: 'up' | 'down';
  comment?: string;
}

/** Chat with an agent (US-1.19.1, US-1.19.2): the answer streams over SSE, tool steps are
 * collapsible, citations are links, Stop cancels the run, thumbs rate it. */
@Component({
  selector: 'forge-agent-chat',
  imports: [
    FormsModule,
    RouterLink,
    TextFieldModule,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzTagModule,
  ],
  template: `
    <div class="chat">
      <aside class="side">
        <label for="chat-agent" class="muted small" i18n>Agent</label>
        <nz-select
          nzId="chat-agent"
          nzShowSearch
          data-testid="chat-agent"
          [ngModel]="agentId()"
          (ngModelChange)="pickAgent($event)"
          [nzNotFoundContent]="noAgents"
        >
          @for (a of agents(); track a.id) {
            <nz-option [nzValue]="a.id" [nzLabel]="a.published ? a.name : a.name + ' (draft)'" />
          }
        </nz-select>
        <ng-template #noAgents>
          <span class="muted" i18n>No agent is published yet.</span>
        </ng-template>
        <button nz-button [disabled]="!agentId()" (click)="newChat()" data-testid="chat-new">
          <nz-icon nzType="plus" /><span i18n>New chat</span>
        </button>
        @for (c of conversations(); track c.id) {
          <button
            type="button"
            class="conversation"
            [class.active]="c.id === conversationId()"
            (click)="open(c.id)"
            [attr.data-testid]="'chat-conversation'"
          >
            {{ c.title || '…' }}
          </button>
        }
      </aside>

      <section class="main">
        @if (message(); as m) {
          <nz-alert
            [nzType]="m.ok ? 'success' : 'error'"
            [nzMessage]="m.text"
            nzShowIcon
            nzCloseable
            (nzOnClose)="message.set(null)"
            data-testid="chat-message"
          />
        }
        @if (!agentId()) {
          <p class="muted" i18n>Pick an agent to start a conversation.</p>
        } @else {
          <div class="thread" aria-live="polite" data-testid="chat-thread" #thread>
            @for (t of turns(); track t.runId) {
              <div class="bubble user" data-testid="chat-user">
                <p>{{ t.question }}</p>
              </div>
              <div class="bubble assistant" data-testid="chat-assistant">
                @if (t.steps.length) {
                  <details class="steps" data-testid="chat-steps">
                    <summary i18n>{{ t.steps.length }} steps</summary>
                    <ul>
                      @for (s of t.steps; track s.ordinal) {
                        <li [class]="s.status ?? ''">
                          <nz-icon [nzType]="s.kind === 'tool' ? 'tool' : 'robot'" />
                          {{ s.name }}
                          @if (s.status === 'error') {
                            <span i18n>(failed)</span>
                          }
                          @if (s.detail) {
                            <span class="muted"> · {{ s.detail }}</span>
                          }
                        </li>
                      }
                    </ul>
                  </details>
                }
                @if (t.status === 'running' || t.status === 'queued') {
                  <p data-testid="chat-answer">
                    @for (seg of live(t); track $index) {
                      {{ seg.text }}
                    }
                    @if (!t.answer) {
                      <span class="muted" i18n>Working…</span>
                    }
                  </p>
                } @else {
                  <p data-testid="chat-answer">
                    @for (seg of live(t); track $index) {
                      @if (seg.citation; as c) {
                        <a
                          class="cite"
                          [href]="c.url"
                          target="_blank"
                          rel="noopener"
                          [title]="c.document_name"
                          >{{ seg.text }}</a
                        >
                      } @else {
                        {{ seg.text }}
                      }
                    }
                  </p>
                }
                @if (t.status === 'failed' || t.status === 'cancelled') {
                  <nz-alert
                    [nzType]="t.status === 'failed' ? 'error' : 'warning'"
                    [nzMessage]="t.error ?? ''"
                    nzShowIcon
                    data-testid="chat-failure"
                  />
                }
                @if (t.citations.length) {
                  <ol class="sources" data-testid="chat-sources">
                    @for (c of t.citations; track c.chunk_id) {
                      <li [value]="c.index">
                        <a [href]="c.url" target="_blank" rel="noopener">
                          {{ c.document_name }}
                          @if (c.page) {
                            <span i18n>, page {{ c.page }}</span>
                          }
                        </a>
                        @if (c.heading_path) {
                          <span class="muted"> · {{ c.heading_path }}</span>
                        }
                      </li>
                    }
                  </ol>
                }
                @if (t.status !== 'running' && t.status !== 'queued') {
                  <div class="feedback">
                    <button
                      nz-button
                      nzType="text"
                      nzSize="small"
                      aria-label="Good answer"
                      i18n-aria-label
                      [class.active]="t.feedback === 'up'"
                      (click)="rate(t, 'up')"
                      data-testid="chat-thumbs-up"
                    >
                      <nz-icon nzType="like" [nzTheme]="t.feedback === 'up' ? 'fill' : 'outline'" />
                    </button>
                    <button
                      nz-button
                      nzType="text"
                      nzSize="small"
                      aria-label="Bad answer"
                      i18n-aria-label
                      (click)="rate(t, 'down')"
                      data-testid="chat-thumbs-down"
                    >
                      <nz-icon
                        nzType="dislike"
                        [nzTheme]="t.feedback === 'down' ? 'fill' : 'outline'"
                      />
                    </button>
                    @if (t.feedback) {
                      <input
                        nz-input
                        nzSize="small"
                        class="comment"
                        maxlength="2000"
                        placeholder="Add a comment (optional)"
                        i18n-placeholder
                        aria-label="Feedback comment"
                        i18n-aria-label
                        data-testid="chat-comment"
                        [(ngModel)]="t.comment"
                        (change)="rate(t, t.feedback)"
                      />
                    }
                    <span class="spacer"></span>
                    <nz-tag>{{ statusText(t.status) }}</nz-tag>
                    <a
                      nz-button
                      nzType="link"
                      nzSize="small"
                      [routerLink]="['/agents/runs', t.runId]"
                      data-testid="chat-run-link"
                      i18n
                      >Details</a
                    >
                  </div>
                }
              </div>
            }
          </div>
          <form class="composer" (ngSubmit)="send()">
            <textarea
              nz-input
              name="content"
              aria-label="Message"
              i18n-aria-label
              placeholder="Ask the agent…"
              i18n-placeholder
              maxlength="20000"
              cdkTextareaAutosize
              cdkAutosizeMinRows="2"
              cdkAutosizeMaxRows="8"
              data-testid="chat-input"
              [(ngModel)]="draft"
              [disabled]="busy()"
              (keydown.enter)="enter($event)"
            ></textarea>
            @if (busy()) {
              <button nz-button nzDanger type="button" (click)="stop()" data-testid="chat-stop">
                <nz-icon nzType="stop" /><span i18n>Stop</span>
              </button>
            } @else {
              <button
                nz-button
                nzType="primary"
                type="submit"
                [disabled]="!draft.trim()"
                data-testid="chat-send"
              >
                <nz-icon nzType="send" /><span i18n>Send</span>
              </button>
            }
          </form>
        }
      </section>
    </div>
  `,
  styleUrls: ['../admin/admin.scss', '../prompts/prompts.scss', './agents.scss'],
})
export class AgentChat {
  private readonly api = inject(AgentsService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly thread = viewChild<ElementRef<HTMLElement>>('thread');

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly agents = signal<AvailableAgent[]>([]);
  protected readonly agentId = signal<string | null>(null);
  protected readonly conversations = signal<ConversationOut[]>([]);
  protected readonly conversationId = signal<string | null>(null);
  protected readonly turns = signal<Turn[]>([]);
  protected readonly busy = signal(false);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected draft = '';

  private source: EventSource | null = null;
  private activeRun: string | null = null;

  constructor() {
    this.destroyRef.onDestroy(() => this.closeStream());
    effect(() => {
      const projectId = this.projectId();
      if (projectId) void this.init(projectId);
    });
  }

  private async init(projectId: string): Promise<void> {
    try {
      const list = await firstValueFrom(this.api.listAvailableAgents({ projectId }));
      this.agents.set(list);
      const wanted = this.route.snapshot.queryParamMap.get('agent');
      const first = list.find((a) => a.id === wanted) ?? list[0];
      if (first) await this.pickAgent(first.id, this.route.snapshot.queryParamMap.get('c'));
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async pickAgent(agentId: string, conversation: string | null = null): Promise<void> {
    this.closeStream();
    this.agentId.set(agentId);
    this.turns.set([]);
    this.conversationId.set(null);
    this.busy.set(false);
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const list = await firstValueFrom(
        this.api.listConversations({ projectId, agentId, limit: 30 }),
      );
      this.conversations.set(list);
      const target = list.find((c) => c.id === conversation) ?? null;
      if (target) await this.open(target.id);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected newChat(): void {
    this.closeStream();
    this.conversationId.set(null);
    this.turns.set([]);
    this.busy.set(false);
    this.message.set(null);
  }

  protected async open(id: string): Promise<void> {
    this.closeStream();
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const detail = await firstValueFrom(
        this.api.getConversation({ projectId, conversationId: id }),
      );
      this.conversationId.set(id);
      const turns: Turn[] = [];
      for (const m of detail.messages) {
        if (m.role === 'user') {
          turns.push({
            runId: m.run_id,
            question: m.content,
            answer: '',
            status: m.status,
            steps: [],
            citations: [],
          });
        } else {
          const t = turns[turns.length - 1];
          t.answer = m.content;
          t.status = m.status;
          t.citations = m.citations ?? [];
          t.feedback = m.feedback?.rating;
          t.comment = m.feedback?.comment;
        }
      }
      this.turns.set(turns);
      const last = turns[turns.length - 1];
      if (last && (last.status === 'running' || last.status === 'queued')) {
        this.busy.set(true);
        this.follow(last.runId);
      }
      this.scroll();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected enter(event: Event): void {
    const key = event as KeyboardEvent;
    if (key.shiftKey) return; // Shift+Enter: new line
    key.preventDefault();
    void this.send();
  }

  protected async send(): Promise<void> {
    const projectId = this.projectId();
    const agentId = this.agentId();
    const content = this.draft.trim();
    if (!projectId || !agentId || !content || this.busy()) return;
    this.message.set(null);
    this.busy.set(true);
    try {
      let conversation = this.conversationId();
      if (!conversation) {
        const created = await firstValueFrom(
          this.api.createConversation({ projectId, conversationIn: { agent_id: agentId } }),
        );
        conversation = created.id;
        this.conversationId.set(conversation);
        this.conversations.update((list) => [created, ...list]);
      }
      const run = await firstValueFrom(
        this.api.sendMessage({
          projectId,
          conversationId: conversation,
          messageIn: { content },
        }),
      );
      this.draft = '';
      this.turns.update((t) => [
        ...t,
        {
          runId: run.id,
          question: content,
          answer: '',
          status: run.status,
          steps: [],
          citations: [],
        },
      ]);
      this.follow(run.id);
      this.scroll();
    } catch (e) {
      this.busy.set(false);
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  /** Follow a run over SSE; the browser reconnects and resumes with Last-Event-ID by itself. */
  private follow(runId: string): void {
    const projectId = this.projectId();
    if (!projectId) return;
    this.closeStream();
    this.activeRun = runId;
    const source = new EventSource(`/api/v1/projects/${projectId}/runs/${runId}/events`);
    this.source = source;
    const on = (type: string, handler: (data: Record<string, unknown>) => void) =>
      source.addEventListener(type, (e) => {
        if (this.activeRun !== runId) return;
        handler(JSON.parse((e as MessageEvent<string>).data) as Record<string, unknown>);
        this.scroll();
      });
    on('run_resumed', () => this.patch(runId, (t) => ({ ...t, answer: '' }))); // a retry re-streams
    on('token', (d) =>
      this.patch(runId, (t) => ({ ...t, status: 'running', answer: t.answer + String(d['text']) })),
    );
    on('step_started', (d) =>
      this.patch(runId, (t) => ({
        ...t,
        status: 'running',
        steps: this.upsert(t.steps, {
          ordinal: Number(d['ordinal']),
          kind: String(d['kind']),
          name: String(d['name']),
        }),
      })),
    );
    on('step_finished', (d) =>
      this.patch(runId, (t) => ({
        ...t,
        steps: this.upsert(t.steps, {
          ordinal: Number(d['ordinal']),
          kind: String(d['kind']),
          name: String(d['name']),
          status: String(d['status']),
          detail: this.stepDetail(d),
        }),
      })),
    );
    on('citations', (d) =>
      this.patch(runId, (t) => ({ ...t, citations: d['citations'] as AgentCitation[] })),
    );
    const finish = (d: Record<string, unknown>) => {
      this.closeStream();
      this.busy.set(false);
      void this.settle(runId, d);
    };
    on('done', finish);
    on('error', finish);
    source.onerror = () => {
      // EventSource retries by itself; if the run is over the server ends the stream for good.
      if (source.readyState === EventSource.CLOSED && this.activeRun === runId) {
        this.busy.set(false);
        void this.settle(runId, {});
      }
    };
  }

  private stepDetail(d: Record<string, unknown>): string | undefined {
    const parts: string[] = [];
    if (d['latency_ms'] !== undefined) parts.push(`${d['latency_ms']} ms`);
    if (d['tokens_in'] !== undefined) parts.push(`${d['tokens_in']}→${d['tokens_out']} tok`);
    return parts.join(' · ') || undefined;
  }

  private upsert(steps: LiveStep[], step: LiveStep): LiveStep[] {
    const i = steps.findIndex((s) => s.ordinal === step.ordinal);
    if (i < 0) return [...steps, step];
    return steps.map((s, n) => (n === i ? { ...s, ...step } : s));
  }

  /** The stored run is the source of truth once it ends: final text, citations, failure. */
  private async settle(runId: string, event: Record<string, unknown>): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const run = await firstValueFrom(this.api.getRun({ projectId, runId }));
      this.patch(runId, (t) => ({
        ...t,
        answer: run.output ?? t.answer,
        status: run.status,
        citations: run.citations,
        error:
          run.status === 'failed' || run.status === 'cancelled'
            ? failureLabel(run.error_code, run.error)
            : undefined,
      }));
    } catch {
      this.patch(runId, (t) => ({
        ...t,
        status: event['status'] === 'succeeded' ? 'succeeded' : t.status,
      }));
    }
  }

  protected async stop(): Promise<void> {
    const projectId = this.projectId();
    const runId = this.activeRun;
    if (!projectId || !runId) return;
    try {
      await firstValueFrom(this.api.cancelRun({ projectId, runId }));
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async rate(t: Turn, rating: 'up' | 'down'): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      const saved = await firstValueFrom(
        this.api.putFeedback({
          projectId,
          runId: t.runId,
          feedbackIn: { rating, comment: t.comment ?? '' },
        }),
      );
      this.patch(t.runId, (x) => ({ ...x, feedback: saved.rating, comment: saved.comment }));
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected live(t: Turn): Segment[] {
    return segments(t.answer, t.citations);
  }

  protected statusText(status: MessageOut['status']): string {
    return statusLabel(status);
  }

  private patch(runId: string, fn: (t: Turn) => Turn): void {
    this.turns.update((list) => list.map((t) => (t.runId === runId ? fn(t) : t)));
  }

  private closeStream(): void {
    this.source?.close();
    this.source = null;
    this.activeRun = null;
  }

  private scroll(): void {
    queueMicrotask(() => {
      const el = this.thread()?.nativeElement;
      el?.lastElementChild?.scrollIntoView?.({ block: 'end' });
    });
  }
}
