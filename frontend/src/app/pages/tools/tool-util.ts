/** Small helpers shared by the Tools Studio pages. */

export interface SchemaField {
  name: string;
  type: string;
  description: string;
  required: boolean;
  enum: string[] | null;
}

/** The fields of a JSON object schema, for the test console form. */
export function schemaFields(schema: Record<string, unknown> | undefined): SchemaField[] {
  const props = (schema?.['properties'] ?? {}) as Record<string, Record<string, unknown>>;
  const required = new Set((schema?.['required'] as string[] | undefined) ?? []);
  return Object.entries(props).map(([name, spec]) => ({
    name,
    type: typeof spec['type'] === 'string' ? (spec['type'] as string) : 'string',
    description: typeof spec['description'] === 'string' ? (spec['description'] as string) : '',
    required: required.has(name),
    enum: Array.isArray(spec['enum']) ? (spec['enum'] as unknown[]).map(String) : null,
  }));
}

/** Form values (strings and booleans) to the typed arguments the schema asks for; empty values
 * are left out. Arrays and objects are typed as JSON. */
export function buildArguments(
  fields: SchemaField[],
  values: Record<string, string | boolean | undefined>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (raw === undefined || raw === '') continue;
    if (f.type === 'boolean') out[f.name] = raw === true || raw === 'true';
    else if (f.type === 'integer') out[f.name] = Number.parseInt(String(raw), 10);
    else if (f.type === 'number') out[f.name] = Number(raw);
    else if (f.type === 'array' || f.type === 'object') out[f.name] = JSON.parse(String(raw));
    else out[f.name] = String(raw);
  }
  return out;
}

export function toolName(raw: string): string {
  const name = raw
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return (/^[a-z]/.test(name) ? name : `t_${name}`).slice(0, 48);
}
