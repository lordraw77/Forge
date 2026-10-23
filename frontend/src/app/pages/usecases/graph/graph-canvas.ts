import {
  Component,
  computed,
  inject,
  input,
  model,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { Injector, afterNextRender } from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  FCanvasComponent,
  FCreateConnectionEvent,
  FFlowComponent,
  FFlowModule,
  FMoveNodesEvent,
  FSelectionChangeEvent,
} from '@foblex/flow';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { NzCheckboxModule } from 'ng-zorro-antd/checkbox';
import { NzDrawerModule } from 'ng-zorro-antd/drawer';
import { NzIconModule } from 'ng-zorro-antd/icon';
import { NzInputModule } from 'ng-zorro-antd/input';
import { NzSelectModule } from 'ng-zorro-antd/select';
import { NzAlertModule } from 'ng-zorro-antd/alert';

import {
  Condition,
  Edge,
  GraphNode,
  NodeType,
  UseCaseGraph,
  addNode,
  autoLayout,
  connect,
  disconnect,
  edges,
  fromDefinition,
  inputPort,
  moveNode,
  outputPorts,
  removeNode,
  renameNode,
  updateNode,
} from './graph-model';
import { splitList } from '../usecase-util';

export interface Asset {
  name: string;
  version: number;
  description: string;
}
export interface Assets {
  agent?: Asset[];
  tool?: Asset[];
  prompt?: Asset[];
}
export interface CanvasIssue {
  code: string;
  message: string;
  node?: string | null;
}

const PALETTE: { type: NodeType; label: string }[] = [
  { type: 'agent', label: $localize`Agent` },
  { type: 'tool', label: $localize`Tool` },
  { type: 'llm', label: $localize`Prompt` },
  { type: 'code', label: $localize`Code` },
  { type: 'router', label: $localize`Router` },
  { type: 'for_each', label: $localize`For loop` },
  { type: 'while', label: $localize`While loop` },
  { type: 'human_approval', label: $localize`Approval` },
  { type: 'end', label: $localize`End` },
];
const OPS: Condition['op'][] = ['=', '!=', '>', '<', 'contains'];

function jsonText(value: unknown): string {
  return value === undefined ? '' : JSON.stringify(value, null, 2);
}

/** Visual graph editor on Foblex Flow (spike E0.5c, ADR-010). The definition is the single
 * source of truth: every change produces a new definition value. Node settings open in a drawer
 * on double click; server issues highlight their node; `states` colour nodes during a test run. */
