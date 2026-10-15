import {ChangeDetectionStrategy, Component, input} from '@angular/core';

/** Empty result state. Project an optional call to action as content. */
@Component({
  selector: 'dr-empty-state',
  template: `
    <div class="state state--empty">
      <h2 class="state__title">{{ title() }}</h2>
      @if (message()) {
        <p class="state__text">{{ message() }}</p>
      }
      <ng-content />
    </div>
  `,
  styleUrl: './states.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class EmptyStateComponent {
  readonly title = input.required<string>();
  readonly message = input('');
}
