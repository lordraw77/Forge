import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { CatalogUseCase, RunFileOut, UseCaseRunOut, UseCasesService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';
import { durationSeconds, fieldValue, statusColor, statusLabel } from './catalog-util';

interface FormField {
  name: string;
  label?: string;
  type: string;
  required?: boolean;
  help?: string;
  options?: string[];
  default?: unknown;
}

/** One use case for an end user (US-2.9.1 #2): description, the input form generated from the
 * published version and the user's own run history. Agents, prompts and graphs are never shown. */
@Component({
  selector: 'forge-catalog-use-case',
  imports: [
    DatePipe,
    DecimalPipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <a nz-button nzType="link" routerLink="/catalog" i18n>← Catalog</a>
    </div>
    @if (error(); as e) {
      <nz-alert nzType="error" [nzMessage]="e" nzShowIcon data-testid="uc-error" />
    }
    @if (item(); as u) {
      <h2 data-testid="uc-title">
        {{ u.name }} <span class="muted">v{{ u.version }}</span>
      </h2>
      <p>{{ u.description }}</p>
      <p>
        <nz-tag>{{ u.catalog }} · {{ u.category }}</nz-tag>
        @for (t of u.tags; track t) {
          <nz-tag nzColor="blue">{{ t }}</nz-tag>
        }
        <nz-tag>{{ outputLabel(u.output_type) }}</nz-tag>
      </p>

      <form class="form run-form" (ngSubmit)="run()" data-testid="uc-form">
        @for (f of fields(); track f.name) {
          <label [attr.for]="'in-' + f.name"
            >{{ f.label || f.name }}{{ f.required ? ' *' : '' }}</label
          >
          @switch (f.type) {
            @case ('long_text') {
              <textarea
                nz-input
                rows="5"
                [id]="'in-' + f.name"
                [name]="f.name"
                [ngModel]="values()[f.name] ?? ''"
                (ngModelChange)="set(f.name, $event)"
              ></textarea>
            }
            @case ('select') {
              <nz-select
                [nzId]="'in-' + f.name"
                [ngModel]="values()[f.name]"
                (ngModelChange)="set(f.name, $event)"
                [name]="f.name"
                nzAllowClear
              >
                @for (o of f.options ?? []; track o) {
                  <nz-option [nzValue]="o" [nzLabel]="o" />
                }
              </nz-select>
            }
            @case ('file') {
              <div class="file">
                <input
                  type="file"
                  [id]="'in-' + f.name"
                  [attr.data-testid]="'file-' + f.name"
                  (change)="upload(f.name, $event)"
                />
                @if (uploads()[f.name]; as up) {
                  <span class="muted">{{ up.name }} · {{ up.size | number }} B</span>
                }
              </div>
            }
            @default {
              <input
                nz-input
                [id]="'in-' + f.name"
                [name]="f.name"
                [type]="f.type === 'number' ? 'number' : 'text'"
                [ngModel]="values()[f.name] ?? ''"
                (ngModelChange)="set(f.name, $event)"
              />
            }
          }
          @if (f.help) {
            <small class="muted">{{ f.help }}</small>
          }
        }
        <div class="buttons">
          <button
            nz-button
            nzType="primary"
            type="submit"
            [disabled]="starting() || uploading()"
            data-testid="uc-run"
          >
            <nz-icon nzType="play-circle" /><span i18n>Run</span>
          </button>
        </div>
      </form>

      <h3 i18n>Your runs</h3>
      <nz-table #table [nzData]="runs()" nzSize="small" [nzPageSize]="10" data-testid="uc-history">
        <thead>
          <tr>
            <th i18n>Started</th>
            <th i18n>Status</th>
            <th i18n>Version</th>
            <th i18n>Time</th>
            <th i18n>Cost</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          @for (r of table.data; track r.id) {
            <tr>
              <td>{{ r.created_at | date: 'medium' }}</td>
              <td>
                <nz-tag [nzColor]="color(r.status)">{{ label(r.status) }}</nz-tag>
              </td>
              <td>v{{ r.version }}</td>
              <td>{{ seconds(r) ?? '—' }}{{ seconds(r) !== null ? ' s' : '' }}</td>
              <td>{{ r.cost | number: '1.2-4' }} USD</td>
              <td class="actions">
                <a nz-button nzSize="small" [routerLink]="['/catalog/runs', r.id]" i18n>Open</a>
                <button nz-button nzSize="small" (click)="rerun(r)" i18n>Run again</button>
              </td>
            </tr>
          } @empty {
            <tr>
              <td colspan="6" class="muted" i18n>You have not run this use case yet.</td>
            </tr>
          }
        </tbody>
      </nz-table>
    }
  `,
  styles: `
    .run-form {
      max-width: 40rem;
      margin: 1rem 0 2rem;
    }
    .file {
      display: flex;
      gap: 0.75rem;
      align-items: center;
    }
  `,
  styleUrl: '../admin/admin.scss',
})
export class CatalogUseCasePage {
  private readonly api = inject(UseCasesService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly session = inject(Session);

  protected readonly item = signal<CatalogUseCase | null>(null);
  protected readonly runs = signal<UseCaseRunOut[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly values = signal<Record<string, string>>({});
  protected readonly uploads = signal<Record<string, RunFileOut>>({});
  protected readonly starting = signal(false);
  protected readonly uploading = signal(false);
  protected readonly fields = computed(() => (this.item()?.input_fields ?? []) as FormField[]);

  constructor() {
    effect(() => {
      if (this.session.project()) void this.load();
    });
  }

  protected color = statusColor;
  protected label = statusLabel;
  protected seconds = durationSeconds;

  protected outputLabel(type: string): string {
    return (
      {
        markdown: $localize`Text`,
        json: 'JSON',
        docx: 'Word (DOCX)',
        xlsx: 'Excel (XLSX)',
        pdf: 'PDF',
        chat: $localize`Chat`,
      }[type] ?? type
    );
  }

  private async load(): Promise<void> {
    const projectId = this.session.project()!.id;
    const useCaseId = this.route.snapshot.paramMap.get('id')!;
    try {
      const item = await firstValueFrom(this.api.catalogUseCase({ projectId, useCaseId }));
      this.item.set(item);
      const initial: Record<string, string> = {};
      for (const f of item.input_fields as FormField[]) {
        if (f.default !== undefined && f.default !== null) initial[f.name] = String(f.default);
      }
      this.values.set(initial);
      this.runs.set(await firstValueFrom(this.api.listUseCaseRuns({ projectId, useCaseId })));
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }

  protected set(name: string, value: string): void {
    this.values.update((v) => ({ ...v, [name]: value }));
  }

  protected async upload(name: string, event: Event): Promise<void> {
    const file = (event.target as HTMLInputElement).files?.[0];
    const projectId = this.session.project()?.id;
    if (!file || !projectId) return;
    this.uploading.set(true);
    this.error.set(null);
    try {
      const stored = await firstValueFrom(this.api.uploadInputFile({ projectId, file }));
      this.uploads.update((u) => ({ ...u, [name]: stored }));
      this.set(name, stored.id);
    } catch (e) {
      this.error.set(problemMessage(e));
      this.uploads.update((u) => Object.fromEntries(Object.entries(u).filter(([k]) => k !== name)));
      this.set(name, '');
    } finally {
      this.uploading.set(false);
    }
  }

  protected async run(): Promise<void> {
    const projectId = this.session.project()?.id;
    const item = this.item();
    if (!projectId || !item) return;
    const inputs: Record<string, unknown> = {};
    for (const f of this.fields()) {
      const v = fieldValue(f.type, this.values()[f.name]);
      if (v !== undefined) inputs[f.name] = v;
    }
    this.starting.set(true);
    this.error.set(null);
    try {
      const started = await firstValueFrom(
        this.api.startUseCaseRun({
          projectId,
          useCaseId: item.id,
          runStartIn: { inputs, mode: 'published' },
        }),
      );
      await this.router.navigate(['/catalog/runs', (started as { id: string }).id]);
    } catch (e) {
      this.error.set(problemMessage(e));
    } finally {
      this.starting.set(false);
    }
  }

  protected async rerun(r: UseCaseRunOut): Promise<void> {
    const projectId = this.session.project()?.id;
    if (!projectId) return;
    try {
      const again = await firstValueFrom(this.api.rerunUseCaseRun({ projectId, runId: r.id }));
      await this.router.navigate(['/catalog/runs', again.id]);
    } catch (e) {
      this.error.set(problemMessage(e));
    }
  }
}
