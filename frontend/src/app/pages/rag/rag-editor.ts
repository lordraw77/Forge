import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { A11yModule } from '@angular/cdk/a11y';
import { TextFieldModule } from '@angular/cdk/text-field';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
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
  CollectionOut,
  DataService,
  PipelineConfig,
  PipelineDetail,
  PipelineVersionOut,
  PromptOut,
  PromptsService,
  RagService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { ModelOption, ModelOptions } from '../prompts/model-options';

type Draft = Omit<PipelineConfig, 'collection_id'> & { collection_id: string | null };

function defaults(): Draft {
  // Spec "RAG design" defaults (also the server's).
  return {
    collection_id: null,
    llm: 'default-chat',
    embedding_model: 'embedding',
    search_method: 'hybrid',
    reranker: 'rerank',
    prompt_id: null,
    chunk_size: 512,
    chunk_overlap: 64,
    node_parser: 'structure',
    response_mode: 'compact',
    top_k: 6,
    candidates: 40,
    similarity_threshold: 0.3,
    context_budget: 6000,
    temperature: 0.1,
  };
}

/** New / Edit RAG pipeline (US-1.17.1): Part 2 parameters; every save is a new version. */
@Component({
  selector: 'forge-rag-editor',
  imports: [
    A11yModule,
    DatePipe,
    FormsModule,
    RouterLink,
    TextFieldModule,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
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
  templateUrl: './rag-editor.html',
  styleUrls: ['../admin/admin.scss', '../prompts/prompts.scss'],
})
export class RagEditor {
  readonly id = input<string | undefined>(undefined);

  private readonly api = inject(RagService);
  private readonly data = inject(DataService);
  private readonly prompts = inject(PromptsService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  private readonly modelOptions = inject(ModelOptions);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly pipeline = signal<PipelineDetail | null>(null);
  protected readonly collections = signal<CollectionOut[]>([]);
  protected readonly chatModels = signal<ModelOption[]>([]);
  protected readonly embeddingModels = signal<ModelOption[]>([]);
  protected readonly rerankers = signal<ModelOption[]>([]);
  protected readonly promptList = signal<PromptOut[]>([]);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly saving = signal(false);
  protected readonly versions = signal<PipelineVersionOut[]>([]);

  protected name = $localize`Untitled RAG`;
  protected editingName = false;
  protected draft: Draft = defaults();
  protected note = '';
  protected describeOpen = false;
  protected description = '';
  protected versionsOpen = false;
  private afterDescribe: 'test' | 'stay' = 'test';

  protected readonly searchMethods = [
    { value: 'semantic', label: $localize`Semantic Search` },
    { value: 'hybrid', label: $localize`Hybrid Search` },
    { value: 'vector', label: $localize`Vector Search` },
  ];
  protected readonly parsers = [
    { value: 'structure', label: $localize`Structure-aware (by heading)` },
    { value: 'recursive', label: $localize`Recursive (paragraphs, sentences)` },
    { value: 'fixed', label: $localize`Fixed size` },
  ];
  protected readonly responseModes = [
    { value: 'compact', label: $localize`Compact (all chunks in one prompt)` },
    { value: 'refine', label: $localize`Refine (one chunk at a time)` },
  ];

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      const id = this.id();
      if (projectId) void this.load(projectId, id);
    });
  }

  private async load(projectId: string, id: string | undefined): Promise<void> {
    // Saving a new item navigates to its edit URL, which re-runs this: do not reload (and wipe the
    // draft and the "saved" message) for the item that is already open.
    if (id && this.pipeline()?.id === id) return;
    this.message.set(null);
    if (!id) {
      // Defaults first: the user may type before the dropdown options arrive.
      this.pipeline.set(null);
      this.name = $localize`Untitled RAG`;
      this.draft = defaults();
    }
    try {
      const [collections, chat, embedding, rerank, prompts] = await Promise.all([
        firstValueFrom(this.data.listCollections({ projectId })),
        this.modelOptions.load(projectId, 'chat'),
        this.modelOptions.load(projectId, 'embedding'),
        this.modelOptions.load(projectId, 'reranker'),
        firstValueFrom(this.prompts.listPrompts({ projectId, limit: 200 })),
      ]);
      this.collections.set(collections);
      this.chatModels.set(chat);
      this.embeddingModels.set(embedding);
      this.rerankers.set(rerank);
      this.promptList.set(prompts.items);
      if (!id) {
        // Fill only what the user has not chosen yet.
        const fallback = (options: ModelOption[], current: string | null | undefined) =>
          options.some((o) => o.value === current) ? current : (options[0]?.value ?? current);
        this.draft.collection_id ??= collections[0]?.id ?? null;
        this.draft.llm = fallback(chat, this.draft.llm) ?? undefined;
        this.draft.embedding_model = fallback(embedding, this.draft.embedding_model) ?? undefined;
        if (!rerank.length) this.draft.reranker = null;
        else this.draft.reranker = fallback(rerank, this.draft.reranker);
        return;
      }
      const p = await firstValueFrom(this.api.getRagPipeline({ projectId, pipelineId: id }));
      this.pipeline.set(p);
      this.name = p.name;
      this.draft = { ...defaults(), ...p.version.config };
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected canSave(): boolean {
    return !this.saving() && !!this.draft.collection_id && !!this.draft.llm;
  }

  protected save(then: 'test' | 'stay'): void {
    if (!this.pipeline()) {
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

  private config(): PipelineConfig {
    return { ...this.draft, collection_id: this.draft.collection_id! };
  }

  private async persist(then: 'test' | 'stay'): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.saving.set(true);
    this.message.set(null);
    try {
      const existing = this.pipeline();
      const saved = existing
        ? await firstValueFrom(
            this.api.saveRagPipelineVersion({
              projectId,
              pipelineId: existing.id,
              pipelineVersionIn: { config: this.config(), note: this.note },
            }),
          )
        : await firstValueFrom(
            this.api.createRagPipeline({
              projectId,
              pipelineCreateIn: {
                name: this.name.trim() || $localize`Untitled RAG`,
                description: this.description.trim(),
                config: this.config(),
              },
            }),
          );
      this.pipeline.set(saved);
      this.note = '';
      if (then === 'test') {
        await this.router.navigate(['/rag', saved.id, 'test']);
      } else {
        if (!existing) await this.router.navigate(['/rag', saved.id, 'edit'], { replaceUrl: true });
        this.message.set({ ok: true, text: $localize`Saved version ${saved.latest_version}:n:.` });
      }
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.saving.set(false);
    }
  }

  protected async commitName(): Promise<void> {
    this.editingName = false;
    const p = this.pipeline();
    const name = this.name.trim();
    if (!name) {
      this.name = p?.name ?? $localize`Untitled RAG`;
      return;
    }
    if (!p || name === p.name) return;
    try {
      this.pipeline.set(
        await firstValueFrom(
          this.api.updateRagPipeline({
            projectId: this.projectId()!,
            pipelineId: p.id,
            pipelineUpdateIn: { name },
          }),
        ),
      );
    } catch (e) {
      this.name = p.name;
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async openVersions(): Promise<void> {
    const p = this.pipeline();
    if (!p) return;
    this.versionsOpen = true;
    try {
      this.versions.set(
        await firstValueFrom(
          this.api.listRagPipelineVersions({ projectId: this.projectId()!, pipelineId: p.id }),
        ),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected restore(v: PipelineVersionOut): void {
    this.draft = { ...defaults(), ...v.config };
    this.note = $localize`Restored from v${v.version}:n:`;
    this.versionsOpen = false;
  }

  protected test(): void {
    const p = this.pipeline();
    if (p) void this.router.navigate(['/rag', p.id, 'test']);
  }

  /** Index parameters changed: saving builds a new index in the background. */
  protected reindexes(): boolean {
    const v = this.pipeline()?.version.config;
    if (!v) return false;
    return (
      v.collection_id !== this.draft.collection_id ||
      v.embedding_model !== this.draft.embedding_model ||
      v.chunk_size !== this.draft.chunk_size ||
      v.chunk_overlap !== this.draft.chunk_overlap ||
      v.node_parser !== this.draft.node_parser
    );
  }
}
