import { Component, input } from '@angular/core';
import { MessageModule } from 'primeng/message';

/** Stand-in for studios that are not implemented yet; title and phase come from route data. */
@Component({
  selector: 'forge-placeholder',
  imports: [MessageModule],
  template: `
    <h1>{{ title() }}</h1>
    <p-message severity="info" i18n>This area is delivered in Phase {{ phase() }}.</p-message>
  `,
})
export class Placeholder {
  readonly title = input.required<string>();
  readonly phase = input.required<number>();
}
