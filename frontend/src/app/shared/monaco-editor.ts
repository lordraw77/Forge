import {
  Component,
  ElementRef,
  OnDestroy,
  effect,
  input,
  model,
  untracked,
  viewChild,
} from '@angular/core';

import { themeMode } from '../core/theme-mode';

type Monaco = typeof import('monaco-editor/esm/vs/editor/editor.api');
type Editor = import('monaco-editor/esm/vs/editor/editor.api').editor.IStandaloneCodeEditor;

let loading: Promise<Monaco> | null = null;

/** Monaco is loaded once, on first use, with static workers served from our own bundle (no CDN,
 * works air-gapped). Only the JSON language is added to the core editor. */
function loadMonaco(): Promise<Monaco> {
  loading ??= (async () => {
    const [monaco] = await Promise.all([
      import('monaco-editor/esm/vs/editor/editor.api'),
      import('monaco-editor/esm/vs/editor/editor.all.js'),
      import('monaco-editor/esm/vs/language/json/monaco.contribution.js'),
    ]);
    (self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
      getWorker(_id: string, label: string): Worker {
        if (label === 'json') {
          return new Worker(
            new URL(
              '../../../node_modules/monaco-editor/esm/vs/language/json/json.worker.js',
              import.meta.url,
            ),
            { type: 'module' },
          );
        }
        return new Worker(
          new URL(
            '../../../node_modules/monaco-editor/esm/vs/editor/editor.worker.js',
            import.meta.url,
          ),
          { type: 'module' },
        );
      },
    };
    return monaco;
  })();
  return loading;
}

function dark(): boolean {
  const mode = themeMode();
  return (
    mode === 'dark' ||
    (mode === 'system' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches)
  );
}

/** Code editor for JSON definitions and templates (PO decision 2026-10-19: Monaco, static
 * workers). Two-way `value`; follows the light/dark theme. */
@Component({
  selector: 'forge-monaco',
  template: `<div #host class="host" [style.height]="height()" [attr.aria-label]="label()"></div>`,
  styles: `
    .host {
      border: 1px solid var(--forge-surface-200);
      border-radius: 6px;
      overflow: hidden;
    }
  `,
})
export class MonacoEditor implements OnDestroy {
  readonly value = model('');
  readonly language = input('json');
  readonly readOnly = input(false);
  readonly height = input('18rem');
  readonly label = input('Code editor');
  private readonly host = viewChild.required<ElementRef<HTMLElement>>('host');
  private editor: Editor | null = null;
  private monaco: Monaco | null = null;
  private applying = false;
  private destroyed = false;

  constructor() {
    effect(() => {
      const el = this.host().nativeElement;
      void loadMonaco().then((monaco) => {
        if (this.destroyed || this.editor) return;
        this.monaco = monaco;
        const editor = monaco.editor.create(el, {
          value: untracked(() => this.value()),
          language: untracked(() => this.language()),
          readOnly: untracked(() => this.readOnly()),
          theme: dark() ? 'vs-dark' : 'vs',
          automaticLayout: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 13,
          tabSize: 2,
          wordWrap: untracked(() => (this.language() === 'json' ? 'off' : 'on')),
          autoClosingBrackets: untracked(() =>
            this.language() === 'json' ? 'languageDefined' : 'never',
          ),
          autoClosingQuotes: untracked(() =>
            this.language() === 'json' ? 'languageDefined' : 'never',
          ),
          ariaLabel: untracked(() => this.label()),
        });
        editor.onDidChangeModelContent(() => {
          if (!this.applying) this.value.set(editor.getValue());
        });
        this.editor = editor;
      });
    });
    effect(() => {
      const v = this.value();
      const editor = this.editor;
      if (editor && editor.getValue() !== v) {
        this.applying = true;
        editor.setValue(v);
        this.applying = false;
      }
    });
    effect(() => {
      const isDark = dark();
      this.monaco?.editor.setTheme(isDark ? 'vs-dark' : 'vs');
    });
    effect(() => {
      this.editor?.updateOptions({ readOnly: this.readOnly() });
    });
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.editor?.dispose();
  }
}
