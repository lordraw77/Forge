import { AgentCitation, AgentRunOut } from '../../api-client';

export type RunStatus = AgentRunOut['status'];

/** NG-ZORRO tag colour of a run status. */
export function statusColor(status: RunStatus): string {
  switch (status) {
    case 'succeeded':
      return 'success';
    case 'failed':
      return 'error';
    case 'cancelled':
      return 'warning';
    case 'running':
      return 'processing';
    default:
      return 'default';
  }
}

export function statusLabel(status: RunStatus): string {
  switch (status) {
    case 'succeeded':
      return $localize`Succeeded`;
    case 'failed':
      return $localize`Failed`;
    case 'cancelled':
      return $localize`Stopped`;
    case 'running':
      return $localize`Running`;
    default:
      return $localize`Queued`;
  }
}

/** Human text for the failure reasons the runtime reports (`limit_exceeded:<which>` and others). */
export function failureLabel(code: string | null, detail: string | null): string {
  switch (code) {
    case 'limit_exceeded:max_steps':
      return $localize`The agent reached its limit of model calls.`;
    case 'limit_exceeded:max_tokens':
      return $localize`The agent reached its token limit.`;
    case 'limit_exceeded:max_cost':
      return $localize`The agent reached its cost limit.`;
    case 'limit_exceeded:timeout':
      return $localize`The agent ran out of time.`;
    case 'cancelled':
      return $localize`The run was stopped.`;
    default:
      return detail || code || '';
  }
}

export interface Segment {
  text: string;
  citation?: AgentCitation;
}

/** Splits an answer into text and `[n]` markers that match a citation, so they can be links. */
export function segments(text: string, citations: AgentCitation[]): Segment[] {
  if (!citations.length) return [{ text }];
  const byIndex = new Map(citations.map((c) => [c.index, c]));
  const out: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(/[[【](\d{1,4})[\]】]/g)) {
    const citation = byIndex.get(Number(match[1]));
    if (!citation) continue;
    if (match.index > last) out.push({ text: text.slice(last, match.index) });
    out.push({ text: match[0], citation });
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

export function pretty(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}
