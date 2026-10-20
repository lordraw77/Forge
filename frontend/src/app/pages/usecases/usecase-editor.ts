import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzInputNumberModule } from 'ng-zorro-antd/input-number';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import {
  CatalogOut,
  UseCaseDefaults,
  UseCaseDetail,
  UseCaseInputField,
  UseCaseIssueOut,
  UseCaseUpdateIn,
  UseCaseVersionDiff,
  UseCaseVersionSummary,
  UseCasesService,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { MonacoEditor } from '../../shared/monaco-editor';
import { UseCaseTestRun } from './usecase-test-run';
import { emptyDefinition, fieldName, newField, parseJsonObject, splitList } from './usecase-util';

type Pattern = 'supervisor' | 'graph';
type OutputType = UseCaseUpdateIn.OutputTypeEnum;
type FieldRow = UseCaseInputField & { optionsText: string; defaultText: string };

/** Use case editor (US-2.5.1, 2.5.2): settings, input form, output, defaults, the workflow
 * definition (JSON until the canvas of M2.3c), lifecycle, versions, diff and rollback. */
@Component({
  selector: 'forge-usecase-editor',
  imports: [
    DatePipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzIconModule,
    NzInputModule,
    NzInputNumberModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
    UseCaseTestRun,
    MonacoEditor,
  ],
  template: `
    <div class="head">
      <h2>
        @if (detail(); as d) {
          {{ d.name }}
          <nz-tag [nzColor]="statusColor()">{{ d.status }}</nz-tag>
          @if (d.dirty && d.published_version) {
            <nz-tag nzColor="orange" i18n>Unpublished changes</nz-tag>
          }
        } @else {
          <span i18n>New use case</span>
        }
      </h2>
      <span class="spacer"></span>
      @if (detail(); as d) {
        <a nz-button [routerLink]="['..', 'workflow']" i18n>Workflow</a>
        <a nz-button [routerLink]="['..', 'test']" i18n>Test page</a>
      }
      <a nz-button routerLink="../library" i18n>Back to library</a>
    </div>

    @if (message(); as m) {
      <nz-alert [nzType]="m.ok ? 'success' : 'error'" [nzMessage]="m.text" nzShowIcon />
    }
    @if (issues().length) {
      <nz-alert nzType="warning" nzShowIcon [nzMessage]="issueTitle" [nzDescription]="issueList" />
      <ng-template #issueTitle><span i18n>The workflow needs attention</span></ng-template>
      <ng-template #issueList>
        <ul data-testid="uc-issues">
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

    <form class="form" (ngSubmit)="save()" data-testid="uc-form">
      <h3 i18n>General</h3>
      <div class="settings">
        <div class="form">
          <label for="uc-name" i18n>Name</label>
          <input nz-input id="uc-name" name="name" [(ngModel)]="name" required maxlength="80" />
        </div>
        <div class="form">
          <label for="uc-pattern" i18n>Pattern</label>
          <nz-select
            nzId="uc-pattern"
            name="pattern"
            [(ngModel)]="pattern"
            (ngModelChange)="onPattern()"
            [nzDisabled]="!!detail()"
          >
            <nz-option
              nzValue="supervisor"
              nzLabel="Supervisor (agent team in sequence)"
              i18n-nzLabel
            />
            <nz-option nzValue="graph" nzLabel="Graph (nodes and edges)" i18n-nzLabel />
          </nz-select>
        </div>
        <div class="form">
          <label for="uc-icon" i18n>Icon</label>
          <input nz-input id="uc-icon" name="icon" [(ngModel)]="icon" maxlength="60" />
        </div>
        <div class="form">
          <label for="uc-tags" i18n>Tags (comma separated)</label>
          <input nz-input id="uc-tags" name="tags" [(ngModel)]="tagsText" />
        </div>
        <div class="form">
          <label for="uc-catalog" i18n>Catalog</label>
          <nz-select
            nzId="uc-catalog"
            name="catalog"
            nzAllowClear
            nzPlaceHolder="Not filed"
            i18n-nzPlaceHolder
            [(ngModel)]="catalogId"
            (ngModelChange)="categoryId = null"
          >
            @for (c of catalogs(); track c.id) {
              <nz-option [nzValue]="c.id" [nzLabel]="c.name" />
            }
          </nz-select>
        </div>
        <div class="form">
          <label for="uc-category" i18n>Category</label>
          <nz-select
            nzId="uc-category"
            name="category"
            nzAllowClear
            [nzDisabled]="!catalogId"
            [(ngModel)]="categoryId"
          >
            @for (k of categories(); track k.id) {
              <nz-option [nzValue]="k.id" [nzLabel]="k.name" />
            }
          </nz-select>
        </div>
      </div>
      <label for="uc-description" i18n>Description</label>
      <textarea
        nz-input
        id="uc-description"
        name="description"
        rows="3"
        maxlength="2000"
        [(ngModel)]="description"
      ></textarea>

      <h3 i18n>Input form</h3>
      <p class="intro" i18n>
        What the user fills in before a run. Files and collections are chosen at run time.
      </p>
      @for (f of fields(); track $index; let i = $index) {
        <div class="settings field-row" [attr.data-testid]="'uc-field-' + i">
          <div class="form">
            <label [attr.for]="'f-name-' + i" i18n>Field name</label>
            <input
              nz-input
              [id]="'f-name-' + i"
              [name]="'f-name-' + i"
              [(ngModel)]="f.name"
              (blur)="f.name = slug(f.name)"
            />
          </div>
          <div class="form">
            <label [attr.for]="'f-label-' + i" i18n>Label</label>
            <input nz-input [id]="'f-label-' + i" [name]="'f-label-' + i" [(ngModel)]="f.label" />
          </div>
          <div class="form">
            <label [attr.for]="'f-type-' + i" i18n>Type</label>
            <nz-select [nzId]="'f-type-' + i" [name]="'f-type-' + i" [(ngModel)]="f.type">
              <nz-option nzValue="text" nzLabel="Text" i18n-nzLabel />
              <nz-option nzValue="long_text" nzLabel="Long text" i18n-nzLabel />
              <nz-option nzValue="number" nzLabel="Number" i18n-nzLabel />
              <nz-option nzValue="select" nzLabel="Choice" i18n-nzLabel />
              <nz-option nzValue="file" nzLabel="File" i18n-nzLabel />
              <nz-option nzValue="collection" nzLabel="Data collection" i18n-nzLabel />
            </nz-select>
          </div>
          @if (f.type === 'select') {
            <div class="form">
              <label [attr.for]="'f-opts-' + i" i18n>Options (comma separated)</label>
              <input
                nz-input
                [id]="'f-opts-' + i"
                [name]="'f-opts-' + i"
                [(ngModel)]="f.optionsText"
              />
            </div>
          }
          @if (
            f.type === 'text' ||
            f.type === 'long_text' ||
            f.type === 'select' ||
            f.type === 'number'
          ) {
            <div class="form">
              <label [attr.for]="'f-def-' + i" i18n>Default</label>
              <input
                nz-input
                [id]="'f-def-' + i"
                [name]="'f-def-' + i"
                [(ngModel)]="f.defaultText"
              />
            </div>
          }
          <div class="form">
            <label [attr.for]="'f-help-' + i" i18n>Help text</label>
            <input nz-input [id]="'f-help-' + i" [name]="'f-help-' + i" [(ngModel)]="f.help" />
          </div>
          <div class="form">
            <label
              nz-checkbox
              [nzId]="'f-req-' + i"
              [name]="'f-req-' + i"
              [(ngModel)]="f.required"
              i18n
              >Required</label
            >
            <button nz-button nzType="link" nzDanger type="button" (click)="removeField(i)" i18n>
              Remove field
            </button>
          </div>
        </div>
      }
      <div>
        <button nz-button type="button" (click)="addField()" data-testid="uc-add-field">
          <nz-icon nzType="plus" /><span i18n>Add field</span>
        </button>
      </div>

      <h3 i18n>Output</h3>
      <div class="settings">
        <div class="form">
          <label for="uc-output" i18n>Final output</label>
          <nz-select nzId="uc-output" name="output" [(ngModel)]="outputType">
            <nz-option nzValue="markdown" nzLabel="Markdown" />
            <nz-option nzValue="json" nzLabel="JSON (with schema)" i18n-nzLabel />
            <nz-option nzValue="docx" nzLabel="Word (DOCX)" />
            <nz-option nzValue="xlsx" nzLabel="Excel (XLSX)" />
            <nz-option nzValue="pdf" nzLabel="PDF" />
            <nz-option nzValue="chat" nzLabel="Chat transcript" i18n-nzLabel />
          </nz-select>
        </div>
        <div class="form">
          <label for="uc-cost" i18n>Maximum cost per run (USD)</label>
          <nz-input-number
            nzId="uc-cost"
            name="cost"
            [(ngModel)]="maxCost"
            [nzMin]="0.01"
            [nzMax]="100"
            [nzStep]="0.5"
          />
        </div>
        <div class="form">
          <label for="uc-duration" i18n>Maximum duration (seconds)</label>
          <nz-input-number
            nzId="uc-duration"
            name="duration"
            [(ngModel)]="maxDuration"
            [nzMin]="10"
            [nzMax]="86400"
            [nzStep]="60"
          />
        </div>
        <div class="form">
          <label for="uc-hitl" i18n>Human approval policy</label>
          <nz-select nzId="uc-hitl" name="hitl" [(ngModel)]="hitlPolicy">
            <nz-option nzValue="agent_setting" nzLabel="As set on each agent" i18n-nzLabel />
            <nz-option nzValue="always" nzLabel="Always" i18n-nzLabel />
            <nz-option nzValue="never" nzLabel="Never" i18n-nzLabel />
          </nz-select>
        </div>
      </div>
      @if (outputType === 'json') {
        <label for="uc-schema" i18n>Output JSON Schema</label>
        <forge-monaco
          [(value)]="schemaText"
          height="9rem"
          label="Output JSON Schema"
          i18n-label
          data-testid="uc-output-schema"
        />
      }

      <h3 i18n>Workflow definition</h3>
      <p class="intro" i18n>
        JSON per the use case graph schema (ADR-010). The visual canvas and the Planner Agent arrive
        with the next step; agent, tool and prompt references look like
        <code>name&#64;version</code> and must be published.
      </p>
      <forge-monaco
        [(value)]="definitionText"
        height="22rem"
        label="Workflow definition"
        i18n-label
        data-testid="uc-definition"
      />

      <div class="buttons">
        <button
          nz-button
          nzType="primary"
          type="submit"
          [disabled]="saving()"
          data-testid="uc-save"
          i18n
        >
          Save draft
        </button>
      </div>
    </form>

    @if (detail(); as d) {
      <h3 i18n>Lifecycle</h3>
      <div class="buttons lifecycle">
        @if (d.status === 'draft') {
          <button nz-button (click)="setStatus('testing')" data-testid="uc-testing" i18n>
            Move to testing
          </button>
        }
        @if (d.status === 'testing') {
          <button nz-button (click)="setStatus('draft')" i18n>Back to draft</button>
        }
        @if (d.status === 'published') {
          <button
            nz-button
            nzDanger
            (click)="setStatus('deprecated')"
            data-testid="uc-deprecate"
            i18n
          >
            Deprecate
          </button>
        }
        <button nz-button nzType="primary" (click)="openPublish()" data-testid="uc-publish">
          <span>{{
            d.status === 'deprecated' || !d.published_version ? publishLabel : publishNewLabel
          }}</span>
        </button>
      </div>

      <forge-usecase-test-run [useCase]="d" />

      <h3 i18n>Versions</h3>
      <nz-table
        [nzData]="versions()"
        nzSize="small"
        [nzShowPagination]="false"
        [nzPageSize]="1000"
        data-testid="uc-versions"
      >
        <thead>
          <tr>
            <th i18n>Version</th>
            <th i18n>Note</th>
            <th i18n>Published</th>
            <th class="actions"><span class="sr-only" i18n>Actions</span></th>
          </tr>
        </thead>
        <tbody>
          @for (v of versions(); track v.version) {
            <tr [attr.data-testid]="'uc-version-' + v.version">
              <td>
                v{{ v.version }}
                @if (v.live) {
                  <nz-tag nzColor="success" i18n>Live</nz-tag>
                }
              </td>
              <td>{{ v.note }}</td>
              <td>{{ v.published_at | date: 'short' }}</td>
              <td class="actions">
                <button nz-button nzType="link" nzSize="small" (click)="compare(v.version)" i18n>
                  Compare with live
                </button>
                @if (!v.live) {
                  <button nz-button nzType="link" nzSize="small" (click)="rollback(v.version)" i18n>
                    Roll back to this
                  </button>
                }
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="4" class="muted" i18n>Not published yet.</td>
            </tr>
          }
        </tbody>
      </nz-table>

      @if (diff(); as df) {
        <h4 data-testid="uc-diff">
          <span i18n>Differences between v{{ df.left.version }} and v{{ df.right.version }}</span>
        </h4>
        @if (!df.changed.length) {
          <p class="muted" i18n>No differences.</p>
        }
        @for (field of df.changed; track field) {
          <div class="diff-block">
            <strong>{{ field }}</strong>
            <div class="diff-cols">
              <pre class="diff">{{ show(df.left, field) }}</pre>
              <pre class="diff">{{ show(df.right, field) }}</pre>
            </div>
          </div>
        }
      }
    }

    <nz-modal
      [(nzVisible)]="publishOpen"
      nzTitle="Publish use case"
      i18n-nzTitle
      [nzFooter]="null"
      [nzWidth]="460"
    >
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="publish()">
          <p i18n>
            The draft becomes a new immutable version. Runs already started keep the version they
            began with.
          </p>
          <label for="pub-note" i18n>Note</label>
          <input nz-input id="pub-note" name="note" [(ngModel)]="note" maxlength="300" />
          @if (publishError(); as e) {
            <nz-alert nzType="error" [nzMessage]="e" nzShowIcon />
          }
          <button nz-button nzType="primary" type="submit" data-testid="uc-publish-confirm" i18n>
            Publish
          </button>
        </form>
      </ng-container>
    </nz-modal>
  `,
  styles: `
    .mono {
      font-family: var(--forge-font-mono, monospace);
      font-size: 0.8rem;
    }
    .field-row {
      border: 1px solid var(--forge-surface-200);
      border-radius: 6px;
      padding: 0.75rem;
    }
    .diff-cols {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
    }
    .lifecycle {
      justify-content: flex-start;
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class UseCaseEditor {
  private readonly api = inject(UseCasesService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  private readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly detail = signal<UseCaseDetail | null>(null);
  protected readonly catalogs = signal<CatalogOut[]>([]);
  protected readonly versions = signal<UseCaseVersionSummary[]>([]);
  protected readonly diff = signal<UseCaseVersionDiff | null>(null);
  protected readonly fields = signal<FieldRow[]>([]);
  protected readonly issues = signal<UseCaseIssueOut[]>([]);
  protected readonly message = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly saving = signal(false);
  protected readonly publishError = signal<string | null>(null);

  protected name = '';
  protected pattern: Pattern = 'supervisor';
  protected description = '';
  protected icon = '';
  protected tagsText = '';
  protected catalogId: string | null = null;
  protected categoryId: string | null = null;
  protected outputType: OutputType = 'markdown';
  protected schemaText = '{\n  "type": "object"\n}';
  protected maxCost = 5;
  protected maxDuration = 1800;
  protected hitlPolicy: UseCaseDefaults.HitlPolicyEnum = 'agent_setting';
  private aliases: Record<string, string> = {};
  protected definitionText = JSON.stringify(emptyDefinition('supervisor'), null, 2);

  protected publishOpen = false;
  protected note = '';
  protected readonly publishLabel = $localize`Publish`;
  protected readonly publishNewLabel = $localize`Publish draft as new version`;

  protected readonly categories = computed(
    () => this.catalogs().find((c) => c.id === this.catalogId)?.categories ?? [],
  );
  protected readonly statusColor = computed(
    () =>
      ({ draft: 'default', testing: 'processing', published: 'success', deprecated: 'warning' })[
        this.detail()?.status ?? 'draft'
      ] ?? 'default',
  );

  private id: string | null = null;

  constructor() {
    effect(() => {
      if (this.projectId()) void this.load();
    });
  }

  private async load(): Promise<void> {
    const projectId = this.projectId()!;
    this.id = this.route.snapshot.paramMap.get('id');
    this.catalogs.set(await firstValueFrom(this.api.listCatalogs({ projectId })));
    if (!this.id) return;
    try {
      this.apply(await firstValueFrom(this.api.getUseCase({ projectId, useCaseId: this.id })));
      await this.loadVersions();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }

  private async loadVersions(): Promise<void> {
    if (!this.id) return;
    this.versions.set(
      await firstValueFrom(
        this.api.listVersions({ projectId: this.projectId()!, useCaseId: this.id }),
      ),
    );
  }

  private apply(d: UseCaseDetail): void {
    this.detail.set(d);
    this.id = d.id;
    this.issues.set(d.issues ?? []);
    this.name = d.name;
    this.pattern = d.pattern as Pattern;
    this.description = d.description;
    this.icon = d.icon;
    this.tagsText = d.tags.join(', ');
    this.catalogId = d.catalog_id;
    this.categoryId = d.category_id;
    this.outputType = d.output_type as OutputType;
    this.schemaText = d.output_schema ? JSON.stringify(d.output_schema, null, 2) : this.schemaText;
    const defaults = d.defaults as UseCaseDefaults;
    this.maxCost = defaults.max_cost_usd ?? 5;
    this.maxDuration = defaults.max_duration_s ?? 1800;
    this.hitlPolicy = defaults.hitl_policy ?? 'agent_setting';
    this.aliases = defaults.model_aliases ?? {};
    this.definitionText = JSON.stringify(d.definition, null, 2);
    this.fields.set(
      (d.input_fields as UseCaseInputField[]).map((f) => ({
        ...f,
        optionsText: (f.options ?? []).join(', '),
        defaultText: f.default === null || f.default === undefined ? '' : String(f.default),
      })),
    );
  }

  protected onPattern(): void {
    if (!this.detail())
      this.definitionText = JSON.stringify(emptyDefinition(this.pattern), null, 2);
  }

  protected slug(raw: string): string {
    return fieldName(raw);
  }

  protected addField(): void {
    const row = newField(this.fields().length + 1);
    this.fields.update((f) => [...f, { ...row, optionsText: '', defaultText: '' }]);
  }

  protected removeField(index: number): void {
    this.fields.update((f) => f.filter((_, i) => i !== index));
  }

  private inputFields(): UseCaseInputField[] {
    return this.fields().map((f) => {
      const { optionsText, defaultText, ...field } = f;
      const options = f.type === 'select' ? splitList(optionsText) : [];
      let value: string | number | null = null;
      if (defaultText !== '' && ['text', 'long_text', 'select'].includes(f.type))
        value = defaultText;
      if (defaultText !== '' && f.type === 'number') value = Number(defaultText);
      return { ...field, label: field.label || field.name, options, default: value };
    });
  }

  private body(): UseCaseUpdateIn | null {
    const definition = parseJsonObject(this.definitionText);
    if (definition.error) {
      this.message.set({ ok: false, text: $localize`Definition: ${definition.error}:error:` });
      return null;
    }
    let schema: Record<string, unknown> | null = null;
    if (this.outputType === 'json') {
      const parsed = parseJsonObject(this.schemaText);
      if (parsed.error || !parsed.value) {
        this.message.set({ ok: false, text: $localize`Output schema: ${parsed.error}:error:` });
        return null;
      }
      schema = parsed.value;
    }
    return {
      name: this.name,
      description: this.description,
      icon: this.icon,
      tags: splitList(this.tagsText),
      catalog_id: this.catalogId,
      category_id: this.categoryId,
      input_fields: this.inputFields(),
      output_type: this.outputType,
      output_schema: schema,
      defaults: {
        max_cost_usd: this.maxCost,
        max_duration_s: this.maxDuration,
        hitl_policy: this.hitlPolicy,
        model_aliases: this.aliases,
      },
      definition: definition.value ?? {},
    };
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    const body = this.body();
    if (!projectId || !body) return;
    this.saving.set(true);
    this.message.set(null);
    try {
      let d: UseCaseDetail;
      if (this.id) {
        d = await firstValueFrom(
          this.api.updateUseCase({ projectId, useCaseId: this.id, useCaseUpdateIn: body }),
        );
      } else {
        d = await firstValueFrom(
          this.api.createUseCase({
            projectId,
            useCaseCreateIn: { ...body, name: this.name, pattern: this.pattern },
          }),
        );
      }
      this.apply(d);
      this.message.set({ ok: true, text: $localize`Draft saved` });
      if (!this.route.snapshot.paramMap.get('id')) {
        await this.router.navigate(['/build', d.id, 'edit'], { replaceUrl: true });
      }
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.saving.set(false);
    }
  }

  protected async setStatus(status: 'draft' | 'testing' | 'deprecated'): Promise<void> {
    await this.act(async () =>
      this.apply(
        await firstValueFrom(
          this.api.setUseCaseStatus({
            projectId: this.projectId()!,
            useCaseId: this.id!,
            useCaseStatusIn: { status },
          }),
        ),
      ),
    );
  }

  protected openPublish(): void {
    this.note = '';
    this.publishError.set(null);
    this.publishOpen = true;
  }

  protected async publish(): Promise<void> {
    this.publishError.set(null);
    try {
      this.apply(
        await firstValueFrom(
          this.api.publishUseCase({
            projectId: this.projectId()!,
            useCaseId: this.id!,
            useCasePublishIn: { note: this.note },
          }),
        ),
      );
      this.publishOpen = false;
      await this.loadVersions();
      this.diff.set(null);
      this.message.set({
        ok: true,
        text: $localize`Published version ${this.detail()?.published_version}:version:`,
      });
    } catch (e) {
      this.publishError.set(problemMessage(e));
    }
  }

  protected async rollback(version: number): Promise<void> {
    if (!confirm($localize`Roll back to version ${version}:version:? The draft is replaced by it.`))
      return;
    await this.act(async () => {
      this.apply(
        await firstValueFrom(
          this.api.publishUseCase({
            projectId: this.projectId()!,
            useCaseId: this.id!,
            useCasePublishIn: { version },
          }),
        ),
      );
      await this.loadVersions();
      this.diff.set(null);
    });
  }

  protected async compare(version: number): Promise<void> {
    const live = this.detail()?.published_version;
    if (!live) return;
    await this.act(async () =>
      this.diff.set(
        await firstValueFrom(
          this.api.diffVersions({
            projectId: this.projectId()!,
            useCaseId: this.id!,
            left: version,
            right: live,
          }),
        ),
      ),
    );
  }

  protected show(v: UseCaseVersionDiff['left'], field: string): string {
    const value = (v as unknown as Record<string, unknown>)[field];
    return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }

  private async act(action: () => Promise<void>): Promise<void> {
    this.message.set(null);
    try {
      await action();
    } catch (e) {
      this.message.set({ ok: false, text: problemMessage(e) });
    }
  }
}
