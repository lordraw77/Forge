import { describe, expect, it } from 'vitest';

import { CatalogUseCase } from '../../api-client';
import { durationSeconds, fieldValue, filterCatalog, pretty } from './catalog-util';

function item(over: Partial<CatalogUseCase>): CatalogUseCase {
  return {
    id: 'x',
    name: 'Name',
    description: '',
    icon: '',
    tags: [],
    catalog_id: 'c',
    catalog: 'Cat',
    category_id: 'k1',
    category: 'Kat',
    version: 1,
    input_schema: {},
    input_fields: [],
    output_type: 'markdown',
    favorite: false,
    last_run_at: null,
    ...over,
  } as CatalogUseCase;
}

describe('filterCatalog', () => {
  const items = [
    item({ id: '1', name: 'Invoice checker', tags: ['finance'], favorite: true }),
    item({ id: '2', name: 'Release notes', description: 'Writes the notes', category_id: 'k2' }),
    item({ id: '3', name: 'Triage', last_run_at: '2026-10-20T10:00:00Z' }),
    item({ id: '4', name: 'Digest', last_run_at: '2026-10-21T10:00:00Z' }),
  ];
  const ids = (list: CatalogUseCase[]): string[] => list.map((i) => i.id);

  it('searches name and description, ignoring case', () => {
    expect(ids(filterCatalog(items, 'INVOICE', null, null, 'all'))).toEqual(['1']);
    expect(ids(filterCatalog(items, 'writes', null, null, 'all'))).toEqual(['2']);
  });

  it('filters by category and tag', () => {
    expect(ids(filterCatalog(items, '', 'k2', null, 'all'))).toEqual(['2']);
    expect(ids(filterCatalog(items, '', null, 'finance', 'all'))).toEqual(['1']);
  });

  it('shows favorites, and recent runs latest first', () => {
    expect(ids(filterCatalog(items, '', null, null, 'favorites'))).toEqual(['1']);
    expect(ids(filterCatalog(items, '', null, null, 'recent'))).toEqual(['4', '3']);
  });
});

describe('helpers', () => {
  it('measures a finished run in seconds', () => {
    expect(
      durationSeconds({ started_at: '2026-10-20T10:00:00Z', finished_at: '2026-10-20T10:00:07Z' }),
    ).toBe(7);
    expect(durationSeconds({ started_at: '2026-10-20T10:00:00Z', finished_at: null })).toBeNull();
  });

  it('turns form values into API values', () => {
    expect(fieldValue('number', '3')).toBe(3);
    expect(fieldValue('text', '')).toBeUndefined();
    expect(fieldValue('text', 'a')).toBe('a');
  });

  it('prints strings as they are and objects as JSON', () => {
    expect(pretty('a')).toBe('a');
    expect(pretty({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(pretty(null)).toBe('');
  });
});
