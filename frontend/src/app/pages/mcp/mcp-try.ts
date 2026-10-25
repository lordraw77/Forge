import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { firstValueFrom } from 'rxjs';

import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** The parts of an MCP reply this page reads. */
interface JsonSchema {
  properties?: Record<string, { type?: string; default?: unknown }>;
  required?: string[];
}
interface McpTool {
  name: string;
  description?: string;
  inputSchema?: JsonSchema;
}
interface McpPrompt {
  name: string;
  description?: string;
  arguments?: { name: string }[];
}
interface McpContent {
  type?: string;
  text?: string;
}
interface McpResult {
  tools?: McpTool[];
  prompts?: McpPrompt[];
  content?: McpContent[];
  messages?: { content: McpContent }[];
  isError?: boolean;
  structuredContent?: { conversation_id?: string };
}

interface Offer {
  kind: 'tool' | 'prompt';
  name: string;
  description: string;
  /** JSON text for the arguments, with one placeholder per declared property. */
  skeleton: string;
}

/** Calls the project's MCP server as a client would (tools/list, prompts/list, tools/call,
 * prompts/get), signed in with the user's session instead of an API key. Calling a tool runs it
 * for real: an agent answers, a search reads documents. */
@Component({
  selector: 'forge-mcp-try',
  imports: [
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
  ],
  template: `
    @if (error()) {
      <nz-alert nzType="error" [nzMessage]="error()" />
    }
    <p class="intro" i18n>
      This is what an MCP client gets from the server right now. Pick a tool or a prompt, fill in
      the arguments and send: it runs for real, as you.
    </p>
    <div class="pick">
      <nz-select
        nzId="try-offer"
        nzShowSearch
        nzPlaceHolder="Tool or prompt"
        i18n-nzPlaceHolder
        aria-label="Tool or prompt"
        i18n-aria-label
        [ngModel]="selected()"
        (ngModelChange)="choose($event)"
      >
        @for (o of offers(); track o.kind + o.name) {
          <nz-option [nzValue]="o.kind + ':' + o.name" [nzLabel]="o.name + ' (' + o.kind + ')'" />
        }
      </nz-select>
      <button nz-button (click)="refresh()" i18n>Reload list</button>
    </div>
    @if (current(); as o) {
      <p class="hint">{{ o.description }}</p>
      <label for="try-args" i18n>Arguments (JSON)</label>
      <textarea nz-input id="try-args" rows="6" class="mono" [(ngModel)]="args"></textarea>
      <div class="footer">
        <button
          nz-button
          nzType="primary"
          [disabled]="busy()"
          (click)="confirming.set(o)"
          data-testid="try-send"
          i18n
        >
          Send
        </button>
      </div>
      <nz-modal
        [nzVisible]="!!confirming()"
        nzTitle="Run it for real?"
        i18n-nzTitle
        nzOkText="Run"
        i18n-nzOkText
        (nzOnOk)="runConfirmed()"
        (nzOnCancel)="confirming.set(null)"
        [nzOkDisabled]="busy()"
      >
        <ng-container *nzModalContent>
          <p i18n>
            “{{ o.name }}” runs now as you, not as a simulation: an agent or a prompt uses model
            tokens and a data tool reads real documents. Run it?
          </p>
        </ng-container>
      </nz-modal>
    } @else if (loaded() && !offers().length) {
      <nz-alert
        nzType="info"
        nzMessage="Nothing is exposed (or everything is paused). Add something in the Exposed tab."
        i18n-nzMessage
      />
    }
    @if (answer()) {
      <h2 i18n>Answer</h2>
      <pre class="out" data-testid="try-answer">{{ answer() }}</pre>
      <details>
        <summary i18n>Raw response</summary>
        <pre class="out">{{ raw() }}</pre>
      </details>
    }
  `,
  styleUrl: '../admin/admin.scss',
  styles: `
    .pick {
      display: flex;
      gap: 0.5rem;
      align-items: center;
      nz-select {
        min-width: 24rem;
      }
    }
    .mono,
    .out {
      font-family: monospace;
      font-size: 0.8rem;
    }
    .out {
      white-space: pre-wrap;
      word-break: break-word;
      padding: 0.75rem;
      border: 1px solid var(--forge-border-color, #d9d9d9);
      border-radius: 4px;
    }
    .hint {
      color: var(--forge-text-muted-color);
      font-size: 0.8rem;
    }
    .footer {
      display: flex;
      justify-content: flex-end;
      margin-top: 0.75rem;
    }
    h2 {
      margin-top: 1.25rem;
    }
  `,
})
export class McpTry {
  private readonly http = inject(HttpClient);
  private readonly route = inject(ActivatedRoute);
  private readonly session = inject(Session);

