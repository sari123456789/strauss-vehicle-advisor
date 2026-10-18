import {ChangeDetectionStrategy, Component, computed, input} from '@angular/core';

/** Loading placeholder: a polite status announcement plus skeleton lines shaped like the content. */
@Component({
  selector: 'dr-loading-state',
  template: `
    <div class="loading" role="status" aria-live="polite" aria-busy="true">
      <span class="sr-only">{{ label() }}</span>
      @for (line of lineList(); track $index) {
        <span class="loading__line" [style.inline-size.%]="line" aria-hidden="true"></span>
      }
    </div>
  `,
  styleUrl: './states.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoadingStateComponent {
  readonly label = input('טוען…');
  readonly lines = input(3);
  protected readonly lineList = computed(() =>
    Array.from({length: Math.max(1, this.lines())}, (_, i) => (i % 3 === 2 ? 55 : 100 - (i % 3) * 15)));
}
