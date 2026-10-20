import { UseCaseInputField } from '../../api-client';

/** A blank input field for the form builder. */
export function newField(index: number): UseCaseInputField {
  return {
    name: `field_${index}`,
    label: '',
    type: 'text',
    required: false,
    help: '',
    options: [],
  };
}

/** Field names look like [a-z][a-z0-9_]*, up to 48 characters. */
export function fieldName(raw: string): string {
  const slug = raw
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^[^a-z]+/, '')
    .replace(/_+$/, '')
    .slice(0, 48);
  return slug || 'field';
}

/** Comma- or newline-separated text to a list without empty or repeated entries. */
export function splitList(raw: string): string[] {
  return [
    ...new Set(
      raw
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ];
}

export function parseJsonObject(raw: string): {
  value: Record<string, unknown> | null;
  error: string | null;
} {
  if (!raw.trim()) return { value: {}, error: null };
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return { value: value as Record<string, unknown>, error: null };
    }
    return { value: null, error: $localize`Must be a JSON object` };
  } catch (e) {
    return { value: null, error: e instanceof Error ? e.message : String(e) };
  }
}

/** The definition a new use case starts from. */
export function emptyDefinition(pattern: string): Record<string, unknown> {
  return pattern === 'supervisor'
    ? { agents: [] }
    : { nodes: [{ id: 'start', type: 'start', output: 'input' }] };
}
