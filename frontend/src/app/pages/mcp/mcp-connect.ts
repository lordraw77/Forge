import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { NzInputModule } from 'ng-zorro-antd/input';

import { Session } from '../../core/session';

/** How an external MCP client connects to the project's server. */
@Component({
  selector: 'forge-mcp-connect',
  imports: [NzInputModule, RouterLink],
  template: `
    <p class="intro" i18n>
      Every project has its own MCP server. Clients sign in with a project API key that has the
      “mcp.invoke” scope; create one in
      <a routerLink="/projects/info">Projects Studio → Information</a>. What the server offers is
      the <a routerLink="/mcp/exposed">Exposed</a> list.
    </p>
    <h2 i18n>Endpoint</h2>
    <p>
      <code data-testid="mcp-url">{{ url() }}</code>
    </p>
    <p class="hint" i18n>
      Streamable HTTP, JSON-RPC, no session. Behind a reverse proxy, open Forge at its public
      address so that this address is the one clients can reach.
    </p>
    <h2>Claude Code</h2>
    <textarea nz-input readonly rows="2" class="mono" data-testid="mcp-snippet-code">{{
      claudeCode()
    }}</textarea>
    <h2>Claude Desktop, Cursor and others (mcp-remote)</h2>
    <textarea nz-input readonly rows="12" class="mono" data-testid="mcp-snippet-json">{{
      jsonConfig()
    }}</textarea>
    <h2>curl</h2>
    <textarea nz-input readonly rows="6" class="mono">{{ curl() }}</textarea>
  `,
  styleUrl: '../admin/admin.scss',
  styles: `
    h2 {
      margin-top: 1.25rem;
    }
    .mono,
    code {
      font-family: monospace;
      font-size: 0.8rem;
    }
    .hint {
      color: var(--forge-text-muted-color);
      font-size: 0.8rem;
    }
  `,
})
export class McpConnect {
  private readonly session = inject(Session);

  protected readonly url = computed(
    () => `${window.location.origin}/api/v1/projects/${this.session.project()?.id ?? ''}/mcp`,
  );
  protected readonly claudeCode = computed(
    () =>
      `claude mcp add --transport http forge ${this.url()} --header "Authorization: Bearer fk_…"`,
  );
  protected readonly jsonConfig = computed(() =>
    JSON.stringify(
      {
        mcpServers: {
          forge: {
            command: 'npx',
            args: [
              '-y',
              'mcp-remote',
              this.url(),
              '--header',
              'Authorization: Bearer ${FORGE_API_KEY}',
            ],
            env: { FORGE_API_KEY: 'fk_…' },
          },
        },
      },
      null,
      2,
    ),
  );
  protected readonly curl = computed(
    () =>
      `curl -s ${this.url()} \\\n` +
      `  -H "Authorization: Bearer fk_…" \\\n` +
      `  -H "Content-Type: application/json" \\\n` +
      `  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`,
  );
}
