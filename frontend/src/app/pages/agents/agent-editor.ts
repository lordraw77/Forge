import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { A11yModule } from '@angular/cdk/a11y';
import { TextFieldModule } from '@angular/cdk/text-field';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzDrawerModule } from 'ng-zorro-antd/drawer';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';
import { firstValueFrom } from 'rxjs';

import {
  AgentConfig,
  AgentDetail,
  AgentVersionOut,
  AgentsService,
  BuiltinToolRef,
  PipelineOut,
  PromptOut,
  PromptsService,
  RagService,
  RagTool,
  Variable,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { ModelOption, ModelOptions } from '../prompts/model-options';

interface PipelinePick {
  pipelineId: string;
  /** null = follow the published version when the agent version is saved. */
  version: number | null;
  name?: string | null;
}

interface Draft {
  promptId: string | null;
  promptVersion: number | null;
  variables: Record<string, string>;
  model: string;
  pipelines: PipelinePick[];
  httpGet: boolean;
  hosts: string[];
  currentTime: boolean;
  maxSteps: number;
  maxTokens: number;
  timeoutS: number;
  maxCostUsd: number;
  temperature: number | null;
  maxOutputTokens: number | null;
}

function defaults(): Draft {
  // The server's defaults (US-1.18.1 #2).
  return {
    promptId: null,
    promptVersion: null,
    variables: {},
    model: 'default-chat',
    pipelines: [],
    httpGet: false,
    hosts: [],
    currentTime: false,
    maxSteps: 8,
    maxTokens: 50000,
    timeoutS: 120,
    maxCostUsd: 1,
    temperature: 0.2,
    maxOutputTokens: null,
  };
}

function fromConfig(cfg: AgentConfig): Draft {
  const d = defaults();
  d.promptId = cfg.prompt.prompt_id;
  d.promptVersion = cfg.prompt.version ?? null;
  d.variables = { ...(cfg.prompt.variables ?? {}) };
  d.model = cfg.model ?? d.model;
  for (const t of cfg.tools ?? []) {
    if (t.kind === 'rag') {
      const rag = t as RagTool;
      d.pipelines.push({
        pipelineId: rag.pipeline_id,
        version: rag.version ?? null,
        name: rag.name,
      });
    } else {
      const builtin = t as BuiltinToolRef;
      if (builtin.name === 'http_get') {
        d.httpGet = true;
        d.hosts = [...(builtin.allowed_hosts ?? [])];
      } else {
        d.currentTime = true;
      }
    }
  }
  d.maxSteps = cfg.limits?.max_steps ?? d.maxSteps;
  d.maxTokens = cfg.limits?.max_tokens ?? d.maxTokens;
  d.timeoutS = cfg.limits?.timeout_s ?? d.timeoutS;
  d.maxCostUsd = cfg.limits?.max_cost_usd ?? d.maxCostUsd;
  d.temperature = cfg.temperature ?? null;
  d.maxOutputTokens = cfg.max_output_tokens ?? null;
  return d;
}

/** New / Edit agent (US-1.18.1): prompt + model alias + tools + RAG pipelines + limits. Every
 * save is a new immutable version; the prompt and pipelines are pinned to versions on save. */
@Component({
  selector: 'forge-agent-editor',
  imports: [
    A11yModule,
    DatePipe,
    FormsModule,
    RouterLink,
    TextFieldModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzCheckboxModule,
    NzDrawerModule,
    NzIconModule,
    NzInputModule,
    NzInputNumberModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule,
  ],
  templateUrl: './agent-editor.html',
  styleUrls: ['../admin/admin.scss', '../prompts/prompts.scss'],
})
export class AgentEditor {
  readonly id = input<string | undefined>(undefined);

  private readonly api = inject(AgentsService);
  private readonly prompts = inject(PromptsService);
  private readonly rag = inject(RagService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  private readonly modelOptions = inject(ModelOptions);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly agent = signal<AgentDetail | null>(null);
  protected readonly promptList = signal<PromptOut[]>([]);
  protected readonly pipelineList = signal<PipelineOut[]>([]);
  protected readonly chatModels = signal<ModelOption[]>([]);
  protected readonly promptVariables = signal<Variable[]>([]);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly saving = signal(false);
  protected readonly versions = signal<AgentVersionOut[]>([]);

  protected readonly promptVersionPlaceholder = $localize`published`;
  protected name = $localize`Untitled agent`;
  protected editingName = false;
  protected draft: Draft = defaults();
  protected note = '';
  protected describeOpen = false;
  protected description = '';
  protected versionsOpen = false;
  /** Pipelines in the dropdown that are not yet in the draft. */
  protected pipelineToAdd: string | null = null;
  private afterDescribe: 'chat' | 'stay' = 'chat';

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      const id = this.id();
      if (projectId) void this.load(projectId, id);
    });
  }

  private async load(projectId: string, id: string | undefined): Promise<void> {
    // Saving a new agent navigates to its edit URL, which re-runs this: keep the open draft.
    if (id && this.agent()?.id === id) return;
    this.message.set(null);
    if (!id) {
      this.agent.set(null);
      this.name = $localize`Untitled agent`;
      this.draft = defaults();
    }
    try {
      const [prompts, pipelines, chat] = await Promise.all([
        firstValueFrom(this.prompts.listPrompts({ projectId, limit: 200 })),
        firstValueFrom(this.rag.listRagPipelines({ projectId, limit: 200 })),
        this.modelOptions.load(projectId, 'chat'),
      ]);
      this.promptList.set(prompts.items);
      this.pipelineList.set(pipelines.items);
      this.chatModels.set(chat);
      if (!id) {
        if (!chat.some((o) => o.value === this.draft.model)) {
          this.draft.model = chat[0]?.value ?? this.draft.model;
        }
        this.draft.promptId ??= prompts.items[0]?.id ?? null;
        await this.loadPromptVariables();
        return;
      }
      const a = await firstValueFrom(this.api.getAgent({ projectId, agentId: id }));
      this.agent.set(a);
      this.name = a.name;
      this.draft = fromConfig(a.version.config);
      await this.loadPromptVariables();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  /** The variables the chosen prompt declares, to give them values for this agent. */
  protected async loadPromptVariables(): Promise<void> {
    const projectId = this.projectId();
    const promptId = this.draft.promptId;
    if (!projectId || !promptId) {
      this.promptVariables.set([]);
      return;
    }
    try {
      const p = await firstValueFrom(this.prompts.getPrompt({ projectId, promptId }));
      this.promptVariables.set(p.version.variables);
    } catch {
      this.promptVariables.set([]);
    }
  }

  protected pickPrompt(): void {
    this.draft.promptVersion = null;
    this.draft.variables = {};
    void this.loadPromptVariables();
  }

  protected pipelineName(pick: PipelinePick): string {
    return this.pipelineList().find((p) => p.id === pick.pipelineId)?.name ?? pick.name ?? '?';
  }

  protected availablePipelines(): PipelineOut[] {
    const used = new Set(this.draft.pipelines.map((p) => p.pipelineId));
    return this.pipelineList().filter((p) => !used.has(p.id));
  }

  protected addPipeline(): void {
    if (!this.pipelineToAdd) return;
    this.draft.pipelines = [
      ...this.draft.pipelines,
      { pipelineId: this.pipelineToAdd, version: null },
    ];
    this.pipelineToAdd = null;
  }

  protected removePipeline(pick: PipelinePick): void {
    this.draft.pipelines = this.draft.pipelines.filter((p) => p !== pick);
  }

  protected canSave(): boolean {
    return !this.saving() && !!this.draft.promptId && !!this.draft.model;
  }

  protected hasTools(): boolean {
    return this.draft.pipelines.length > 0 || this.draft.httpGet || this.draft.currentTime;
  }

  protected save(then: 'chat' | 'stay'): void {
    if (!this.agent()) {
      this.afterDescribe = then;
      this.describeOpen = true;
      return;
    }
    void this.persist(then);
  }

  protected confirmDescription(): void {
    if (!this.description.trim()) return;
    this.describeOpen = false;
    void this.persist(this.afterDescribe);
  }

  private config(): AgentConfig {
    const d = this.draft;
    const variables = Object.fromEntries(
      Object.entries(d.variables).filter(([, value]) => value !== ''),
    );
    const tools: AgentConfig['tools'] = d.pipelines.map((p): RagTool => ({
      kind: 'rag',
      pipeline_id: p.pipelineId,
      version: p.version,
      name: p.name ?? null,
    }));
    if (d.httpGet) {
      tools.push({ kind: 'builtin', name: 'http_get', allowed_hosts: d.hosts });
    }
    if (d.currentTime) tools.push({ kind: 'builtin', name: 'current_time' });
    return {
      prompt: { prompt_id: d.promptId!, version: d.promptVersion, variables },
      model: d.model,
      tools,
      limits: {
        max_steps: d.maxSteps,
        max_tokens: d.maxTokens,
        timeout_s: d.timeoutS,
        max_cost_usd: d.maxCostUsd,
      },
      temperature: d.temperature,
      max_output_tokens: d.maxOutputTokens,
    };
  }

  private async persist(then: 'chat' | 'stay'): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.saving.set(true);
    this.message.set(null);
    try {
      const existing = this.agent();
      const saved = existing
        ? await firstValueFrom(
            this.api.saveAgentVersion({
              projectId,
              agentId: existing.id,
              agentVersionIn: { config: this.config(), note: this.note },
            }),
          )
        : await firstValueFrom(
            this.api.createAgent({
              projectId,
              agentCreateIn: {
                name: this.name.trim() || $localize`Untitled agent`,
                description: this.description.trim(),
                config: this.config(),
              },
            }),
          );
      this.agent.set(saved);
      this.note = '';
      this.draft = fromConfig(saved.version.config); // references now show their pinned versions
      if (then === 'chat') {
        await this.router.navigate(['/agents/chat'], { queryParams: { agent: saved.id } });
      } else {
        if (!existing)
          await this.router.navigate(['/agents', saved.id, 'edit'], { replaceUrl: true });
        this.message.set({ ok: true, text: $localize`Saved version ${saved.latest_version}:n:.` });
      }
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.saving.set(false);
    }
  }

  protected async publish(): Promise<void> {
    const a = this.agent();
    const projectId = this.projectId();
    if (!a || !projectId) return;
    try {
      const published = await firstValueFrom(
        this.api.publishAgent({ projectId, agentId: a.id, agentPublishIn: {} }),
      );
      this.agent.set(published);
      this.message.set({
        ok: true,
        text: $localize`Published version ${published.published_version}:n:.`,
      });
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async unpublish(): Promise<void> {
    const a = this.agent();
    const projectId = this.projectId();
    if (!a || !projectId) return;
    try {
      this.agent.set(
        await firstValueFrom(this.api.removeAgentTag({ projectId, agentId: a.id, tag: 'prod' })),
      );
      this.message.set({ ok: true, text: $localize`Unpublished.` });
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async commitName(): Promise<void> {
    this.editingName = false;
    const a = this.agent();
    const name = this.name.trim();
    if (!name) {
      this.name = a?.name ?? $localize`Untitled agent`;
      return;
    }
    if (!a || name === a.name) return;
    try {
      this.agent.set(
        await firstValueFrom(
          this.api.updateAgent({
            projectId: this.projectId()!,
            agentId: a.id,
            agentUpdateIn: { name },
          }),
        ),
      );
    } catch (e) {
      this.name = a.name;
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async openVersions(): Promise<void> {
    const a = this.agent();
    if (!a) return;
    this.versionsOpen = true;
    try {
      this.versions.set(
        await firstValueFrom(
          this.api.listAgentVersions({ projectId: this.projectId()!, agentId: a.id }),
        ),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected restore(v: AgentVersionOut): void {
    this.draft = fromConfig(v.config);
    this.note = $localize`Restored from v${v.version}:n:`;
    this.versionsOpen = false;
    void this.loadPromptVariables();
  }

  protected chat(): void {
    const a = this.agent();
    if (a) void this.router.navigate(['/agents/chat'], { queryParams: { agent: a.id } });
  }
}
