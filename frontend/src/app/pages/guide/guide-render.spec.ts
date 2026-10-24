import { describe, expect, it } from 'vitest';

import { chaptersFromReadme, guideHref, headingId, renderGuide } from './guide-render';

describe('guide rendering', () => {
  it('slugs headings like GitHub', () => {
    expect(headingId("10.2 The project's MCP server")).toBe('102-the-projects-mcp-server');
  });

  it('reads the chapters from the README table', () => {
    const readme =
      '| # | Chapter |\n| --- | --- |\n| 1 | [Getting started](01-getting-started.md) | x |\n| 10 | [Sync](10-sync.md) | y |';
    expect(chaptersFromReadme(readme)).toEqual([
      { slug: '01-getting-started', title: 'Getting started' },
      { slug: '10-sync', title: 'Sync' },
    ]);
  });

  it('keeps chapter links inside the app and drops repository-only links', () => {
    expect(guideHref('05-tools.md#call-log')).toBe('/guide/05-tools#call-log');
    expect(guideHref('README.md')).toBe('/guide/');
    expect(guideHref('https://example.com')).toBe('https://example.com');
    expect(guideHref('../it/README.md')).toBeNull();
  });

  it('serves figures from the app and opens outside links in a new tab', () => {
    const html = renderGuide(
      '## Tools\n\n![Library](../images/en/05-library.png)\n\n[docs](https://example.com) and [next](06-agents.md)',
    );
    expect(html).toContain('<h2 id="tools">');
    expect(html).toContain('src="/guide/images/en/05-library.png"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('href="/guide/06-agents"');
  });
});
