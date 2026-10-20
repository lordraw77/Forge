import { emptyDefinition, fieldName, parseJsonObject, splitList } from './usecase-util';

describe('use case util', () => {
  it('slugs field names', () => {
    expect(fieldName('My Topic!')).toBe('my_topic');
    expect(fieldName('9 lives')).toBe('lives');
    expect(fieldName('???')).toBe('field');
    expect(fieldName('a'.repeat(80)).length).toBe(48);
  });

  it('splits lists without repeats', () => {
    expect(splitList('a, b\n b,,c ')).toEqual(['a', 'b', 'c']);
    expect(splitList('')).toEqual([]);
  });

  it('parses only JSON objects', () => {
    expect(parseJsonObject('').value).toEqual({});
    expect(parseJsonObject('{"a":1}').value).toEqual({ a: 1 });
    expect(parseJsonObject('[1]').error).not.toBeNull();
    expect(parseJsonObject('{').value).toBeNull();
  });

  it('starts each pattern with the right shape', () => {
    expect(emptyDefinition('supervisor')).toEqual({ agents: [] });
    expect((emptyDefinition('graph')['nodes'] as unknown[]).length).toBe(1);
  });
});
