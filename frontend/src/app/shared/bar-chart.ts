import { Component, computed, input } from '@angular/core';

export interface BarPoint {
  label: string;
  value: number;
  /** Second value drawn over the bar (for example errors inside calls). */
  overlay?: number;
}

/** Dependency-free bar chart: bars scale to the largest value, labels show on hover and to
 * screen readers. Kept in plain HTML so it works air-gapped and in the dark theme. */
@Component({
  selector: 'forge-bar-chart',
  template: `
    <div class="bars" role="img" [attr.aria-label]="title()">
      @for (b of bars(); track $index) {
        <div class="col" [title]="b.label + ': ' + b.value">
          <div class="bar" [style.height.%]="b.height">
            @if (b.overlayHeight > 0) {
              <div class="overlay" [style.height.%]="b.overlayHeight"></div>
            }
          </div>
        </div>
      } @empty {
        <p class="empty" i18n>No data in this period</p>
      }
    </div>
    @if (bars().length) {
      <div class="axis">
        <span>{{ bars()[0].label }}</span>
        <span>{{ bars()[bars().length - 1].label }}</span>
      </div>
    }
  `,
  styles: `
    .bars {
      display: flex;
      align-items: flex-end;
      gap: 2px;
      height: 8rem;
    }
    .col {
      flex: 1;
      height: 100%;
      display: flex;
      align-items: flex-end;
      min-width: 2px;
    }
    .bar {
      width: 100%;
      min-height: 2px;
      background: var(--forge-primary-color);
      border-radius: 2px 2px 0 0;
      display: flex;
      align-items: flex-end;
    }
    .overlay {
      width: 100%;
      background: var(--forge-red-500, #cf1322);
    }
    .axis {
      display: flex;
      justify-content: space-between;
      font-size: 0.75rem;
      color: var(--forge-text-muted-color);
    }
    .empty {
      color: var(--forge-text-muted-color);
      margin: auto;
    }
  `,
})
export class BarChart {
  readonly points = input.required<BarPoint[]>();
  readonly title = input('');

  protected readonly bars = computed(() => {
    const pts = this.points();
    const max = Math.max(...pts.map((p) => p.value), 0);
    return pts.map((p) => ({
      ...p,
      height: max ? (p.value / max) * 100 : 0,
      overlayHeight: p.value ? ((p.overlay ?? 0) / p.value) * 100 : 0,
    }));
  });
}
