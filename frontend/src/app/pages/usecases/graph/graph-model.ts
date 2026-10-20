/**
 * Use case graph, JSON schema v1 (ADR-010), plus a non-semantic `ui` block for canvas layout.
 * Pure functions only: the editor component is a thin view over these.
 */

export type NodeType =
  'start' | 'agent' | 'tool' | 'llm' | 'router' | 'for_each' | 'while' | 'human_approval' | 'end';

export interface RouterSection {
  name: string;
  conditions: Condition[];
  combine?: 'and' | 'or';
}

export interface Condition {
  field: string;
  op: '=' | '!=' | '>' | '<' | 'contains';
  value: string;
}

export interface GraphNode {
  id: string;
  type: NodeType;
  after?: string[];
  after_mode?: 'all' | 'any';
  display_text?: string;
  agent_ref?: string;
  tool_ref?: string;
  prompt_ref?: string;
  task?: string;
  input?: string;
  output?: string;
  items?: string | unknown[];
  args?: Record<string, unknown>;
  variables?: Record<string, unknown>;
  sections?: RouterSection[];
  conditions?: Condition[];
  combine?: 'and' | 'or';
  max_iterations?: number;
  hitl?: boolean;
  /** Approval: what to check, which Role Names may decide, hours to wait. */
  instructions?: string;
  assignees?: string[];
  timeout_hours?: number;
}

export interface UseCaseGraph {
  schema_version?: 1;
  pattern?: 'graph';
  nodes: GraphNode[];
  limits?: { max_steps?: number; max_cost_usd?: number; timeout_s?: number };
  /** Canvas layout only; ignored by validation and execution. */
  ui?: { positions: Record<string, { x: number; y: number }> };
}

export interface Port {
  /** Connector id; for outputs this is exactly the reference used in `after`. */
  id: string;
  label: string;
}

export interface Edge {
  id: string;
  source: string; // output port id == `after` reference
  target: string; // input port id
}

export const INPUT_SUFFIX = '::in';

export function inputPort(node: GraphNode): Port | null {
  return node.type === 'start' ? null : { id: node.id + INPUT_SUFFIX, label: 'in' };
}

/** Output ports by node type: the reference grammar of `after` (ADR-010). */
export function outputPorts(node: GraphNode): Port[] {
  switch (node.type) {
    case 'end':
      return [];
    case 'router':
      return [
        ...(node.sections ?? []).map((s) => ({ id: `${node.id}.${s.name}`, label: s.name })),
        { id: `${node.id}.default`, label: 'default' },
      ];
    case 'for_each':
    case 'while':
      return [
        { id: `${node.id}.loop`, label: 'loop' },
        { id: `${node.id}.done`, label: 'done' },
      ];
    default:
      return [{ id: node.id, label: 'out' }];
  }
}

export function edges(graph: UseCaseGraph): Edge[] {
  return graph.nodes.flatMap((n) =>
    (n.after ?? []).map((ref) => ({
      id: `${ref}->${n.id}`,
      source: ref,
      target: n.id + INPUT_SUFFIX,
    })),
  );
}

function targetNodeId(inputId: string): string {
  return inputId.endsWith(INPUT_SUFFIX) ? inputId.slice(0, -INPUT_SUFFIX.length) : inputId;
}

/** Connecting output `source` to input `target` adds `source` to the target's `after`. */
export function connect(graph: UseCaseGraph, source: string, target: string): UseCaseGraph {
  const id = targetNodeId(target);
  return {
    ...graph,
    nodes: graph.nodes.map((n) =>
      n.id === id && !(n.after ?? []).includes(source)
        ? { ...n, after: [...(n.after ?? []), source] }
        : n,
    ),
  };
}

export function disconnect(graph: UseCaseGraph, edge: Edge): UseCaseGraph {
  const id = targetNodeId(edge.target);
  return {
    ...graph,
    nodes: graph.nodes.map((n) =>
      n.id === id ? { ...n, after: (n.after ?? []).filter((r) => r !== edge.source) } : n,
    ),
  };
}

