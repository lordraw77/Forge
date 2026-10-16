import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { routes } from './app.routes';
import { MENU } from './shell/menu';

describe('App shell (US-1.4.1)', () => {
  let harness: RouterTestingHarness;
  let http: HttpTestingController;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes, withComponentInputBinding()),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    harness = await RouterTestingHarness.create();
    http = TestBed.inject(HttpTestingController);
  });

  it('shows the nine menu entries of the spec, in order', async () => {
    await harness.navigateByUrl('/');
    const labels = Array.from(
      harness.routeNativeElement!.ownerDocument.querySelectorAll('nav a span'),
    ).map((el) => el.textContent?.trim());
    expect(labels).toEqual(MENU.map((m) => m.label));
    expect(labels.length).toBe(9);
  });

  it('shows the API version once the backend answers', async () => {
    await harness.navigateByUrl('/');
    http.expectOne('/api/v1/version').flush({ name: 'forge', version: '0.1.0' });
    harness.detectChanges();
    expect(harness.routeNativeElement!.ownerDocument.body.textContent).toContain('API 0.1.0');
  });

  it('flags the API as offline when the backend is unreachable', async () => {
    await harness.navigateByUrl('/');
    http.expectOne('/api/v1/version').flush('down', { status: 502, statusText: 'Bad Gateway' });
    harness.detectChanges();
    expect(harness.routeNativeElement!.ownerDocument.body.textContent).toContain('API offline');
  });

  it('routes a studio entry to its placeholder with the phase', async () => {
    await harness.navigateByUrl('/mcp');
    expect(harness.routeNativeElement!.textContent).toContain('MCP Studio');
    expect(harness.routeNativeElement!.textContent).toContain('Phase 3');
  });
});
