import {
  HttpErrorResponse,
  HttpEvent,
  HttpHandlerFn,
  HttpHeaders,
  HttpInterceptorFn,
  HttpRequest,
  HttpResponse,
} from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import {
  Observable,
  catchError,
  filter,
  finalize,
  shareReplay,
  switchMap,
  take,
  throwError,
} from 'rxjs';

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export function readCookie(name: string): string | null {
  const match = document.cookie.split('; ').find((c) => c.startsWith(name + '='));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

/** Double-submit CSRF token on unsafe requests authenticated by cookie (ADR-011). */
export const csrfInterceptor: HttpInterceptorFn = (req, next) => {
  const token = readCookie('forge_csrf');
  if (UNSAFE.has(req.method) && token && req.url.startsWith('/api/')) {
    req = req.clone({ setHeaders: { 'X-CSRF-Token': token } });
  }
  return next(req);
};

let refreshing: Observable<unknown> | null = null;

/** On an expired access token, rotate the session once (shared by concurrent calls) and retry. */
export const refreshInterceptor: HttpInterceptorFn = (req, next) => {
  const router = inject(Router);
  return next(req).pipe(
    catchError((error: unknown) => {
      if (
        !(error instanceof HttpErrorResponse) ||
        error.status !== 401 ||
        req.url.includes('/auth/')
      ) {
        return throwError(() => error);
      }
      refreshing ??= refreshSession(next).pipe(
        shareReplay(1),
        finalize(() => (refreshing = null)),
      );
      return refreshing.pipe(
        switchMap(() => next(withCsrf(req))),
        catchError((refreshError: unknown) => {
          // `/me` is the guards' session probe: they redirect themselves, and navigating from
          // here would re-run the login page guard and probe again forever.
          if (!req.url.endsWith('/api/v1/me')) {
            void router.navigate(['/login'], { queryParams: { returnUrl: router.url } });
          }
          return throwError(() => refreshError);
        }),
      );
    }),
  );
};

function refreshSession(next: HttpHandlerFn): Observable<HttpEvent<unknown>> {
  const headers = new HttpHeaders({ 'X-CSRF-Token': readCookie('forge_csrf') ?? '' });
  // Only the final response counts: `next` also emits a Sent event, which must not trigger
  // a retry of its own.
  return next(new HttpRequest('POST', '/api/v1/auth/refresh', null, { headers })).pipe(
    filter((event) => event instanceof HttpResponse),
    take(1),
  );
}

function withCsrf(req: HttpRequest<unknown>): HttpRequest<unknown> {
  const token = readCookie('forge_csrf');
  return UNSAFE.has(req.method) && token
    ? req.clone({ setHeaders: { 'X-CSRF-Token': token } })
    : req;
}

/** Human-readable message from an RFC 9457 problem response. */
export function problemMessage(error: unknown, fallback = $localize`Something went wrong`): string {
  if (error instanceof HttpErrorResponse && error.error && typeof error.error === 'object') {
    const p = error.error as { title?: string; detail?: unknown; code?: string };
    let detail = typeof p.detail === 'string' ? p.detail : '';
    if (Array.isArray(p.detail)) {
      // 422 validation errors: show why, without Pydantic's "Value error, " prefix.
      detail = p.detail
        .map((d: { msg?: string }) => (d.msg ?? '').replace(/^Value error, /, ''))
        .filter(Boolean)
        .join('; ');
    }
    return [p.title, detail].filter(Boolean).join(': ') || fallback;
  }
  return fallback;
}

export function problemCode(error: unknown): string | null {
  return error instanceof HttpErrorResponse && error.error && typeof error.error === 'object'
    ? ((error.error as { code?: string }).code ?? null)
    : null;
}
