import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, DestroyRef, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { A11yModule } from '@angular/cdk/a11y';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCardModule } from 'ng-zorro-antd/card';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { NzTooltipModule } from 'ng-zorro-antd/tooltip';
import { firstValueFrom } from 'rxjs';

import {
  AclPrincipalsOut,
  CollectionOut,
  DataService,
  ProjectsService,
  DocumentOut,
  KnowledgeBaseOut,
  UploadResult,
} from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

const MAX_REQUEST_BYTES = 95 * 1024 * 1024; // the server limit is 100 MB per request
const MAX_FILE_BYTES = 100 * 1024 * 1024;
const ACCEPT = '.pdf,.docx,.pptx,.xlsx,.html,.htm,.md,.markdown,.txt,.png,.jpg,.jpeg';

type Notice = { ok: boolean; text: string } | null;

/** Projects Studio → Data: Data Collections, uploads and per-file status (E1.14). */
@Component({
  selector: 'forge-project-data',
  imports: [
    A11yModule,
    DatePipe,
    DecimalPipe,
    FormsModule,
    RouterLink,
    NzAlertModule,
    NzButtonModule,
    NzCardModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
    NzTooltipModule,
  ],
  templateUrl: './project-data.html',
  styleUrls: ['../admin/admin.scss', './project-data.scss'],
})
export class ProjectData {
  /** Route parameter: a collection id shows its documents. */
  readonly id = input<string | undefined>(undefined);

  private readonly api = inject(DataService);
  private readonly projects = inject(ProjectsService);
  private readonly session = inject(Session);
  private readonly router = inject(Router);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly canWrite = computed(() => this.session.can('asset.write'));

  protected readonly collections = signal<CollectionOut[]>([]);
  protected readonly collection = signal<CollectionOut | null>(null);
  protected readonly documents = signal<DocumentOut[]>([]);
  protected readonly knowledgeBases = signal<KnowledgeBaseOut[]>([]);
  protected readonly notice = signal<Notice>(null);
  protected readonly uploading = signal(false);
  protected readonly uploadResults = signal<UploadResult[]>([]);
  protected readonly accept = ACCEPT;