  protected readonly offers = signal<Offer[]>([]);
  protected readonly selected = signal<string | null>(null);
  protected readonly loaded = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly answer = signal('');
  protected readonly raw = signal('');
  protected args = '{}';
  private nextId = 1;
  private readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly current = computed(
    () => this.offers().find((o) => `${o.kind}:${o.name}` === this.selected()) ?? null,
  );

  constructor() {
    effect(() => {
      if (this.projectId()) void this.refresh();
    });
  }

  private async rpc(method: string, params?: unknown): Promise<McpResult | undefined> {
    const reply = await firstValueFrom(
      this.http.post<{ result?: McpResult; error?: { message: string } }>(
        `/api/v1/projects/${this.projectId()}/mcp`,
        {
          jsonrpc: '2.0',
          id: this.nextId++,
          method,
          params,
        },
      ),
    );
    this.raw.set(JSON.stringify(reply, null, 2));
    if (reply?.error) throw new Error(reply.error.message);
    return reply?.result;
  }

  async refresh(): Promise<void> {
    try {
      const [tools, prompts] = await Promise.all([
        this.rpc('tools/list'),
        this.rpc('prompts/list'),
      ]);
      const offers: Offer[] = [
        ...(tools?.tools ?? []).map((t) => ({
          kind: 'tool' as const,
          name: t.name,
          description: t.description ?? '',
          skeleton: skeleton(t.inputSchema),
        })),
        ...(prompts?.prompts ?? []).map((p) => ({
          kind: 'prompt' as const,
          name: p.name,
          description: p.description ?? '',
          skeleton: JSON.stringify(
            Object.fromEntries((p.arguments ?? []).map((a) => [a.name, ''])),
            null,
            2,
          ),
        })),
      ];
      this.offers.set(offers);
      this.raw.set('');
      this.error.set(null);
      this.loaded.set(true);
      const wanted = this.route.snapshot.queryParamMap.get('name');
      const first = offers.find((o) => o.name === wanted) ?? offers[0];
      if (first && !this.current()) this.choose(`${first.kind}:${first.name}`);
    } catch (e) {
      this.error.set(failure(e));
    }
  }

  protected choose(key: string | null): void {
    this.selected.set(key);
    this.args = this.current()?.skeleton ?? '{}';
    this.answer.set('');
  }

  protected readonly confirming = signal<Offer | null>(null);

  protected runConfirmed(): void {
    const o = this.confirming();
    this.confirming.set(null);
    if (o) void this.send(o);
  }

  protected async send(o: Offer): Promise<void> {
    let parsed: unknown;
    try {
      parsed = JSON.parse(this.args || '{}');
    } catch {
      this.error.set($localize`The arguments are not valid JSON.`);
      return;
    }
    this.busy.set(true);
    this.answer.set('');
    try {
      const result =
        o.kind === 'tool'
          ? await this.rpc('tools/call', { name: o.name, arguments: parsed })
          : await this.rpc('prompts/get', { name: o.name, arguments: parsed });
      this.answer.set(show(result));
      this.error.set(null);
    } catch (e) {
      this.error.set(failure(e));
    } finally {
      this.busy.set(false);
    }
  }
}

/** The server's own words for a failed call (a JSON-RPC error), else the usual problem text. */
function failure(e: unknown): string {
  return e instanceof Error && !(e instanceof HttpErrorResponse) ? e.message : problemMessage(e);
}

/** A JSON skeleton of a tool's arguments: required properties first, empty values. */
function skeleton(schema: JsonSchema | undefined): string {
  const props = schema?.properties ?? {};
  const required: string[] = schema?.required ?? [];
  const names = [...required, ...Object.keys(props).filter((k) => !required.includes(k))];
  const value = (p: { type?: string; default?: unknown } | undefined) =>
    p?.type === 'integer' || p?.type === 'number' ? (p.default ?? 0) : (p?.default ?? '');
  return JSON.stringify(Object.fromEntries(names.map((n) => [n, value(props[n])])), null, 2);
}

/** The text of a tool result or of a rendered prompt, as a person would read it. */
function show(result: McpResult | undefined): string {
  const parts: McpContent[] = result?.content ?? result?.messages?.map((m) => m.content) ?? [];
  const text = parts.map((c) => (c?.type === 'text' ? c.text : JSON.stringify(c))).join('\n\n');
  const note = result?.isError ? '⚠ ' : '';
  const conv = result?.structuredContent?.conversation_id;
  return note + text + (conv ? `\n\nconversation_id: ${conv}` : '');
}