@Component({
  selector: 'forge-graph-canvas',
  imports: [
    FFlowModule,
    FormsModule,
    NzAlertModule,
    NzButtonModule,
    NzCheckboxModule,
    NzDrawerModule,
    NzIconModule,
    NzInputModule,
    NzSelectModule,
  ],
  template: `
    <div class="editor" tabindex="0" (keydown)="onKeydown($event)">
      @if (!readonly()) {
        <header class="toolbar">
          @for (p of palette; track p.type) {
            <button
              nz-button
              nzSize="small"
              [attr.data-testid]="'add-' + p.type"
              (click)="add(p.type)"
            >
              + {{ p.label }}
            </button>
          }
          <span class="spacer"></span>
          <button nz-button nzSize="small" (click)="layout()" data-testid="auto-layout" i18n>
            Auto layout
          </button>
          <button
            nz-button
            nzSize="small"
            nzDanger
            [attr.aria-label]="deleteLabel"
            (click)="deleteSelection()"
          >
            <span nz-icon nzType="delete"></span>
          </button>
        </header>
      }
      <div class="canvas" data-testid="graph-canvas">
        <f-flow
          fDraggable
          (fFullRendered)="onRendered()"
          (fCreateConnection)="onConnect($event)"
          (fMoveNodes)="onMove($event)"
          (fSelectionChange)="onSelection($event)"
        >
          <f-canvas fZoom>
            <f-connection-for-create fType="bezier">
              <f-connection-marker-arrow />
            </f-connection-for-create>
            @for (edge of edgeList(); track edge.id) {
              <f-connection
                [fConnectionId]="edge.id"
                [fSourceId]="edge.source"
                [fTargetId]="edge.target"
                fType="bezier"
              >
                <f-connection-marker-arrow />
              </f-connection>
            }
            @for (node of graph().nodes; track node.id) {
              <div
                fNode
                [fNodeId]="node.id"
                [fNodePosition]="position(node)"
                class="node"
                [class]="'node type-' + node.type"
                [class.invalid]="invalidNodes().has(node.id)"
                [class.selected]="selectedId() === node.id"
                [class]="stateClass(node.id)"
                [attr.data-node-id]="node.id"
                [attr.data-state]="states()[node.id] ?? ''"
              >
                @if (inputPort(node); as port) {
                  <div
                    fConnector
                    fConnectorType="target"
                    [fConnectorId]="port.id"
                    class="port in"
                  ></div>
                }
                <div class="title" fDragHandle (dblclick)="openConfig(node.id)">
                  <span class="type">{{ node.type }}</span>
                  <span class="name">{{ node.id }}</span>
                  @if (node.hitl) {
                    <span nz-icon nzType="user" title="Human in the loop"></span>
                  }
                </div>
                <div class="detail">
                  {{ node.agent_ref ?? node.tool_ref ?? node.prompt_ref ?? text(node.items) ?? '' }}
                </div>
                @if (node.after_mode === 'any') {
                  <div class="detail">after: any</div>
                }
                <div class="outs">
                  @for (port of outputPorts(node); track port.id) {
                    <div class="out-row">
                      <span>{{ port.label }}</span>
                      <div
                        fConnector
                        fConnectorType="source"
                        [fConnectorId]="port.id"
                        [fConnectorMultiple]="true"
                        class="port out"
                      ></div>
                    </div>
                  }
                </div>
              </div>
            }
          </f-canvas>
        </f-flow>
      </div>
    </div>

    <nz-drawer
      [nzVisible]="drawerOpen()"
      (nzOnClose)="drawerOpen.set(false)"
      nzPlacement="right"
      nzTitle="Node configuration"
      i18n-nzTitle
      [nzWidth]="420"
    >
      <ng-container *nzDrawerContent>
        @if (selected(); as node) {
          <div class="form" data-testid="node-drawer">
            @for (i of nodeIssues(node.id); track $index) {
              <nz-alert nzType="error" [nzMessage]="i.message" nzShowIcon />
            }
            <label for="node-name" i18n>Node Name</label>
            <div class="row">
              <input nz-input id="node-name" [readonly]="readonly()" [(ngModel)]="renameTo" />
              @if (!readonly()) {
                <button nz-button nzSize="small" (click)="rename()" i18n>Rename</button>
              }
            </div>
            <label for="node-type" i18n>Node Type</label>
            <input nz-input id="node-type" [value]="node.type" disabled />

            @switch (node.type) {
              @case ('start') {
                <label for="n-display" i18n>Display text</label>
                <input
                  nz-input
                  id="n-display"
                  [ngModel]="node.display_text"
                  (ngModelChange)="patch('display_text', $event)"
                />
              }
              @case ('agent') {
                <label for="n-agent" i18n>Agent</label>
                <nz-select
                  nzId="n-agent"
                  nzShowSearch
                  nzAllowClear
                  [ngModel]="node.agent_ref"
                  (ngModelChange)="patch('agent_ref', $event)"
                >
                  @for (a of assets().agent ?? []; track a.name) {
                    <nz-option
                      [nzValue]="a.name + '@' + a.version"
                      [nzLabel]="a.name + ' v' + a.version"
                    />
                  }
                </nz-select>
                <label for="n-task" i18n>Task</label>
                <textarea
                  nz-input
                  id="n-task"
                  rows="3"
                  [ngModel]="node.task"
                  (ngModelChange)="patch('task', $event)"
                ></textarea>
                <label for="n-input" i18n>Input (use &#123;variables&#125;)</label>
                <textarea
                  nz-input
                  id="n-input"
                  rows="3"
                  [ngModel]="node.input"
                  (ngModelChange)="patch('input', $event)"
                ></textarea>
                <label for="n-output" i18n>Output variable</label>
                <input
                  nz-input
                  id="n-output"
                  [ngModel]="node.output"
                  (ngModelChange)="patch('output', $event)"
                />
              }
              @case ('tool') {
                <label for="n-tool" i18n>Tool</label>
                <nz-select
                  nzId="n-tool"
                  nzShowSearch
                  nzAllowClear
                  [ngModel]="node.tool_ref"
                  (ngModelChange)="patch('tool_ref', $event)"
                >
                  @for (a of assets().tool ?? []; track a.name) {
                    <nz-option
                      [nzValue]="a.name + '@' + a.version"
                      [nzLabel]="a.name + ' v' + a.version"
                    />
                  }
                </nz-select>
                <label for="n-args" i18n>Arguments (JSON)</label>
                <textarea
                  nz-input
                  id="n-args"
                  rows="5"
                  class="mono"
                  [ngModel]="json(node.args)"
                  (ngModelChange)="patchJson('args', $event)"
                ></textarea>
                <label for="n-output" i18n>Output variable</label>
                <input
                  nz-input
                  id="n-output"
                  [ngModel]="node.output"
                  (ngModelChange)="patch('output', $event)"
                />
              }
              @case ('code') {
                <label for="n-code" i18n>Python (sandbox: no network, standard library only)</label>
                <textarea
                  nz-input
                  id="n-code"
                  rows="10"
                  class="mono"
                  spellcheck="false"
                  [ngModel]="node.code"
                  (ngModelChange)="patch('code', $event)"
                ></textarea>
                <label for="n-args" i18n>Arguments (JSON, passed to run)</label>
                <textarea
                  nz-input
                  id="n-args"
                  rows="4"
                  class="mono"
                  [ngModel]="json(node.args)"
                  (ngModelChange)="patchJson('args', $event)"
                ></textarea>
                <label for="n-output" i18n>Output variable</label>
                <input
                  nz-input
                  id="n-output"
                  [ngModel]="node.output"
                  (ngModelChange)="patch('output', $event)"
                />
              }
              @case ('llm') {
                <label for="n-prompt" i18n>Prompt</label>
                <nz-select
                  nzId="n-prompt"
                  nzShowSearch
                  nzAllowClear
                  [ngModel]="node.prompt_ref"
                  (ngModelChange)="patch('prompt_ref', $event)"
                >
                  @for (a of assets().prompt ?? []; track a.name) {
                    <nz-option
                      [nzValue]="a.name + '@' + a.version"
                      [nzLabel]="a.name + ' v' + a.version"
                    />
                  }
                </nz-select>
                <label for="n-vars" i18n>Variables (JSON)</label>
                <textarea
                  nz-input
                  id="n-vars"
                  rows="5"
                  class="mono"
                  [ngModel]="json(node.variables)"
                  (ngModelChange)="patchJson('variables', $event)"
                ></textarea>
                <label for="n-output" i18n>Output variable</label>
                <input
                  nz-input
                  id="n-output"
                  [ngModel]="node.output"
                  (ngModelChange)="patch('output', $event)"
                />
              }
              @case ('router') {
                @for (s of node.sections ?? []; track $index; let si = $index) {
                  <fieldset class="section">
                    <div class="row">
                      <input
                        nz-input
                        [attr.aria-label]="sectionLabel"
                        [ngModel]="s.name"
                        (ngModelChange)="section(si, { name: $event })"
                      />
                      <nz-select
                        [ngModel]="s.combine ?? 'and'"
                        (ngModelChange)="section(si, { combine: $event })"
                        class="narrow"
                      >
                        <nz-option nzValue="and" nzLabel="AND" />
                        <nz-option nzValue="or" nzLabel="OR" />
                      </nz-select>
                      <button nz-button nzSize="small" nzDanger (click)="removeSection(si)">
                        <span nz-icon nzType="delete"></span>
                      </button>
                    </div>
                    @for (c of s.conditions; track $index; let ci = $index) {
                      <div class="row">
                        <input
                          nz-input
                          placeholder="field"
                          i18n-placeholder
                          [ngModel]="c.field"
                          (ngModelChange)="cond(si, ci, { field: $event })"
                        />
                        <nz-select
                          [ngModel]="c.op"
                          (ngModelChange)="cond(si, ci, { op: $event })"
                          class="narrow"
                        >
                          @for (o of ops; track o) {
                            <nz-option [nzValue]="o" [nzLabel]="o" />
                          }
                        </nz-select>
                        <input
                          nz-input
                          placeholder="value"
                          i18n-placeholder
                          [ngModel]="c.value"
                          (ngModelChange)="cond(si, ci, { value: $event })"
                        />
                        <button nz-button nzSize="small" (click)="removeCond(si, ci)">
                          <span nz-icon nzType="close"></span>
                        </button>
                      </div>
                    }
                    <button nz-button nzSize="small" (click)="addCond(si)" i18n>
                      Add condition
                    </button>
                  </fieldset>
                }
                <button nz-button (click)="addSection()" data-testid="add-section" i18n>
                  Add section
                </button>
              }
              @case ('for_each') {
                <label for="n-items" i18n>Items (list variable or one per line)</label>
                <textarea
                  nz-input
                  id="n-items"
                  rows="3"
                  [ngModel]="text(node.items)"
                  (ngModelChange)="patch('items', $event)"
                ></textarea>
                <label for="n-max" i18n>Maximum iterations</label>
                <input
                  nz-input
                  id="n-max"
                  type="number"
                  [ngModel]="node.max_iterations ?? 50"
                  (ngModelChange)="patch('max_iterations', +$event)"
                />
              }
              @case ('while') {
                @for (c of node.conditions ?? []; track $index; let ci = $index) {
                  <div class="row">
                    <input
                      nz-input
                      placeholder="field"
                      i18n-placeholder
                      [ngModel]="c.field"
                      (ngModelChange)="whileCond(ci, { field: $event })"
                    />
                    <nz-select
                      [ngModel]="c.op"
                      (ngModelChange)="whileCond(ci, { op: $event })"
                      class="narrow"
                    >
                      @for (o of ops; track o) {
                        <nz-option [nzValue]="o" [nzLabel]="o" />
                      }
                    </nz-select>
                    <input
                      nz-input
                      placeholder="value"
                      i18n-placeholder
                      [ngModel]="c.value"
                      (ngModelChange)="whileCond(ci, { value: $event })"
                    />
                  </div>
                }
                <button nz-button nzSize="small" (click)="addWhileCond()" i18n>
                  Add condition
                </button>
                <label for="n-max" i18n>Maximum iterations</label>
                <input
                  nz-input
                  id="n-max"
                  type="number"
                  [ngModel]="node.max_iterations ?? 50"
                  (ngModelChange)="patch('max_iterations', +$event)"
                />
              }
              @case ('human_approval') {
                <label for="n-ainput" i18n>Text to review (use &#123;variables&#125;)</label>
                <textarea
                  nz-input
                  id="n-ainput"
                  rows="4"
                  data-testid="n-approval-input"
                  [ngModel]="node.input"
                  (ngModelChange)="patch('input', $event)"
                ></textarea>
                <label for="n-output" i18n>Output variable</label>
                <input
                  nz-input
                  id="n-output"
                  [ngModel]="node.output"
                  (ngModelChange)="patch('output', $event)"
                />
              }
              @case ('end') {
                <label for="n-end" i18n>Result (use &#123;variables&#125;)</label>
                <textarea
                  nz-input
                  id="n-end"
                  rows="3"
                  [ngModel]="text(node.output)"
                  (ngModelChange)="patch('output', $event)"
                ></textarea>
              }
            }

            @if (node.type === 'agent' || node.type === 'tool' || node.type === 'llm') {
              <label
                nz-checkbox
                [nzId]="'n-hitl'"
                [ngModel]="!!node.hitl"
                (ngModelChange)="patch('hitl', $event ? true : undefined)"
                i18n
              >
                Human in the loop (a person reviews the output)
              </label>
            }
            @if (node.type === 'human_approval' || node.hitl) {
              <label for="n-instr" i18n>Instructions for the reviewer</label>
              <textarea
                nz-input
                id="n-instr"
                rows="2"
                [ngModel]="node.instructions"
                (ngModelChange)="patch('instructions', $event || undefined)"
              ></textarea>
              <label for="n-assignees" i18n>Who may decide (Role Names, comma separated)</label>
              <input
                nz-input
                id="n-assignees"
                [ngModel]="(node.assignees ?? []).join(', ')"
                (ngModelChange)="patch('assignees', splitNames($event))"
              />
              <label for="n-hours" i18n>Hours to wait (1 to 720)</label>
              <input
                nz-input
                id="n-hours"
                type="number"
                [ngModel]="node.timeout_hours ?? ''"
                (ngModelChange)="patch('timeout_hours', $event ? +$event : undefined)"
              />
            }

            @if (node.type !== 'start') {
              <label for="after-mode" i18n>Execute After mode</label>
              <nz-select
                nzId="after-mode"
                [ngModel]="node.after_mode ?? 'all'"
                (ngModelChange)="patch('after_mode', $event === 'all' ? undefined : $event)"
              >
                <nz-option nzValue="all" nzLabel="All parents" i18n-nzLabel />
                <nz-option nzValue="any" nzLabel="Any parent" i18n-nzLabel />
              </nz-select>
              <small i18n>Runs after: {{ (node.after ?? []).join(', ') || '—' }}</small>
            }
          </div>
        }
      </ng-container>
    </nz-drawer>
  `,
  styleUrl: './graph-canvas.scss',
})
export class GraphCanvas {
  private readonly canvas = viewChild(FCanvasComponent);
  private readonly flow = viewChild(FFlowComponent);
  private readonly injector = inject(Injector);

