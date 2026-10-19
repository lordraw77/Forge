import { describe, expect, it } from 'vitest';

import { AgentCitation } from '../../api-client';
import { failureLabel, pretty, segments, statusColor } from './agent-util';

function citation(index: number): AgentCitation {
  return {
    index,
    chunk_id: `chunk-${index}`,
    document_id: 'doc',
    document_name: 'manual.md',
    page: null,
    heading_path: '',
    score: 0.5,
    excerpt: '',
    url: '/doc',
  };
}

describe('segments', () => {
  it('turns [n] markers that match a citation into link segments', () => {
    const parts = segments('Restore with forge [1] and renew [2] daily. See [9].', [
      citation(1),
      citation(2),
    ]);
    expect(parts.map((p) => p.text).join('')).toBe(
      'Restore with forge [1] and renew [2] daily. See [9].',
    );
    expect(parts.filter((p) => p.citation).map((p) => p.citation?.index)).toEqual([1, 2]);
    // [9] has no citation: it stays plain text.
    expect(parts[parts.length - 1].citation).toBeUndefined();
  });

  it('returns the text as is without citations', () => {
    expect(segments('Plain [1] text', [])).toEqual([{ text: 'Plain [1] text' }]);
  });
});

describe('run labels', () => {
  it('explains limits in words and falls back to the server detail', () => {
    expect(failureLabel('limit_exceeded:max_cost', null)).toContain('cost');
    expect(failureLabel('limit_exceeded:timeout', null)).toContain('time');
    expect(failureLabel('upstream_unavailable', 'The model is down')).toBe('The model is down');
    expect(failureLabel('worker_failed', null)).toBe('worker_failed');
  });

  it('colours statuses', () => {
    expect(statusColor('succeeded')).toBe('success');
    expect(statusColor('failed')).toBe('error');
    expect(statusColor('running')).toBe('processing');
    expect(statusColor('queued')).toBe('default');
  });

  it('prints values for the run view', () => {
    expect(pretty(null)).toBe('');
    expect(pretty('text')).toBe('text');
    expect(pretty({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
});
