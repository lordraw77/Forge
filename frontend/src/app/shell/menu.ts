/** Side menu entries, in the order given by the spec (Part 2, Interface). */
export interface MenuEntry {
  path: string;
  label: string;
  /** Ant Design icon name (registered in core/theme.ts). */
  icon: string;
  /** Phase in which the area becomes functional (plan §3). */
  phase: 1 | 2 | 3;
}

export const MENU: readonly MenuEntry[] = [
  { path: 'projects', label: $localize`Projects Studio`, icon: 'project', phase: 1 },
  { path: 'catalog', label: $localize`Use Cases Catalog`, icon: 'appstore', phase: 2 },
  { path: 'approvals', label: $localize`Approvals`, icon: 'safety-certificate', phase: 2 },
  { path: 'build', label: $localize`Build Your Own Use Case`, icon: 'apartment', phase: 2 },
  { path: 'prompts', label: $localize`Prompt Studio`, icon: 'edit', phase: 1 },
  { path: 'tools', label: $localize`Tools Studio`, icon: 'tool', phase: 1 },
  { path: 'rag', label: $localize`RAG Studio`, icon: 'book', phase: 1 },
  { path: 'agents', label: $localize`Agentic AI Studio`, icon: 'robot', phase: 1 },
  { path: 'mcp', label: $localize`MCP Studio`, icon: 'cloud-server', phase: 3 },
  {
    path: 'governance',
    label: $localize`Governance and Evaluation Studio`,
    icon: 'safety-certificate',
    phase: 2,
  },
  { path: 'analytics', label: $localize`Analytics`, icon: 'bar-chart', phase: 2 },
];