  /** The stored definition: `{nodes: [...], ui: {positions}}`. */
  readonly definition = model.required<Record<string, unknown>>();
  readonly issues = input<CanvasIssue[]>([]);
  readonly assets = input<Assets>({});
  readonly readonly = input(false);
  /** Node id → `running` | `succeeded` | `failed` | `skipped`, for the test page. */
  readonly states = input<Record<string, string>>({});
  readonly selectedNode = output<string | null>();

  protected readonly palette = PALETTE;
  protected splitNames = splitList;
  protected readonly ops = OPS;
  protected readonly deleteLabel = $localize`Delete selection`;
  protected readonly sectionLabel = $localize`Section name`;
  protected readonly graph = computed<UseCaseGraph>(() => fromDefinition(this.definition()));
  protected readonly edgeList = computed(() => edges(this.graph()));
  protected readonly invalidNodes = computed(
    () =>
      new Set(
        this.issues()
          .filter((i) => i.node)
          .map((i) => i.node as string),
      ),
  );
  protected readonly selectedId = signal<string | null>(null);
  protected readonly selected = computed(
    () => this.graph().nodes.find((n) => n.id === this.selectedId()) ?? null,
  );
  protected readonly drawerOpen = signal(false);
  protected renameTo = '';
  private selection: { nodeIds: string[]; connectionIds: string[] } = {
    nodeIds: [],
    connectionIds: [],
  };

