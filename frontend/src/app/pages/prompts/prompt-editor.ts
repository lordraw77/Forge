import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TextFieldModule } from '@angular/cdk/text-field';
import { Router } from '@angular/router';
import { A11yModule } from '@angular/cdk/a11y';
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
  AssistIn,
  AssistOut,
  Example,
  PromptDetail,
  PromptsService,
  TemplateIssue,
  Variable,
  VersionIn,
  VersionOut,
  VersionSummary,
} from '../../api-client';
import { problemCode, problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { MonacoEditor } from '../../shared/monaco-editor';
import { ModelOption, ModelOptions } from './model-options';
import { FRAMEWORKS, FREE_FORM_KEY } from './frameworks';
import { detectVariables } from './variables';

interface Draft {
  model: string;
  system_template: string;
  user_template: string;
  variables: Variable[];
  examples: Example[];
  temperature: number | null;
  max_tokens: number | null;
  note: string;
}

const DEFAULT_MODEL = 'default-chat';

function emptyDraft(): Draft {
  return {
    model: DEFAULT_MODEL,
    system_template: '',
    user_template: '',
    variables: [],
    examples: [],
    temperature: null,
    max_tokens: null,
    note: '',
  };
}

/** New / Edit prompt (US-1.10.1, US-1.10.2, US-1.13.1). Every save is a new version. */
@Component({
  selector: 'forge-prompt-editor',
  imports: [
    A11yModule,
    MonacoEditor,
    DatePipe,
    FormsModule,
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
  templateUrl: './prompt-editor.html',
  styleUrls: ['../admin/admin.scss', './prompts.scss'],
})
export class PromptEditor {
  /** Route parameter; absent on "New Prompt". */
  readonly id = input<string | undefined>(undefined);

  private readonly api = inject(PromptsService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  private readonly modelOptions = inject(ModelOptions);

  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly prompt = signal<PromptDetail | null>(null);
  protected readonly models = signal<ModelOption[]>([]);
  protected readonly aliasOptions = computed(() => this.models().filter((m) => m.kind === 'alias'));
  protected readonly modelOnlyOptions = computed(() =>
    this.models().filter((m) => m.kind === 'model'),
  );
  protected readonly issues = signal<TemplateIssue[]>([]);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly saving = signal(false);
  protected readonly frameworks = FRAMEWORKS;
  protected readonly nameLabel = $localize`Variable name`;
  protected readonly valueLabel = $localize`Value`;
  protected readonly removeLabel = $localize`Remove`;
  protected readonly inputLabel = $localize`Input`;
  protected readonly outputLabel = $localize`Output`;
  protected readonly modelDefault = $localize`model default`;

  protected name = $localize`Untitled Prompt`;
  protected editingName = false;
  protected draft: Draft = emptyDraft();
  /** Variables added automatically from the templates and not touched since. */
  private autoAdded = new Set<string>();
  protected detected: string[] = [];

  // first-save description dialog
  protected describeOpen = false;
  protected description = '';
  private afterDescribe: 'test' | 'stay' = 'test';

  // Help Me Write
  protected assistOpen = false;
  protected assistBusy = signal(false);
  protected assistIn: AssistIn = { description: '', framework: null };
  protected readonly assistResult = signal<AssistOut | null>(null);
  protected readonly assistError = signal<string | null>(null);

  // versions drawer
  protected versionsOpen = false;
  protected readonly versions = signal<VersionSummary[]>([]);
  protected readonly viewing = signal<VersionOut | null>(null);

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
    if (id && this.prompt()?.id === id) return;
    this.message.set(null);
    this.issues.set([]);
    if (!id) {
      // Defaults first: the user may type before the model options arrive.
      this.prompt.set(null);
      this.name = $localize`Untitled Prompt`;
      this.draft = emptyDraft();
      this.autoAdded.clear();
      this.detected = [];
    }
    try {
      const options = await this.modelOptions.load(projectId);
      this.models.set(options);
      if (!id) {
        const chosen = options.some((o) => o.value === this.draft.model);
        if (!chosen && options.length) this.draft.model = options[0].value;
        return;
      }
      const prompt = await firstValueFrom(this.api.getPrompt({ projectId, promptId: id }));
      this.prompt.set(prompt);
      this.name = prompt.name;
      this.fill(prompt.version);
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  private fill(v: VersionOut): void {
    this.draft = {
      model: v.model,
      system_template: v.system_template,
      user_template: v.user_template,
      variables: v.variables.map((x) => ({ ...x })),
      examples: v.examples.map((x) => ({ ...x })),
      temperature: v.model_params.temperature ?? null,
      max_tokens: v.model_params.max_tokens ?? null,
      note: '',
    };
    this.autoAdded.clear();
    this.detected = detectVariables(v.system_template, v.user_template);
  }

  // ------------------------------------------------------------ variables
  /** Keep the variable list in step with `{name}` typed in the templates. */
  protected syncVariables(): void {
    this.detected = detectVariables(this.draft.system_template, this.draft.user_template);
    const present = new Set(this.draft.variables.map((v) => v.name));
    for (const name of this.detected) {
      if (!present.has(name)) {
        this.draft.variables.push({ name, value: '', is_file: false, required: true });
        this.autoAdded.add(name);
      }
    }
    this.draft.variables = this.draft.variables.filter(
      (v) => this.detected.includes(v.name) || !this.autoAdded.has(v.name) || !!v.value,
    );
    for (const name of [...this.autoAdded]) {
      if (!this.detected.includes(name)) this.autoAdded.delete(name);
    }
  }

  protected touched(v: Variable): void {
    this.autoAdded.delete(v.name);
  }

  protected addVariable(): void {
    let n = this.draft.variables.length + 1;
    while (this.draft.variables.some((v) => v.name === `var_${n}`)) n++;
    this.draft.variables = [
      ...this.draft.variables,
      { name: `var_${n}`, value: '', is_file: false, required: false },
    ];
  }

  protected removeVariable(index: number): void {
    const [removed] = this.draft.variables.splice(index, 1);
    this.autoAdded.delete(removed.name);
    this.draft.variables = [...this.draft.variables];
  }

  protected unused(v: Variable): boolean {
    return !this.detected.includes(v.name);
  }

  // ------------------------------------------------------------ examples
  protected addExample(): void {
    this.draft.examples = [...this.draft.examples, { input: '', output: '' }];
  }

  protected removeExample(index: number): void {
    this.draft.examples = this.draft.examples.filter((_, i) => i !== index);
  }

  // ------------------------------------------------------------ name
  protected async commitName(): Promise<void> {
    this.editingName = false;
    const prompt = this.prompt();
    const name = this.name.trim();
    if (!name) {
      this.name = prompt?.name ?? $localize`Untitled Prompt`;
      return;
    }
    if (!prompt || name === prompt.name) return;
    try {
      const updated = await firstValueFrom(
        this.api.updatePrompt({
          projectId: this.projectId()!,
          promptId: prompt.id,
          promptUpdateIn: { name },
        }),
      );
      this.prompt.set(updated);
      this.message.set({ ok: true, text: $localize`Renamed.` });
    } catch (e) {
      this.name = prompt.name;
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  // ------------------------------------------------------------ save
  private body(): VersionIn {
    return {
      model: this.draft.model,
      system_template: this.draft.system_template,
      user_template: this.draft.user_template,
      variables: this.draft.variables,
      examples: this.draft.examples.filter((e) => e.input.trim() || e.output.trim()),
      model_params: {
        temperature: this.draft.temperature,
        max_tokens: this.draft.max_tokens,
      },
      note: this.draft.note,
    };
  }

  protected canSave(): boolean {
    return (
      !this.saving() &&
      !!this.draft.model &&
      !!(this.draft.system_template.trim() || this.draft.user_template.trim())
    );
  }

  protected save(then: 'test' | 'stay'): void {
    if (!this.prompt()) {
      // Description is asked on first save (spec Part 2, Prompt fields).
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

  private async persist(then: 'test' | 'stay'): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    this.saving.set(true);
    this.issues.set([]);
    this.message.set(null);
    try {
      const existing = this.prompt();
      const saved = existing
        ? await firstValueFrom(
            this.api.savePromptVersion({
              projectId,
              promptId: existing.id,
              versionIn: this.body(),
            }),
          )
        : await firstValueFrom(
            this.api.createPrompt({
              projectId,
              promptCreateIn: {
                name: this.name.trim() || $localize`Untitled Prompt`,
                description: this.description.trim(),
                version: this.body(),
              },
            }),
          );
      this.prompt.set(saved);
      this.fill(saved.version);
      if (then === 'test') {
        await this.router.navigate(['/prompts', saved.id, 'test']);
      } else if (!existing) {
        await this.router.navigate(['/prompts', saved.id, 'edit'], { replaceUrl: true });
        this.message.set({ ok: true, text: $localize`Saved version ${saved.latest_version}:n:.` });
      } else {
        this.message.set({ ok: true, text: $localize`Saved version ${saved.latest_version}:n:.` });
      }
    } catch (e) {
      if (problemCode(e) === 'template_invalid') {
        const detail = (e as { error?: { detail?: TemplateIssue[] } }).error?.detail ?? [];
        this.issues.set(detail);
      }
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.saving.set(false);
    }
  }

  protected issueFor(field: TemplateIssue['field']): TemplateIssue | undefined {
    return this.issues().find((i) => i.field === field);
  }

  // ------------------------------------------------------------ Help Me Write
  protected openAssist(): void {
    this.assistResult.set(null);
    this.assistError.set(null);
    this.assistOpen = true;
  }

  protected frameworkHint(key: string | null | undefined): string {
    return FRAMEWORKS.find((f) => f.value === (key ?? null))?.hint ?? '';
  }

  protected frameworkLabel(key: string | null | undefined): string {
    if (key === FREE_FORM_KEY) return FRAMEWORKS[0].label;
    return FRAMEWORKS.find((f) => f.value === key)?.label ?? key ?? '';
  }

  protected async generate(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId || this.assistIn.description.trim().length < 10) return;
    this.assistBusy.set(true);
    this.assistError.set(null);
    try {
      this.assistResult.set(
        await firstValueFrom(this.api.assistPrompt({ projectId, assistIn: this.assistIn })),
      );
    } catch (e) {
      this.assistError.set(problemMessage(e));
    } finally {
      this.assistBusy.set(false);
    }
  }

  protected insertAssist(): void {
    const result = this.assistResult();
    if (!result) return;
    this.draft.system_template = result.system_template;
    this.draft.user_template = result.user_template;
    this.syncVariables();
    this.assistOpen = false;
  }

  // ------------------------------------------------------------ versions
  protected async openVersions(): Promise<void> {
    const prompt = this.prompt();
    if (!prompt) return;
    this.viewing.set(null);
    this.versionsOpen = true;
    try {
      this.versions.set(
        await firstValueFrom(
          this.api.listPromptVersions({ projectId: this.projectId()!, promptId: prompt.id }),
        ),
      );
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async view(v: VersionSummary): Promise<void> {
    const prompt = this.prompt();
    if (!prompt) return;
    this.viewing.set(
      await firstValueFrom(
        this.api.getPromptVersion({
          projectId: this.projectId()!,
          promptId: prompt.id,
          version: v.version,
        }),
      ),
    );
  }

  protected restore(v: VersionOut): void {
    this.fill(v);
    this.draft.note = $localize`Restored from v${v.version}:n:`;
    this.versionsOpen = false;
    this.message.set({
      ok: true,
      text: $localize`Version ${v.version}:n: loaded into the editor. Saving creates a new version.`,
    });
  }

  protected test(): void {
    const prompt = this.prompt();
    if (prompt) void this.router.navigate(['/prompts', prompt.id, 'test']);
  }
}
