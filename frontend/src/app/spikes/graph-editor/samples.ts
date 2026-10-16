import { GraphNode, UseCaseGraph, autoLayout } from './graph.model';

/** The spec's PRD example (Part 1, Graph definition) plus a router with two sections. */
export const PRD_GRAPH: UseCaseGraph = autoLayout({
  schema_version: 1,
  pattern: 'graph',
  inputs: [{ name: 'prd_document', type: 'file', required: true }],
  nodes: [
    { id: 'start', type: 'start', display_text: 'Upload a PRD', output: 'prd_document' },
    {
      id: 'epics',
      type: 'agent',
      agent_ref: 'epic-writer@3',
      task: 'Generate epics',
      input: '{prd_document}',
      output: 'epics_list',
      after: ['start'],
    },
    {
      id: 'per_epic',
      type: 'for_each',
      items: '{epics_list}',
      max_iterations: 50,
      after: ['epics'],
    },
    {
      id: 'stories',
      type: 'agent',
      agent_ref: 'story-writer@1',
      input: '{item}',
      output: 'stories',
      after: ['per_epic.loop'],
    },
    {
      id: 'size_check',
      type: 'router',
      after: ['per_epic.done'],
      sections: [
        { name: 'large', conditions: [{ field: 'stories.count', op: '>', value: '50' }] },
        { name: 'small', conditions: [{ field: 'stories.count', op: '<', value: '51' }] },
      ],
    },
    {
      id: 'review',
      type: 'agent',
      agent_ref: 'qa-reviewer@2',
      hitl: true,
      input: '{stories}',
      after: ['size_check.large'],
    },
    {
      id: 'publish',
      type: 'tool',
      tool_ref: 'jira-create-issues@1',
      after: ['size_check.small', 'review'],
      after_mode: 'any',
    },
    { id: 'end', type: 'end', after: ['publish'] },
  ],
  limits: { max_steps: 200, max_cost_usd: 5, timeout_s: 1800 },
});

/** Synthetic graph of `n` nodes for the rendering benchmark: a start, then a wide DAG. */
export function syntheticGraph(n: number): UseCaseGraph {
  const nodes: GraphNode[] = [{ id: 'start', type: 'start' }];
  for (let i = 1; i < n; i++) {
    const parent = i < 6 ? 'start' : `n${Math.max(1, Math.floor(i / 5))}`;
    nodes.push({
      id: `n${i}`,
      type: i % 7 === 0 ? 'tool' : 'agent',
      agent_ref: 'agent@1',
      after: [parent],
    });
  }
  return autoLayout({ schema_version: 1, pattern: 'graph', nodes });
}
