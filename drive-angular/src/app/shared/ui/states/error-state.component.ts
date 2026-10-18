import {ChangeDetectionStrategy, Component, input, output} from '@angular/core';

/**
 * Failure state with a natural Hebrew message and an optional safe retry.
 * Never pass raw HTTP statuses, stack traces or server payloads as `message`;
 * map stable server codes to Hebrew text before rendering.
 */
@Component({
  selector: 'dr-error-state',
  template: `
    <div class="state state--error" role="alert">
      <svg class="state__icon" viewBox="0 0 24 24" width="28" height="28" aria-hidden="true" focusable="false">
        <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.5" />
        <path d="M12 7v6M12 16.5v.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
      </svg>
      <h2 class="state__title">{{ title() }}</h2>
      <p class="state__text">{{ message() }}</p>
      @if (retryable()) {
        <button type="button" class="btn btn--outline" (click)="retry.emit()">{{ retryLabel() }}</button>
      }
    </div>
  `,
  styleUrl: './states.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ErrorStateComponent {
  readonly title = input('משהו השתבש');
  readonly message = input('יש תקלה זמנית, אפשר לנסות שוב.');
  readonly retryable = input(true);
  readonly retryLabel = input('לנסות שוב');
  readonly retry = output<void>();
}
