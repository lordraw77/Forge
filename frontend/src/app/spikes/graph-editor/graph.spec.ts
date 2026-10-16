import {
  addNode,
  connect,
  disconnect,
  edges,
  outputPorts,
  removeNode,
  renameNode,
  semantic,
  UseCaseGraph,
} from './graph.model';
import { validate } from './graph.validate';
import { PRD_GRAPH, syntheticGraph } from './samples';

const codes = (g: UseCaseGraph) => validate(g).map((i) => `${i.node ?? '-'}:${i.code}`);

describe('graph model (ADR-010)', () => {
  it('derives typed output ports per node type', () => {
    const byId = Object.fromEntries(PRD_GRAPH.nodes.map((n) => [n.id, n]));
    expect(outputPorts(byId['per_epic']).map((p) => p.id)).toEqual([
      'per_epic.loop',
      'per_epic.done',
    ]);
    expect(outputPorts(byId['size_check']).map((p) => p.id)).toEqual([
      'size_check.large',
      'size_check.small',
      'size_check.default',
    ]);
    expect(outputPorts(byId['end'])).toEqual([]);
  });

  it('turns `after` references into edges and back', () => {
    const e = edges(PRD_GRAPH);
    expect(e).toContainEqual({
      id: 'per_epic.loop->stories',
      source: 'per_epic.loop',
      target: 'stories::in',
    });
    const cut = disconnect(
      PRD_GRAPH,
      e.find((x) => x.id === 'per_epic.loop->stories')!,
    );
    expect(cut.nodes.find((n) => n.id === 'stories')!.after).toEqual([]);
    const back = connect(cut, 'per_epic.loop', 'stories::in');
    expect(semantic(back)).toEqual(semantic(PRD_GRAPH));
  });

  it('does not duplicate an existing dependency', () => {
    const g = connect(PRD_GRAPH, 'start', 'epics::in');
    expect(g.nodes.find((n) => n.id === 'epics')!.after).toEqual(['start']);
  });

  it('removing a node removes references to all of its ports', () => {
    const g = removeNode(PRD_GRAPH, 'size_check');
    expect(g.nodes.find((n) => n.id === 'review')!.after).toEqual([]);
    expect(g.nodes.find((n) => n.id === 'publish')!.after).toEqual(['review']);
    expect(g.ui!.positions['size_check']).toBeUndefined();
  });

  it('renaming rewrites port references and keeps the layout', () => {
    const g = renameNode(PRD_GRAPH, 'per_epic', 'each_epic');
    expect(g.nodes.find((n) => n.id === 'stories')!.after).toEqual(['each_epic.loop']);
    expect(g.nodes.find((n) => n.id === 'size_check')!.after).toEqual(['each_epic.done']);
    expect(g.ui!.positions['each_epic']).toEqual(PRD_GRAPH.ui!.positions['per_epic']);
    expect(renameNode(PRD_GRAPH, 'per_epic', 'epics')).toBe(PRD_GRAPH); // name taken
    expect(renameNode(PRD_GRAPH, 'per_epic', 'Bad Name')).toBe(PRD_GRAPH);
  });

  it('round-trips through JSON without semantic change', () => {
    const parsed = JSON.parse(JSON.stringify(PRD_GRAPH)) as UseCaseGraph;
    expect(semantic(parsed)).toEqual(semantic(PRD_GRAPH));
    expect(JSON.stringify(semantic(PRD_GRAPH))).not.toContain('positions');
  });
});

describe('graph validation', () => {
  it('accepts the PRD sample', () => {
    expect(validate(PRD_GRAPH)).toEqual([]);
  });

  it('flags a node added but not connected', () => {
    const g = addNode(PRD_GRAPH, 'agent', { x: 0, y: 0 });
    expect(codes(g)).toEqual(
      expect.arrayContaining(['agent_9:no_parent', 'agent_9:unreachable', 'agent_9:no_agent']),
    );
  });

  it('flags explicit cycles (iteration must use loop nodes)', () => {
    const g = connect(PRD_GRAPH, 'review', 'epics::in');
    expect(codes(g)).toEqual(expect.arrayContaining(['epics:cycle', 'review:cycle']));
  });

  it('flags loops without a body or without max_iterations', () => {
    let g = disconnect(
      PRD_GRAPH,
      edges(PRD_GRAPH).find((e) => e.source === 'per_epic.loop')!,
    );
    expect(codes(g)).toContain('per_epic:empty_loop');
    g = {
      ...PRD_GRAPH,
      nodes: PRD_GRAPH.nodes.map((n) =>
        n.id === 'per_epic' ? { ...n, max_iterations: undefined } : n,
      ),
    };
    expect(codes(g)).toContain('per_epic:unbounded_loop');
  });

  it('flags references to ports that do not exist', () => {
    const g = connect(PRD_GRAPH, 'size_check.medium', 'end::in');
    expect(codes(g)).toContain('end:unknown_ref');
  });

  it('warns about router sections that lead nowhere', () => {
    const g = removeNode(PRD_GRAPH, 'review');
    expect(codes(g)).toContain('size_check:dead_section');
  });

  it('validates a 200-node graph quickly', () => {
    const g = syntheticGraph(200);
    const t0 = performance.now();
    expect(validate(g).filter((i) => i.severity === 'error')).toEqual([]);
    expect(performance.now() - t0).toBeLessThan(50);
  });
});
