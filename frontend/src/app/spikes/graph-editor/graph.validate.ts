import { GraphNode, UseCaseGraph, outputPorts } from './graph.model';

export interface Issue {
  severity: 'error' | 'warning';
  node?: string;
  code: string;
  message: string;
}

/** Save-time checks of ADR-010 that the editor runs live. */
export function validate(graph: UseCaseGraph): Issue[] {
  const issues: Issue[] = [];
  const byId = new Map<string, GraphNode>();
  for (const n of graph.nodes) {
    if (byId.has(n.id)) issues.push(err(n.id, 'duplicate_id', `Node name "${n.id}" is used twice`));
    byId.set(n.id, n);
  }

  const owner = new Map<string, string>(); // output port id -> node id
  for (const n of graph.nodes) for (const p of outputPorts(n)) owner.set(p.id, n.id);

  const starts = graph.nodes.filter((n) => n.type === 'start');
  if (starts.length !== 1)
    issues.push(
      err(undefined, 'start_count', `Exactly one start node required, found ${starts.length}`),
    );

  for (const n of graph.nodes) {
    if (n.type === 'start' && n.after?.length)
      issues.push(err(n.id, 'start_has_parent', 'Start cannot have dependencies'));
    if (n.type !== 'start' && !n.after?.length)
      issues.push(err(n.id, 'no_parent', 'Node has no "Execute After" dependency'));
    for (const ref of n.after ?? []) {
      if (!owner.has(ref))
        issues.push(err(n.id, 'unknown_ref', `Depends on unknown output "${ref}"`));
    }
    if ((n.type === 'for_each' || n.type === 'while') && !n.max_iterations) {
      issues.push(err(n.id, 'unbounded_loop', 'Loop needs max_iterations'));
    }
    if (n.type === 'agent' && !n.agent_ref)
      issues.push(warn(n.id, 'no_agent', 'No agent selected'));
  }

  // Outputs nobody consumes: a loop without a body, router sections leading nowhere.
  const used = new Set(graph.nodes.flatMap((n) => n.after ?? []));
  for (const n of graph.nodes) {
    if (n.type === 'for_each' || n.type === 'while') {
      if (!used.has(`${n.id}.loop`))
        issues.push(err(n.id, 'empty_loop', 'Loop has no node on its "loop" output'));
    }
    if (n.type === 'router') {
      for (const p of outputPorts(n)) {
        if (!used.has(p.id) && p.label !== 'default')
          issues.push(warn(n.id, 'dead_section', `Section "${p.label}" leads nowhere`));
      }
    }
  }

  // Cycles: iteration is implicit in loop nodes, so explicit cycles are never allowed.
  const parents = (id: string): string[] =>
    (byId.get(id)?.after ?? []).map((r) => owner.get(r)).filter((p): p is string => !!p);
  const state = new Map<string, 'visiting' | 'done'>();
  const inCycle = new Set<string>();
  const dfs = (id: string, path: string[]): void => {
    if (state.get(id) === 'done') return;
    if (state.get(id) === 'visiting') {
      path.slice(path.indexOf(id)).forEach((c) => inCycle.add(c));
      return;
    }
    state.set(id, 'visiting');
    for (const p of parents(id)) dfs(p, [...path, id]);
    state.set(id, 'done');
  };
  graph.nodes.forEach((n) => dfs(n.id, []));
  for (const id of inCycle)
    issues.push(err(id, 'cycle', 'Node is part of a cycle; use a loop node instead'));

  // Reachability from start.
  if (starts.length === 1) {
    const children = new Map<string, string[]>();
    for (const n of graph.nodes)
      for (const p of parents(n.id)) children.set(p, [...(children.get(p) ?? []), n.id]);
    const seen = new Set<string>([starts[0].id]);
    const queue = [starts[0].id];
    while (queue.length) {
      for (const c of children.get(queue.shift()!) ?? []) {
        if (!seen.has(c)) {
          seen.add(c);
          queue.push(c);
        }
      }
    }
    for (const n of graph.nodes)
      if (!seen.has(n.id))
        issues.push(err(n.id, 'unreachable', 'Node is not reachable from start'));
  }
  return issues;
}

function err(node: string | undefined, code: string, message: string): Issue {
  return { severity: 'error', node, code, message };
}
function warn(node: string | undefined, code: string, message: string): Issue {
  return { severity: 'warning', node, code, message };
}
