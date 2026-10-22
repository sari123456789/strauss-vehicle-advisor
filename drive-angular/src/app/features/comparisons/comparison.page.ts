import {Component, OnInit, computed, inject} from '@angular/core';
import {Router, RouterLink} from '@angular/router';
import {ELIGIBILITY_REASON, FIELD_PATH, SCENARIO_STATUS, optionLabel} from '../../core/api/labels';
import type {ScenarioResult} from '../../core/api/types';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {CarArtComponent} from '../../shared/ui/car-art.component';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {CatalogStore} from '../catalog/state/catalog.store';
import {ProfileStore} from '../profile/state/profile.store';
import {vehicleImageUrl} from '../../shared/ui/vehicle-images';
import {ComparisonsStore} from './state/comparisons.store';
import {compareMessage} from '../advisor/compare-message';
import {ConversationStore} from '../advisor/state/conversation.store';

/**
 * A real comparison of the selected options against the current arrangement
 * (`POST /cases/{id}/comparisons`). Every amount is the Business `display` value; a scenario
 * that is not calculated shows its status and the missing facts instead of a number.
 */
@Component({standalone: true, imports: [RouterLink, CarArtComponent, PageHeadingComponent, MoneyPipe], templateUrl: './comparison.page.html'})
export class ComparisonPage implements OnInit {
  readonly store = inject(ComparisonsStore);
  readonly catalog = inject(CatalogStore);
  readonly statuses = SCENARIO_STATUS;
  readonly reasons = ELIGIBILITY_REASON;
  readonly fieldPath = FIELD_PATH;
  private readonly router = inject(Router);
  private readonly profile = inject(ProfileStore);
  private readonly conversations = inject(ConversationStore);
  /** Photo of the employee's current car, when it is a catalogue vehicle. */
  readonly currentImage = computed(() =>
    vehicleImageUrl(this.profile.profile()?.currentArrangement.vehicle.value?.catalogueId),
  );
  /** Shown only with the current state calculated: the page is about the difference from today. */
  readonly result = computed(() => {
    const r = this.store.result();
    return r?.current?.net ? r : null;
  });
  readonly labels = computed(() => new Map(this.catalog.options().map(o => [o.optionKey, optionLabel(o)])));

  async ngOnInit(): Promise<void> {
    if (!this.catalog.options().length) await this.catalog.load();
    if (!this.profile.profile()) void this.profile.load();
    await this.run();
  }

  async run(): Promise<void> {
    await this.store.compare(this.catalog.selected());
    const r = this.store.result();
    // Without the current state there is no difference to show: the selection goes to the chat,
    // where the advisor asks what it still needs and shows the difference itself.
    if (r && !r.current?.net) {
      const message = compareMessage(this.catalog.selectedOptions());
      this.catalog.clear();
      await this.router.navigate(['/advisor']);
      if (message) void this.conversations.send(message);
    }
  }

  deltaWords(display: string): string {
    const value = Number(display);
    if (value > 0) return 'יישאר לך יותר בכל חודש';
    if (value < 0) return 'יישאר לך פחות בכל חודש';
    return 'בלי שינוי בנטו';
  }

  imageOf(alternative: ScenarioResult): string | null {
    const option = this.catalog.options().find(o => o.optionKey === alternative.optionKey);
    return vehicleImageUrl(option?.catalogueId);
  }

  label(alternative: ScenarioResult): string {
    return this.labels().get(alternative.optionKey) ?? alternative.optionKey;
  }
}
