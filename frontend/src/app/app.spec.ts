import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter, withComponentInputBinding } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { MeOut, provideApi } from './api-client';
import { routes } from './app.routes';
import { MENU } from './shell/menu';

const READY = {
  password_change_required: false,
  eula_required: false,
  totp_enrollment_required: false,
};

function me(overrides: Partial<MeOut> = {}): MeOut {
  return {
    user: {
      id: 'u1',
      username: 'alice',
      email: 'alice@example.com',
      full_name: 'Alice Rossi',
      auth_source: 'local',
      is_super_admin: false,
      status: 'active',
      totp_enabled: false,
      last_active_at: null,
      created_at: '2026-10-16T00:00:00Z',
    },
    flags: READY,
    projects: [
      {
        id: 'p1',
        name: 'Alpha',
        description: '',
        status: 'active',
        created_at: '2026-10-16T00:00:00Z',
        role_name: 'Project Admin',
        role_type: 'project_admin',
        permissions: ['project.manage'],
      },
    ],
    ...overrides,
  };
}

describe('App shell and session (US-1.4.1, US-1.2.2)', () => {
  let harness: RouterTestingHarness;
  let http: HttpTestingController;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes, withComponentInputBinding()),
        provideHttpClient(),
        provideHttpClientTesting(),
        provideApi(''),
      ],
    });
    harness = await RouterTestingHarness.create();
    http = TestBed.inject(HttpTestingController);
  });

  /** Wait for a request issued asynchronously (guards run after navigation starts). */
  async function request(path: string) {
    for (let i = 0; i < 50; i++) {
      const found = http.match((r) => r.url.endsWith(path));
      if (found.length) return found[0];
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    throw new Error(`no request to ${path}`);
  }

  async function signedIn(url: string, body: MeOut = me()): Promise<void> {
    const nav = harness.navigateByUrl(url);
    (await request('/api/v1/me')).flush(body);
    await nav;
    harness.detectChanges();
  }

  it('shows the nine menu entries, the user and their projects', async () => {
    await signedIn('/');
    const doc = harness.routeNativeElement!.ownerDocument;
    const labels = Array.from(doc.querySelectorAll('nav a span')).map((el) =>
      el.textContent?.trim(),
    );
    expect(labels).toEqual(MENU.map((m) => m.label));
    expect(doc.querySelector('[data-testid="user-name"]')?.textContent).toContain('Alice Rossi');
    expect(doc.body.textContent).toContain('Welcome, Alice Rossi');
  });

  it('shows the API version once the backend answers', async () => {
    await signedIn('/');
    (await request('/api/v1/version')).flush({ name: 'forge', version: '0.1.0' });
    harness.detectChanges();
    expect(harness.routeNativeElement!.ownerDocument.body.textContent).toContain('API 0.1.0');
  });

  it('redirects to login without a session', async () => {
    const nav = harness.navigateByUrl('/');
    const unauthenticated = { status: 401, statusText: 'Unauthorized' };
    (await request('/api/v1/me')).flush({ code: 'unauthenticated' }, unauthenticated);
    // The login page's guard checks the session again.
    (await request('/api/v1/me')).flush({ code: 'unauthenticated' }, unauthenticated);
    await nav;
    expect(TestBed.inject(Router).url).toMatch(/^\/login/);
  });

  it('sends a user with a pending password change to the change-password step', async () => {
    const nav = harness.navigateByUrl('/');
    (await request('/api/v1/me')).flush(
      me({ flags: { ...READY, password_change_required: true } }),
    );
    await nav;
    expect(TestBed.inject(Router).url).toBe('/account/password');
  });

  it('hides the Super Admin Console from regular users', async () => {
    await signedIn('/admin/users');
    expect(TestBed.inject(Router).url).toBe('/');
  });

  it('routes a studio entry to its placeholder with the phase', async () => {
    await signedIn('/mcp');
    expect(harness.routeNativeElement!.textContent).toContain('MCP Studio');
    expect(harness.routeNativeElement!.textContent).toContain('Phase 3');
  });
});
