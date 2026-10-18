import { Component, input } from '@angular/core';
import { NzAlertModule } from 'ng-zorro-antd/alert';

/** Stand-in for studios that are not implemented yet; title and phase come from route data. */
@Component({
  selector: 'forge-placeholder',
  imports: [NzAlertModule],
  template: `
    <h1>{{ title() }}</h1>
    <nz-alert nzType="info" [nzMessage]="message" />
    <ng-template #message
      ><span i18n>This area is delivered in Phase {{ phase() }}.</span></ng-template
    >
  `,
})
export class Placeholder {
  readonly title = input.required<string>();
  readonly phase = input.required<number>();
}
