/** Side menu entries, in the order given by the spec (Part 2, Interface). */
export interface MenuEntry {
  path: string;
  label: string;
  icon: string;
  /** Phase in which the area becomes functional (plan §3). */
  phase: 1 | 2 | 3;
}

export const MENU: readonly MenuEntry[] = [
  { path: 'projects', label: $localize`Projects Studio`, icon: 'pi pi-briefcase', phase: 1 },
  { path: 'catalog', label: $localize`Use Cases Catalog`, icon: 'pi pi-th-large', phase: 2 },
  { path: 'build', label: $localize`Build Your Own Use Case`, icon: 'pi pi-sitemap', phase: 2 },
  { path: 'prompts', label: $localize`Prompt Studio`, icon: 'pi pi-pencil', phase: 1 },
  { path: 'tools', label: $localize`Tools Studio`, icon: 'pi pi-wrench', phase: 2 },
  { path: 'rag', label: $localize`RAG Studio`, icon: 'pi pi-book', phase: 1 },
  { path: 'agents', label: $localize`Agentic AI Studio`, icon: 'pi pi-microchip-ai', phase: 1 },
  { path: 'mcp', label: $localize`MCP Studio`, icon: 'pi pi-server', phase: 3 },
  {
    path: 'governance',
    label: $localize`Governance and Evaluation Studio`,
    icon: 'pi pi-shield',
    phase: 2,
  },
];