  protected createOpen = false;
  protected draft = { name: '', description: '' };
  protected renaming: CollectionOut | null = null;
  protected renameName = '';
  protected readonly deleting = signal<CollectionOut | null>(null);
  protected readonly deletingDoc = signal<DocumentOut | null>(null);
  protected aclDoc: DocumentOut | null = null;
  protected aclGroups: string[] = [];
  protected aclRoles: string[] = [];
  protected uploadGroups: string[] = [];
  protected uploadRoles: string[] = [];
  protected readonly principals = signal<AclPrincipalsOut>({ groups: [], roles: [] });
  protected readonly deletedLabel = $localize`deleted`;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      const id = this.id();
      if (projectId) void this.load(projectId, id);
    });
    inject(DestroyRef).onDestroy(() => this.stopPolling());
  }

  private stopPolling(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** Poll while files are being ingested, so statuses move on their own. */
  private schedule(): void {
    this.stopPolling();
    const busy =
      this.collections().some((c) => c.status === 'in_progress') ||
      this.documents().some(
        (d) =>
          d.status === 'pending' ||
          d.status === 'processing' ||
          (d.ingestions ?? []).some((i) => i.status === 'pending' || i.status === 'processing'),
      ) ||
      this.knowledgeBases().some((k) => k.status === 'building');
    if (busy) {
      this.timer = setTimeout(() => {
        const projectId = this.projectId();
        if (projectId) void this.load(projectId, this.id(), true);
      }, 3000);
    }
  }

  private async load(projectId: string, id: string | undefined, quiet = false): Promise<void> {
    try {
      if (id) {
        if (!quiet) {
          this.principals.set(await firstValueFrom(this.projects.aclPrincipals({ projectId })));
        }
        const [collection, documents, kbs] = await Promise.all([
          firstValueFrom(this.api.getCollection({ projectId, collectionId: id })),
          firstValueFrom(this.api.listDocuments({ projectId, collectionId: id })),
          firstValueFrom(this.api.listKnowledgeBases({ projectId, collectionId: id })),
        ]);
        this.collection.set(collection);
        this.documents.set(documents);
        this.knowledgeBases.set(kbs);
      } else {
        this.collection.set(null);
        this.documents.set([]);
        this.knowledgeBases.set([]);
        this.collections.set(await firstValueFrom(this.api.listCollections({ projectId })));
      }
      this.schedule();
    } catch (e) {
      if (!quiet) this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  private reload(): Promise<void> {
    const projectId = this.projectId();
    return projectId ? this.load(projectId, this.id()) : Promise.resolve();
  }

  // ------------------------------------------------------------ collections
  protected async create(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId || !this.draft.name.trim()) return;
    try {
      const created = await firstValueFrom(
        this.api.createCollection({
          projectId,
          collectionIn: { name: this.draft.name.trim(), description: this.draft.description },
        }),
      );
      this.createOpen = false;
      this.draft = { name: '', description: '' };
      await this.router.navigate(['/projects/data', created.id]);
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected openRename(c: CollectionOut): void {
    this.renaming = c;
    this.renameName = c.name;
  }

  protected async rename(): Promise<void> {
    const projectId = this.projectId();
    const target = this.renaming;
    if (!projectId || !target || !this.renameName.trim()) return;
    try {
      await firstValueFrom(
        this.api.updateCollection({
          projectId,
          collectionId: target.id,
          collectionUpdateIn: { name: this.renameName.trim() },
        }),
      );
      this.renaming = null;
      await this.reload();
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async removeCollection(): Promise<void> {
    const projectId = this.projectId();
    const target = this.deleting();
    if (!projectId || !target) return;
    try {
      await firstValueFrom(this.api.deleteCollection({ projectId, collectionId: target.id }));
      this.deleting.set(null);
      this.notice.set({ ok: true, text: $localize`Deleted “${target.name}:name:”.` });
      if (this.id()) await this.router.navigate(['/projects/data']);
      else await this.reload();
    } catch (e) {
      this.deleting.set(null);
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  // ------------------------------------------------------------ upload
  protected async pick(event: Event, folder: boolean): Promise<void> {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = '';
    if (files.length) await this.upload(files, folder);
  }

  private batches(files: File[]): File[][] {
    const out: File[][] = [];
    let current: File[] = [];
    let size = 0;
    for (const f of files) {
      if (current.length && size + f.size > MAX_REQUEST_BYTES) {
        out.push(current);
        current = [];
        size = 0;
      }
      current.push(f);
      size += f.size;
    }
    if (current.length) out.push(current);
    return out;
  }

  private async upload(files: File[], folder: boolean): Promise<void> {
    const projectId = this.projectId();
    const collectionId = this.id();
    if (!projectId || !collectionId) return;
    const tooBig = files.filter((f) => f.size > MAX_FILE_BYTES);
    const ok = files.filter((f) => f.size <= MAX_FILE_BYTES);
    const results: UploadResult[] = tooBig.map((f) => ({
      filename: f.name,
      status: 'rejected',
      error: $localize`file exceeds 100 MB`,
    }));
    this.uploading.set(true);
    this.notice.set(null);
    try {
      for (const batch of this.batches(ok)) {
        results.push(
          ...(await firstValueFrom(
            this.api.uploadDocuments({
              projectId,
              collectionId,
              files: batch,
              paths: folder ? batch.map((f) => f.webkitRelativePath || f.name) : undefined,
              groupIds: this.uploadGroups,
              roleIds: this.uploadRoles,
            }),
          )),
        );
      }
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    } finally {
      this.uploading.set(false);
      this.uploadResults.set(results);
      await this.reload();
    }
  }

  protected counts(results: UploadResult[]): {
    accepted: number;
    unchanged: number;
    rejected: number;
  } {
    return {
      accepted: results.filter((r) => r.status === 'accepted').length,
      unchanged: results.filter((r) => r.status === 'unchanged').length,
      rejected: results.filter((r) => r.status === 'rejected').length,
    };
  }

  protected rejected(results: UploadResult[]): UploadResult[] {
    return results.filter((r) => r.status === 'rejected');
  }

  // ------------------------------------------------------------ documents
  protected downloadUrl(d: DocumentOut): string {
    return `/api/v1/projects/${this.projectId()}/collections/${d.collection_id}/documents/${d.id}/content`;
  }

  protected async retry(d: DocumentOut): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.api.retryDocument({ projectId, collectionId: d.collection_id, documentId: d.id }),
      );
      await this.reload();
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected openAcl(d: DocumentOut): void {
    this.aclDoc = d;
    this.aclGroups = d.acl.filter((a) => a.kind === 'group').map((a) => a.id);
    this.aclRoles = d.acl.filter((a) => a.kind === 'role').map((a) => a.id);
  }

  protected async saveAcl(): Promise<void> {
    const projectId = this.projectId();
    const d = this.aclDoc;
    if (!projectId || !d) return;
    try {
      await firstValueFrom(
        this.api.updateDocument({
          projectId,
          collectionId: d.collection_id,
          documentId: d.id,
          documentUpdateIn: { group_ids: this.aclGroups, role_ids: this.aclRoles },
        }),
      );
      this.aclDoc = null;
      await this.reload();
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async removeDocument(): Promise<void> {
    const projectId = this.projectId();
    const d = this.deletingDoc();
    if (!projectId || !d) return;
    try {
      await firstValueFrom(
        this.api.deleteDocument({ projectId, collectionId: d.collection_id, documentId: d.id }),
      );
      this.deletingDoc.set(null);
      await this.reload();
    } catch (e) {
      this.deletingDoc.set(null);
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected files(c: CollectionOut): number {
    const n = c.documents;
    return (n.ready ?? 0) + (n.failed ?? 0) + (n.pending ?? 0) + (n.processing ?? 0);
  }

  protected statusColor(status: string): string {
    return (
      (
        {
          success: 'success',
          ready: 'success',
          in_progress: 'processing',
          processing: 'processing',
          pending: 'processing',
          building: 'processing',
          failed: 'error',
        } as Record<string, string>
      )[status] ?? 'default'
    );
  }

  protected statusLabel(status: string): string {
    return (
      (
        {
          success: $localize`Success`,
          ready: $localize`Success`,
          in_progress: $localize`In progress`,
          processing: $localize`In progress`,
          pending: $localize`In progress`,
          building: $localize`Indexing`,
          failed: $localize`Failed`,
          empty: $localize`Empty`,
        } as Record<string, string>
      )[status] ?? status
    );
  }
}
