import {
  HttpClient,
  HttpErrorResponse,
  provideHttpClient,
  withInterceptors,
} from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { csrfInterceptor, problemMessage, refreshInterceptor } from './http';

describe('HTTP interceptors (ADR-011)', () => {
  let http: HttpClient;
  let backend: HttpTestingController;

  beforeEach(() => {
    document.cookie = 'forge_csrf=tok123; path=/';
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([csrfInterceptor, refreshInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    backend = TestBed.inject(HttpTestingController);
  });

  it('adds the CSRF header to unsafe API requests only', async () => {
    void firstValueFrom(http.post('/api/v1/auth/eula', { version: '1' }));
    void firstValueFrom(http.get('/api/v1/me'));
    expect(backend.expectOne('/api/v1/auth/eula').request.headers.get('X-CSRF-Token')).toBe(
      'tok123',
    );
    expect(backend.expectOne('/api/v1/me').request.headers.has('X-CSRF-Token')).toBe(false);
  });

  it('refreshes the session once on 401 and retries the request', async () => {
    const result = firstValueFrom(http.get<{ ok: boolean }>('/api/v1/projects'));
    backend.expectOne('/api/v1/projects').flush({}, { status: 401, statusText: 'Unauthorized' });
    const refresh = backend.expectOne('/api/v1/auth/refresh');
    expect(refresh.request.headers.get('X-CSRF-Token')).toBe('tok123');
    refresh.flush(null, { status: 204, statusText: 'No Content' });
    backend.expectOne('/api/v1/projects').flush({ ok: true });
    expect(await result).toEqual({ ok: true });
  });

  it('leaves the redirect to the guards when the session probe fails', async () => {
    const router = TestBed.inject(Router);
    const navigate = vi.spyOn(router, 'navigate');
    const result = firstValueFrom(http.get('/api/v1/me'));
    backend.expectOne('/api/v1/me').flush({}, { status: 401, statusText: 'Unauthorized' });
    backend.expectOne('/api/v1/auth/refresh').flush({}, { status: 401, statusText: 'Unauthorized' });
    await expect(result).rejects.toBeInstanceOf(HttpErrorResponse);
    expect(navigate).not.toHaveBeenCalled();
    backend.verify();
  });

  it('formats RFC 9457 problems', () => {
    const err = new HttpErrorResponse({
      status: 422,
      error: { title: 'Password does not meet the policy', detail: 'Password is too common' },
    });
    expect(problemMessage(err)).toBe('Password does not meet the policy: Password is too common');
  });
});
