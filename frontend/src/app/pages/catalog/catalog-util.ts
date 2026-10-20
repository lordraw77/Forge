import { CatalogUseCase, UseCaseRunOut } from '../../api-client';

export type CatalogView = 'all' | 'favorites' | 'recent';

export const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);

const COLOR: Record<string, string> = {
  queued: 'default',
  running: 'processing',
  waiting_approval: 'warning',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'default',
};

export function statusColor(status: string): string {
  return COLOR[status] ?? 'default';
}

export function statusLabel(status: string): string {
  switch (status) {
    case 'queued':
      return $localize`Queued`;
    case 'running':
      return $localize`Running`;
    case 'waiting_approval':
      return $localize`Waiting for approval`;
    case 'succeeded':
      return $localize`Done`;
    case 'failed':
      return $localize`Failed`;
    case 'cancelled':
      return $localize`Stopped`;
    default:
      return status;
  }
}

/** Search, category, tag and view filters of the catalog page (all done in the browser: the
 * server returns at most 500 use cases the caller may see). */
export function filterCatalog(
  items: CatalogUseCase[],
  q: string,
  categoryId: string | null,
  tag: string | null,
  view: CatalogView,
): CatalogUseCase[] {
  const needle = q.trim().toLowerCase();
  let out = items.filter(
    (i) =>
      (!needle ||
        i.name.toLowerCase().includes(needle) ||
        i.description.toLowerCase().includes(needle)) &&
      (!categoryId || i.category_id === categoryId) &&
      (!tag || i.tags.includes(tag)),
  );
  if (view === 'favorites') out = out.filter((i) => i.favorite);
  if (view === 'recent') {
    out = out
      .filter((i) => !!i.last_run_at)
      .sort((a, b) => (b.last_run_at ?? '').localeCompare(a.last_run_at ?? ''))
      .slice(0, 12);
  }
  return out;
}

/** Seconds a run took, or null while it has not ended. */
export function durationSeconds(
  run: Pick<UseCaseRunOut, 'started_at' | 'finished_at'>,
): number | null {
  if (!run.started_at || !run.finished_at) return null;
  return Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000));
}

/** The value of a form field as the API expects it. */
export function fieldValue(type: string, raw: string | undefined): unknown {
  if (raw === undefined || raw === '') return undefined;
  return type === 'number' ? Number(raw) : raw;
}

export function pretty(value: unknown): string {
  if (value === undefined || value === null) return '';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}
