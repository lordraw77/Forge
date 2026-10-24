import { HttpClient } from '@angular/common/http';
import { Component, computed, effect, ElementRef, inject, signal, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { NzAlertModule } from 'ng-zorro-antd/alert';
import { NzButtonModule } from 'ng-zorro-antd/button';
import { firstValueFrom } from 'rxjs';

import { currentLocale } from '../../core/locale';
import { Chapter, chaptersFromReadme, GuideLang, renderGuide } from './guide-render';

/** The user guide inside the app: the chapters in `docs/guide`, shipped with the web image under
 * `/guide`, shown in the interface language (switchable here). */
@Component({
  selector: 'forge-guide',
  host: { '(click)': 'onClick($event)' },
  imports: [NzAlertModule, NzButtonModule, RouterLink],
  template: `
    <header class="top">
      <h1 i18n>User guide</h1>
      <span class="spacer"></span>
      <button
        nz-button
        nzSize="small"
        (click)="switchLang()"
        data-testid="guide-lang"
        [attr.lang]="lang() === 'en' ? 'it' : 'en'"
      >
        {{ lang() === 'en' ? 'Italiano' : 'English' }}
      </button>
    </header>
    @if (error()) {
      <nz-alert nzType="warning" [nzMessage]="error()" data-testid="guide-error" />
    }
    <div class="layout">
      <nav class="toc" aria-label="Chapters" i18n-aria-label>
        <a routerLink="/guide" [class.active]="!chapter()" data-testid="guide-index" i18n
          >Contents</a
        >
        @for (c of chapters(); track c.slug) {
          <a
            [routerLink]="['/guide', c.slug]"
            [class.active]="chapter() === c.slug"
            [attr.data-testid]="'guide-' + c.slug"
            >{{ c.title }}</a
          >
        }
      </nav>
      <article
        #article
        class="doc"
        [innerHTML]="html()"
        data-testid="guide-doc"
      ></article>
    </div>
  `,
  styles: `
    :host {
      display: block;
    }
    .top {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .spacer {
      flex: 1;
    }
    .layout {
      display: grid;
      grid-template-columns: 15rem minmax(0, 1fr);
      gap: 1.5rem;
      align-items: start;
      margin-top: 0.75rem;
    }
    .toc {
      position: sticky;
      top: 0.5rem;
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
      max-height: calc(100vh - 2rem);
      overflow-y: auto;
      a {
        padding: 0.3rem 0.6rem;
        border-radius: 4px;
        color: inherit;
        text-decoration: none;
        font-size: 0.875rem;
      }
      a:hover {
        background: var(--forge-hover-bg, rgba(127, 127, 127, 0.12));
      }
      a.active {
        font-weight: 600;
        background: var(--forge-hover-bg, rgba(127, 127, 127, 0.12));
      }
    }
    .doc {
      max-width: 60rem;
      line-height: 1.6;
    }
    .doc ::ng-deep {
      img {
        max-width: 100%;
        height: auto;
        border: 1px solid rgba(127, 127, 127, 0.3);
        border-radius: 4px;
      }
      table {
        border-collapse: collapse;
        display: block;
        overflow-x: auto;
        margin: 0.75rem 0;
      }
      th,
      td {
        border: 1px solid rgba(127, 127, 127, 0.3);
        padding: 0.35rem 0.6rem;
        text-align: left;
        vertical-align: top;
      }
      pre {
        overflow-x: auto;
        padding: 0.75rem;
        border-radius: 4px;
        background: rgba(127, 127, 127, 0.12);
      }
      code {
        font-family: monospace;
        font-size: 0.85em;
      }
      blockquote {
        margin: 0.75rem 0;
        padding: 0.25rem 1rem;
        border-left: 4px solid rgba(127, 127, 127, 0.4);
      }
      h1,
      h2,
      h3 {
        margin-top: 1.5rem;
      }
    }
    @media (max-width: 900px) {
      .layout {
        grid-template-columns: 1fr;
      }
      .toc {
        position: static;
        max-height: none;
      }
    }
  `,
})
export class GuidePage {
  private readonly http = inject(HttpClient);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly article = viewChild<ElementRef<HTMLElement>>('article');

  protected readonly lang = signal<GuideLang>(currentLocale());
  protected readonly chapters = signal<Chapter[]>([]);
  protected readonly html = signal<SafeHtml>('');
  protected readonly error = signal<string | null>(null);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  protected readonly chapter = computed(() => this.params().get('chapter'));
  private readonly unavailable = $localize`The user guide is not part of this installation.`;

  constructor() {
    effect(() => {
      void this.show(this.lang(), this.chapter());
    });
  }

  private async text(lang: GuideLang, file: string): Promise<string> {
    return firstValueFrom(this.http.get(`/guide/${lang}/${file}`, { responseType: 'text' }));
  }

  private async show(lang: GuideLang, chapter: string | null): Promise<void> {
    try {
      const readme = await this.text(lang, 'README.md');
      if (/^\s*<!doctype html/i.test(readme)) throw new Error('not shipped');
      const chapters = chaptersFromReadme(readme);
      this.chapters.set(chapters);
      // A chapter keeps its number across languages: 10-… in English is 10-… in Italian.
      const wanted = chapter
        ? (chapters.find((c) => c.slug === chapter) ??
          chapters.find((c) => c.slug.slice(0, 2) === chapter.slice(0, 2)))
        : null;
      const markdown = wanted ? await this.text(lang, `${wanted.slug}.md`) : readme;
      this.html.set(this.sanitizer.bypassSecurityTrustHtml(renderGuide(markdown)));
      this.error.set(null);
      if (chapter && wanted && wanted.slug !== chapter) {
        void this.router.navigate(['/guide', wanted.slug], { replaceUrl: true });
      }
      setTimeout(() => this.scrollToFragment());
    } catch {
      this.chapters.set([]);
      this.html.set('');
      this.error.set(this.unavailable);
    }
  }

  private scrollToFragment(): void {
    const id = this.route.snapshot.fragment;
    const target = id
      ? this.article()?.nativeElement.querySelector(`[id="${CSS.escape(id)}"]`)
      : null;
    if (target) target.scrollIntoView();
    else window.scrollTo?.(0, 0);
  }

  protected switchLang(): void {
    this.lang.update((l) => (l === 'en' ? 'it' : 'en'));
  }

  /** Links inside the text stay in the app. */
  protected onClick(event: MouseEvent): void {
    const a = (event.target as HTMLElement).closest('a');
    const href = a?.getAttribute('href');
    if (!a || !href || a.target === '_blank' || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    if (href.startsWith('#')) {
      document.getElementById(href.slice(1))?.scrollIntoView();
    } else {
      void this.router.navigateByUrl(href);
    }
  }
}