  protected readonly inputPort = inputPort;
  protected readonly outputPorts = outputPorts;

  /** Apply a change: keep the other keys of the definition (limits, patterns) untouched. */
  private commit(next: UseCaseGraph): void {
    this.definition.set({ ...this.definition(), ...next } as Record<string, unknown>);
  }

  protected position(node: GraphNode): { x: number; y: number } {
    return this.graph().ui?.positions[node.id] ?? { x: 0, y: 0 };
  }

  protected stateClass(id: string): string {
    const s = this.states()[id];
    return s ? `state-${s}` : '';
  }

  protected text(value: unknown): string {
    return typeof value === 'string' ? value : value === undefined ? '' : JSON.stringify(value);
  }

  protected json(value: unknown): string {
    return jsonText(value);
  }

  protected nodeIssues(id: string): CanvasIssue[] {
    return this.issues().filter((i) => i.node === id);
  }

  protected onRendered(): void {
    this.canvas()?.fitToScreen({ x: 40, y: 40 }, false);
  }

  protected onConnect(event: FCreateConnectionEvent): void {
    if (this.readonly() || !event.targetId) return;
    this.commit(connect(this.graph(), event.sourceId, event.targetId));
  }

  protected onMove(event: FMoveNodesEvent): void {
    if (this.readonly()) return;
    this.commit(event.nodes.reduce((acc, n) => moveNode(acc, n.id, n.position), this.graph()));
  }

