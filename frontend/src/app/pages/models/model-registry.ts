import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { CheckboxModule } from 'primeng/checkbox';
import { DialogModule } from 'primeng/dialog';
import { InputNumberModule } from 'primeng/inputnumber';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { MultiSelectModule } from 'primeng/multiselect';
import { SelectModule } from 'primeng/select';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';
import { Observable, firstValueFrom } from 'rxjs';

import {
  AdminModelsService,
  AdminService,
  AliasOut,
  ModelIn,
  ModelOut,
  ProjectModelsService,
  ProviderIn,
  ProviderOut,
  TestConnectionIn,
  TestResult,
} from '../../api-client';
import { problemMessage } from '../../core/http';

/** The registry calls, bound to the platform (Super Admin) or to one project (Project Admin). */
interface RegistryApi {
  providers(): Observable<ProviderOut[]>;
  createProvider(body: ProviderIn): Observable<ProviderOut>;
  updateProvider(id: string, body: ProviderIn): Observable<ProviderOut>;
  deleteProvider(id: string): Observable<unknown>;
  models(): Observable<ModelOut[]>;
  createModel(body: ModelIn): Observable<ModelOut>;
  updateModel(id: string, body: ModelIn): Observable<ModelOut>;
  deleteModel(id: string): Observable<unknown>;
  testDraft(body: TestConnectionIn): Observable<TestResult>;
  testModel(id: string): Observable<TestResult>;
  aliases(): Observable<AliasOut[]>;
  setAlias(name: string, modelIds: string[]): Observable<AliasOut>;
  deleteAlias(name: string): Observable<unknown>;
}

function platformApi(a: AdminModelsService): RegistryApi {
  return {
    providers: () => a.adminListProviders(),
    createProvider: (providerIn) => a.adminCreateProvider({ providerIn }),
    updateProvider: (providerId, providerIn) => a.adminUpdateProvider({ providerId, providerIn }),
    deleteProvider: (providerId) => a.adminDeleteProvider({ providerId }),
    models: () => a.adminListModels(),
    createModel: (modelIn) => a.adminCreateModel({ modelIn }),
    updateModel: (modelId, modelIn) => a.adminUpdateModel({ modelId, modelIn }),
    deleteModel: (modelId) => a.adminDeleteModel({ modelId }),
    testDraft: (testConnectionIn) => a.adminTestDraft({ testConnectionIn }),
    testModel: (modelId) => a.adminTestModel({ modelId }),
    aliases: () => a.adminListAliases(),
    setAlias: (name, model_ids) => a.adminSetAlias({ name, aliasIn: { model_ids } }),
    deleteAlias: (name) => a.adminDeleteAlias({ name }),
  };
}

function projectApi(p: ProjectModelsService, projectId: string): RegistryApi {
  return {
    providers: () => p.projectListProviders({ projectId }),
    createProvider: (providerIn) => p.projectCreateProvider({ projectId, providerIn }),
    updateProvider: (providerId, providerIn) =>
      p.projectUpdateProvider({ projectId, providerId, providerIn }),
    deleteProvider: (providerId) => p.projectDeleteProvider({ projectId, providerId }),
    models: () => p.projectListModels({ projectId }),
    createModel: (modelIn) => p.projectCreateModel({ projectId, modelIn }),
    updateModel: (modelId, modelIn) => p.projectUpdateModel({ projectId, modelId, modelIn }),
    deleteModel: (modelId) => p.projectDeleteModel({ projectId, modelId }),
    testDraft: (testConnectionIn) => p.projectTestDraft({ projectId, testConnectionIn }),
    testModel: (modelId) => p.projectTestModel({ projectId, modelId }),
    aliases: () => p.projectListAliases({ projectId }),
    setAlias: (name, model_ids) => p.projectSetAlias({ projectId, name, aliasIn: { model_ids } }),
    deleteAlias: (name) => p.projectDeleteAlias({ projectId, name }),
  };
}

type Notice = { severity: 'success' | 'error' | 'info'; text: string } | null;
type TypeTab = 'llm' | 'embedding' | 'reranker' | 'speech';

