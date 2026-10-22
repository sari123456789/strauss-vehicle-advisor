import {ChangeDetectionStrategy, Component, input} from '@angular/core';
import {RouterLink} from '@angular/router';

export type OptionCardVariant = 'current' | 'electric' | 'waiver';

/** Homepage navigation card ("current car", "switch to electric", "car waiver"). Shows no amounts. */
@Component({
  selector: 'dr-option-card',
  imports: [RouterLink],
  template: `
    <a class="card" [class]="'card card--' + variant()" [class.card--selected]="selected()" [routerLink]="link()">
      @if (variant() === 'waiver') {
        <svg class="card__waiver-icon" viewBox="0 0 60 60" aria-hidden="true" focusable="false">
          <circle cx="30" cy="30" r="28" fill="none" stroke="currentColor" stroke-width="1.5" />
          <path d="M11 49L49 11" fill="none" stroke="currentColor" stroke-width="1.5" />
        </svg>
      } @else {
        <img class="card__image" [src]="imageSrc()" [attr.width]="imageWidth()" [attr.height]="imageHeight()" alt="" loading="lazy" decoding="async" />
      }
      <span class="card__text">
        <span class="card__title">{{ title() }}</span>
        <span class="card__label">{{ label() }}</span>
      </span>
      @if (variant() === 'electric') {
        <span class="card__hint">
          <svg class="card__hint-arrow" viewBox="0 0 24 28" aria-hidden="true" focusable="false">
            <path d="M12 26V3M3 12l9-9 9 9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span class="card__hint-label">ההפרש החודשי</span>
        </span>
      } @else {
        <svg class="card__chevron" viewBox="0 0 12 22" aria-hidden="true" focusable="false">
          <path d="M1.5 1.5l9 9.5-9 9.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      }
    </a>
  `,
  styleUrl: './option-card.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class OptionCardComponent {
  readonly variant = input.required<OptionCardVariant>();
  readonly title = input.required<string>();
  readonly label = input.required<string>();
  readonly link = input.required<string>();
  readonly selected = input(false);
  readonly imageSrc = input('');
  readonly imageWidth = input<number | null>(null);
  readonly imageHeight = input<number | null>(null);
}
