/**
 * Client-side mirror of the backend's variable detection, for live feedback while typing.
 * `{name}` (spec shorthand) and simple `{{ name }}` / `{{ name|filter }}` expressions; the
 * server stays authoritative (POST /prompts/parse, and on save).
 */
const SHORTHAND = /(?<!\{)\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}(?!\})/g;
const EXPRESSION = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)/g;
const STATEMENT =
  /\{%-?\s*(?:if|elif|for\s+[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)?\s+in)\s+(?:not\s+)?([A-Za-z_][A-Za-z0-9_]*)/g;
const LOOP_VAR =
  /\{%-?\s*for\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*,\s*([A-Za-z_][A-Za-z0-9_]*))?\s+in\b/g;
const SET_VAR = /\{%-?\s*set\s+([A-Za-z_][A-Za-z0-9_]*)/g;
const RESERVED = new Set([
  'loop',
  'range',
  'dict',
  'true',
  'false',
  'none',
  'True',
  'False',
  'None',
]);

export function detectVariables(...sources: string[]): string[] {
  const found: { name: string; at: number; source: number }[] = [];
  const local = new Set<string>();
  sources.forEach((text, source) => {
    for (const m of text.matchAll(LOOP_VAR)) {
      local.add(m[1]);
      if (m[2]) local.add(m[2]);
    }
    for (const m of text.matchAll(SET_VAR)) local.add(m[1]);
    for (const re of [SHORTHAND, EXPRESSION, STATEMENT]) {
      for (const m of text.matchAll(re)) found.push({ name: m[1], at: m.index ?? 0, source });
    }
  });
  found.sort((a, b) => a.source - b.source || a.at - b.at);
  const names: string[] = [];
  for (const { name } of found) {
    if (!RESERVED.has(name) && !local.has(name) && !names.includes(name)) names.push(name);
  }
  return names;
}
