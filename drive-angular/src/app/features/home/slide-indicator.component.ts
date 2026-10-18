import {ChangeDetectionStrategy, Component, computed, input} from '@angular/core';

/** Decorative "01 / 03" indicator from the approved hero design. Not interactive; hidden from assistive tech. */
@Component({
  selector: 'dr-slide-indicator',
  template: `
    <span class="indicator" aria-hidden="true">
      <span class="indicator__count"><b>{{ pad(current()) }}</b> / {{ pad(total()) }}</span>
      @for (i of steps(); track i) {
        <span class="indicator__bar" [class.is-current]="i === current()"></span>
      }
    </span>
  `,
  styles: `
    :host { display: block; }
    .indicator { display: flex; align-items: center; gap: var(--indicator-gap, 8px); direction: ltr; color: #8a8b8c; font-size: var(--indicator-font, 14px); }
    .indicator__count { margin-inline-end: var(--indicator-count-gap, 8px); white-space: nowrap; }
    .indicator__count b { color: var(--text); font-weight: 500; }
    .indicator__bar { display: block; inline-size: var(--indicator-bar, 76px); block-size: 3px; background: #6b6c6d; }
    .indicator__bar.is-current { background: linear-gradient(to right, var(--accent) 0 51%, #8f9091 51% 100%); }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SlideIndicatorComponent {
  readonly current = input(1);
  readonly total = input(3);
  protected readonly steps = computed(() => Array.from({length: this.total()}, (_, i) => i + 1));

  protected pad(value: number): string {
    return String(value).padStart(2, '0');
  }
}