export const PROVIDER_TYPES: { value: ProviderIn.ProviderTypeEnum; label: string }[] = [
  { value: 'vllm', label: 'vLLM' },
  { value: 'llamacpp', label: 'llama.cpp server' },
  { value: 'nim', label: 'NVIDIA NIM' },
  { value: 'ollama', label: 'Ollama' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'bedrock', label: 'AWS Bedrock' },
  { value: 'gemini', label: 'Google Gemini' },
  { value: 'vertex', label: 'Google Vertex AI' },
  { value: 'mistral', label: 'Mistral' },
  { value: 'openai_compatible', label: $localize`Generic OpenAI-compatible` },
];
const BASE_URL_REQUIRED = new Set(['vllm', 'llamacpp', 'ollama', 'azure', 'openai_compatible']);
const BASE_URL_HINT: Record<string, string> = {
  vllm: 'http://vllm:8000/v1',
  llamacpp: 'http://llama:8080/v1',
  nim: 'https://integrate.api.nvidia.com/v1',
  ollama: 'http://ollama:11434',
  azure: 'https://<resource>.openai.azure.com',
  openai_compatible: 'https://api.example.com/v1',
};

interface ProviderDraft {
  id: string | null;
  name: string;
  provider_type: ProviderIn.ProviderTypeEnum;
  base_url: string;
  allow_http: boolean;
  api_key: string;
  clear_key: boolean;
  has_credentials: boolean;
  api_version: string;
  region: string;
  vertex_project: string;
  vertex_location: string;
  headers: string;
  active: boolean;
}

interface ModelDraft {
  id: string | null;
  provider_id: string;
  name: string;
  display_name: string;
  model_type: ModelIn.ModelTypeEnum;
  model_id: string;
  embedding_dims: number | null;
  context_window: number | null;
  max_output_tokens: number | null;
  cost_input: number;
  cost_output: number;
  capabilities: ModelIn.CapabilitiesEnum[];
  timeout_s: number;
  max_retries: number;
  rpm_limit: number | null;
  params: string;
  all_projects: boolean;
  enabled_project_ids: string[];
  active: boolean;
}