  protected onSelection(event: FSelectionChangeEvent): void {
    this.selection = { nodeIds: event.nodeIds, connectionIds: event.connectionIds };
    const id = event.nodeIds.length === 1 ? event.nodeIds[0] : null;
    this.selectedId.set(id);
    this.selectedNode.emit(id);
  }

  protected openConfig(id: string): void {
    this.selectedId.set(id);
    this.renameTo = id;
    this.drawerOpen.set(true);
  }

  protected add(type: NodeType): void {
    const n = this.graph().nodes.length;
    this.commit(addNode(this.graph(), type, { x: 40 + (n % 5) * 40, y: 40 + (n % 4) * 120 }));
  }

  protected layout(): void {
    this.commit(autoLayout(this.graph()));
    afterNextRender(() => this.flow()?.reset(), { injector: this.injector });
  }

  protected deleteSelection(): void {
    if (this.readonly()) return;
    const { nodeIds, connectionIds } = this.selection;
    let next = connectionIds.reduce((acc, cid) => {
      const edge = edges(acc).find((e: Edge) => e.id === cid);
      return edge ? disconnect(acc, edge) : acc;
    }, this.graph());
    next = nodeIds.filter((id) => id !== 'start').reduce((acc, id) => removeNode(acc, id), next);
    this.commit(next);
    this.selection = { nodeIds: [], connectionIds: [] };
    this.selectedId.set(null);
  }

