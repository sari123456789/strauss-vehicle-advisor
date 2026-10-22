import {ChangeDetectionStrategy, Component, computed, input, output, signal} from '@angular/core';
import {ELIGIBILITY, ELIGIBILITY_REASON, PROPULSION, optionLabel} from '../../core/api/labels';
import type {OptionItem} from '../../core/api/types';
import {MoneyPipe} from '../format/money.pipe';
import {CarArtComponent} from './car-art.component';
import {vehicleImageUrl} from './vehicle-images';

/**
 * One catalogue option exactly as the Business module returned it (eligibility, reasons and
 * amounts). Shared by the catalogue page and the advisor chat; the photo is only an
 * illustration and falls back to the generic car art when it is unavailable.
 */
@Component({
  selector: 'drive-vehicle-card',
  imports: [CarArtComponent, MoneyPipe],
  template: `
    <article class="panel vehicle" [class.selected]="selected()" [class.compact-card]="compact()">
      @if (imageUrl(); as src) {
        <img class="car-art compact" [src]="src" [alt]="label()" loading="lazy" decoding="async" (error)="imageFailed.set(true)" />
      } @else {
        <drive-car [compact]="true" />
      }
      <span class="eyebrow">{{ kind() }}</span>
      <h2>{{ label() }}</h2>
      <dl>
        @if (option().group) {<dt>קבוצת זכאות</dt><dd>{{ option().group }}</dd>}
        @if (option().defaultParticipation) {<dt>השתתפות חודשית לפי הקטלוג</dt><dd><bdi>{{ option().defaultParticipation | money }}</bdi></dd>}
        @if (option().valueOfUse) {<dt>שווי שימוש חודשי</dt><dd><bdi>{{ option().valueOfUse | money }}</bdi></dd>}
        @if (option().waiverAllowance) {<dt>אחזקת רכב בוויתור</dt><dd><bdi>{{ option().waiverAllowance | money }}</bdi></dd>}
        <dt>זכאות</dt><dd>{{ eligibility[option().eligibility] }}</dd>
      </dl>
      @if (!compact()) {
        @for (r of option().reasons ?? []; track r) {<p class="muted tiny">{{ reasons[r] }}</p>}
      }
      <button class="outline full" type="button" [disabled]="option().eligibility === 'ineligible'" (click)="toggle.emit(option().optionKey)">
        {{ selected() ? '✓ נבחר להשוואה' : 'להוספה להשוואה' }}
      </button>
    </article>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class VehicleCardComponent {
  readonly option = input.required<OptionItem>();
  readonly selected = input(false);
  /** Smaller card for the chat strip (no reason lines). */
  readonly compact = input(false);
  readonly toggle = output<string>();

  protected readonly eligibility = ELIGIBILITY;
  protected readonly reasons = ELIGIBILITY_REASON;
  protected readonly imageFailed = signal(false);
  protected readonly label = computed(() => optionLabel(this.option()));
  protected readonly imageUrl = computed(() => (this.imageFailed() ? null : vehicleImageUrl(this.option().catalogueId)));
  protected readonly kind = computed(() => {
    const o = this.option();
    if (o.category === 'waiver') return 'ויתור';
    if (o.category === 'family') return 'מסלול משפחה';
    return o.propulsion ? PROPULSION[o.propulsion] : 'רכב חברה';
  });
}
