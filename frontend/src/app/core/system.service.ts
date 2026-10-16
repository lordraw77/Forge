import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { catchError, map, of } from 'rxjs';

export interface VersionInfo {
  name: string;
  version: string;
}

/** Backend version; replaced by the generated OpenAPI client once E1.x endpoints exist. */
@Injectable({ providedIn: 'root' })
export class SystemService {
  private readonly http = inject(HttpClient);

  readonly version = toSignal(
    this.http.get<VersionInfo>('/api/v1/version').pipe(
      map((v) => v.version),
      catchError(() => of(null)),
    ),
    { initialValue: undefined },
  );
}
