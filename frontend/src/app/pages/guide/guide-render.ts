import { Marked, Tokens } from 'marked';

export type GuideLang = 'en' | 'it';

export interface Chapter {
  slug: string;
  title: string;
}

/** The slug GitHub gives a heading, so the guide's `#anchor` links work in the app too. */
export function headingId(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N} _-]/gu, '')
    .trim()
    .replace(/ /g, '-');
}

/** Chapters listed in a language's README table: `| 1 | [Title](01-name.md) | … |`. */
export function chaptersFromReadme(markdown: string): Chapter[] {
  const rows = markdown.matchAll(/^\|\s*\d+\s*\|\s*\[([^\]]+)\]\(([^)#]+)\.md\)/gm);
  return [...rows].map((m) => ({ title: m[1], slug: m[2] }));
}

/** Where a link of the guide's Markdown goes in the app: another chapter, an image served with
 * the app, an outside address, or nowhere (the repository's own files, not shipped). */
export function guideHref(href: string): string | null {
  if (/^(https?:|mailto:)/.test(href)) return href;
  if (href.startsWith('#')) return href;
  const chapter = /^([^/#]+)\.md(#.*)?$/.exec(href);
  if (chapter) return `/guide/${chapter[1] === 'README' ? '' : chapter[1]}${chapter[2] ?? ''}`;
  return null;
}

/** Markdown of the guide as HTML. The text ships with the application, so it is trusted. */
export function renderGuide(markdown: string): string {
  const marked = new Marked({
    gfm: true,
    renderer: {
      heading(this, { tokens, depth }: Tokens.Heading) {
        const html = this.parser.parseInline(tokens);
        const id = headingId(tokens.map((t) => ('text' in t ? String(t.text) : '')).join(''));
        return `<h${depth} id="${id}">${html}</h${depth}>\n`;
      },
      link(this, { href, title, tokens }: Tokens.Link) {
        const text = this.parser.parseInline(tokens);
        const target = guideHref(href);
        if (target === null) return text;
        const t = title ? ` title="${title}"` : '';
        return /^https?:/.test(target)
          ? `<a href="${target}"${t} target="_blank" rel="noopener noreferrer">${text}</a>`
          : `<a href="${target}"${t}>${text}</a>`;
      },
      image({ href, title, text }: Tokens.Image) {
        const src = href.replace(/^(\.\.\/)+images\//, '/guide/images/');
        const t = title ? ` title="${title}"` : '';
        return `<img src="${src}" alt="${text}"${t} loading="lazy" />`;
      },
    },
  });
  return marked.parse(markdown, { async: false });
}