export function addNode(
  graph: UseCaseGraph,
  type: NodeType,
  position: { x: number; y: number },
): UseCaseGraph {
  let i = graph.nodes.length + 1;
  while (graph.nodes.some((n) => n.id === `${type}_${i}`)) i++;
  const node: GraphNode = { id: `${type}_${i}`, type };
  if (type === 'router') node.sections = [{ name: 'yes', conditions: [] }];
  if (type === 'for_each' || type === 'while') node.max_iterations = 50;
  return {
    ...graph,
    nodes: [...graph.nodes, node],
    ui: { positions: { ...(graph.ui?.positions ?? {}), [node.id]: position } },
  };
}

/** Removing a node also removes every reference to any of its output ports. */
export function removeNode(graph: UseCaseGraph, id: string): UseCaseGraph {
  const node = graph.nodes.find((n) => n.id === id);
  if (!node) return graph;
  const refs = new Set(outputPorts(node).map((p) => p.id));
  const positions = { ...(graph.ui?.positions ?? {}) };
  delete positions[id];
  return {
    ...graph,
    nodes: graph.nodes
      .filter((n) => n.id !== id)
      .map((n) => ({ ...n, after: (n.after ?? []).filter((r) => !refs.has(r)) })),
    ui: { positions },
  };
}

export function moveNode(
  graph: UseCaseGraph,
  id: string,
  position: { x: number; y: number },
): UseCaseGraph {
  return { ...graph, ui: { positions: { ...(graph.ui?.positions ?? {}), [id]: position } } };
}

/** The semantic definition (what is versioned and executed): `ui` stripped. */
export function semantic(graph: UseCaseGraph): Omit<UseCaseGraph, 'ui'> {
  const copy: Partial<UseCaseGraph> = { ...graph };
  delete copy.ui;
  return copy as Omit<UseCaseGraph, 'ui'>;
}

/** The stored definition (a possibly empty object) as a graph with positions for every node. */
export function fromDefinition(definition: Record<string, unknown>): UseCaseGraph {
  const nodes = Array.isArray(definition['nodes']) ? (definition['nodes'] as GraphNode[]) : [];
  const graph = { ...definition, nodes } as UseCaseGraph;
  const known = graph.ui?.positions ?? {};
  return nodes.every((n) => known[n.id]) ? graph : autoLayout(graph);
}

/** Simple layered layout for graphs without saved positions (longest path from start). */
export function autoLayout(graph: UseCaseGraph): UseCaseGraph {
  const owner = new Map<string, string>();
  for (const n of graph.nodes) for (const p of outputPorts(n)) owner.set(p.id, n.id);
  const depth = new Map<string, number>();
  const visit = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const node = graph.nodes.find((n) => n.id === id);
    const parents = (node?.after ?? []).map((r) => owner.get(r)).filter((p): p is string => !!p);
    const d = parents.length ? Math.max(...parents.map((p) => visit(p, seen))) + 1 : 0;
    depth.set(id, d);
    return d;
  };
  const rows = new Map<number, number>();
  const positions: Record<string, { x: number; y: number }> = {};
  for (const n of graph.nodes) {
    const d = visit(n.id, new Set());
    const row = rows.get(d) ?? 0;
    rows.set(d, row + 1);
    positions[n.id] = { x: d * 260, y: row * 140 };
  }
  return { ...graph, ui: { positions } };
}

/** Rename a node and rewrite every reference to its output ports (`id`, `id.loop`, `id.section`…). */
export function renameNode(graph: UseCaseGraph, from: string, to: string): UseCaseGraph {
  if (from === to || !/^[a-z][a-z0-9_]*$/.test(to) || graph.nodes.some((n) => n.id === to))
    return graph;
  const rewrite = (ref: string): string =>
    ref === from ? to : ref.startsWith(from + '.') ? to + ref.slice(from.length) : ref;
  const positions = { ...(graph.ui?.positions ?? {}) };
  if (positions[from]) {
    positions[to] = positions[from];
    delete positions[from];
  }
  return {
    ...graph,
    nodes: graph.nodes.map((n) => ({
      ...n,
      id: n.id === from ? to : n.id,
      after: n.after?.map(rewrite),
    })),
    ui: { positions },
  };
}

export function updateNode(
  graph: UseCaseGraph,
  id: string,
  patch: Partial<GraphNode>,
): UseCaseGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((n) => (n.id === id ? { ...n, ...patch, id: n.id } : n)),
  };
}
