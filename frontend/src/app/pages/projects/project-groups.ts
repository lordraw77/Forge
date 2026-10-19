import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { A11yModule } from '@angular/cdk/a11y';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzModalModule } from 'ng-zorro-antd/modal';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzTableModule } from 'ng-zorro-antd/table';
import { NzTagModule } from 'ng-zorro-antd/tag';
import { firstValueFrom } from 'rxjs';

import { GroupOut, MemberOut, ProjectsService } from '../../api-client';
import { problemMessage } from '../../core/http';
import { Session } from '../../core/session';

/** Projects Studio → Groups: named sets of members, used by document access (US-1.16.2). */
@Component({
  selector: 'forge-project-groups',
  imports: [
    A11yModule,
    DatePipe,
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzIconModule,
    NzInputModule,
    NzModalModule,
    NzSelectModule,
    NzTableModule,
    NzTagModule,
  ],
  template: `
    <div class="head">
      <p class="intro" i18n>
        Groups decide who reads restricted documents in RAG answers, together with roles. A member
        reads a document when they belong to one of its groups or have one of its roles.
      </p>
      <span class="spacer"></span>
      <button nz-button nzType="primary" (click)="openEdit(null)" data-testid="new-group">
        <nz-icon nzType="plus" /><span i18n>New group</span>
      </button>
    </div>
    @if (notice(); as n) {
      <nz-alert
        [nzType]="n.ok ? 'success' : 'error'"
        [nzMessage]="n.text"
        nzShowIcon
        data-testid="groups-notice"
      />
    }
    <nz-table
      [nzData]="groups()"
      nzSize="small"
      [nzShowPagination]="false"
      [nzPageSize]="1000"
      [nzNoResult]="empty"
    >
      <thead>
        <tr>
          <th i18n>Name</th>
          <th i18n>Members</th>
          <th i18n>Created</th>
          <th class="actions"><span class="sr-only" i18n>Actions</span></th>
        </tr>
      </thead>
      <tbody>
        @for (g of groups(); track g.id) {
          <tr [attr.data-group]="g.name">
            <td>
              {{ g.name }}
              @if (g.description) {
                <div class="muted small">{{ g.description }}</div>
              }
            </td>
            <td>
              <nz-select
                class="members"
                nzMode="multiple"
                nzPlaceHolder="No members"
                i18n-nzPlaceHolder
                [attr.aria-label]="membersLabel"
                [attr.data-testid]="'group-members-' + g.name"
                [ngModel]="g.member_ids"
                (ngModelChange)="setMembers(g, $event)"
              >
                @for (m of members(); track m.user_id) {
                  <nz-option
                    [nzValue]="m.user_id"
                    [nzLabel]="m.full_name + ' (' + m.username + ')'"
                  />
                }
              </nz-select>
            </td>
            <td>{{ g.created_at | date: 'short' }}</td>
            <td class="actions">
              <button nz-button nzType="link" nzSize="small" (click)="openEdit(g)" i18n>
                Edit
              </button>
              <button
                nz-button
                nzType="link"
                nzDanger
                nzSize="small"
                (click)="deleting.set(g)"
                i18n
              >
                Delete
              </button>
            </td>
          </tr>
        }
      </tbody>
    </nz-table>
    <ng-template #empty><span class="muted" i18n>No groups yet.</span></ng-template>

    <nz-modal [(nzVisible)]="editOpen" [nzTitle]="editing ? editTitle : newTitle" [nzFooter]="null">
      <ng-container *nzModalContent>
        <form class="form" (ngSubmit)="save()">
          <label for="grp-name" i18n>Name</label>
          <input
            nz-input
            id="grp-name"
            name="name"
            cdkFocusInitial
            required
            maxlength="80"
            data-testid="group-name"
            [(ngModel)]="draft.name"
          />
          <label for="grp-desc" i18n>Description</label>
          <input
            nz-input
            id="grp-desc"
            name="description"
            maxlength="500"
            [(ngModel)]="draft.description"
          />
          <div class="dialog-actions">
            <button nz-button type="button" (click)="editOpen = false" i18n>Cancel</button>
            <button
              nz-button
              nzType="primary"
              type="submit"
              [disabled]="!draft.name.trim()"
              data-testid="group-save"
              i18n
            >
              Save
            </button>
          </div>
        </form>
      </ng-container>
    </nz-modal>

    <nz-modal
      [nzVisible]="!!deleting()"
      nzTitle="Delete group"
      i18n-nzTitle
      [nzFooter]="null"
      (nzOnCancel)="deleting.set(null)"
    >
      <ng-container *nzModalContent>
        <p i18n>
          Delete “{{ deleting()?.name }}”? Documents restricted to this group only will no longer be
          readable by anyone until their access is changed.
        </p>
        <div class="dialog-actions">
          <button nz-button (click)="deleting.set(null)" cdkFocusInitial i18n>No</button>
          <button
            nz-button
            nzDanger
            nzType="primary"
            (click)="remove()"
            data-testid="group-delete-yes"
            i18n
          >
            Yes
          </button>
        </div>
      </ng-container>
    </nz-modal>
  `,
  styleUrls: ['../admin/admin.scss'],
  styles: [
    `
      .members {
        min-width: 22rem;
        width: 100%;
      }
      .muted {
        color: var(--forge-text-muted-color);
      }
      .small {
        font-size: 0.8rem;
      }
    `,
  ],
})
export class ProjectGroups {
  private readonly api = inject(ProjectsService);
  private readonly session = inject(Session);
  protected readonly projectId = computed(() => this.session.project()?.id ?? null);
  protected readonly groups = signal<GroupOut[]>([]);
  protected readonly members = signal<MemberOut[]>([]);
  protected readonly notice = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly deleting = signal<GroupOut | null>(null);
  protected readonly membersLabel = $localize`Members`;
  protected readonly newTitle = $localize`New group`;
  protected readonly editTitle = $localize`Edit group`;
  protected editOpen = false;
  protected editing: GroupOut | null = null;
  protected draft = { name: '', description: '' };

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      if (projectId) void this.load(projectId);
    });
  }

  private async load(projectId: string): Promise<void> {
    try {
      const [groups, members] = await Promise.all([
        firstValueFrom(this.api.listGroups({ projectId })),
        firstValueFrom(this.api.listMembers({ projectId })),
      ]);
      this.groups.set(groups);
      this.members.set(members);
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected openEdit(g: GroupOut | null): void {
    this.editing = g;
    this.draft = { name: g?.name ?? '', description: g?.description ?? '' };
    this.editOpen = true;
  }

  protected async save(): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    const groupIn = { name: this.draft.name.trim(), description: this.draft.description };
    try {
      if (this.editing) {
        await firstValueFrom(
          this.api.updateGroup({ projectId, groupId: this.editing.id, groupIn }),
        );
      } else {
        await firstValueFrom(this.api.createGroup({ projectId, groupIn }));
      }
      this.editOpen = false;
      await this.load(projectId);
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }

  protected async setMembers(g: GroupOut, userIds: string[]): Promise<void> {
    const projectId = this.projectId();
    if (!projectId) return;
    try {
      await firstValueFrom(
        this.api.setGroupMembers({
          projectId,
          groupId: g.id,
          groupMembersIn: { user_ids: userIds },
        }),
      );
      this.notice.set({ ok: true, text: $localize`Members of “${g.name}:name:” updated.` });
    } catch (e) {
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
    await this.load(projectId);
  }

  protected async remove(): Promise<void> {
    const projectId = this.projectId();
    const g = this.deleting();
    if (!projectId || !g) return;
    try {
      await firstValueFrom(this.api.deleteGroup({ projectId, groupId: g.id }));
      this.deleting.set(null);
      await this.load(projectId);
    } catch (e) {
      this.deleting.set(null);
      this.notice.set({ ok: false, text: problemMessage(e) });
    }
  }
}
