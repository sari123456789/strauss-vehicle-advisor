import {ChangeDetectionStrategy, Component, input} from '@angular/core';

/** Vehicle photograph used by the legacy demo pages; keeps the source aspect ratio (cropped, never stretched). */
@Component({
  selector: 'drive-car',
  template: `
    <img
      class="car-art"
      [class.compact]="compact()"
      src="/assets/home/hero-car-800.webp"
      srcset="/assets/home/hero-car-800.webp 800w, /assets/home/hero-car-1240.webp 1240w"
      sizes="(min-width: 1024px) 50vw, 100vw"
      width="1672"
      height="941"
      alt="רכב חשמלי כסוף בתאורת סטודיו"
      loading="lazy"
      decoding="async" />
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CarArtComponent {
  readonly compact = input(false);
}