function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split('\n')) {
    const i = line.indexOf(':');
    if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

/** Providers, models and aliases of one scope (US-1.9.1; F1.2 fields; Test Connection). */
@Component({
  selector: 'forge-model-registry',
  imports: [
    FormsModule,
    ButtonModule,
    CheckboxModule,
    DialogModule,
    InputNumberModule,
    InputTextModule,
    MessageModule,
    MultiSelectModule,
    SelectModule,
    SelectButtonModule,
    TableModule,
    TagModule,
    TextareaModule,
  ],
  templateUrl: './model-registry.html',
  styleUrls: ['../admin/admin.scss', './model-registry.scss'],
})
export class ModelRegistry {
  /** null = platform (Super Admin console); a project id = Projects Studio. */
  readonly projectId = input<string | null>(null);
  readonly canEdit = input(true);

  private readonly adminModels = inject(AdminModelsService);
  private readonly adminService = inject(AdminService);
  private readonly projectModels = inject(ProjectModelsService);

  protected readonly providerTypes = PROVIDER_TYPES;
  protected readonly typeTabs: { value: TypeTab; label: string }[] = [
    { value: 'llm', label: $localize`LLM` },
    { value: 'embedding', label: $localize`Embedding` },
    { value: 'reranker', label: $localize`Reranker` },
    { value: 'speech', label: $localize`Speech` },
  ];
  protected readonly modelTypes = [
    { value: 'chat', label: $localize`Chat` },
    { value: 'guard', label: $localize`Guard (classifier)` },
    { value: 'embedding', label: $localize`Embedding` },
    { value: 'reranker', label: $localize`Reranker` },
    { value: 'speech', label: $localize`Speech` },
  ];
  protected readonly capabilityOptions = [
    { value: 'streaming', label: $localize`Streaming` },
    { value: 'tools', label: $localize`Tool calling` },
    { value: 'json', label: $localize`JSON mode` },
    { value: 'vision', label: $localize`Vision` },
  ];

  protected readonly providers = signal<ProviderOut[]>([]);
  protected readonly models = signal<ModelOut[]>([]);
  protected readonly aliases = signal<AliasOut[]>([]);
  protected readonly projects = signal<{ id: string; name: string }[]>([]);
  protected readonly notice = signal<Notice>(null);
  protected readonly dialogError = signal<string | null>(null);
  protected readonly testResult = signal<TestResult | null>(null);
  protected readonly testing = signal<string | null>(null);
  protected readonly tab = signal<TypeTab>('llm');
  protected readonly pendingDelete = signal<{
    question: string;
    run: () => Observable<unknown>;
  } | null>(null);

  protected readonly scope = computed(() => (this.projectId() ? 'project' : 'platform'));
  protected readonly visibleModels = computed(() => {
    const tab = this.tab();
    return this.models().filter((m) =>
      tab === 'llm' ? m.model_type === 'chat' || m.model_type === 'guard' : m.model_type === tab,
    );
  });
  /** Models a chain may use: in project scope, platform models enabled here and own models. */
  protected readonly chainOptions = computed(() =>
    this.models().map((m) => ({ id: m.id, label: `${m.name} (${m.model_type})` })),
  );

  protected readonly newProviderTitle = $localize`New provider`;
  protected readonly editProviderTitle = $localize`Edit provider`;
  protected readonly newModelTitle = $localize`New model`;
  protected readonly editModelTitle = $localize`Edit model`;
  protected readonly newAliasTitle = $localize`New alias`;
  protected readonly keepKeyHint = $localize`Leave blank to keep the stored key`;
  protected readonly resetLabel = $localize`Reset`;
  protected readonly deleteLabel = $localize`Delete`;

  protected providerOpen = false;
  protected modelOpen = false;
  protected aliasOpen = false;
  protected providerDraft: ProviderDraft = this.emptyProvider();
  protected modelDraft: ModelDraft = this.emptyModel();
  protected aliasName = '';
  protected aliasIsNew = false;
  protected aliasChain: string[] = [];
  protected aliasPick: string | null = null;

  private api: RegistryApi = platformApi(this.adminModels);

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      this.api = projectId
        ? projectApi(this.projectModels, projectId)
        : platformApi(this.adminModels);
      void this.load();
    });
  }

  protected async load(): Promise<void> {
    try {
      const [models, aliases] = await Promise.all([
        firstValueFrom(this.api.models()),
        firstValueFrom(this.api.aliases()),
      ]);
      this.models.set(models);
      this.aliases.set(aliases);
      if (this.canEdit()) this.providers.set(await firstValueFrom(this.api.providers()));
      if (!this.projectId() && this.projects().length === 0) {
        const all = await firstValueFrom(this.adminService.listProjects({}));
        this.projects.set(all.map((p) => ({ id: p.id, name: p.name })));
      }
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    }
  }

  protected okText(t: TestResult): string {
    const dims = t.dimensions ? ' · ' + $localize`${t.dimensions}:dims: dimensions` : '';
    const sample = t.sample ? ` · “${t.sample}”` : '';
    return $localize`Connection successful in ${t.latency_ms}:ms: ms` + dims + sample;
  }

  protected providerLabel(type: string): string {
    return PROVIDER_TYPES.find((t) => t.value === type)?.label ?? type;
  }

  protected modelEditable(m: ModelOut): boolean {
    return this.canEdit() && m.scope === this.scope();
  }

  protected baseUrlHint(type: string): string {
    return BASE_URL_HINT[type] ?? $localize`Default endpoint of the provider`;
  }

  protected baseUrlRequired(type: string): boolean {
    return BASE_URL_REQUIRED.has(type);
  }

  // ---- providers
  private emptyProvider(): ProviderDraft {
    return {
      id: null,
      name: '',
      provider_type: 'openai_compatible',
      base_url: '',
      allow_http: false,
      api_key: '',
      clear_key: false,
      has_credentials: false,
      api_version: '',
      region: '',
      vertex_project: '',
      vertex_location: '',
      headers: '',
      active: true,
    };
  }

  protected openProvider(p?: ProviderOut): void {
    const cfg = (p?.config ?? {}) as Record<string, unknown>;
    const headers = (cfg['headers'] ?? {}) as Record<string, string>;
    this.providerDraft = p
      ? {
          id: p.id,
          name: p.name,
          provider_type: p.provider_type as ProviderIn.ProviderTypeEnum,
          base_url: p.base_url ?? '',
          allow_http: cfg['allow_http'] === true,
          api_key: '',
          clear_key: false,
          has_credentials: p.has_credentials,
          api_version: String(cfg['api_version'] ?? ''),
          region: String(cfg['region'] ?? ''),
          vertex_project: String(cfg['vertex_project'] ?? ''),
          vertex_location: String(cfg['vertex_location'] ?? ''),
          headers: Object.entries(headers)
            .map(([k, v]) => `${k}: ${v}`)
            .join('\n'),
          active: p.status === 'active',
        }
      : this.emptyProvider();
    this.dialogError.set(null);
    this.providerOpen = true;
  }

  private providerBody(d: ProviderDraft): ProviderIn {
    const config: Record<string, unknown> = { headers: parseHeaders(d.headers) };
    if (d.base_url.startsWith('http://')) config['allow_http'] = d.allow_http;
    if (d.api_version) config['api_version'] = d.api_version;
    if (d.region) config['region'] = d.region;
    if (d.vertex_project) config['vertex_project'] = d.vertex_project;
    if (d.vertex_location) config['vertex_location'] = d.vertex_location;
    return {
      name: d.name,
      provider_type: d.provider_type,
      base_url: d.base_url || null,
      // Write-only: blank keeps the stored key on edit, "" with "remove" clears it.
      api_key: d.clear_key ? '' : d.api_key || null,
      config,
      status: d.active ? 'active' : 'disabled',
    };
  }

  protected async saveProvider(): Promise<void> {
    const d = this.providerDraft;
    try {
      const body = this.providerBody(d);
      await firstValueFrom(
        d.id ? this.api.updateProvider(d.id, body) : this.api.createProvider(body),
      );
      this.providerOpen = false;
      this.notice.set({ severity: 'success', text: $localize`Provider ${d.name}:name: saved` });
      await this.load();
    } catch (e) {
      this.dialogError.set(problemMessage(e));
    }
  }

  protected deleteProvider(p: ProviderOut): void {
    this.askDelete($localize`Delete provider ${p.name}:name:?`, () =>
      this.api.deleteProvider(p.id),
    );
  }

  protected askDelete(question: string, run: () => Observable<unknown>): void {
    this.pendingDelete.set({ question, run });
  }

  protected async confirmDelete(): Promise<void> {
    const pending = this.pendingDelete();
    this.pendingDelete.set(null);
    if (!pending) return;
    try {
      await firstValueFrom(pending.run());
      await this.load();
    } catch (e) {
      this.notice.set({ severity: 'error', text: this.blockedMessage(e) });
    }
  }

  // ---- models
  private emptyModel(): ModelDraft {
    return {
      id: null,
      provider_id: '',
      name: '',
      display_name: '',
      model_type: 'chat',
      model_id: '',
      embedding_dims: null,
      context_window: null,
      max_output_tokens: null,
      cost_input: 0,
      cost_output: 0,
      capabilities: ['streaming'],
      timeout_s: 60,
      max_retries: 2,
      rpm_limit: null,
      params: '',
      all_projects: false,
      enabled_project_ids: [],
      active: true,
    };
  }

  protected openModel(m?: ModelOut): void {
    const tab = this.tab();
    this.modelDraft = m
      ? {
          id: m.id,
          provider_id: m.provider_id,
          name: m.name,
          display_name: m.display_name,
          model_type: m.model_type as ModelIn.ModelTypeEnum,
          model_id: m.model_id,
          embedding_dims: m.embedding_dims,
          context_window: m.context_window,
          max_output_tokens: m.max_output_tokens,
          cost_input: Number(m.cost_input),
          cost_output: Number(m.cost_output),
          capabilities: m.capabilities as ModelIn.CapabilitiesEnum[],
          timeout_s: m.timeout_s,
          max_retries: m.max_retries,
          rpm_limit: m.rpm_limit,
          params: Object.keys(m.params).length ? JSON.stringify(m.params, null, 2) : '',
          all_projects: m.all_projects,
          enabled_project_ids: [...m.enabled_project_ids],
          active: m.status === 'active',
        }
      : {
          ...this.emptyModel(),
          model_type: tab === 'llm' ? 'chat' : tab,
          provider_id: this.providers()[0]?.id ?? '',
        };
    this.dialogError.set(null);
    this.testResult.set(null);
    this.modelOpen = true;
  }

  private modelBody(d: ModelDraft): ModelIn {
    let params: Record<string, unknown> = {};
    if (d.params.trim()) {
      params = JSON.parse(d.params) as Record<string, unknown>;
    }
    return {
      provider_id: d.provider_id,
      name: d.name,
      display_name: d.display_name || d.name,
      model_type: d.model_type,
      model_id: d.model_id,
      embedding_dims: d.model_type === 'embedding' ? d.embedding_dims : null,
      context_window: d.context_window || null,
      max_output_tokens: d.max_output_tokens || null,
      cost_input: String(d.cost_input ?? 0),
      cost_output: String(d.cost_output ?? 0),
      capabilities: d.capabilities,
      timeout_s: d.timeout_s,
      max_retries: d.max_retries,
      rpm_limit: d.rpm_limit || null,
      params,
      all_projects: this.projectId() ? false : d.all_projects,
      enabled_project_ids: this.projectId() ? [] : d.enabled_project_ids,
      status: d.active ? 'active' : 'disabled',
    };
  }

  protected async saveModel(): Promise<void> {
    const d = this.modelDraft;
    try {
      const body = this.modelBody(d);
      await firstValueFrom(d.id ? this.api.updateModel(d.id, body) : this.api.createModel(body));
      this.modelOpen = false;
      this.notice.set({ severity: 'success', text: $localize`Model ${body.name}:name: saved` });
      await this.load();
    } catch (e) {
      this.dialogError.set(
        e instanceof SyntaxError
          ? $localize`Extra parameters must be valid JSON`
          : problemMessage(e),
      );
    }
  }

  /** Test Connection on the unsaved draft (US-1.5.2). */
  protected async testDraft(): Promise<void> {
    const d = this.modelDraft;
    this.testResult.set(null);
    this.testing.set('draft');
    try {
      const body = this.modelBody(d);
      this.testResult.set(
        await firstValueFrom(
          this.api.testDraft({
            provider_id: d.provider_id,
            model: {
              model_type: body.model_type,
              model_id: body.model_id,
              params: body.params,
              embedding_dims: body.embedding_dims,
              timeout_s: body.timeout_s,
            },
          }),
        ),
      );
    } catch (e) {
      this.dialogError.set(problemMessage(e));
    } finally {
      this.testing.set(null);
    }
  }

  protected async testSaved(m: ModelOut): Promise<void> {
    this.testing.set(m.id);
    try {
      const r = await firstValueFrom(this.api.testModel(m.id));
      this.notice.set({
        severity: r.ok ? 'success' : 'error',
        text: r.ok
          ? $localize`${m.name}:name: answered in ${r.latency_ms}:ms: ms`
          : $localize`${m.name}:name: failed: ${r.detail}:detail:`,
      });
    } catch (e) {
      this.notice.set({ severity: 'error', text: problemMessage(e) });
    } finally {
      this.testing.set(null);
    }
  }

  protected deleteModel(m: ModelOut): void {
    this.askDelete($localize`Delete model ${m.name}:name:?`, () => this.api.deleteModel(m.id));
  }

  /** 409 responses list what blocks the deletion (aliases, models). */
  private blockedMessage(e: unknown): string {
    const err = e as {
      error?: { detail?: { references?: { name: string }[]; models?: string[] } };
    };
    const detail = err?.error?.detail;
    const names = detail?.references?.map((r) => r.name) ?? detail?.models ?? [];
    return names.length ? $localize`In use by: ${names.join(', ')}:names:` : problemMessage(e);
  }

  // ---- aliases
  protected openAlias(a?: AliasOut): void {
    this.aliasIsNew = !a;
    this.aliasName = a?.name ?? '';
    this.aliasChain = a ? [...a.model_ids] : [];
    this.aliasPick = null;
    this.dialogError.set(null);
    this.aliasOpen = true;
  }

  protected modelName(id: string): string {
    return this.models().find((m) => m.id === id)?.name ?? id;
  }

  protected addToChain(): void {
    if (this.aliasPick && !this.aliasChain.includes(this.aliasPick)) {
      this.aliasChain = [...this.aliasChain, this.aliasPick];
    }
    this.aliasPick = null;
  }

  protected move(index: number, by: number): void {
    const chain = [...this.aliasChain];
    const [item] = chain.splice(index, 1);
    chain.splice(index + by, 0, item);
    this.aliasChain = chain;
  }

  protected removeFromChain(index: number): void {
    this.aliasChain = this.aliasChain.filter((_, i) => i !== index);
  }

  protected async saveAlias(): Promise<void> {
    try {
      await firstValueFrom(this.api.setAlias(this.aliasName, this.aliasChain));
      this.aliasOpen = false;
      await this.load();
    } catch (e) {
      this.dialogError.set(problemMessage(e));
    }
  }

  protected aliasRemovable(a: AliasOut): boolean {
    return this.canEdit() && a.model_ids.length > 0 && a.scope === this.scope();
  }

  protected deleteAlias(a: AliasOut): void {
    this.askDelete(
      this.projectId()
        ? $localize`Remove the project setting of ${a.name}:name:? The platform default applies again.`
        : $localize`Delete alias ${a.name}:name:?`,
      () => this.api.deleteAlias(a.name),
    );
  }
}