  protected patch(field: keyof GraphNode, value: unknown): void {
    const id = this.selectedId();
    if (!id || this.readonly()) return;
    this.commit(
      updateNode(this.graph(), id, {
        [field]: value === '' || value === false ? undefined : value,
      }),
    );
  }

  protected patchJson(field: 'args' | 'variables', raw: string): void {
    try {
      this.patch(field, raw.trim() ? JSON.parse(raw) : undefined);
    } catch {
      // keep typing: the previous valid value stays until the JSON parses
    }
  }

  protected rename(): void {
    const from = this.selectedId();
    if (!from) return;
    const to = this.renameTo.trim();
    const before = this.graph();
    const after = renameNode(before, from, to);
    if (after !== before) {
      this.commit(after);
      this.selectedId.set(to);
    }
  }

  // ---- router sections and conditions
  private sections(): NonNullable<GraphNode['sections']> {
    return [...(this.selected()?.sections ?? [])];
  }

  protected section(i: number, change: Record<string, unknown>): void {
    const next = this.sections();
    const old = next[i].name;
    next[i] = { ...next[i], ...change } as (typeof next)[number];
    const id = this.selectedId();
    this.patch('sections', next);
    // renaming a section renames the port that other nodes depend on
    if (id && typeof change['name'] === 'string' && change['name'] !== old) {
      const rewritten = this.graph().nodes.map((n) => ({
        ...n,
        after: n.after?.map((r) => (r === `${id}.${old}` ? `${id}.${change['name']}` : r)),
      }));
      this.commit({ ...this.graph(), nodes: rewritten });
    }
  }

  protected addSection(): void {
    const next = this.sections();
    next.push({ name: `path_${next.length + 1}`, conditions: [] });
    this.patch('sections', next);
  }

  protected removeSection(i: number): void {
    const id = this.selectedId();
    const next = this.sections();
    const [gone] = next.splice(i, 1);
    this.patch('sections', next);
    if (id && gone) {
      const ref = `${id}.${gone.name}`;
      this.commit({
        ...this.graph(),
        nodes: this.graph().nodes.map((n) => ({ ...n, after: n.after?.filter((r) => r !== ref) })),
      });
    }
  }

  protected cond(si: number, ci: number, change: Partial<Condition>): void {
    const next = this.sections();
    const conditions = [...next[si].conditions];
    conditions[ci] = { ...conditions[ci], ...change };
    next[si] = { ...next[si], conditions };
    this.patch('sections', next);
  }

  protected addCond(si: number): void {
    const next = this.sections();
    next[si] = {
      ...next[si],
      conditions: [...next[si].conditions, { field: '', op: '=', value: '' }],
    };
    this.patch('sections', next);
  }

  protected removeCond(si: number, ci: number): void {
    const next = this.sections();
    next[si] = { ...next[si], conditions: next[si].conditions.filter((_, i) => i !== ci) };
    this.patch('sections', next);
  }

  protected whileCond(ci: number, change: Partial<Condition>): void {
    const conditions = [...(this.selected()?.conditions ?? [])];
    conditions[ci] = { ...conditions[ci], ...change };
    this.patch('conditions', conditions);
  }

  protected addWhileCond(): void {
    this.patch('conditions', [
      ...(this.selected()?.conditions ?? []),
      { field: '', op: '=', value: '' },
    ]);
  }

  protected onKeydown(event: KeyboardEvent): void {
    if (
      (event.key === 'Delete' || event.key === 'Backspace') &&
      !(event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement)
    ) {
      this.deleteSelection();
    }
  }

  protected list(raw: string): string[] {
    return splitList(raw);
  }
}
