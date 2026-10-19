import { buildArguments, schemaFields, toolName } from './tool-util';

describe('tool util', () => {
  const schema = {
    type: 'object',
    properties: {
      id: { type: 'integer', description: 'Pet id' },
      mode: { type: 'string', enum: ['a', 'b'] },
      flag: { type: 'boolean' },
      tags: { type: 'array' },
    },
    required: ['id'],
  };

  it('reads fields from a schema', () => {
    const fields = schemaFields(schema);
    expect(fields.map((f) => f.name)).toEqual(['id', 'mode', 'flag', 'tags']);
    expect(fields[0]).toMatchObject({ required: true, type: 'integer', description: 'Pet id' });
    expect(fields[1].enum).toEqual(['a', 'b']);
    expect(schemaFields(undefined)).toEqual([]);
  });

  it('builds typed arguments and skips empty values', () => {
    const args = buildArguments(schemaFields(schema), {
      id: '7',
      mode: '',
      flag: true,
      tags: '["x","y"]',
    });
    expect(args).toEqual({ id: 7, flag: true, tags: ['x', 'y'] });
  });

  it('makes a valid tool name', () => {
    expect(toolName('Get Pet!')).toBe('get_pet');
    expect(toolName('1st call')).toBe('t_1st_call');
    expect(toolName('x'.repeat(80)).length).toBe(48);
  });
});
