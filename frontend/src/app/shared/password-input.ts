import { ChangeDetectionStrategy, Component, input, model } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { NzInputModule } from 'ng-zorro-antd/input';

/** Password field with NG-ZORRO's show/hide toggle; the `<input>` gets `inputId` for its label. */
@Component({
  selector: 'forge-password',
  imports: [FormsModule, NzInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nz-input-password>
      <input
        nz-input
        type="password"
        [id]="inputId()"
        [name]="inputId()"
        [attr.autocomplete]="autocomplete()"
        [ngModel]="value()"
        (ngModelChange)="value.set($event)"
      />
    </nz-input-password>
  `,
  styles: `
    :host {
      display: block;
    }
  `,
})
export class PasswordInput {
  readonly inputId = input.required<string>();
  readonly autocomplete = input('current-password');
  readonly value = model('');
}
