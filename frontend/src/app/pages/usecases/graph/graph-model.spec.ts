import {
  UseCaseGraph,
  addNode,
  connect,
  disconnect,
  edges,
  fromDefinition,
  outputPorts,
  removeNode,
  renameNode,
  semantic,
} from './graph-model';

const base: UseCaseGraph = {
  nodes: [
    { id: 'start', type: 'start' },
    { id: 'each', type: 'for_each', after: ['start'], max_iterations: 5 },
    { id: 'work', type: 'agent', after: ['each.loop'], agent_ref: 'a@1' },
    {
      id: 'route',
      type: 'router',
      after: ['each.done'],
      sections: [{ name: 'big', conditions: [] }],
    },
    { id: 'end', type: 'end', after: ['route.big', 'route.default'] },
  ],
};

describe('use case graph model', () => {
  it('names the output ports a node offers', () => {
    expect(outputPorts(base.nodes[1]).map((p) => p.id)).toEqual(['each.loop', 'each.done']);
    expect(outputPorts(base.nodes[3]).map((p) => p.id)).toEqual(['route.big', 'route.default']);
    expect(outputPorts(base.nodes[4])).toEqual([]);
  });

  it('turns after references into edges, connects and disconnects', () => {
    expect(edges(base).map((e) => e.id)).toContain('each.loop->work');
    const joined = connect(base, 'work', 'end::in');
    expect(joined.nodes[4].after).toContain('work');
    const edge = edges(joined).find((e) => e.id === 'work->end');
    expect(disconnect(joined, edge!).nodes[4].after).not.toContain('work');
  });

  it('removes a node together with the references to its ports', () => {
    const cut = removeNode(base, 'route');
    expect(cut.nodes.find((n) => n.id === 'end')?.after).toEqual([]);
  });

  it('renames a node and rewrites its port references', () => {
    const renamed = renameNode(base, 'each', 'loop_all');
    expect(renamed.nodes[2].after).toEqual(['loop_all.loop']);
    expect(renameNode(base, 'each', 'Bad Name')).toBe(base);
    expect(renameNode(base, 'each', 'work')).toBe(base);
  });

  it('adds nodes with sensible defaults and keeps positions out of the semantic JSON', () => {
    const added = addNode(base, 'router', { x: 1, y: 2 });
    const node = added.nodes.at(-1)!;
    expect(node.sections?.length).toBe(1);
    expect(added.ui?.positions[node.id]).toEqual({ x: 1, y: 2 });
    expect('ui' in semantic(added)).toBe(false);
    expect(addNode(base, 'while', { x: 0, y: 0 }).nodes.at(-1)?.max_iterations).toBe(50);
  });

  it('lays out a stored definition that has no positions', () => {
    const graph = fromDefinition({ nodes: base.nodes });
    expect(Object.keys(graph.ui!.positions).sort()).toEqual(base.nodes.map((n) => n.id).sort());
    expect(fromDefinition({}).nodes).toEqual([]);
  });
});
