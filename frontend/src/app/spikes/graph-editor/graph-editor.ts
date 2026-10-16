import { JsonPipe } from '@angular/common';
import {
  Component,
  Injector,
  afterNextRender,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  FCanvasComponent,
  FCreateConnectionEvent,
  FFlowComponent,
  FFlowModule,
  FMoveNodesEvent,
  FSelectionChangeEvent,
} from '@foblex/flow';
import { ButtonModule } from 'primeng/button';
import { DrawerModule } from 'primeng/drawer';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { TagModule } from 'primeng/tag';

import {
  Edge,
  GraphNode,
  NodeType,
  UseCaseGraph,
  addNode,
  connect,
  disconnect,
  edges,
  inputPort,
  moveNode,
  outputPorts,
  removeNode,
  renameNode,
  semantic,
  updateNode,
} from './graph.model';
import { validate } from './graph.validate';
import { PRD_GRAPH, syntheticGraph } from './samples';

declare global {
  interface Window {
    __forgeGraph?: { metrics: () => Record<string, number>; json: () => string };
  }
}

const PALETTE: NodeType[] = [
  'agent',
  'tool',
  'llm',
  'router',
  'for_each',
  'while',
  'human_approval',
  'end',
];

/**
 * Spike E0.5c: graph use-case editor on Foblex Flow over the ADR-010 schema.
 * All state is one immutable signal; Foblex events (emitted outside Angular's zone) only
 * produce new graph values, which is what zoneless change detection needs.
 */
@Component({
  selector: 'forge-graph-editor',
  imports: [
    FFlowModule,
    FormsModule,
    JsonPipe,
    ButtonModule,
    DrawerModule,
    InputTextModule,
    MessageModule,
    SelectModule,
    TagModule,
  ],
  templateUrl: './graph-editor.html',
  styleUrl: './graph-editor.scss',
})
export class GraphEditor {
  private readonly canvas = viewChild(FCanvasComponent);
  private readonly flow = viewChild(FFlowComponent);
  private readonly injector = inject(Injector);

  protected readonly palette = PALETTE;
  protected readonly graph = signal<UseCaseGraph>(PRD_GRAPH);
  protected readonly edges = computed(() => edges(this.graph()));
  protected readonly issues = computed(() => validate(this.graph()));
  protected readonly invalidNodes = computed(
    () =>
      new Set(
        this.issues()
          .filter((i) => i.severity === 'error' && i.node)
          .map((i) => i.node!),
      ),
  );
  protected readonly json = computed(() => JSON.stringify(semantic(this.graph()), null, 2));
  protected readonly selectedId = signal<string | null>(null);
  protected readonly selected = computed(
    () => this.graph().nodes.find((n) => n.id === this.selectedId()) ?? null,
  );
  protected readonly drawerOpen = signal(false);
  protected readonly metrics = signal<Record<string, number>>({});
  protected renameTo = '';

  private renderStart = performance.now();
  private selection: { nodeIds: string[]; connectionIds: string[] } = {
    nodeIds: [],
    connectionIds: [],
  };

  constructor() {
    window.__forgeGraph = { metrics: () => this.metrics(), json: () => this.json() };
  }

  protected inputPort = inputPort;
  protected outputPorts = outputPorts;

  protected position(node: GraphNode): { x: number; y: number } {
    return this.graph().ui?.positions[node.id] ?? { x: 0, y: 0 };
  }

  protected onRendered(): void {
    const ms = Math.round(performance.now() - this.renderStart);
    this.metrics.update((m) => ({ ...m, last_render_ms: ms, nodes: this.graph().nodes.length }));
    this.canvas()?.fitToScreen({ x: 40, y: 40 }, false);
  }

  protected onConnect(event: FCreateConnectionEvent): void {
    if (event.targetId) this.graph.update((g) => connect(g, event.sourceId, event.targetId!));
  }

  protected onMove(event: FMoveNodesEvent): void {
    this.graph.update((g) => event.nodes.reduce((acc, n) => moveNode(acc, n.id, n.position), g));
  }

  protected onSelection(event: FSelectionChangeEvent): void {
    this.selection = { nodeIds: event.nodeIds, connectionIds: event.connectionIds };
    this.selectedId.set(event.nodeIds.length === 1 ? event.nodeIds[0] : null);
  }

  protected openConfig(id: string): void {
    this.selectedId.set(id);
    this.renameTo = id;
    this.drawerOpen.set(true);
  }

  protected deleteSelection(): void {
    const { nodeIds, connectionIds } = this.selection;
    this.graph.update((g) => {
      let next = connectionIds.reduce((acc, cid) => {
        const edge = edges(acc).find((e: Edge) => e.id === cid);
        return edge ? disconnect(acc, edge) : acc;
      }, g);
      next = nodeIds.filter((id) => id !== 'start').reduce((acc, id) => removeNode(acc, id), next);
      return next;
    });
    this.selection = { nodeIds: [], connectionIds: [] };
    this.selectedId.set(null);
  }

  protected add(type: NodeType): void {
    const n = this.graph().nodes.length;
    this.graph.update((g) => addNode(g, type, { x: 40 + (n % 5) * 40, y: -160 - (n % 3) * 40 }));
  }

  protected patch(field: keyof GraphNode, value: unknown): void {
    const id = this.selectedId();
    if (id) this.graph.update((g) => updateNode(g, id, { [field]: value || undefined }));
  }

  protected rename(): void {
    const from = this.selectedId();
    if (!from) return;
    const before = this.graph();
    const after = renameNode(before, from, this.renameTo.trim());
    if (after !== before) {
      this.graph.set(after);
      this.selectedId.set(this.renameTo.trim());
    }
  }

  protected loadSample(): void {
    this.reload(PRD_GRAPH);
  }

  protected loadSynthetic(n: number): void {
    this.reload(syntheticGraph(n));
  }

  private reload(graph: UseCaseGraph): void {
    this.renderStart = performance.now();
    this.selectedId.set(null);
    this.graph.set(graph);
    // fFullRendered fires once per lifecycle; reset() re-runs it after the new nodes render.
    afterNextRender(() => this.flow()?.reset(), { injector: this.injector });
  }

  protected onKeydown(event: KeyboardEvent): void {
    if (
      (event.key === 'Delete' || event.key === 'Backspace') &&
      !(event.target instanceof HTMLInputElement)
    ) {
      this.deleteSelection();
    }
  }
}
